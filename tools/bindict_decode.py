#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NeoX bindict section decoder.

容器布局:
  [0:4]   u32 字符串数
  [4:8]   0
  [8:8+4n] u32 字符串结束偏移
  [..]     字符串池 utf-8
  [rs]     u32 main_len（rs + main_len = root）
  [rs+4..root) 主区：值流（子值先写，父值后写）
  [root..]    索引节点

节点 tag: 低 4 位是类型，高 4 位是修饰位
  0x0 修饰位 0x10 = 带 key 类型字节, 0x20 = 带 value/元素类型字节
  0x40 = 条目表（u32 键指纹 + u32 偏移 / u32 偏移）, 0x80 = 类实例
  类型 6 = 字典, 7 = 列表, 8 = 类实例, 9 = 结构体(类定义)
值 tag:
  0x01 无符号 varint, 0x11 zigzag varint, 0x03 bool(1B), 0x04 None
  0x05 字符串池下标, 0x0b 主区偏移引用, 0x12 f32, 0x22 f64
  0x06 内联字典, 0x07 内联列表, 0x27 带元素 tag 的列表
  0x36 带 key/value tag 的字典, 0x86/0xc6 类实例
"""

from __future__ import annotations

import struct

MAX_STRINGS = 400000
MAX_COUNT = 200000
MAX_DEPTH = 48
MAX_CLASS_FIELDS = 20000
MAX_UNIT_RECORDS = 200000


def read_leb(data: bytes, pos: int, end: int | None = None):
    value = 0
    shift = 0
    end = len(data) if end is None else end
    while pos < end and shift <= 63:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos
    return None, pos


def _fix_mojibake(text: str) -> str:
    if not text or all(ord(char) < 128 for char in text):
        return text
    try:
        return text.encode("gbk").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def parse_strings(data: bytes):
    if len(data) < 12:
        return None
    count = struct.unpack_from("<I", data, 0)[0]
    if not count or count > MAX_STRINGS:
        return None
    blob_start = 8 + count * 4
    if blob_start > len(data):
        return None
    strings = []
    begin = 0
    for index in range(count):
        end = struct.unpack_from("<I", data, 8 + index * 4)[0]
        if end < begin or blob_start + end > len(data):
            return None
        raw = data[blob_start + begin:blob_start + end]
        strings.append(raw.decode("utf-8", "replace"))
        begin = end
    row_start = blob_start + begin
    if row_start + 4 > len(data):
        return None
    main_len = struct.unpack_from("<I", data, row_start)[0]
    if main_len < 4 or main_len > 64 * 1024 * 1024:
        return None
    root = row_start + main_len
    if root + 2 > len(data):
        return None
    return strings, row_start, main_len, root


class Bindict:
    def __init__(self, raw: bytes):
        self.raw = raw
        parsed = parse_strings(raw)
        if not parsed:
            raise ValueError("bad bindict header")
        self.strings, self.row_start, self.main_len, self.root = parsed
        self.body = raw[self.row_start:]
        self._defs = {}

    def text(self, index):
        if isinstance(index, int) and 0 <= index < len(self.strings):
            return self.strings[index]
        return None

    def definition(self, offset: int):
        if offset in self._defs:
            return self._defs[offset]
        if not 0 <= offset < len(self.body):
            return None
        count, pos = read_leb(self.body, offset, len(self.body))
        if not count or count > MAX_CLASS_FIELDS:
            return None
        tracked, pos = read_leb(self.body, pos, len(self.body))
        if tracked is None or tracked > count:
            tracked = count
        fields = []
        for _ in range(count):
            fid, pos = read_leb(self.body, pos, len(self.body))
            if fid is None or pos >= len(self.body):
                return None
            fields.append((fid, self.body[pos]))
            pos += 1
        self._defs[offset] = (fields, tracked)
        return self._defs[offset]

    def scalar(self, pos: int, tag: int, depth: int = 0):
        body = self.body
        if tag == 0x01:
            return read_leb(body, pos, len(body))
        if tag == 0x11:
            value, pos = read_leb(body, pos, len(body))
            if value is None:
                return None, pos
            return (value >> 1) ^ -(value & 1), pos
        if tag == 0x03:
            if pos >= len(body):
                raise ValueError("bool eof")
            return body[pos], pos + 1
        if tag == 0x04:
            return None, pos
        if tag == 0x05:
            index, pos = read_leb(body, pos, len(body))
            return self.text(index), pos
        if tag == 0x0b:
            offset, pos = read_leb(body, pos, len(body))
            return self.ref(offset, depth + 1), pos
        if tag == 0x12:
            if pos + 4 > len(body):
                raise ValueError("f32 eof")
            return struct.unpack_from("<f", body, pos)[0], pos + 4
        if tag == 0x22:
            if pos + 8 > len(body):
                raise ValueError("f64 eof")
            return struct.unpack_from("<d", body, pos)[0], pos + 8
        if tag == 0x27:
            if pos >= len(body):
                raise ValueError("array eof")
            elem = body[pos]
            count, pos = read_leb(body, pos + 1, len(body))
            if count is None or count > MAX_COUNT:
                raise ValueError("array count")
            out = []
            for _ in range(count):
                item, pos = self.scalar(pos, elem, depth + 1)
                out.append(item)
            return out, pos
        if tag == 0x36:
            if pos + 2 > len(body):
                raise ValueError("tdict eof")
            key_tag, val_tag = body[pos], body[pos + 1]
            count, pos = read_leb(body, pos + 2, len(body))
            if count is None or count > MAX_COUNT:
                raise ValueError("tdict count")
            out = {}
            for _ in range(count):
                key, pos = self.scalar(pos, key_tag, depth + 1)
                value, pos = self.scalar(pos, val_tag, depth + 1)
                try:
                    out[key] = value
                except TypeError:
                    out[str(key)] = value
            return out, pos
        if tag == 0x06:
            count, pos = read_leb(body, pos, len(body))
            if count is None or count > MAX_COUNT:
                raise ValueError("dict count")
            out = {}
            for _ in range(count):
                key, pos = read_leb(body, pos, len(body))
                if key is None or pos >= len(body):
                    raise ValueError("dict key")
                value, pos = self.value(pos, body[pos], depth + 1)
                try:
                    out[key] = value
                except TypeError:
                    out[str(key)] = value
            return out, pos
        if tag == 0x07:
            count, pos = read_leb(body, pos, len(body))
            if count is None or count > MAX_COUNT:
                raise ValueError("list count")
            out = []
            for _ in range(count):
                if pos >= len(body):
                    raise ValueError("list item")
                value, pos = self.value(pos, body[pos], depth + 1)
                out.append(value)
            return out, pos
        raise ValueError("scalar tag %02x" % tag)

    def value(self, pos: int, tag: int, depth: int = 0):
        if depth > MAX_DEPTH or pos >= len(self.body):
            raise ValueError("depth/eof")
        if tag & 0x80:
            return self.class_instance(pos, tag, depth + 1)
        if (tag & 0x0F) in (0x06, 0x07) and tag & 0xF0:
            return self.node_at(pos, depth + 1)
        if tag not in (0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0B, 0x11, 0x12, 0x22, 0x27, 0x36):
            raise ValueError("value tag %02x" % tag)
        return self.scalar(pos + 1, tag, depth + 1)

    def ref(self, offset: int, depth: int = 0):
        if offset is None or not 0 <= offset < self.main_len or depth > MAX_DEPTH:
            return None
        try:
            value, _pos = self.value(offset, self.body[offset], depth)
            return value
        except (ValueError, IndexError, struct.error, RecursionError):
            return None

    def class_instance(self, pos: int, tag: int, depth: int = 0):
        body = self.body
        offset, pos = read_leb(body, pos + 1, len(body))
        definition = self.definition(offset) if offset is not None else None
        if definition is None:
            raise ValueError("bad class def")
        fields, tracked = definition
        bitmap_len = (tracked + 7) // 8
        if tag & 0x40:
            bitmap_offset, pos = read_leb(body, pos, len(body))
            if bitmap_offset is None:
                raise ValueError("bad bitmap offset")
            bitmap = body[bitmap_offset:bitmap_offset + bitmap_len]
        else:
            bitmap = body[pos:pos + bitmap_len]
            pos += bitmap_len
        if len(bitmap) < bitmap_len:
            raise ValueError("bitmap eof")
        out = {}
        for index in range(len(fields)):
            present = True
            if index < tracked:
                present = bool(bitmap[index >> 3] >> (index & 7) & 1)
            if not present:
                continue
            field_id, field_tag = fields[index]
            value, pos = self.scalar(pos, field_tag, depth + 1)
            key = self.text(field_id)
            out[key if key is not None else field_id] = value
        return out, pos

    def node_at(self, offset: int, depth: int = 0):
        return self.node(offset, depth)

    def node(self, offset: int, depth: int = 0):
        body = self.body
        if not 0 <= offset < len(body):
            raise ValueError("node offset")
        tag = body[offset]
        kind = tag & 0x0F
        mod = tag & 0xF0
        pos = offset + 1
        key_tag = None
        val_tag = None
        if mod & 0x10:
            key_tag = body[pos]
            pos += 1
        if mod & 0x20:
            val_tag = body[pos]
            pos += 1
        count, pos = read_leb(body, pos, len(body))
        if count is None or count > MAX_COUNT:
            raise ValueError("node count")
        if kind == 6:
            if mod & 0x40:
                cursor = pos + count * 8
                rows = []
                for _ in range(count):
                    if key_tag is None:
                        key, cursor = read_leb(body, cursor, len(body))
                    else:
                        key, cursor = self.scalar(cursor, key_tag, depth)
                    if cursor is None or cursor >= len(body):
                        break
                    if val_tag is None:
                        value, cursor = self.value(cursor, body[cursor], depth + 1)
                    else:
                        value, cursor = self.scalar(cursor, val_tag, depth + 1)
                    rows.append((key, value))
                return dict(rows), cursor
            rows = {}
            cursor = pos
            for _ in range(count):
                if key_tag is None:
                    key, cursor = read_leb(body, cursor, len(body))
                else:
                    key, cursor = self.scalar(cursor, key_tag, depth)
                if cursor is None or cursor >= len(body):
                    break
                if val_tag is None:
                    value, cursor = self.value(cursor, body[cursor], depth + 1)
                else:
                    value, cursor = self.scalar(cursor, val_tag, depth + 1)
                try:
                    rows[key] = value
                except TypeError:
                    rows[str(key)] = value
            return rows, cursor
        if kind == 7:
            if mod & 0x40:
                cursor = pos + count * 4
                out = []
                for _ in range(count):
                    if cursor >= len(body):
                        break
                    value, cursor = self.value(cursor, val_tag if val_tag else body[cursor], depth + 1)
                    out.append(value)
                return out, cursor
            if val_tag is not None:
                out = []
                cursor = pos
                for _ in range(count):
                    value, cursor = self.scalar(cursor, val_tag, depth + 1)
                    out.append(value)
                return out, cursor
            out = []
            cursor = pos
            for _ in range(count):
                if cursor >= len(body):
                    break
                value, cursor = self.value(cursor, body[cursor], depth + 1)
                out.append(value)
            return out, cursor
        raise ValueError("node kind %d" % kind)


UNIT_TYPE_VALUES = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536)


def _num(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _vec3(value):
    if not isinstance(value, list) or len(value) < 3:
        return None
    items = [_num(item) for item in value[:3]]
    if any(item is None or abs(item) > 50000 for item in items):
        return None
    return items


def _unit_row(unit_id, record, offset):
    name = record.get("name")
    if isinstance(name, str):
        name = _fix_mojibake(name)
    else:
        name = ""
    if not name and isinstance(record.get("model"), str):
        name = record["model"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    model = record.get("model")
    model_id = record.get("model_id")
    if isinstance(model, int) and not isinstance(model_id, int):
        model_id = model
        model = ""
    if not isinstance(model, str):
        model = ""
    material = record.get("mat_path") or record.get("material_path") or record.get("material")
    if not isinstance(material, str):
        material = ""
    position = _vec3(record.get("world_position")) or _vec3(record.get("position"))
    scale = _vec3(record.get("ctx_real_scale")) or _vec3(record.get("real_scale")) or _vec3(record.get("scale"))
    angle = _vec3(record.get("ctx_model_angle")) or _vec3(record.get("model_angle"))
    alpha = record.get("ctx_model_alpha")
    if alpha is None:
        alpha = record.get("model_alpha")
    if isinstance(alpha, (int, float)) and not isinstance(alpha, bool) and alpha > 1:
        alpha = alpha / 255.0
    elif not isinstance(alpha, (int, float)) or isinstance(alpha, bool):
        alpha = None
    visible = record.get("model_visible")
    if visible is None:
        visible = record.get("filter_visible")
    paint = []
    for key in ("paint_area1", "paint_area2", "paint_area3", "paint_area4"):
        value = record.get(key)
        items = [_num(item) for item in value[:3]] if isinstance(value, list) and len(value) >= 3 else []
        if len(items) == 3 and all(item is not None for item in items):
            paint.append([max(0, min(255, int(round(item)))) for item in items])
        else:
            paint.append(None)
    eid = record.get("eid")
    if not isinstance(eid, int) or isinstance(eid, bool) or not 1000 <= eid <= 100000000:
        eid = None
    unit_type = record.get("unit_type")
    if unit_type not in UNIT_TYPE_VALUES:
        unit_type = None
    unit_type_alt = record.get("type")
    if unit_type_alt not in UNIT_TYPE_VALUES:
        unit_type_alt = None
    row = {
        "unit_id": unit_id,
        "name": name,
        "eid": eid,
        "type": unit_type_alt if unit_type_alt is not None else unit_type,
        "unit_type": unit_type,
        "model_id": model_id if isinstance(model_id, int) and not isinstance(model_id, bool) else None,
        "model": model,
        "material": material,
        "camp": record.get("camp_id") if isinstance(record.get("camp_id"), int) else None,
        "parent_unit_id": record.get("parent_unit_id") if isinstance(record.get("parent_unit_id"), int) else 0,
        "position": [round(item, 3) for item in position] if position else None,
        "scale": scale,
        "angle": angle,
        "paint": paint,
        "alpha": round(float(alpha), 3) if alpha is not None else None,
        "visible": bool(visible) if isinstance(visible, (bool, int)) else True,
        "record_offset": offset,
        "fields": record,
    }
    row["partial"] = not (name or eid or position)
    return row


def _scan_unit_table(body, expected=None):
    best = None
    start = 0
    while True:
        pos = body.find(b"\x36\x01\x0b", start)
        if pos < 0:
            break
        start = pos + 1
        count, cursor = read_leb(body, pos + 3, len(body))
        if not count or count > MAX_UNIT_RECORDS or count > len(body) // 2:
            continue
        pairs = []
        ok = True
        for _ in range(count):
            unit_id, cursor = read_leb(body, cursor, len(body))
            offset, cursor = read_leb(body, cursor, len(body))
            if unit_id is None or offset is None:
                ok = False
                break
            pairs.append((unit_id, offset))
        if not ok or len(pairs) < 2:
            continue
        offsets = [offset for _unit_id, offset in pairs]
        if offsets != sorted(offsets):
            continue
        if not all(2 < offset < len(body) for offset in offsets):
            continue
        hits = sum(1 for offset in offsets if body[offset] & 0x80 and (body[offset] & 0x0F) == 6)
        if hits * 2 < len(offsets):
            continue
        score = (1 if expected is not None and len(pairs) == expected else 0, len(pairs))
        if best is None or score > best[0]:
            best = (score, pos, pairs)
    return best


def _looks_like_unit_dict(top):
    if not top or not all(isinstance(key, int) for key in top):
        return False
    hits = 0
    for unit_id, record in top.items():
        if not isinstance(record, dict):
            return False
        eid = record.get("eid")
        if record.get("unit_id") == unit_id:
            hits += 1
        elif isinstance(eid, int) and not isinstance(eid, bool) and 1000 <= eid <= 100000000 and "name" in record:
            hits += 1
    return hits * 2 >= len(top)


def _decode_unit_table(decoder, pairs, rows):
    for unit_id, offset in pairs:
        if len(rows) >= MAX_UNIT_RECORDS:
            break
        if not 0 <= offset < decoder.main_len:
            rows.append({"unit_id": unit_id, "fields": {}, "record_offset": offset, "partial": True})
            continue
        try:
            record, _pos = decoder.value(offset, decoder.body[offset])
        except (ValueError, IndexError, struct.error, RecursionError):
            rows.append({"unit_id": unit_id, "fields": {}, "record_offset": offset, "partial": True})
            continue
        if not isinstance(record, dict):
            rows.append({"unit_id": unit_id, "fields": {}, "record_offset": offset, "partial": True})
            continue
        rows.append(_unit_row(unit_id, record, offset))


def _plain_rows(top):
    if isinstance(top, list):
        rows = []
        for index, value in enumerate(top):
            row = {"index": index}
            if isinstance(value, dict) and value:
                row.update(value)
            else:
                row["value"] = value
            rows.append(row)
        return rows
    if not top:
        return []
    if not any(isinstance(value, dict) and value for value in top.values()):
        return [dict(top)]
    rows = []
    for index, (key, value) in enumerate(top.items()):
        row = {"index": index, "key": key}
        if isinstance(value, dict) and value:
            row.update(value)
        else:
            row["value"] = value
        rows.append(row)
    return rows


def decode_section(raw: bytes):
    if not raw:
        return None
    try:
        decoder = Bindict(raw)
    except (ValueError, IndexError, struct.error):
        return None
    strings = list(decoder.strings)
    try:
        top, _end = decoder.node(decoder.root - decoder.row_start)
    except (ValueError, IndexError, struct.error, RecursionError):
        return None
    if not isinstance(top, (dict, list)):
        return {"strings": strings, "rows": [], "row_count": 0, "unit_count": 0,
                "kind": "scalar", "root_tag": raw[decoder.root], "top_keys": None}

    rows = []
    kind = "dict" if isinstance(top, dict) else "list"
    unit_count = 0
    table = None
    if isinstance(top, dict) and ("unit" in top or "batch_unit_data" in top):
        unit_value = top.get("unit")
        expected = len(unit_value) if isinstance(unit_value, dict) and unit_value else None
        table = _scan_unit_table(decoder.body, expected)
    if table is not None:
        kind = "unit_table"
        _decode_unit_table(decoder, table[2], rows)
        unit_count = len(rows)
    elif isinstance(top, dict) and _looks_like_unit_dict(top):
        kind = "unit_dict"
        for unit_id, record in top.items():
            rows.append(_unit_row(unit_id, record, None))
        unit_count = len(rows)
    else:
        rows = _plain_rows(top)

    return {
        "strings": strings,
        "rows": rows,
        "row_count": len(rows),
        "unit_count": unit_count,
        "kind": kind,
        "root_tag": decoder.raw[decoder.root],
        "top_keys": list(top.keys()) if isinstance(top, dict) else None,
    }


def decode_map_sections(sections):
    results = []
    for section_id, raw in sections:
        decoded = decode_section(raw)
        if decoded is None:
            results.append({"section_id": section_id, "ok": False, "rows": [], "unit_count": 0})
            continue
        decoded["section_id"] = section_id
        decoded["ok"] = True
        results.append(decoded)
    return results
