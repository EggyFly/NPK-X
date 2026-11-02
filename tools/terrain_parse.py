#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""NeoX GMP terrain blocks (TESM/TESL) to WebGL triangle mesh."""

from __future__ import annotations

import math
import struct

TESM_MAGIC = b"TESM"
TESL_MAGIC = b"TESL"
VAND_MAGIC = b"VAND"
FIXED_UNIT = 4294967296.0
MAX_VERTICES = 65535
MAX_TRIANGLES = 200000
MAX_TILES = 512
MAX_POLYS = 400000
MAX_VERTS = 400000
MAX_LINKS = 4000000


def _u32(data, pos):
    return struct.unpack_from("<I", data, pos)[0]


def _fixed(value):
    return round(value / FIXED_UNIT, 3)


def _align8(value):
    return (value + 7) & ~7


def _blocks(raw):
    if raw[:4] == TESM_MAGIC:
        return [raw]
    if raw[:4] != TESL_MAGIC or len(raw) < 8:
        return []
    blocks = []
    pos = 4
    while pos + 4 <= len(raw) and len(blocks) < MAX_TILES:
        size = struct.unpack_from(">I", raw, pos)[0]
        if size < 0x40 or pos + 4 + size > len(raw):
            break
        block = raw[pos + 4:pos + 4 + size]
        if block[:4] == TESM_MAGIC:
            blocks.append(block)
        pos += 4 + size
    return blocks


def _tesm_header(block):
    if len(block) < 0x40 or block[:4] != TESM_MAGIC:
        return None
    version = _u32(block, 4)
    count = _u32(block, 8)
    hint = _u32(block, 0x0C)
    if version > 16 or count > MAX_TILES:
        return None
    values = [_fixed(value) for value in struct.unpack_from("<6q", block, 0x10)]
    if not all(math.isfinite(value) for value in values):
        return None
    records = []
    pos = 0x40
    for _ in range(count):
        if pos + 8 > len(block):
            break
        key, size = struct.unpack_from("<II", block, pos)
        pos += 8
        if size > len(block) - pos:
            break
        records.append((key, size, block[pos:pos + size]))
        pos += size
    return {"version": version, "count": count, "hint": hint, "values": values, "records": records}


def _vand_header(tile):
    if len(tile) < 0x90 or tile[:4] != VAND_MAGIC:
        return None
    if _u32(tile, 4) != 7:
        return None
    values = struct.unpack_from("<9i", tile, 0x18)
    poly, vert, link, dmesh, dvert, dtri, bv, offn, offb = values
    if not (0 <= poly <= MAX_POLYS and 0 <= vert <= MAX_VERTS):
        return None
    if not (0 <= link <= MAX_LINKS and 0 <= dmesh <= poly and 0 <= offn <= poly):
        return None
    if not (0 <= dvert <= MAX_VERTS and 0 <= dtri <= MAX_LINKS and 0 <= bv <= MAX_LINKS):
        return None
    if not (0 <= offb <= poly):
        return None
    regular = offb if offn else poly
    positions = [0x90]
    positions.append(positions[-1] + vert * 24)
    positions.append(positions[-1] + poly * 32)
    positions.append(positions[-1] + link * 12)
    positions.append(_align8(positions[-1]))
    positions.append(positions[-1] + dmesh * 12)
    positions.append(_align8(positions[-1]))
    positions.append(positions[-1] + dvert * 24)
    positions.append(_align8(positions[-1]))
    positions.append(positions[-1] + dtri * 4)
    positions.append(_align8(positions[-1]))
    positions.append(positions[-1] + bv * 16)
    positions.append(_align8(positions[-1]))
    positions.append(positions[-1] + offn * 64)
    if positions[-1] > len(tile):
        return None
    return {
        "poly": poly, "vert": vert, "link": link, "dmesh": dmesh, "dvert": dvert,
        "dtri": dtri, "bv": bv, "offn": offn, "offb": offb, "regular": regular,
        "vert_off": positions[0], "poly_off": positions[1], "end": positions[-1],
    }


def _tile_vertices(tile, header):
    vert = header["vert"]
    offset = header["vert_off"]
    result = []
    for index in range(vert):
        x, y, z = struct.unpack_from("<3q", tile, offset + index * 24)
        result.append([_fixed(x), _fixed(y), _fixed(z)])
    return result


def _tile_triangles(tile, header, vertices):
    offset = header["poly_off"]
    limit = header["regular"]
    triangles = []
    areas = {}
    for index in range(limit):
        base = offset + index * 32
        count = tile[base + 0x1E]
        if count < 3 or count > 6:
            continue
        loop = []
        ok = True
        for slot in range(count):
            vertex = struct.unpack_from("<H", tile, base + 4 + slot * 2)[0]
            if vertex >= len(vertices):
                ok = False
                break
            loop.append(vertex)
        if not ok:
            continue
        area = tile[base + 0x12]
        areas[area] = areas.get(area, 0) + 1
        for slot in range(1, count - 1):
            triangles.append((loop[0], loop[slot], loop[slot + 1]))
    return triangles, areas


