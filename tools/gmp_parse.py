#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NeoX .gmp map layout reader: zstd frames + MessagePack chunks."""

from __future__ import annotations

import math
import re
import struct

import zstandard

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
MAX_MSGPACK_RAW = 64 * 1024 * 1024
OFFMAP_LIMIT = 50000.0
REF_RE = re.compile(
    r"[A-Za-z0-9_./\\-]{2,240}\.(?:gim|mesh|kta|dds|ktx|png|jpe?g|tga|bmp|webp|gif|"
    r"sfx|efx|mtg|skeleton|rawanimation|animation|animgraph|animconfig|trackgroup|"
    r"spr|csb|json|xml|plist|lua|mp4|webm)\b",
    re.IGNORECASE,
)
REF_RE_BYTES = re.compile(REF_RE.pattern.encode("ascii"), re.IGNORECASE)

class MsgpackError(ValueError):
    pass


def _read_uint(data, pos, width):
    if pos + width > len(data):
        raise MsgpackError("truncated integer")
    return int.from_bytes(data[pos:pos + width], "big"), pos + width


def _decode(data, pos=0):
    if pos >= len(data):
        raise MsgpackError("unexpected end")
    marker = data[pos]
    pos += 1
    if marker <= 0x7F:
        return marker, pos
    if marker >= 0xE0:
        return marker - 256, pos
    if 0x80 <= marker <= 0x8F:
        return _decode_map(data, pos, marker & 0x0F)
    if 0x90 <= marker <= 0x9F:
        return _decode_array(data, pos, marker & 0x0F)
    if 0xA0 <= marker <= 0xBF:
        length = marker & 0x1F
        if pos + length > len(data):
            raise MsgpackError("truncated string")
        return data[pos:pos + length].decode("utf-8", "replace"), pos + length
    if marker == 0xC0:
        return None, pos
    if marker == 0xC2:
        return False, pos
    if marker == 0xC3:
        return True, pos
    if marker in (0xC4, 0xC5, 0xC6):
        width = {0xC4: 1, 0xC5: 2, 0xC6: 4}[marker]
        length, pos = _read_uint(data, pos, width)
        if pos + length > len(data):
            raise MsgpackError("truncated binary")
        return data[pos:pos + length], pos + length
    if marker in (0xCA, 0xCB):
        if marker == 0xCA:
            if pos + 4 > len(data):
                raise MsgpackError("truncated float")
            return struct.unpack_from(">f", data, pos)[0], pos + 4
        if pos + 8 > len(data):
            raise MsgpackError("truncated double")
        return struct.unpack_from(">d", data, pos)[0], pos + 8
    if marker in (0xCC, 0xCD, 0xCE, 0xCF):
        width = {0xCC: 1, 0xCD: 2, 0xCE: 4, 0xCF: 8}[marker]
        return _read_uint(data, pos, width)
    if marker in (0xD0, 0xD1, 0xD2, 0xD3):
        width = {0xD0: 1, 0xD1: 2, 0xD2: 4, 0xD3: 8}[marker]
        if pos + width > len(data):
            raise MsgpackError("truncated signed integer")
        return int.from_bytes(data[pos:pos + width], "big", signed=True), pos + width
    if marker in (0xD4, 0xD5, 0xD6, 0xD7, 0xD8):
        length = {0xD4: 1, 0xD5: 2, 0xD6: 4, 0xD7: 8, 0xD8: 16}[marker]
        if pos + 1 + length > len(data):
            raise MsgpackError("truncated fixext")
        return data[pos + 1:pos + 1 + length], pos + 1 + length
    if marker in (0xD9, 0xDA, 0xDB):
        width = {0xD9: 1, 0xDA: 2, 0xDB: 4}[marker]
        length, pos = _read_uint(data, pos, width)
        if pos + length > len(data):
            raise MsgpackError("truncated string")
        return data[pos:pos + length].decode("utf-8", "replace"), pos + length
    if marker in (0xDC, 0xDD):
        width = 2 if marker == 0xDC else 4
        count, pos = _read_uint(data, pos, width)
        return _decode_array(data, pos, count)
    if marker in (0xDE, 0xDF):
        width = 2 if marker == 0xDE else 4
        count, pos = _read_uint(data, pos, width)
        return _decode_map(data, pos, count)
    if marker in (0xC7, 0xC8, 0xC9):
        width = {0xC7: 1, 0xC8: 2, 0xC9: 4}[marker]
        length, pos = _read_uint(data, pos, width)
        if pos + 1 + length > len(data):
            raise MsgpackError("truncated ext")
        return data[pos + 1:pos + 1 + length], pos + 1 + length
    raise MsgpackError(f"unsupported marker 0x{marker:02x}")


