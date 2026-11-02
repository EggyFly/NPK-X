"""Parse a NeoX .skeleton payload into bind pose data for the web preview."""

from __future__ import annotations

import math
import struct
from typing import Any

from neox_pose import mat_mul, trs_matrix

ALIGN = 16
ROOT_VALUES = (0xFFFF, 0xFFFE)
CHUNK_TAGS = (b"HEAD", b"DATA", b"NAME")


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _align16(value: int) -> int:
    return (value + ALIGN - 1) & ~(ALIGN - 1)


def _round(value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        return 0.0
    if abs(number) < 5e-7:
        return 0.0
    return round(number, 6)


def _chunks(data: bytes) -> dict[bytes, bytes]:
    if len(data) < 24 or not data.startswith(b"SKELETON"):
        raise ValueError("not a NeoX skeleton payload")
    position = _u32(data, 20)
    chunks: dict[bytes, bytes] = {}
    if position + 8 <= len(data) and data[position:position + 4] in CHUNK_TAGS:
        while position + 8 <= len(data):
            tag = data[position:position + 4]
            if tag not in CHUNK_TAGS:
                break
            size = _u32(data, position + 4)
            end = position + 8 + size
            if end > len(data):
                raise ValueError("skeleton chunk exceeds payload")
            chunks[tag] = data[position + 8:end]
            position = end
    if b"DATA" not in chunks or b"NAME" not in chunks:
        for tag in CHUNK_TAGS:
            position = 8
            while position + 8 <= len(data):
                if data[position:position + 4] == tag:
                    size = _u32(data, position + 4)
                    if position + 8 + size <= len(data):
                        chunks.setdefault(tag, data[position + 8:position + 8 + size])
                        break
                position += 1
    if b"DATA" not in chunks or b"NAME" not in chunks:
        raise ValueError("skeleton is missing DATA or NAME chunk")
    return chunks


def _read_names(chunk: bytes) -> list[str]:
    if len(chunk) < 4:
        raise ValueError("empty NAME chunk")
    count = _u32(chunk, 0)
    if count > 200000:
        raise ValueError("unreasonable bone count")
    position = 4
    names = []
    for _ in range(count):
        if position + 4 > len(chunk):
            raise ValueError("NAME string length is truncated")
        size = _u32(chunk, position)
        position += 4
        if size > 4096 or position + size > len(chunk):
            raise ValueError("NAME string exceeds chunk")
        raw = chunk[position:position + size].split(b"\x00", 1)[0]
        names.append(raw.decode("latin1"))
        position += size
    return names


def _read_group(data: bytes, position: int, name_count: int) -> tuple[dict[str, Any], int] | None:
    position = _align16(position)
    indices = []
    while position + 4 <= len(data):
        value = _u32(data, position)
        if value == 0 or value >= name_count:
            break
        indices.append(value)
        position += 4
    if not indices:
        return None
    position = _align16(position)
    if position + len(indices) * 2 > len(data):
        raise ValueError("skeleton parent array exceeds DATA chunk")
    parents = [_u16(data, position + index * 2) for index in range(len(indices))]
    position = _align16(position + len(indices) * 2)
    if position + len(indices) * 40 > len(data):
        raise ValueError("skeleton transform array exceeds DATA chunk")
    transforms = [struct.unpack_from("<10f", data, position + index * 40) for index in range(len(indices))]
    return {
        "indices": indices,
        "parents": parents,
        "transforms": transforms,
    }, position + len(indices) * 40


def _empty(warnings: list[str]) -> dict[str, Any]:
    return {
        "names": [],
        "parents": [],
        "bind_local": [],
        "bind_world": [],
        "bone_count": 0,
        "warnings": warnings,
    }


def parse(payload: bytes) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        return _parse(payload, warnings)
    except Exception as exc:
        warnings.append("骨架解析失败: %s" % exc)
        return _empty(warnings)


def _parse(payload: bytes, warnings: list[str]) -> dict[str, Any]:
    chunks = _chunks(payload)
    names = _read_names(chunks[b"NAME"])
    data = chunks[b"DATA"]
    head = chunks.get(b"HEAD", b"")
    name_count = len(names)

    trs_count = None
    extra_count = None
    if len(head) >= 16:
        counts = _u32(head, 12)
        trs_count = counts & 0xFFFF
        extra_count = counts >> 16
        if trs_count + extra_count + 1 != name_count:
            warnings.append("HEAD 计数与 NAME 数量不一致")
            trs_count = None
            extra_count = None

    first = _read_group(data, 0, name_count)
    if not first:
        warnings.append("DATA 中没有骨骼分组")
        return _empty(warnings)
    groups = [first[0]]
    position = first[1]

    if extra_count is None:
        second = _read_group(data, position, name_count) if position < len(data) else None
        if second:
            groups.append(second[0])
    elif extra_count:
        second = _read_group(data, position, name_count)
        if second:
            groups.append(second[0])
            if len(second[0]["indices"]) != extra_count:
                warnings.append("附加骨骼数量与 HEAD 不一致")
        else:
            warnings.append("缺少附加骨骼分组")

    if trs_count is not None and len(groups[0]["indices"]) != trs_count:
        warnings.append("主骨骼数量与 HEAD 不一致")

    order = [index for group in groups for index in group["indices"]]
    total = len(order)
    used = set(order)
    skipped = [index for index in range(name_count) if index not in used and index != 0]
    if skipped:
        warnings.append("跳过 %d 个未参与绑定的节点" % len(skipped))

    bone_names = [names[index] for index in order]
    parents: list[int] = []
    for group_index, group in enumerate(groups):
        for value in group["parents"]:
            if value in ROOT_VALUES or value >= total:
                parents.append(-1)
                continue
            if group_index == 0 and value >= len(groups[0]["indices"]):
                parents.append(-1)
                continue
            parents.append(value)

    bind_local = []
    locals_matrix = []
    for group in groups:
        for values in group["transforms"]:
            bind_local.append({
                "t": [_round(value) for value in values[0:3]],
                "q": [_round(value) for value in values[3:7]],
                "s": [_round(value) for value in values[7:10]],
            })
            locals_matrix.append(trs_matrix(values))

    world: list[Any] = [None] * total
    cycle_seen = False
    for index in range(total):
        chain = []
        cursor = index
        seen: set[int] = set()
        while cursor >= 0 and world[cursor] is None and cursor not in seen:
            seen.add(cursor)
            chain.append(cursor)
            cursor = parents[cursor]
        if cursor >= 0 and cursor in seen and not cycle_seen:
            warnings.append("检测到骨骼父级环路，已在断点处截断")
            cycle_seen = True
        while chain:
            current = chain.pop()
            parent = parents[current]
            local = locals_matrix[current]
            if 0 <= parent < total and world[parent] is not None:
                world[current] = mat_mul(world[parent], local)
            else:
                world[current] = local

    bind_world = [[_round(value) for row in matrix for value in row] for matrix in world]

    return {
        "names": bone_names,
        "parents": parents,
        "bind_local": bind_local,
        "bind_world": bind_world,
        "bone_count": total,
        "warnings": warnings,
    }
