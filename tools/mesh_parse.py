"""NeoX .mesh reader for the v4 packed layout used by the current client.

The vertex block starts with a format byte. Bit 0 selects 8-byte 20-bit
fixed-point positions or 12-byte floats; normals are 3-byte snorm values.
Faces are s16 cumulative deltas and UVs are float16 pairs. Skinned meshes
carry a 12-byte joint/weight tail.
"""

import math
import struct

MAX_BYTES = 64 * 1024 * 1024
MAGIC = 0xBBC88034


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _read_table(data: bytes, offset: int) -> dict | None:
    if offset + 10 > len(data):
        return None
    records = []
    pos = offset
    while pos + 10 <= len(data):
        if _u16(data, pos) == 1:
            pos += 2
            break
        vcount = _u32(data, pos)
        fcount = _u32(data, pos + 4)
        uv_layers = data[pos + 8]
        color_len = data[pos + 9]
        if not (0 < vcount <= 2_000_000 and 0 < fcount <= 2_000_000):
            return None
        if not 1 <= uv_layers <= 8 or color_len > 8:
            return None
        records.append((vcount, fcount, uv_layers, color_len))
        pos += 10
    if not records or pos + 8 > len(data):
        return None
    total_vcount = _u32(data, pos)
    total_fcount = _u32(data, pos + 4)
    if not (0 < total_vcount <= 2_000_000 and 0 <= total_fcount <= 2_000_000):
        return None
    return {
        "kind": "single" if len(records) == 1 else "multi",
        "vcount": records[0][0],
        "fcount": records[0][1],
        "uv_layers": records[0][2],
        "color_len": records[0][3],
        "end": pos + 8,
        "records": records,
        "total_vcount": total_vcount,
        "total_fcount": total_fcount,
    }


def _decode_position(packed: int) -> list:
    values = []
    for shift in (0, 20, 40):
        fraction = (packed >> shift) & 0xFFF
        integer = (packed >> (shift + 12)) & 0x7F
        value = integer + fraction / 2048.0
        if (packed >> (shift + 19)) & 1:
            value = -value
        values.append(value)
    return values


def _field(value: int, start: int, end: int) -> int:
    return (value >> start) & ((1 << (end - start)) - 1)


_LAYOUTS_115 = (
    ((8, 19), (20, 24), (48, 59), (60, 64), 0),
    ((8, 19), (20, 27), (48, 59), (60, 64), 3),
    ((8, 19), (20, 28), (48, 59), (60, 64), 4),
    ((8, 18), (21, 28), (47, 59), (60, 64), 5),
)


def _decode_position_115(packed: int, layout) -> list:
    xl, xh, zl, zh, wrap = layout
    x = _field(packed, *xl) + (_field(packed, *xh) << (xl[1] - xl[0]))
    y = _field(packed, 27, 39) + (_field(packed, 40, 47) << 12)
    z = _field(packed, *zl) + (_field(packed, *zh) << (zl[1] - zl[0]))
    if wrap:
        z += _field(packed, 0, wrap) << 15
    return [x, y, z]


def _edge_score(vertices: list, indices: list) -> float:
    total = 0.0
    count = 0
    for index in range(0, len(indices) - 2, 3):
        a, b, c = indices[index], indices[index + 1], indices[index + 2]
        for left, right in ((a, b), (b, c), (c, a)):
            p, q = vertices[left], vertices[right]
            total += math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2])
            count += 1
    return total / count if count else 0.0


def _choose_115_positions(data: bytes, start: int, vcount: int, indices: list) -> list:
    best = None
    for layout in _LAYOUTS_115:
        vertices = []
        for index in range(vcount):
            packed = struct.unpack_from("<Q", data, start + index * 8)[0]
            x, y, z = _decode_position_115(packed, layout)
            vertices.append([x, y, z])
        spans = []
        for axis in range(3):
            spans.append(max(vertex[axis] for vertex in vertices) - min(vertex[axis] for vertex in vertices))
        low = min(spans)
        if low <= 0 or max(spans) / low > 20:
            continue
        score = _edge_score(vertices, indices) / max(spans)
        if best is None or score < best[0]:
            best = (score, vertices)
    if best is None:
        vertices = []
        for index in range(vcount):
            packed = struct.unpack_from("<Q", data, start + index * 8)[0]
            x, y, z = _decode_position_115(packed, _LAYOUTS_115[0])
            vertices.append([x, y, z])
        return vertices
    return best[1]


