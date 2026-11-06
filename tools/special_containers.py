#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import io
import math
import struct
import zlib

import zstandard

SAMPLE_ROWS = 500
MAX_CONSTS = 300
MAX_PROTOS = 64
MAX_PROTO_DEPTH = 60
MAX_STR = 400
LIST_CHUNK = 256 * 1024

LUAC_DATA = b"\x19\x93\x0d\x0a\x1a\x0a"
LUAC_INT = 0x5678
LUAC_NUM = 370.5


class SpecialError(ValueError):
    pass


def match_name(name: str):
    lower = (name or "").lower()
    if lower == "path_hash_file":
        return "path_hash"
    if lower == "path_hash_file_full_res_list":
        return "path_list"
    if lower.startswith("auction_goods_name"):
        return "lua_chunk"
    if lower == "rv":
        return "res_version"
    return None


def parse_path_hash(raw: bytes, limit: int = SAMPLE_ROWS) -> dict:
    if raw[:4] != b"CCZ!":
        raise SpecialError("path_hash_file magic CCZ! missing")
    zlib_off = raw.find(b"\x78\x9c", 4)
    if zlib_off < 0:
        raise SpecialError("path_hash_file has no zlib payload")
    plain = zlib.decompress(raw[zlib_off:])
    if len(plain) % 8:
        raise SpecialError("path_hash_file payload is not 8-byte records")
    sample = []
    total = 0
    for fnv, length in struct.iter_unpack("<II", plain):
        total += 1
        if len(sample) < limit:
            sample.append(["0x%08x" % fnv, length])
    return {
        "kind": "path_hash",
        "header_hex": raw[:16].hex(" "),
        "zlib_offset": zlib_off,
        "payload_size": len(plain),
        "record_count": total,
        "sample": sample,
        "note": "special.sampleCut" if total > limit else None,
    }


def parse_path_list(raw: bytes, offset: int = 0, limit: int = SAMPLE_ROWS) -> dict:
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        stream = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw))
    else:
        stream = io.BytesIO(raw)
    total = 0
    plain_bytes = 0
    rows = []
    pending = b""
    while True:
        block = stream.read(LIST_CHUNK)
        if not block:
            break
        plain_bytes += len(block)
        parts = (pending + block).split(b"\n")
        pending = parts.pop()
        for part in parts:
            total += 1
            if offset < total <= offset + limit:
                rows.append(part.decode("utf-8", "replace"))
    if pending:
        total += 1
        if offset < total <= offset + limit:
            rows.append(pending.decode("utf-8", "replace"))
    return {
        "kind": "path_list",
        "byte_size": plain_bytes,
        "line_count": total,
        "offset": offset,
        "rows": rows,
        "note": "special.pageCut" if total > limit else None,
    }


class _LuaReader:
    __slots__ = ("data", "pos")

    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def byte(self):
        if self.pos >= len(self.data):
            raise SpecialError("lua chunk truncated")
        value = self.data[self.pos]
        self.pos += 1
        return value

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise SpecialError("lua chunk truncated")
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def varint(self):
        value = 0
        for _ in range(10):
            byte = self.byte()
            value = (value << 7) | (byte & 0x7F)
            if byte & 0x80:
                return value
        raise SpecialError("lua varint overflow")

    def integer(self):
        return int.from_bytes(self.take(8), "little", signed=True)

    def number(self):
        return struct.unpack("<d", self.take(8))[0]

    def string(self):
        size = self.varint()
        if size == 0:
            return None
        return self.take(size - 1)


def _lua_text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace")


def _lua_proto(reader: _LuaReader, depth: int) -> dict:
    if depth > MAX_PROTO_DEPTH:
        raise SpecialError("lua proto nesting too deep")
    proto = {
        "source": _lua_text(reader.string()),
        "linedefined": reader.varint(),
        "lastlinedefined": reader.varint(),
        "numparams": reader.byte(),
        "is_vararg": bool(reader.byte()),
        "maxstacksize": reader.byte(),
    }
    proto["code_count"] = reader.varint()
    reader.take(proto["code_count"] * 4)
    constants = []
    const_count = reader.varint()
    for _ in range(const_count):
        tag = reader.byte()
        if tag == 0:
            continue
        if tag == 1:
            constants.append(False)
        elif tag == 0x11:
            constants.append(True)
        elif tag == 3:
            constants.append(reader.integer())
        elif tag == 0x13:
            value = reader.number()
            constants.append(value if math.isfinite(value) else repr(value))
        elif tag in (4, 0x14):
            text = _lua_text(reader.string())
            constants.append(text[:MAX_STR] if len(text) > MAX_STR else text)
        else:
            raise SpecialError("unknown lua constant tag 0x%02x" % tag)
    proto["constant_count"] = const_count
    proto["constants"] = constants[:MAX_CONSTS]
    upvalue_count = reader.varint()
    reader.take(upvalue_count * 3)
    proto_count = reader.varint()
    protos = [_lua_proto(reader, depth + 1) for _ in range(proto_count)]
    proto["proto_count"] = proto_count
    proto["protos"] = protos[:MAX_PROTOS]
    line_count = reader.varint()
    reader.take(line_count)
    abs_count = reader.varint()
    for _ in range(abs_count):
        reader.varint()
        reader.varint()
    loc_count = reader.varint()
    for _ in range(loc_count):
        reader.string()
        reader.varint()
        reader.varint()
    proto["upvalue_names"] = [_lua_text(reader.string()) for _ in range(reader.varint())][:16]
    return proto


def parse_lua_chunk(raw: bytes) -> dict:
    if len(raw) < 32:
        raise SpecialError("lua chunk too small")
    if raw[0] != 0x04:
        raise SpecialError("not a NeoX luac container (first byte 0x%02x)" % raw[0])
    reader = _LuaReader(raw, 1)
    version = reader.byte()
    if version != 0x54:
        raise SpecialError("unsupported lua version 0x%02x" % version)
    fmt = reader.byte()
    if reader.take(6) != LUAC_DATA:
        raise SpecialError("bad LUAC_DATA check")
    sizes = reader.take(3)
    if reader.integer() != LUAC_INT or reader.number() != LUAC_NUM:
        raise SpecialError("bad LUAC_INT/LUAC_NUM check")
    main_nup = reader.byte()
    main = _lua_proto(reader, 0)
    if reader.pos != len(raw):
        raise SpecialError("lua chunk trailing bytes: %d" % (len(raw) - reader.pos))
    return {
        "kind": "lua_chunk",
        "lua_version": "5.%d" % (version & 0x0F),
        "format": fmt,
        "sizes": "%d/%d/%d" % (sizes[0], sizes[1], sizes[2]),
        "size": len(raw),
        "main_upvalues": main_nup,
        "source": main["source"],
        "main": main,
        "note": "special.luaBytecode",
    }


def parse_res_version(raw: bytes) -> dict:
    text = raw.decode("ascii", "replace").strip()
    if not text.isdigit():
        raise SpecialError("rv is not an ASCII decimal revision")
    return {
        "kind": "res_version",
        "value": text,
        "size": len(raw),
        "note": "special.rvMeaning",
    }


def parse_special(kind: str, payload: bytes, offset: int = 0, limit: int = SAMPLE_ROWS) -> dict:
    if kind == "path_hash":
        return parse_path_hash(payload, limit)
    if kind == "path_list":
        return parse_path_list(payload, offset, limit)
    if kind == "lua_chunk":
        return parse_lua_chunk(payload)
    if kind == "res_version":
        return parse_res_version(payload)
    raise SpecialError("unknown special kind " + kind)
