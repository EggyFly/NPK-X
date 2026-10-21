#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only parser for Wwise BNK banks used by the NPK browser.

The parser keeps the original bank bytes and exposes the chunk table, embedded
WEM media (DIDX/DATA), and the HIRC object summary.  It never decodes audio.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

HIRC_TYPES = {
    1: "State",
    2: "Sound",
    3: "Action",
    4: "Event",
    5: "Random/Sequence",
    6: "Switch Container",
    7: "Actor-Mixer",
    8: "Bus",
    9: "Layer Container",
    10: "Music Segment",
    11: "Music Track",
    12: "Music Switch",
    13: "Music Playlist",
    14: "Attenuation",
    15: "Dialogue Event",
}


class BankFormatError(ValueError):
    pass


def _u32(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise BankFormatError("truncated u32")
    return struct.unpack_from("<I", data, offset)[0]


def _chunk_id(raw: bytes) -> str:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return "0x" + raw.hex()
    return text if text.isprintable() else "0x" + raw.hex()


def _wem_info(payload: bytes) -> dict[str, Any]:
    info: dict[str, Any] = {
        "container": None,
        "codec": None,
        "channels": None,
        "sample_rate": None,
        "bits": None,
        "data_size": None,
    }
    if len(payload) < 12:
        return info
    if payload[:4] == b"RIFF" and payload[8:12] == b"WAVE":
        info["container"] = "RIFF/WAVE"
    elif payload[:4] == b"RIFX" and payload[8:12] == b"WAVE":
        info["container"] = "RIFX/WAVE"
    else:
        return info
    pos = 12
    while pos + 8 <= len(payload):
        cid = payload[pos:pos + 4]
        size = _u32(payload, pos + 4)
        body = payload[pos + 8:pos + 8 + size]
        if cid == b"fmt " and len(body) >= 16:
            tag, channels, rate, _byte_rate, _align, bits = struct.unpack_from("<HHIIHH", body)
            info.update({"channels": channels, "sample_rate": rate, "bits": bits})
            info["codec"] = {
                0x0001: "PCM",
                0x0002: "ADPCM",
                0x0003: "IEEE Float",
                0x0011: "IMA ADPCM",
                0x0055: "MPEG Layer 3",
                0x0161: "WMA",
                0x0166: "AAC",
                0xFFFF: "Vorbis",
            }.get(tag, f"0x{tag:04x}")
        elif cid == b"vorb":
            info["codec"] = "Vorbis"
        elif cid == b"data":
            info["data_size"] = size
        pos += 8 + size
        if size & 1:
            pos += 1
    return info


@dataclass
class Media:
    index: int
    media_id: int
    offset: int
    size: int
    valid: bool
    container: str | None
    codec: str | None
    channels: int | None
    sample_rate: int | None
    bits: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "id": self.media_id,
            "id_hex": f"{self.media_id:08x}",
            "offset": self.offset,
            "size": self.size,
            "valid": self.valid,
            "container": self.container,
            "codec": self.codec,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "bits": self.bits,
        }


@dataclass
class Bank:
    data: bytes
    header: dict[str, Any] = field(default_factory=dict)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    media: list[Media] = field(default_factory=list)
    hirc: dict[str, Any] = field(
        default_factory=lambda: {
            "count": 0, "types": [], "objects": [], "music_tracks": [], "truncated": False
        }
    )
    strings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _data_chunk: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "Wwise BNK",
            "header": self.header,
            "chunks": self.chunks,
            "media": [item.as_dict() for item in self.media],
            "hirc": self.hirc,
            "strings": self.strings[:200],
            "warnings": self.warnings,
        }

    def media_payload(self, index: int | None = None, media_id: int | None = None) -> tuple[Media, bytes]:
        item = None
        if index is not None:
            if 0 <= index < len(self.media):
                item = self.media[index]
        elif media_id is not None:
            item = next((candidate for candidate in self.media if candidate.media_id == media_id), None)
        if item is None:
            raise BankFormatError("embedded WEM not found")
        if not item.valid or self._data_chunk is None:
            raise BankFormatError("embedded WEM is outside the DATA chunk")
        return item, self._data_chunk[item.offset:item.offset + item.size]


