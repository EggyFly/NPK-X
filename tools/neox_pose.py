"""Apply a NeoX animation's cached pose to a skinned CMesh (CPU LBS)."""

from __future__ import annotations

import base64
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def mat_inv(m):
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(4)] for i, row in enumerate(m)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        value = a[col][col]
        a[col] = [item / value for item in a[col]]
        for row in range(4):
            if row == col:
                continue
            factor = a[row][col]
            if factor:
                a[row] = [a[row][k] - factor * a[col][k] for k in range(8)]
    return [row[4:] for row in a]


def quat_matrix(x, y, z, w):
    length = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / length, y / length, z / length, w / length
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def trs_matrix(values):
    tx, ty, tz = values[0:3]
    rotation = quat_matrix(*values[3:7])
    scale = values[7:10]
    matrix = [
        [rotation[row][0] * scale[0], rotation[row][1] * scale[1], rotation[row][2] * scale[2], translation]
        for row, translation in zip(range(3), (tx, ty, tz))
    ]
    matrix.append([0.0, 0.0, 0.0, 1.0])
    return matrix


def parse_skeleton(data):
    bone_count = struct.unpack_from("<H", data, 12)[0]
    if bone_count <= 0:
        return None
    parents = [data[14 + index] for index in range(bone_count)]
    names = [
        data[14 + bone_count + index * 32:14 + bone_count + (index + 1) * 32].split(b"\x00")[0].decode("latin1")
        for index in range(bone_count)
    ]
    flags = 14 + bone_count + bone_count * 32
    if flags >= len(data) or not data[flags]:
        return None
    bounds_start = flags + 1
    matrices_start = bounds_start + bone_count * 28
    if matrices_start + bone_count * 64 > len(data):
        return None
    bounds = [struct.unpack_from("<7f", data, bounds_start + index * 28) for index in range(bone_count)]
    raw_matrices = [struct.unpack_from("<16f", data, matrices_start + index * 64) for index in range(bone_count)]
    bind = []
    for index in range(bone_count):
        values = raw_matrices[index]
        bind.append([[values[col * 4 + row] for col in range(4)] for row in range(4)])
    return {
        "names": names,
        "parents": parents,
        "bind": bind,
        "bounds": bounds,
        "raw_matrices": raw_matrices,
    }


def parse_cached_pose(source):
    if isinstance(source, (bytes, bytearray)):
        root = ET.fromstring(source)
    else:
        root = ET.parse(Path(source)).getroot()
    names_node = root.find(".//JointNames")
    pose_node = root.find(".//Pose")
    if names_node is None or pose_node is None:
        return None
    names = names_node.get("Value", "").split(",")
    raw = base64.b64decode(pose_node.get("Value", ""))
    values = struct.unpack_from(f"<{len(raw) // 4}f", raw, 0)
    if len(values) != len(names) * 10:
        return None
    return {name: values[index * 10:(index + 1) * 10] for index, name in enumerate(names)}