def _decode_array(data, pos, count):
    result = []
    for _ in range(count):
        value, pos = _decode(data, pos)
        result.append(value)
    return result, pos


def _decode_map(data, pos, count):
    result = {}
    for _ in range(count):
        key, pos = _decode(data, pos)
        value, pos = _decode(data, pos)
        try:
            result[key] = value
        except TypeError:
            result[str(key)] = value
    return result, pos


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return str(value)


def _key(mapping, *names):
    if not isinstance(mapping, dict):
        return None
    for name in names:
        if name in mapping:
            return mapping[name]
        if name.encode("utf-8") in mapping:
            return mapping[name.encode("utf-8")]
    return None


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def _position(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    values = [_number(item) for item in value[:3]]
    if any(item is None for item in values):
        return None
    while len(values) < 3:
        values.append(0.0)
    return values


def _vec(value, count):
    if not isinstance(value, (list, tuple)):
        return None
    values = [_number(item) for item in value[:count]]
    if any(item is None for item in values):
        return None
    return [round(item, 3) for item in values]


def _color(value):
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    result = []
    for item in value[:3]:
        number = _number(item)
        if number is None:
            return None
        result.append(max(0, min(255, int(round(number)))))
    return result


def _walk(value, budget):
    if budget[0] <= 0:
        return
    budget[0] -= 1
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item, budget)
    elif isinstance(value, (list, tuple)):
        if value and all(not isinstance(item, (dict, list, tuple)) for item in value[:8]):
            return
        for item in value:
            yield from _walk(item, budget)


def _looks_msgpack(raw: bytes) -> bool:
    if not raw:
        return False
    marker = raw[0]
    return 0x80 <= marker <= 0x9F or 0xDC <= marker <= 0xDF


def _frames(payload: bytes):
    pos = 0
    while True:
        start = payload.find(ZSTD_MAGIC, pos)
        if start < 0:
            break
        decompressor = zstandard.ZstdDecompressor().decompressobj()
        try:
            raw = decompressor.decompress(payload[start:])
        except zstandard.ZstdError:
            pos = start + 4
            continue
        if not getattr(decompressor, "eof", True):
            pos = start + 4
            continue
        consumed = len(payload) - start - len(decompressor.unused_data)
        if consumed <= 0:
            pos = start + 4
            continue
        yield start, consumed, raw
        pos = start + consumed


