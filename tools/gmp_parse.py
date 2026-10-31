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
    if not isinstance(value, (list, tuple)) or len(value) < count:
        return None
    values = [_number(item) for item in value[:count]]
    if any(item is None for item in values):
        return None
    return [round(item, 3) for item in values]


def _first_vector(mapping, names, convert):
    for name in names:
        value = convert(_key(mapping, name))
        if value is not None:
            return value
    return None


def _parse_unit(unit_id, unit):
    position = _first_vector(unit, ("ctx_world_position", "world_position", "position"), _position)
    if position is None:
        return None
    name = _text(_key(unit, "name") or "")
    unit_type = _key(unit, "type", "unit_type")
    model_id = _key(unit, "model_id")
    model = _key(unit, "ctx_model_path", "model_path", "model")
    if isinstance(model, int):
        if not isinstance(model_id, int):
            model_id = model
        model = ""
    model = _text(model or "")
    material = _text(_key(unit, "mat_path", "material_path") or "")
    if not name and model:
        name = model.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # 组件编号和模型编号属于不同表，不能用 model_id 冒充 eid。
    eid = _key(unit, "eid")
    camp = _key(unit, "camp_id")
    parent = _key(unit, "parent_unit_id")
    color = _color(_key(unit, "paint_area1"))
    paint = [color, _color(_key(unit, "paint_area2")), _color(_key(unit, "paint_area3"))]
    scale = _first_vector(unit, ("ctx_real_scale", "real_scale", "scale"), lambda value: _vec(value, 3))
    angle = _first_vector(unit, ("ctx_model_angle", "trans_model_angle", "model_angle"), lambda value: _vec(value, 3))
    alpha = _number(_key(unit, "ctx_model_alpha"))
    if alpha is not None:
        # 批次缓存使用字节透明度，普通组件使用 0 到 1 的透明度。
        alpha /= 255.0
    else:
        alpha = _number(_key(unit, "model_alpha"))
    visible = _key(unit, "ctx_model_visible", "model_visible", "init_model_visible")
    return {
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
        "paint": paint,
        "alpha": round(alpha, 3) if alpha is not None else None,
        "visible": bool(visible) if isinstance(visible, (bool, int)) else True,
        "scale": scale,
        "angle": angle,
    }


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


def _find_map_name(value, depth=0):
    if depth > 30:
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("map_name", b"map_name"):
                return item
        for item in value.values():
            found = _find_map_name(item, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for item in value[:400]:
            found = _find_map_name(item, depth + 1)
            if found is not None:
                return found
    return None


def read_map_name(payload: bytes) -> str | None:
    if not payload.startswith(b"DZ") and ZSTD_MAGIC not in payload[:4096]:
        raise ValueError("not a GMP container")
    sections = _section_formats(payload)
    for index, (offset, compressed, raw) in enumerate(_frames(payload)):
        if index >= 5:
            break
        if len(raw) > 2 * 1024 * 1024 or sections.get(offset, {}).get("encoding") == "bindict":
            continue
        try:
            value, end = _decode(raw)
        except (MsgpackError, RecursionError):
            continue
        if end != len(raw):
            continue
        found = _find_map_name(value)
        if found is not None:
            text = _text(found).strip()
            if text:
                return text
    return None


def _section_formats(payload: bytes) -> dict:
    if not payload.startswith(b"DZ") or len(payload) < 34:
        return {}
    has_bindict_flag = bool(struct.unpack_from("!I", payload, 22)[0] & 2)
    count = struct.unpack_from("!H", payload, 32)[0]
    stride = 8 if has_bindict_flag else 6
    position = 34 + count * stride
    if not count or position > len(payload):
        return {}
    sections = {}
    for index in range(count):
        fields = struct.unpack_from("!HHI" if has_bindict_flag else "!HI", payload, 34 + index * stride)
        section_id, size = fields[0], fields[-1]
        if position + size > len(payload):
            return {}
        # 原生 bindict 与 MessagePack 都使用 Zstd 压缩，须按段表判断编码。
        encoding = "bindict" if has_bindict_flag and fields[1] else "msgpack"
        sections[position] = {"section_id": section_id, "encoding": encoding}
        position += size
    return sections if position == len(payload) else {}


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
    sections = _section_formats(payload)
    for index, (offset, compressed, raw) in enumerate(_frames(payload)):
        chunk = {"index": index, "offset": offset, "compressed": compressed, "raw": len(raw)}
        chunk.update(sections.get(offset, {}))
        unsupported = chunk.get("encoding") == "bindict"
        skipped = len(raw) > MAX_MSGPACK_RAW
        try:
            if skipped or unsupported:
                value, end = None, 0
            else:
                value, end = _decode(raw)
        except (MsgpackError, RecursionError):
            value, end = None, 0
        if unsupported:
            chunk["kind"] = "unsupported"
            chunk["consumed"] = 0
        elif isinstance(value, (dict, list)) and end == len(raw):
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
    unit_records = {}
    batch_records = {}
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
                for names, records in ((("unit", "units"), unit_records), (("batch_unit_data",), batch_records)):
                    group = _key(node, *names)
                    if not isinstance(group, dict):
                        continue
                    for unit_key, unit in group.items():
                        if not isinstance(unit, dict):
                            continue
                        unit_id = _key(unit, "unit_id")
                        if not isinstance(unit_id, int):
                            unit_id = unit_key if isinstance(unit_key, int) else None
                        if unit_id is None:
                            continue
                        records.setdefault(unit_id, {}).update({_text(key): value for key, value in unit.items()})

    unit_list = []
    for unit_id in unit_records.keys() | batch_records.keys():
        # 普通记录保留名称和组件类型，批次记录补充最终模型、材质和世界变换。
        normal = unit_records.get(unit_id, {})
        merged = dict(normal)
        merged.update(batch_records.get(unit_id, {}))
        for key in ("name", "eid", "type", "unit_type", "camp_id", "parent_unit_id"):
            if key in normal:
                merged[key] = normal[key]
        unit = _parse_unit(unit_id, merged)
        if unit is None:
            continue
        unit_list.append(unit)
        eid, unit_type = unit["eid"], unit["type"]
        if isinstance(eid, int):
            eids[eid] = eids.get(eid, 0) + 1
        if isinstance(unit_type, int):
            type_counts[unit_type] = type_counts.get(unit_type, 0) + 1
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
        "unsupported_chunks": sum(1 for chunk in chunks if chunk["kind"] == "unsupported"),
        "unit_data_unsupported": any(chunk["kind"] == "unsupported" and chunk.get("section_id") == 3 for chunk in chunks),
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


