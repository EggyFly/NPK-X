"""NeoX .mesh reader: quantized snorm16 vertices + strip-ish index stream.

布局 (逆向自真实样本):
  magic 3480c8bb (LE u32)
  u16 04 00, u16 05 00 (版本)
  u32 0
  u32 child_len (数据区结束偏移, 尾声明在其后到 EOF)
  u32 vertex_count, u32 index_count (重复两次, 中间夹 0x00010001)
  顶点区: vertex_count x 8B, 每顶点 4 x snorm16 (x,y,z,w)
  附加区: vertex_count x 3B 颜色, 然后子网格绑定表 + u16 索引流
  索引值可能带 0x8000 标志位 (mask 0x7FFF), 0xFFFF 是 strip restart
  尾部声明: 01 00 10 00 00 00 01 00 04 00 (变长)
坐标是 [-1,1] 归一化值, 需用 GIM SubMesh BoundingInfo 反归一化:
  real = center + normalized * half
"""

import struct

MAX_BYTES = 64 * 1024 * 1024
MAGIC = 0xBBC88034


def _scan_indices(data: bytes, start: int, end: int, vcount: int, icount: int) -> list:
    want = min(icount, 4000)
    best_start = -1
    best_len = 0
    cur = 0
    cur_start = 0
    for probe in (start, start + 1):
        offset = probe
        cur = 0
        while offset < end - 1:
            value = int.from_bytes(data[offset:offset + 2], "little")
            if value == 0xFFFF or (value & 0x7FFF) < vcount:
                if cur == 0:
                    cur_start = offset
                cur += 1
                if cur > best_len:
                    best_len = cur
                    best_start = cur_start
                if best_len >= want:
                    break
            else:
                cur = 0
            offset += 2
        if best_len >= want:
            break
    if best_len < 3:
        return []
    count = min(best_len, want)
    raw = struct.unpack_from(f"<{count}H", data, best_start)
    return [v & 0x7FFF for v in raw if v != 0xFFFF]


def parse(data: bytes) -> dict:
    if len(data) < 40 or struct.unpack_from("<I", data, 0)[0] != MAGIC:
        raise ValueError("not a NeoX mesh file")
    child_len = struct.unpack_from("<I", data, 12)[0]
    vertex_count = struct.unpack_from("<I", data, 16)[0]
    index_count = struct.unpack_from("<I", data, 20)[0]
    if vertex_count > 2_000_000 or child_len > len(data) or child_len < 36:
        raise ValueError("mesh header out of range")
    vertices = []
    pos_end = min(36 + vertex_count * 8, len(data))
    for offset in range(36, pos_end, 8):
        x, y, z, w = struct.unpack_from("<4h", data, offset)
        vertices.append([x / 32767.0, y / 32767.0, z / 32767.0])
    indices = []
    scan_start = min(pos_end + vertex_count * 3, child_len)
    # 索引区不在固定位置: 逐顶点绑定表(约 12B/顶点)之后才是 u16 流.
    # 用总长度约束定位: 索引区末尾紧贴 child_len, 长度约 icount x 2B.
    idx_bytes = min(index_count * 2 + 32, child_len - scan_start)
    if idx_bytes > 8:
        back_start = child_len - idx_bytes
        indices = _scan_indices(data, min(scan_start, back_start), child_len, vertex_count or 1, index_count)
    return {
        "size": len(data),
        "vertex_count": vertex_count,
        "index_count": index_count,
        "vertices": vertices,
        "indices": indices,
    }


def denormalize(parsed: dict, center: list, half: list) -> dict:
    out = dict(parsed)
    real = []
    for x, y, z in parsed["vertices"]:
        real.append([
            center[0] + x * half[0],
            center[1] + y * half[1],
            center[2] + z * half[2],
        ])
    out["vertices"] = real
    return out