def _hungarian(cost):
    rows = len(cost)
    cols = len(cost[0]) if cost else 0
    if rows == 0 or rows > cols:
        return [-1] * rows
    u = [0.0] * (rows + 1)
    v = [0.0] * (cols + 1)
    p = [0] * (cols + 1)
    way = [0] * (cols + 1)
    for i in range(1, rows + 1):
        p[0] = i
        j0 = 0
        minv = [float("inf")] * (cols + 1)
        used = [False] * (cols + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = float("inf")
            j1 = 0
            for j in range(1, cols + 1):
                if used[j]:
                    continue
                current = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if current < minv[j]:
                    minv[j] = current
                    way[j] = j0
                if minv[j] < delta:
                    delta = minv[j]
                    j1 = j
            for j in range(cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    assignment = [-1] * rows
    for j in range(1, cols + 1):
        if p[j]:
            assignment[p[j] - 1] = j - 1
    return assignment


def recover_joint_remap(parsed, skeleton):
    bone_count = len(skeleton["names"])
    joints = parsed.get("joints") or []
    weights = parsed.get("weights") or []
    vertices = parsed.get("vertices") or []
    max_raw = 0
    for joint_row in joints:
        for raw_joint in joint_row:
            if raw_joint != 255 and raw_joint > max_raw:
                max_raw = raw_joint
    if joints and max_raw < bone_count:
        return list(range(bone_count)), {"matched": 0, "identity": True, "bones": bone_count}
    palette_size = max(bone_count, max_raw + 1)
    lows = [[float("inf")] * 3 for _ in range(palette_size)]
    highs = [[-float("inf")] * 3 for _ in range(palette_size)]
    counts = [0] * palette_size
    for position, joint_row, weight_row in zip(vertices, joints, weights):
        for raw_joint, weight in zip(joint_row, weight_row):
            if raw_joint == 255 or weight <= 1e-7:
                continue
            counts[raw_joint] += 1
            for axis in range(3):
                if position[axis] < lows[raw_joint][axis]:
                    lows[raw_joint][axis] = position[axis]
                if position[axis] > highs[raw_joint][axis]:
                    highs[raw_joint][axis] = position[axis]
    active = [index for index, count in enumerate(counts) if count]
    if not active:
        return list(range(bone_count)), {"matched": 0}
    actual = {}
    for raw_joint in active:
        actual[raw_joint] = (
            [(lows[raw_joint][axis] + highs[raw_joint][axis]) * 0.5 for axis in range(3)],
            [(highs[raw_joint][axis] - lows[raw_joint][axis]) * 0.5 for axis in range(3)],
        )
    predicted = []
    for bounds, matrix in zip(skeleton["bounds"], skeleton["raw_matrices"]):
        cx, cy, cz, ex, ey, ez, _radius = bounds
        center = [
            cx * matrix[0] + cy * matrix[4] + cz * matrix[8] + matrix[12],
            cx * matrix[1] + cy * matrix[5] + cz * matrix[9] + matrix[13],
            cx * matrix[2] + cy * matrix[6] + cz * matrix[10] + matrix[14],
        ]
        extents = [
            abs(matrix[0]) * ex + abs(matrix[4]) * ey + abs(matrix[8]) * ez,
            abs(matrix[1]) * ex + abs(matrix[5]) * ey + abs(matrix[9]) * ez,
            abs(matrix[2]) * ex + abs(matrix[6]) * ey + abs(matrix[10]) * ez,
        ]
        predicted.append((center, extents))

    def distance(left, right):
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))

    def cost(raw_joint, named_bone):
        actual_center, actual_extents = actual[raw_joint]
        expected_center, expected_extents = predicted[named_bone]
        return distance(actual_center, expected_center) + 0.35 * distance(actual_extents, expected_extents)

    remap = list(range(palette_size))
    matched = 0
    known = [raw_joint for raw_joint in active if raw_joint < bone_count]
    if known:
        costs = [[cost(raw_joint, named) for named in range(bone_count)] for raw_joint in known]
        assignment = _hungarian(costs)
        for row, named in enumerate(assignment):
            if named >= 0:
                remap[known[row]] = named
                matched += 1
    extra = [raw_joint for raw_joint in active if raw_joint >= bone_count]
    for raw_joint in extra:
        best = min(range(bone_count), key=lambda named: cost(raw_joint, named))
        remap[raw_joint] = best
        matched += 1
    return remap, {"matched": matched, "active": len(active), "bones": bone_count, "palette": palette_size}