def _read_normals(data: bytes, start: int, count: int) -> list | None:
    normals = []
    total = 0.0
    for index in range(count):
        x, y, z = struct.unpack_from("<3b", data, start + index * 3)
        normals.append([round(x / 127.0, 3), round(y / 127.0, 3), round(z / 127.0, 3)])
        total += math.sqrt(x * x + y * y + z * z) / 127.0
    if count <= 0 or total / count < 0.5:
        return None
    return normals


def _compute_normals(vertices: list, indices: list) -> list:
    normals = [[0.0, 0.0, 0.0] for _ in vertices]
    for index in range(0, len(indices) - 2, 3):
        a, b, c = indices[index], indices[index + 1], indices[index + 2]
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        for target in (a, b, c):
            normals[target][0] += nx
            normals[target][1] += ny
            normals[target][2] += nz
    for normal in normals:
        length = math.sqrt(normal[0] ** 2 + normal[1] ** 2 + normal[2] ** 2) or 1.0
        normal[0] = round(normal[0] / length, 3)
        normal[1] = round(normal[1] / length, 3)
        normal[2] = round(normal[2] / length, 3)
    return normals


def _parse_bone_header(data: bytes) -> dict:
    bone_count = _u16(data, 12)
    pos = 14 + bone_count
    pos += bone_count * 32
    if data[pos]:
        pos += 28 * bone_count
    pos += 1
    pos += bone_count * 64
    pos += 1
    data_end = _u32(data, pos)
    pos += 4
    records = []
    while pos + 10 <= len(data):
        if _u16(data, pos) == 1:
            pos += 2
            break
        records.append((_u32(data, pos), _u32(data, pos + 4), data[pos + 8], data[pos + 9]))
        pos += 10
    vcount = _u32(data, pos)
    fcount = _u32(data, pos + 4)
    pos += 8
    return {
        "data_end": data_end,
        "vcount": vcount,
        "fcount": fcount,
        "records": records,
        "data_start": pos,
    }


def _decode_faces(blob: bytes, vcount: int) -> tuple[list, int]:
    indices = []
    valid = 0
    current = 0
    for (delta,) in struct.iter_unpack("<h", blob):
        current += delta
        if 0 <= current < vcount:
            indices.append(current)
            valid += 1
        else:
            current = 0
            indices.append(0)
    return indices, valid


def _face_probe(blob: bytes, vcount: int) -> tuple[int, int]:
    current = 0
    valid = 0
    total = 0
    for (delta,) in struct.iter_unpack("<h", blob):
        current += delta
        total += 1
        if 0 <= current < vcount:
            valid += 1
        else:
            break
    return valid, total


def _find_face_start(data: bytes, norm_end: int, uv_start: int, vcount: int) -> tuple[int, int] | None:
    if uv_start <= norm_end or vcount <= 0:
        return None
    bases = (3 * vcount, 4 * vcount, 7 * vcount, 11 * vcount, 12 * vcount, 15 * vcount, 16 * vcount, 0)
    best = None
    for base in bases:
        for extra in range(0, 257):
            start = norm_end + base + extra
            if start >= uv_start - 6:
                break
            size = uv_start - start
            if size % 6:
                continue
            fcount = size // 6
            probe = data[start:start + 96]
            if len(probe) % 2:
                probe = probe[:-1]
            valid, total = _face_probe(probe, vcount)
            if total == 0 or valid < total:
                continue
            indices, valid = _decode_faces(data[start:start + size], vcount)
            if valid < fcount * 3 * 0.999:
                continue
            score = (fcount, -start)
            if best is None or score > best[0]:
                best = (score, start, fcount)
    if not best:
        return None
    return best[1], best[2]