def _winding(vertices, triangles):
    up = 0
    down = 0
    for a, b, c in triangles[:96]:
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        ny = uz * vx - ux * vz
        if ny > 0:
            up += 1
        elif ny < 0:
            down += 1
    return down > up


def _compact(vertices, indices):
    mapping = {}
    kept = []
    compacted = []
    for vertex in indices:
        if vertex not in mapping:
            mapping[vertex] = len(kept)
            kept.append(vertices[vertex])
        compacted.append(mapping[vertex])
    return kept, compacted


def _sample(vertices, indices, max_vertices, max_triangles):
    max_vertices = max(max_vertices, 3) if max_vertices > 0 else MAX_VERTICES
    max_triangles = max_triangles if max_triangles > 0 else MAX_TRIANGLES
    total = len(indices) // 3
    if total <= max_triangles and len(vertices) <= max_vertices:
        return vertices, indices, 1
    stride = max(1, int(math.ceil(total / float(max_triangles)))) if max_triangles else 1
    while True:
        mapping = {}
        kept = []
        kept_indices = []
        for triangle in range(0, total, stride):
            three = indices[triangle * 3:triangle * 3 + 3]
            for vertex in three:
                if vertex not in mapping:
                    mapping[vertex] = len(kept)
                    kept.append(vertices[vertex])
                kept_indices.append(mapping[vertex])
        if len(kept) <= max_vertices and len(kept_indices) // 3 <= max_triangles:
            return kept, kept_indices, stride
        stride += 1


def _bounds(vertices):
    if not vertices:
        return None
    xs = [item[0] for item in vertices]
    ys = [item[1] for item in vertices]
    zs = [item[2] for item in vertices]
    return {
        "min_x": round(min(xs), 3), "max_x": round(max(xs), 3),
        "min_y": round(min(ys), 3), "max_y": round(max(ys), 3),
        "min_z": round(min(zs), 3), "max_z": round(max(zs), 3),
    }


def parse(raw: bytes, max_vertices: int = MAX_VERTICES, max_triangles: int = MAX_TRIANGLES) -> dict | None:
    if not raw:
        return None
    blocks = _blocks(raw)
    if not blocks:
        return None
    vertices = []
    indices = []
    tile_count = 0
    poly_count = 0
    offmesh_count = 0
    areas = {}
    versions = []
    hints = []
    headers = []
    for block in blocks:
        header = _tesm_header(block)
        if header is None:
            continue
        versions.append(header["version"])
        hints.append(header["hint"])
        headers.append(header["values"])
        for _key, _size, tile in header["records"]:
            info = _vand_header(tile)
            if info is None:
                continue
            tile_vertices = _tile_vertices(tile, info)
            tile_triangles, tile_areas = _tile_triangles(tile, info, tile_vertices)
            if not tile_vertices or not tile_triangles:
                continue
            if _winding(tile_vertices, tile_triangles):
                tile_triangles = [(a, c, b) for a, b, c in tile_triangles]
            base = len(vertices)
            vertices.extend(tile_vertices)
            for a, b, c in tile_triangles:
                indices.extend((base + a, base + b, base + c))
            tile_count += 1
            poly_count += info["regular"]
            offmesh_count += info["offn"]
            for area, count in tile_areas.items():
                areas[area] = areas.get(area, 0) + count
    if not vertices or not indices:
        return None
    vertices, indices = _compact(vertices, indices)
    source_vertices = len(vertices)
    source_triangles = len(indices) // 3
    source_bounds = _bounds(vertices)
    vertices, indices, stride = _sample(vertices, indices, max_vertices, max_triangles)
    bounds = source_bounds
    origin = [bounds["min_x"], bounds["min_z"]] if bounds else [0.0, 0.0]
    return {
        "magic": TESM_MAGIC.decode() if raw[:4] == TESM_MAGIC else TESL_MAGIC.decode(),
        "version": versions[0] if versions else None,
        "grid": None,
        "origin": origin,
        "cell_size": None,
        "heights": [item[1] for item in vertices],
        "vertices": vertices,
        "indices": indices,
        "materials": [],
        "textures": [],
        "bounds": bounds,
        "blocks": len(blocks),
        "tiles": tile_count,
        "polygons": poly_count,
        "offmesh": offmesh_count,
        "vertex_count": len(vertices),
        "triangle_count": len(indices) // 3,
        "source_vertices": source_vertices,
        "source_triangles": source_triangles,
        "sampled": stride > 1,
        "stride": stride,
        "versions": versions,
        "hints": hints,
        "areas": {str(key): value for key, value in sorted(areas.items())},
        "header_values": headers,
    }