def apply_cached_pose(parsed, data, animation_path: Path):
    skeleton = parse_skeleton(data)
    if not skeleton:
        return None, "no embedded skeleton"
    cached = parse_cached_pose(animation_path)
    if not cached:
        return None, "no cached pose"
    names = skeleton["names"]
    parents = skeleton["parents"]
    bind = skeleton["bind"]
    inverse_bind = []
    for matrix in bind:
        inverse = mat_inv(matrix)
        if inverse is None:
            return None, "non-invertible bind matrix"
        inverse_bind.append(inverse)
    bind_local = []
    for index, parent in enumerate(parents):
        if 0 <= parent < len(bind):
            inverse = mat_inv(bind[parent])
            bind_local.append(mat_mul(inverse, bind[index]) if inverse else bind[index])
        else:
            bind_local.append(bind[index])
    locals_ = []
    matched = 0
    for index, name in enumerate(names):
        values = cached.get(name)
        if values is None:
            locals_.append(bind_local[index])
        else:
            locals_.append(trs_matrix(values))
            matched += 1
    if matched < max(3, len(names) // 4):
        return None, f"pose matched only {matched}/{len(names)} bones"
    globals_ = [None] * len(names)
    visiting = set()

    def resolve(index):
        if globals_[index] is not None:
            return globals_[index]
        if index in visiting:
            return bind[index]
        visiting.add(index)
        parent = parents[index] if index < len(parents) else 255
        result = locals_[index]
        if 0 <= parent < len(names):
            result = mat_mul(resolve(parent), result)
        visiting.remove(index)
        globals_[index] = result
        return result

    for index in range(len(names)):
        resolve(index)
    skin = [mat_mul(globals_[index], inverse_bind[index]) for index in range(len(names))]
    mapping, mapping_report = recover_joint_remap(parsed, skeleton)
    joints = parsed.get("joints") or []
    weights = parsed.get("weights") or []
    if len(joints) != len(parsed["vertices"]) or len(weights) != len(parsed["vertices"]):
        return None, "mesh has no skin weights"
    vertices = []
    normals = []
    source_normals = parsed.get("normals") or []
    active = 0
    for vertex_index, (x, y, z) in enumerate(parsed["vertices"]):
        total_x = total_y = total_z = 0.0
        normal_x = normal_y = normal_z = 0.0
        used = 0
        accepted_weight = 0.0
        for joint_index, weight in zip(joints[vertex_index], weights[vertex_index]):
            if joint_index == 255 or joint_index >= len(mapping) or weight <= 1e-7:
                continue
            mapped_joint = mapping[joint_index]
            if mapped_joint < 0 or mapped_joint >= len(skin):
                continue
            matrix = skin[mapped_joint]
            total_x += weight * (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3])
            total_y += weight * (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3])
            total_z += weight * (matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3])
            if vertex_index < len(source_normals):
                nx, ny, nz = source_normals[vertex_index]
                normal_x += weight * (matrix[0][0] * nx + matrix[0][1] * ny + matrix[0][2] * nz)
                normal_y += weight * (matrix[1][0] * nx + matrix[1][1] * ny + matrix[1][2] * nz)
                normal_z += weight * (matrix[2][0] * nx + matrix[2][1] * ny + matrix[2][2] * nz)
            accepted_weight += weight
            used += 1
        if used and accepted_weight > 1e-8:
            total_x /= accepted_weight
            total_y /= accepted_weight
            total_z /= accepted_weight
            normal_x /= accepted_weight
            normal_y /= accepted_weight
            normal_z /= accepted_weight
        if used:
            active += 1
            vertices.append([round(total_x, 5), round(total_y, 5), round(total_z, 5)])
            length = math.sqrt(normal_x * normal_x + normal_y * normal_y + normal_z * normal_z)
            if length > 1e-8:
                normals.append([round(normal_x / length, 4), round(normal_y / length, 4), round(normal_z / length, 4)])
            else:
                normals.append(source_normals[vertex_index] if vertex_index < len(source_normals) else [0.0, 1.0, 0.0])
        else:
            vertices.append([x, y, z])
            normals.append(source_normals[vertex_index] if vertex_index < len(source_normals) else [0.0, 1.0, 0.0])
    if active <= 0:
        return None, "no active skin weights"
    output = dict(parsed)
    output["vertices"] = vertices
    output["normals"] = normals
    mins = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
    maxs = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
    output["bounds"] = {"min": mins, "max": maxs}
    submeshes = []
    vertex_offset = 0
    for sub in parsed.get("submeshes", []):
        count = int(sub.get("vertex_count") or 0)
        chunk = vertices[vertex_offset:vertex_offset + count]
        lo = [min(vertex[axis] for vertex in chunk) for axis in range(3)] if chunk else [0.0, 0.0, 0.0]
        hi = [max(vertex[axis] for vertex in chunk) for axis in range(3)] if chunk else [0.0, 0.0, 0.0]
        item = dict(sub)
        item["bounds"] = {"min": lo, "max": hi}
        submeshes.append(item)
        vertex_offset += count
    output["submeshes"] = submeshes
    return output, {
        "matched_bones": matched,
        "bones": len(names),
        "active_vertices": active,
        "joint_remap": mapping_report,
    }
