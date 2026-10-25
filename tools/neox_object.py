"""NeoX .gim/.mtg object reader.

格式 (逆向自真实样本, 500/500 随机文件验证):
  magic c159410d (big-endian 读)
  u32  文件声明长度 (little-endian)
  u32  0
  varint 字符串条数 N + N 个零分隔字符串 (类名/对象名池)
  varint 字段条数 M + M 个零分隔字段名
  值区:
    varint A (语义未定)
    varint 对象数 obj_count (前面 8 字节为标志区)
    obj_count 个 (varint, varint) 元数据对
    obj_count 个对象:
      varint 属性数 K
      K × (varint fid, varint type, value)
      01 00 结束符
值类型: 0=null 1=CStr(含 bool/路径/名字) 2=u32 3=u8 5=f32 6=u32数量+float数组
引用: type1 相对路径字符串 (反斜杠分隔), 如 Mesh->.mesh, MtgPath->.mtg
"""

import struct

MAX_BYTES = 64 * 1024 * 1024
MAGIC = 0xC159410D


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def parse(data: bytes) -> dict:
    if len(data) < 13 or int.from_bytes(data[0:4], "big") != MAGIC:
        raise ValueError("not a NeoX object file")
    declared = int.from_bytes(data[4:8], "little")
    pos = 12
    count, pos = _read_varint(data, pos)
    table = []
    for _ in range(count):
        end = data.find(b"\x00", pos)
        if end < 0:
            raise ValueError("unterminated string")
        table.append(data[pos:end].decode("latin1", "replace"))
        pos = end + 1
    count, pos = _read_varint(data, pos)
    fields = []
    for _ in range(count):
        end = data.find(b"\x00", pos)
        if end < 0:
            raise ValueError("unterminated field")
        fields.append(data[pos:end].decode("latin1", "replace"))
        pos = end + 1
    blob = data[pos:]
    marker, _ = _read_varint(blob, 0)
    obj_count, vpos = _read_varint(blob, 8)
    meta = []
    for _ in range(obj_count):
        x, vpos = _read_varint(blob, vpos)
        y, vpos = _read_varint(blob, vpos)
        meta.append({"x": x, "y": y})
    objects = []
    for _ in range(obj_count):
        prop_count, vpos = _read_varint(blob, vpos)
        props = {}
        for _ in range(prop_count):
            fid, vpos = _read_varint(blob, vpos)
            typ, vpos = _read_varint(blob, vpos)
            value, vpos = _read_value(blob, vpos, typ)
            name = fields[fid] if fid < len(fields) else f"field{fid}"
            props[name] = value
        if blob[vpos:vpos + 2] != b"\x01\x00":
            raise ValueError("bad object terminator")
        vpos += 2
        objects.append(props)
    return {
        "size": len(data),
        "declared": declared,
        "table": table,
        "fields": fields,
        "objects": objects,
        "meta": meta,
    }


def build_tree(parsed: dict) -> list:
    objects = parsed.get("objects") or []
    meta = parsed.get("meta") or []
    children = [[] for _ in objects]
    level = [0] if objects else []
    pointer = 1
    while level and pointer < len(objects):
        next_level = []
        for parent in level:
            count = 0
            if parent < len(meta) and isinstance(meta[parent], dict):
                try:
                    count = int(meta[parent].get("y") or 0)
                except (TypeError, ValueError):
                    count = 0
            for _ in range(max(0, min(count, len(objects) - pointer))):
                child = pointer
                pointer += 1
                children[parent].append(child)
                next_level.append(child)
        level = next_level
    return children


def _read_value(data: bytes, pos: int, typ: int):
    if typ == 0:
        return None, pos
    if typ == 1:
        end = data.find(b"\x00", pos)
        if end < 0:
            raise ValueError("unterminated value string")
        return data[pos:end].decode("latin1", "replace"), end + 1
    if typ == 2:
        return int.from_bytes(data[pos:pos + 4], "little"), pos + 4
    if typ == 3:
        return data[pos], pos + 1
    if typ == 5:
        return struct.unpack_from("<f", data, pos)[0], pos + 4
    if typ == 6:
        count = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
        values = struct.unpack_from("<" + "f" * count, data, pos)
        return list(values), pos + 4 * count
    raise ValueError(f"unknown value type {typ}")


def summarize(data: bytes) -> dict:
    parsed = parse(data)
    refs = []
    for obj in parsed["objects"]:
        for name, value in obj.items():
            if isinstance(value, str) and "\\" in value and len(value) < 240:
                refs.append(value)
    return {
        "table": parsed["table"],
        "fields": parsed["fields"],
        "objects": parsed["objects"],
        "references": refs,
    }