def parse_bank(data: bytes) -> Bank:
    if len(data) < 8 or data[:4] != b"BKHD":
        raise BankFormatError("not a Wwise BKHD bank")
    bank = Bank(data=data)
    pos = 0
    didx: list[tuple[int, int, int]] = []
    data_chunk: bytes | None = None
    hirc_payload: bytes | None = None
    stid_payload: bytes | None = None
    while pos + 8 <= len(data):
        if len(bank.chunks) >= 128:
            bank.warnings.append("too many chunks; stopped at 128")
            break
        raw_id = data[pos:pos + 4]
        size = _u32(data, pos + 4)
        end = pos + 8 + size
        if end > len(data):
            raise BankFormatError(f"truncated {raw_id!r} chunk at 0x{pos:x}")
        payload = data[pos + 8:end]
        chunk_id = _chunk_id(raw_id)
        bank.chunks.append({"id": chunk_id, "offset": pos, "size": size})
        if raw_id == b"BKHD":
            if len(payload) >= 16:
                words = list(struct.unpack_from("<4I", payload))
                bank.header = {
                    "version": words[0],
                    "bank_id": words[1],
                    "bank_id_hex": f"{words[1]:08x}",
                    "header_words": [f"0x{word:08x}" for word in words[2:]],
                    "chunk_size": size,
                    "raw_hex": payload.hex(),
                }
            else:
                bank.header = {"chunk_size": size, "raw_hex": payload.hex()}
                bank.warnings.append("short BKHD header")
        elif raw_id == b"DIDX":
            if size % 12:
                bank.warnings.append("DIDX size is not a multiple of 12")
            for offset in range(0, size - (size % 12), 12):
                didx.append(struct.unpack_from("<3I", payload, offset))
        elif raw_id == b"DATA":
            if data_chunk is None:
                data_chunk = payload
            else:
                bank.warnings.append("multiple DATA chunks; using the first")
        elif raw_id == b"HIRC":
            hirc_payload = payload
        elif raw_id == b"STID":
            stid_payload = payload
        pos = end
    if pos < len(data):
        trailing = data[pos:]
        if len(trailing) > 15 or any(trailing):
            bank.warnings.append(f"{len(trailing)} trailing bytes after the last chunk")
    if data_chunk is None and didx:
        bank.warnings.append("DIDX exists without a DATA chunk")
    bank._data_chunk = data_chunk
    for index, (media_id, offset, size) in enumerate(didx):
        valid = data_chunk is not None and offset + size <= len(data_chunk)
        payload = data_chunk[offset:offset + size] if valid else b""
        info = _wem_info(payload)
        bank.media.append(
            Media(
                index=index,
                media_id=media_id,
                offset=offset,
                size=size,
                valid=valid,
                container=info["container"],
                codec=info["codec"],
                channels=info["channels"],
                sample_rate=info["sample_rate"],
                bits=info["bits"],
            )
        )
    if hirc_payload is not None:
        bank.hirc = _parse_hirc(hirc_payload, bank.warnings)
    if stid_payload is not None:
        bank.strings = _printable_strings(stid_payload)
    return bank


def _parse_hirc(payload: bytes, warnings: list[str]) -> dict[str, Any]:
    if len(payload) < 4:
        warnings.append("short HIRC chunk")
        return {"count": 0, "types": [], "objects": [], "truncated": False}
    count = _u32(payload, 0)
    pos = 4
    objects: list[dict[str, Any]] = []
    music_tracks: list[dict[str, Any]] = []
    counts: dict[int, int] = {}
    truncated = False
    for index in range(count):
        if pos + 5 > len(payload):
            warnings.append("HIRC object table is truncated")
            truncated = True
            break
        object_type = payload[pos]
        size = _u32(payload, pos + 1)
        if pos + 5 + size > len(payload):
            warnings.append("HIRC object exceeds the chunk")
            truncated = True
            break
        body = payload[pos + 5:pos + 5 + size]
        counts[object_type] = counts.get(object_type, 0) + 1
        if object_type == 11 and len(body) >= 18:
            object_id = struct.unpack_from("<I", body, 0)[0]
            music_tracks.append(
                {"id": object_id, "media_id": struct.unpack_from("<I", body, 14)[0]}
            )
        if len(objects) < 300:
            object_id = struct.unpack_from("<I", body, 0)[0] if len(body) >= 4 else None
            media_id = None
            if object_type == 11 and len(body) >= 18:
                media_id = struct.unpack_from("<I", body, 14)[0]
            objects.append(
                {
                    "index": index,
                    "type": object_type,
                    "type_name": HIRC_TYPES.get(object_type, f"Type {object_type}"),
                    "id": object_id,
                    "id_hex": f"{object_id:08x}" if object_id is not None else None,
                    "media_id": media_id,
                    "size": size,
                }
            )
        pos += 5 + size
    types = [
        {"type": key, "name": HIRC_TYPES.get(key, f"Type {key}"), "count": value}
        for key, value in sorted(counts.items())
    ]
    return {
        "count": count,
        "types": types,
        "objects": objects,
        "music_tracks": music_tracks,
        "truncated": truncated or len(objects) < count,
    }


def _printable_strings(payload: bytes) -> list[str]:
    strings: list[str] = []
    current = bytearray()
    for byte in payload:
        if 32 <= byte < 127:
            current.append(byte)
        else:
            if len(current) >= 3:
                text = current.decode("ascii")
                if text not in strings:
                    strings.append(text)
            current.clear()
    if len(current) >= 3:
        text = current.decode("ascii")
        if text not in strings:
            strings.append(text)
    return strings