def _scan_face_start(data: bytes, start: int, end: int, vcount: int, fcount: int) -> int | None:
    size = fcount * 6
    if size <= 0 or start < 0 or end - start < size:
        return None
    for pos in range(start, end - size + 1):
        probe = data[pos:pos + 96]
        if len(probe) % 2:
            probe = probe[:-1]
        valid, total = _face_probe(probe, vcount)
        if total == 0 or valid < total:
            continue
        _, valid = _decode_faces(data[pos:pos + size], vcount)
        if valid >= fcount * 3 * 0.999:
            return pos
    return None


def parse(data: bytes) -> dict:
    if len(data) < 40 or _u32(data, 0) != MAGIC:
        raise ValueError("not a NeoX mesh file")
    if len(data) > MAX_BYTES:
        raise ValueError("mesh too large")
    version = _u16(data, 4)
    flag = _u16(data, 8)
    if flag != 0:
        info = _parse_bone_header(data)
        total_vcount = info["vcount"]
        total_fcount = info["fcount"]
        data_start = info["data_start"]
        data_end = info["data_end"]
        uv_layers = info["records"][0][2] if info["records"] else 1
        color_len = info["records"][0][3] if info["records"] else 0
        if total_vcount <= 0 or total_fcount <= 0 or data_end > len(data):
            raise ValueError("skinned mesh header out of range")
        tail_size = 12 * total_vcount + 32
        skin_start = data_end - tail_size
        uv_start = skin_start - 4 * uv_layers * total_vcount
        face_start = uv_start - 6 * total_fcount
        if face_start < data_start or uv_start < face_start:
            raise ValueError("skinned mesh layout out of range")
        indices, valid = _decode_faces(data[face_start:face_start + 6 * total_fcount], total_vcount)
        if valid < total_fcount * 3 * 0.999:
            raise ValueError("mesh face stream invalid")
        geometry = (115 * total_vcount + 16) // 8
        if face_start - data_start == geometry:
            variant = "v4-115"
            vertices = _choose_115_positions(data, data_start, total_vcount, indices)
            normals = _compute_normals(vertices, indices)
        else:
            vertex_flag = data[data_start]
            pos_start = data_start + 1
            if vertex_flag & 1:
                variant = "v4-12"
                vertices = []
                for index in range(total_vcount):
                    x, y, z = struct.unpack_from("<3f", data, pos_start + index * 12)
                    vertices.append([round(x, 4), round(y, 4), round(z, 4)])
                norm_start = pos_start + total_vcount * 12
            else:
                variant = "v4-20"
                vertices = []
                for index in range(total_vcount):
                    packed = struct.unpack_from("<Q", data, pos_start + index * 8)[0]
                    x, y, z = _decode_position(packed)
                    vertices.append([round(x, 4), round(y, 4), round(z, 4)])
                norm_start = pos_start + total_vcount * 8
            normals = _read_normals(data, norm_start, total_vcount) if norm_start + total_vcount * 3 <= face_start else None
            if normals is None:
                normals = _compute_normals(vertices, indices)
        uvs = []
        for index in range(total_vcount):
            u, v = struct.unpack_from("<2e", data, uv_start + index * 4 * uv_layers)
            uvs.append([round(u, 5), round(v, 5)])
        mins = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
        maxs = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
        return {
            "size": len(data),
            "version": version,
            "flag": flag,
            "variant": variant,
            "packed": True,
            "vertex_count": total_vcount,
            "face_count": total_fcount,
            "index_count": total_fcount * 3,
            "uv_layers": uv_layers,
            "color_len": color_len,
            "vertices": vertices,
            "normals": normals,
            "uvs": uvs,
            "indices": indices,
            "bounds": {"min": mins, "max": maxs},
            "submeshes": [
                {"vertex_count": item[0], "face_count": item[1], "face_offset": sum(record[1] for record in info["records"][:index])}
                for index, item in enumerate(info["records"])
            ],
        }
    header = 12
    if version < 4 and _u32(data, header + 8) == 0:
        raise ValueError("mesh has no faces")
    data_end = _u32(data, header)
    if data_end > len(data) or data_end <= 16:
        raise ValueError("mesh data range out of bounds")
    table_off = 16
    if data_end + 6 <= len(data):
        multiple = _u16(data, data_end)
        if multiple >= 1:
            table_off = _u32(data, data_end + 2)
    if table_off >= data_end:
        raise ValueError("mesh submesh table out of range")
    table = _read_table(data, table_off)
    if not table:
        raise ValueError("mesh submesh table not found")
    uv_layers = table["uv_layers"]
    color_len = table["color_len"]
    if len({(item[2], item[3]) for item in table["records"]}) > 1:
        raise ValueError("mesh submesh uv layouts differ")
    total_vcount = table["total_vcount"]
    total_fcount = table["total_fcount"]
    if total_fcount == 0:
        raise ValueError("mesh has no faces")
    vertex_start = table["end"]
    if vertex_start >= data_end:
        raise ValueError("mesh vertex data out of range")
    vertex_flag = data[vertex_start]
    pos_start = vertex_start + 1
    pos_size = total_vcount * (12 if vertex_flag & 1 else 8)
    if total_vcount <= 0 or pos_size > data_end - pos_start:
        raise ValueError("mesh vertex count out of range")
    norm_start = pos_start + pos_size
    norm_end = norm_start + total_vcount * 3
    post_size = 4 * uv_layers * total_vcount + 4 * color_len * total_vcount
    if table["kind"] == "single" and not (vertex_flag & 1):
        geometry115 = (115 * total_vcount + 16) // 8
        base115 = geometry115 + 6 * total_fcount + post_size
        extra = (data_end - vertex_start) - base115
        tail_size = 12 * total_vcount + 32
        if extra in (4 * total_vcount, tail_size, 4 * total_vcount + tail_size):
            raise ValueError("v4 115-bit prop mesh positions are not verified")
    uv_start = data_end - post_size
    if uv_start <= norm_end:
        raise ValueError("mesh uv range out of bounds")
    face_start = uv_start - 6 * total_fcount
    if face_start >= norm_end:
        _, valid = _decode_faces(data[face_start:face_start + 6 * total_fcount], total_vcount)
    else:
        valid = 0
    if valid < total_fcount * 3 * 0.999:
        found = _find_face_start(data, norm_end, uv_start, total_vcount)
        if not found:
            raise ValueError("mesh face stream not found")
        face_start, total_fcount = found
    if uv_start + 4 * uv_layers * total_vcount > data_end:
        raise ValueError("mesh uv range out of bounds")
    indices, valid = _decode_faces(data[face_start:face_start + total_fcount * 6], total_vcount)
    if valid < total_fcount * 3 * 0.999:
        raise ValueError("mesh face stream invalid")
    vertices = []
    if vertex_flag & 1:
        variant = "v4-12"
        for index in range(total_vcount):
            x, y, z = struct.unpack_from("<3f", data, pos_start + index * 12)
            vertices.append([round(x, 4), round(y, 4), round(z, 4)])
    else:
        variant = "v4-20"
        for index in range(total_vcount):
            packed = struct.unpack_from("<Q", data, pos_start + index * 8)[0]
            x, y, z = _decode_position(packed)
            vertices.append([round(x, 4), round(y, 4), round(z, 4)])
    normals = _read_normals(data, norm_start, total_vcount) if norm_end <= face_start else None
    if normals is None:
        normals = _compute_normals(vertices, indices)
    uvs = []
    for index in range(total_vcount):
        u, v = struct.unpack_from("<2e", data, uv_start + index * 4 * uv_layers)
        uvs.append([round(u, 5), round(v, 5)])
    mins = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
    maxs = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
    submeshes = []
    if table["kind"] == "single":
        submeshes.append({"vertex_count": total_vcount, "face_count": total_fcount, "face_offset": 0})
    else:
        face_offset = 0
        for item in table["records"]:
            submeshes.append({"vertex_count": item[0], "face_count": item[1], "face_offset": face_offset})
            face_offset += item[1]
    return {
        "size": len(data),
        "version": version,
        "flag": flag,
        "variant": variant,
        "packed": True,
        "vertex_count": total_vcount,
        "face_count": total_fcount,
        "index_count": total_fcount * 3,
        "uv_layers": uv_layers,
        "color_len": color_len,
        "vertices": vertices,
        "normals": normals,
        "uvs": uvs,
        "indices": indices,
        "bounds": {"min": mins, "max": maxs},
        "submeshes": submeshes,
    }


def denormalize(parsed: dict, center: list, half: list) -> dict:
    if parsed.get("packed"):
        return parsed
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
