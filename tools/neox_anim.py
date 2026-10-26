"""Decode NeoX legacy .rawanimation ACL v4 clips for the web viewer."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import subprocess
import tempfile
from pathlib import Path

import neox_pose


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def parse_rawanimation(data: bytes) -> dict | None:
    head = data.find(b"HEAD", 20)
    if head < 0:
        return None
    data_chunk = data.find(b"DATA", head + 8)
    if data_chunk < 0:
        return None
    name_chunk = data.find(b"NAME", data_chunk + 8)
    if name_chunk < 0:
        return None
    head_size = _u32(data, head + 4)
    if head + 8 + head_size > len(data):
        return None
    fps = struct.unpack_from("<f", data, head + 8)[0]
    duration = struct.unpack_from("<f", data, head + 12)[0]
    data_size = _u32(data, data_chunk + 4)
    acl = data[data_chunk + 8:data_chunk + 8 + data_size]
    name_size = _u32(data, name_chunk + 4)
    name_start = name_chunk + 8
    if name_start + 4 > len(data):
        return None
    count = _u32(data, name_start)
    pos = name_start + 4
    names = []
    for _ in range(count):
        if pos + 4 > len(data):
            break
        size = _u32(data, pos)
        pos += 4
        if size <= 0 or pos + size > len(data):
            break
        names.append(data[pos:pos + size].decode("latin1"))
        pos += size
    version = _u32(acl, 12) & 0xFFFF if len(acl) >= 16 else 0
    return {
        "fps": fps if fps > 0 else 30.0,
        "duration": duration if duration > 0 else 0.0,
        "acl": acl,
        "version": version,
        "names": names[1:] if names else [],
    }


def decode_acl(acl: bytes, cache_dir: Path, decoder: Path, decoder_v10: Path | None = None,
               version: int = 4) -> dict | None:
    selected = decoder_v10 if version == 10 and decoder_v10 and decoder_v10.is_file() else decoder
    if not selected.is_file():
        return None
    digest = hashlib.sha1(("v" + str(version)).encode("ascii") + acl).hexdigest()
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw_path = cache_dir / f"{digest}.f32"
    meta_path = cache_dir / f"{digest}.json"
    if raw_path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["data"] = raw_path.read_bytes()
            return meta
        except Exception:
            pass
    with tempfile.TemporaryDirectory(prefix="neox_acl_") as temp:
        source = Path(temp) / "input.acl"
        target = Path(temp) / "output.f32"
        source.write_bytes(acl)
        result = subprocess.run(
            [str(selected), str(source), str(target)],
            capture_output=True,
            timeout=60,
        )
        if not target.is_file():
            return None
        bones = samples = 0
        rate = 30.0
        layout = 12 if version == 10 else 10
        text_out = (result.stdout + b"\n" + result.stderr).decode("latin1", errors="replace")
        if version == 10:
            try:
                report = json.loads(result.stdout.decode("latin1", errors="replace"))
                bones = int(report.get("num_tracks") or 0)
                samples = int(report.get("num_samples") or 0)
                rate = float(report.get("sample_rate") or 30.0)
            except Exception:
                pass
        if bones <= 0 or samples <= 0:
            for part in text_out.replace(",", " ").split():
                if part.startswith("bones="):
                    bones = int(part.split("=", 1)[1])
                elif part.startswith("samples="):
                    samples = int(part.split("=", 1)[1])
                elif part.startswith("rate="):
                    rate = float(part.split("=", 1)[1])
        payload = target.read_bytes()
        if bones <= 0 or samples <= 0 or len(payload) != bones * samples * layout * 4:
            return None
        meta = {
            "bones": bones,
            "samples": samples,
            "rate": rate,
            "duration": samples / rate if rate else 0.0,
            "layout": layout,
            "data": payload,
        }
        raw_path.write_bytes(payload)
        meta_path.write_text(json.dumps({k: v for k, v in meta.items() if k != "data"}), encoding="utf-8")
        return meta


def build_animation(mesh_payload: bytes, parsed: dict, skeleton: dict, raw_payload: bytes,
                    cache_dir: Path, decoder: Path, decoder_v10: Path | None = None,
                    cached_animation: bytes | None = None) -> dict | None:
    if not skeleton or not parsed.get("joints"):
        return None
    raw = parse_rawanimation(raw_payload)
    if not raw or not raw["names"]:
        return None
    decoded = decode_acl(raw["acl"], cache_dir, decoder, decoder_v10, raw.get("version", 4))
    if not decoded:
        return None
    names = skeleton["names"]
    lookup = {name: index for index, name in enumerate(names)}
    track_bones = [lookup.get(name, -1) for name in raw["names"]]
    floats = struct.unpack(f"<{len(decoded['data']) // 4}f", decoded["data"])
    frame_count = decoded["samples"]
    track_count = decoded["bones"]
    layout = decoded.get("layout", 10)
    if len(floats) != frame_count * track_count * layout:
        return None

    def local_values(track, frame=0):
        offset = (frame * track_count + track) * layout
        if layout == 12:
            qx, qy, qz, qw = floats[offset:offset + 4]
            tx, ty, tz = floats[offset + 4:offset + 7]
            sx, sy, sz = floats[offset + 8:offset + 11]
        else:
            qx, qy, qz, qw = floats[offset:offset + 4]
            tx, ty, tz = floats[offset + 4:offset + 7]
            sx, sy, sz = floats[offset + 7:offset + 10]
        return [tx, ty, tz, qx, qy, qz, qw, sx, sy, sz]
    stride = 1
    if frame_count > 300:
        stride = (frame_count + 299) // 300
    keep = list(range(0, frame_count, stride))
    if keep[-1] != frame_count - 1:
        keep.append(frame_count - 1)
    packed_values = []
    for frame in keep:
        for track in range(track_count):
            packed_values.extend(local_values(track, frame))
    packed = struct.pack(f"<{len(packed_values)}f", *packed_values)
    mapping, _mapping_report = neox_pose.recover_joint_remap(parsed, skeleton)
    vertices = parsed.get("vertices") or []
    indices = parsed.get("indices") or []
    vertex_bones = [-1] * len(vertices)
    joint_rows = parsed.get("joints") or []
    weight_rows = parsed.get("weights") or []
    skinned = 0
    if len(joint_rows) == len(vertices) and len(weight_rows) == len(vertices):
        for joint_row, weight_row in zip(joint_rows, weight_rows):
            if any(j != 255 and j < len(mapping) and w > 1e-4 for j, w in zip(joint_row, weight_row)):
                skinned += 1
    if vertices and indices and skinned < len(vertices) * 0.9:
        parent = list(range(len(vertices)))

        def find(value):
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(left, right):
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[root_right] = root_left

        for index in range(0, len(indices) - 2, 3):
            union(indices[index], indices[index + 1])
            union(indices[index + 1], indices[index + 2])
        groups = {}
        for index in range(len(vertices)):
            groups.setdefault(find(index), []).append(index)

        target = None
        if cached_animation:
            try:
                posed, _pose_report = neox_pose.apply_cached_pose(parsed, mesh_payload, cached_animation)
                target = posed["vertices"] if posed else None
            except Exception:
                target = None

        skin_frame = None
        if target:
            bone_count = len(skeleton["names"])
            inverse_bind = [neox_pose.mat_inv(matrix) for matrix in skeleton["bind"]]
            bind_local = []
            for bone, parent_bone in enumerate(skeleton["parents"]):
                if 0 <= parent_bone < bone_count and inverse_bind[parent_bone]:
                    bind_local.append(neox_pose.mat_mul(inverse_bind[parent_bone], skeleton["bind"][bone]))
                else:
                    bind_local.append(skeleton["bind"][bone])
            track_for_bone = {}
            for track, bone in enumerate(track_bones):
                if bone >= 0:
                    track_for_bone[bone] = track
            locals_frame = []
            for bone in range(bone_count):
                track = track_for_bone.get(bone)
                if track is not None:
                    locals_frame.append(neox_pose.trs_matrix(local_values(track)))
                else:
                    locals_frame.append(bind_local[bone])
            globals_frame = [None] * bone_count

            def resolve(index):
                if globals_frame[index] is not None:
                    return globals_frame[index]
                parent_bone = skeleton["parents"][index]
                globals_frame[index] = (
                    neox_pose.mat_mul(resolve(parent_bone), locals_frame[index])
                    if 0 <= parent_bone < bone_count else locals_frame[index]
                )
                return globals_frame[index]

            for bone in range(bone_count):
                resolve(bone)
            skin_frame = [
                neox_pose.mat_mul(globals_frame[bone], inverse_bind[bone]) if inverse_bind[bone] else globals_frame[bone]
                for bone in range(bone_count)
            ]

        def transform_point(matrix, point):
            x, y, z = point
            return (
                matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
                matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
                matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
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
            return sum((a - b) ** 2 for a, b in zip(left, right)) ** 0.5

        limit = max(32, len(vertices) // 10)
        humanoid = any("bip001" in name.lower() or name.lower().startswith("biped") for name in skeleton["names"])
        head_bone = next(
            (index for index, name in enumerate(skeleton["names"])
             if name.lower().endswith("head") or " head" in name.lower()),
            -1,
        )
        for part in groups.values():
            if len(part) > limit:
                continue
            lows = [min(vertices[index][axis] for index in part) for axis in range(3)]
            highs = [max(vertices[index][axis] for index in part) for axis in range(3)]
            center = [(lows[axis] + highs[axis]) * 0.5 for axis in range(3)]
            extents = [(highs[axis] - lows[axis]) * 0.5 for axis in range(3)]
            if skin_frame and target:
                best = min(
                    range(len(skin_frame)),
                    key=lambda bone: sum(
                        (transform_point(skin_frame[bone], vertices[index])[0] - target[index][0]) ** 2
                        + (transform_point(skin_frame[bone], vertices[index])[1] - target[index][1]) ** 2
                        + (transform_point(skin_frame[bone], vertices[index])[2] - target[index][2]) ** 2
                        for index in part
                    ),
                )
            else:
                best = min(
                    range(len(predicted)),
                    key=lambda bone: distance(center, predicted[bone][0]) + 0.35 * distance(extents, predicted[bone][1]),
                )
            if humanoid and head_bone >= 0 and center[1] > 2.8:
                best = head_bone
            elif humanoid and 1.2 < center[1] < 2.6 and abs(center[0]) > 0.7:
                side = "l" if center[0] < 0 else "r"
                arm_bones = [
                    index for index, name in enumerate(skeleton["names"])
                    if any(token in name.lower() for token in ("upperarm", "forearm", " hand"))
                    and f" {side} " in f" {name.lower()} "
                ]
                if arm_bones:
                    best = min(
                        arm_bones,
                        key=lambda bone: distance(center, predicted[bone][0])
                        + 0.35 * distance(extents, predicted[bone][1]),
                    )
            for index in part:
                vertex_bones[index] = best
    bind = []
    for matrix in skeleton["bind"]:
        bind.extend(matrix[row][col] for col in range(4) for row in range(4))
    return {
        "fps": decoded["rate"] / stride,
        "duration": len(keep) / (decoded["rate"] / stride) if decoded["rate"] else 0.0,
        "frame_count": len(keep),
        "track_count": track_count,
        "track_bones": track_bones,
        "parents": skeleton["parents"],
        "bind": bind,
        "bone_names": names,
        "joint_map": mapping,
        "vertex_bones": vertex_bones,
        "tracks": base64.b64encode(packed).decode("ascii"),
    }
