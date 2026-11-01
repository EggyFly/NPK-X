#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NeoX bindict 解码器（GMP sid3 单位表）.

结构（已用 1000601/1000100/8103008 + editor_model.bin 实测）:
  [0:4]   u32 字符串数
  [4:8]   0
  [8:8+4n] u32 字符串结束偏移（累计）
  [..]     字符串池 utf-8
  [rs]     u32 main_len（rs+main_len = 索引起点）
  [rs+4..] 值池 payload
  [root]   b-tree 节点
值偏移一律相对 rs（即 body_full[off]）。
"""

from __future__ import annotations

import struct

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def read_leb(data: bytes, pos: int, end: int | None = None):
    value = 0
    shift = 0
    end = len(data) if end is None else end
    while pos < end:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos
    return None, pos


def leb_bytes(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def parse_strings(data: bytes):
    """返回 (strings, blob_start, row_start, main_len, root) 或 None."""
    if len(data) < 12:
        return None
    count = struct.unpack_from("<I", data, 0)[0]
    if not count or count > 400000:
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
        strings.append(data[blob_start + begin:blob_start + end].decode("utf-8", "replace"))
        begin = end
    row_start = blob_start + begin
    if row_start + 4 > len(data):
        return None
    main_len = struct.unpack_from("<I", data, row_start)[0]
    root = row_start + main_len
    if main_len > 64 * 1024 * 1024 or root + 4 > len(data):
        return None
    return strings, blob_start, row_start, main_len, root


def parse_index_node(data: bytes, root: int, total: int):
    """解析索引节点，返回 [(key, offset), ...].

    节点头: tag(1) [+1 if tag&0x10] [+1 if tag&0x20] count(leb)
             count * 8 字节条目（哈希/键指纹）
             count 个 (leb key, leb offset)
    tag 低半字节固定 6。0x76 两个附加字节，0x56 一个。
    """
    if root >= len(data):
        return []
    tag = data[root]
    if (tag & 0x0F) != 6:
        return []
    pos = root + 1
    if tag & 0x10:
        pos += 1
    if tag & 0x20:
        pos += 1
    count, pos = read_leb(data, pos, total)
    if count is None or count > 300000:
        return []
    pos += count * 8
    rows = []
    while pos < total:
        key, pos = read_leb(data, pos, total)
        off, pos = read_leb(data, pos, total)
        if key is None or off is None:
            break
        rows.append((key, off))
        if len(rows) > count + 8:
            break
    return rows


def parse_vecs(rec: bytes):
    """从记录里抓 3 分量向量候选.

    值标签（实测）:
      0x27 <元素标签> <count> <data>   定长数组: 0x12=f32, 0x22=f64, 0x01=leb int
      0x07 <count> [标签化值...]      列表
      0x12 <4B> f32 / 0x22 <8B> f64
      0x06 <count> ...                 字典
      0x36 <键标签> <值标签> <count> ... 整数键字典
    """
    out = []
    n = len(rec)
    for i in range(n):
        tag = rec[i]
        if tag == 0x27 and i + 2 < n and rec[i + 1] in (0x12, 0x22) and rec[i + 2] == 3:
            if rec[i + 1] == 0x12 and i + 15 <= n:
                out.append((i, "arr32", tuple(struct.unpack_from("<3f", rec, i + 3))))
            elif rec[i + 1] == 0x22 and i + 27 <= n:
                out.append((i, "arr64", tuple(struct.unpack_from("<3d", rec, i + 3))))
        elif tag == 0x07 and i + 1 < n and rec[i + 1] == 3:
            pos = i + 2
            vals = []
            ok = True
            for _ in range(3):
                if pos >= n:
                    ok = False
                    break
                sub = rec[pos]
                if sub == 0x12 and pos + 5 <= n:
                    vals.append(struct.unpack_from("<f", rec, pos + 1)[0])
                    pos += 5
                elif sub == 0x22 and pos + 9 <= n:
                    vals.append(struct.unpack_from("<d", rec, pos + 1)[0])
                    pos += 9
                else:
                    ok = False
                    break
            if ok:
                out.append((i, "list3", tuple(vals)))
    return out


def find_unit_table(body: bytes):
    """找 `36 01 0b <count> (unit_id, end_offset)*` 表；取条目最多的."""
    best = None
    start = 0
    while True:
        pos = body.find(b"\x36\x01\x0b", start)
        if pos < 0:
            break
        start = pos + 1
        count, cur = read_leb(body, pos + 3, len(body))
        if not count or count > 200000:
            continue
        pairs = []
        ok = True
        while len(pairs) < count:
            a, cur = read_leb(body, cur, len(body))
            b, cur = read_leb(body, cur, len(body))
            if a is None or b is None:
                ok = False
                break
            pairs.append((a, b))
        if not ok or len(pairs) < 2:
            continue
        offsets = [b for a, b in pairs]
        if offsets != sorted(offsets):
            continue
        if not all(0 < b < len(body) for b in offsets):
            continue
        score = (len(pairs), max(p[0] for p in pairs) > 100000)
        if best is None or score > best[0]:
            best = (score, pos, pairs)
    return best


def scan_string_refs(rec: bytes, strings):
    """记录里出现的小整数 = 字符串池下标；挑 CJK 名字与路径."""
    names = []
    paths = []
    n = len(rec)
    for i in range(n):
        value, pos = read_leb(rec, i, n)
        if value is None or not (0 < value < len(strings)):
            continue
        if leb_bytes(value) != rec[i:pos]:
            continue
        text = strings[value]
        if not text:
            continue
        if any("\u4e00" <= ch <= "\u9fff" for ch in text):
            names.append((value, text))
        elif "/" in text and "." in text:
            paths.append((value, text))
    return names, paths


def decode_section(raw: bytes):
    parsed = parse_strings(raw)
    if not parsed:
        return None
    strings, blob_start, row_start, main_len, root = parsed
    body = raw[row_start:]                      # 含 4 字节 main_len
    payload = raw[row_start + 4:root]           # 值池
    top = parse_index_node(raw, root, len(raw))
    table = find_unit_table(body)
    units = []
    if table:
        _, table_pos, pairs = table
        # 每条记录末尾有 4 字节尾部（实测恒为 c6 fc 0e XX），
        # 表里的 offset 指向该尾部起点，因此记录正文 = [上一 offset + 4, 本 offset)。
        prev_end = 4
        first_start = 4
        if len(body) > 8 and body[4] == 0x27 and body[5] == 0x01:
            count0, cursor = read_leb(body, 6, len(body))
            if count0 is not None and count0 < 100000:
                for _ in range(count0):
                    _, cursor = read_leb(body, cursor, len(body))
                first_start = cursor
        for index, (uid, end) in enumerate(pairs):
            start = first_start if index == 0 else prev_end + 4
            if end <= start or end > len(body):
                prev_end = max(prev_end, end)
                continue
            rec = body[start:end]
            vecs = parse_vecs(rec)
            sane = [item for item in vecs if all(abs(x) < 50000 for x in item[2])]
            best = None
            if sane:
                best = max(sane, key=lambda item: abs(item[2][0]) + abs(item[2][2]))
            names, paths = scan_string_refs(rec, strings)
            units.append({
                "unit_id": uid,
                "record": [start, end],
                "vectors": [{"at": at, "kind": kind, "value": [round(x, 4) for x in value]} for at, kind, value in vecs],
                "position_guess": [round(x, 4) for x in best[2]] if best else None,
                "cjk_strings": [text for _, text in names][:8],
                "path_strings": [text for _, text in paths][:8],
            })
            prev_end = end
    return {
        "strings": strings,
        "blob_start": blob_start,
        "row_start": row_start,
        "main_len": main_len,
        "root": root,
        "top_keys": [{"key": k, "offset": o, "name": strings[k] if 0 <= k < len(strings) else None} for k, o in top],
        "unit_table_offset": table[1] if table else None,
        "unit_count": len(units),
        "units": units,
    }