def parse(payload: bytes) -> dict:
    if not payload.startswith(b"DZ") and ZSTD_MAGIC not in payload[:4096]:
        raise ValueError("not a GMP container")
    chunks = []
    objects = []
    references = set()
    for index, (offset, compressed, raw) in enumerate(_frames(payload)):
        chunk = {"index": index, "offset": offset, "compressed": compressed, "raw": len(raw)}
        skipped = len(raw) > MAX_MSGPACK_RAW
        try:
            if skipped:
                value, end = None, 0
            else:
                value, end = _decode(raw)
        except (MsgpackError, RecursionError):
            value, end = None, 0
        if isinstance(value, (dict, list)):
            objects.append(value)
            chunk["kind"] = "msgpack"
            chunk["consumed"] = end
        elif skipped and _looks_msgpack(raw):
            chunk["kind"] = "msgpack"
            chunk["consumed"] = 0
            chunk["skipped"] = True
        else:
            chunk["kind"] = "binary"
            chunk["consumed"] = 0
        chunks.append(chunk)
        if len(references) < 200:
            for match in REF_RE_BYTES.finditer(raw):
                references.add(match.group().decode("ascii", "ignore").replace("\\", "/"))
                if len(references) >= 200:
                    break
    if not chunks:
        raise ValueError("no zstd chunks found")

    map_name = None
    party_map_id = None
    temp_map_id = None
    egg_island_scene = None
    units = {}
    eids = {}
    type_counts = {}

    for root in objects:
        budget = [1500000]
        for node in _walk(root, budget):
            if isinstance(node, dict):
                if map_name is None:
                    value = _key(node, "map_name")
                    if value is not None:
                        map_name = _text(value)
                if party_map_id is None:
                    value = _key(node, "party_map_id")
                    if isinstance(value, int):
                        party_map_id = value
                if temp_map_id is None:
                    value = _key(node, "temp_map_id")
                    if isinstance(value, int):
                        temp_map_id = value
                if egg_island_scene is None:
                    value = _key(node, "egg_island_scene")
                    if isinstance(value, bool):
                        egg_island_scene = value
                group = _key(node, "unit", "units")
                if not isinstance(group, dict):
                    group = _key(node, "batch_unit_data")
                if isinstance(group, dict):
                    for unit_key, unit in group.items():
                        if not isinstance(unit, dict):
                            continue
                        position = _position(_key(unit, "ctx_world_position", "world_position", "position"))
                        if position is None:
                            continue
                        unit_id = _key(unit, "unit_id")
                        if not isinstance(unit_id, int):
                            unit_id = unit_key if isinstance(unit_key, int) else None
                        if unit_id is None or unit_id in units:
                            continue
                        name = _text(_key(unit, "name") or "")
                        unit_type = _key(unit, "type", "unit_type")
                        model_id = _key(unit, "model_id")
                        model = _text(_key(unit, "ctx_model_path", "model_path", "model") or "")
                        material = _text(_key(unit, "mat_path", "material_path") or "")
                        if not name and model:
                            name = model.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                        eid = _key(unit, "eid")
                        if not isinstance(eid, int) and isinstance(model_id, int):
                            eid = model_id
                        camp = _key(unit, "camp_id")
                        parent = _key(unit, "parent_unit_id")
                        color = _color(_key(unit, "paint_area1"))
                        scale = _vec(_key(unit, "ctx_real_scale", "real_scale", "scale"), 3)
                        angle = _vec(_key(unit, "ctx_model_angle", "model_angle"), 3)
                        units[unit_id] = {
                            "id": unit_id,
                            "name": name,
                            "type": unit_type if isinstance(unit_type, int) else None,
                            "eid": eid if isinstance(eid, int) else None,
                            "model_id": model_id if isinstance(model_id, int) else None,
                            "model": model,
                            "material": material,
                            "camp": camp if isinstance(camp, int) else None,
                            "parent": parent if isinstance(parent, int) else 0,
                            "position": [round(item, 3) for item in position],
                            "color": color,
                            "scale": scale,
                            "angle": angle,
                        }
                        if isinstance(eid, int):
                            eids[eid] = eids.get(eid, 0) + 1
                        if isinstance(unit_type, int):
                            type_counts[unit_type] = type_counts.get(unit_type, 0) + 1

    unit_list = list(units.values())
    positions = [unit["position"] for unit in unit_list]
    bounds = _bounds(positions)
    unit_list.sort(key=lambda item: (item["eid"] or 0, item["name"], item["id"]))
    return {
        "map_name": map_name,
        "party_map_id": party_map_id,
        "temp_map_id": temp_map_id,
        "egg_island_scene": egg_island_scene,
        "unit_count": len(unit_list),
        "units": unit_list[:12000],
        "unit_truncated": len(unit_list) > 12000,
        "eids": {str(key): value for key, value in sorted(eids.items())},
        "types": {str(key): value for key, value in sorted(type_counts.items())},
        "references": sorted(references)[:200],
        "chunks": chunks,
        "parsed_chunks": sum(1 for chunk in chunks if chunk["kind"] == "msgpack"),
        "skipped_chunks": sum(1 for chunk in chunks if chunk.get("skipped")),
        "binary_chunks": sum(1 for chunk in chunks if chunk["kind"] == "binary"),
        "bounds": bounds,
    }


def _bounds(positions):
    positions = [
        item for item in positions
        if all(abs(value) < OFFMAP_LIMIT for value in item)
    ]
    if not positions:
        return None
    xs = [item[0] for item in positions]
    ys = [item[1] for item in positions]
    zs = [item[2] for item in positions]
    range_z = max(zs) - min(zs)
    range_y = max(ys) - min(ys)
    axis = "xz" if range_z > 1e-6 or range_y <= 1e-6 else "xy"
    first = xs
    second = zs if axis == "xz" else ys
    return {
        "axis": axis,
        "min_x": round(min(first), 3),
        "max_x": round(max(first), 3),
        "min_y": round(min(second), 3),
        "max_y": round(max(second), 3),
    }


