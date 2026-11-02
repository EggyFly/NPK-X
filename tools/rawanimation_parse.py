"""Parse NeoX .rawanimation into tracks for web/animation_preview.js."""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import animation_parse
import neox_anim

ACL_TAGS = {0xAC10AC10: 4, 0xAC11AC11: 10}
CHANNEL_LAYOUT = (("translation", ("x", "y", "z"), 3), ("rotation", ("x", "y", "z", "w"), 4),
                  ("scale", ("x", "y", "z"), 3))
DEFAULT_CACHE = Path(tempfile.gettempdir()) / "npkx_rawanimation"
MAX_SAMPLES = 4000


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _align16(value):
    return (value + 15) // 16 * 16


def _error(message, **extra):
    result = {"format": "rawanimation", "tracks": [], "names": [], "error": message}
    result.update(extra)
    return result


def _find_chunks(payload):
    if len(payload) < 24 or payload[:8] != b"RAWANIMA":
        return None
    start = _u32(payload, 20)
    if start + 8 <= len(payload) and payload[start:start + 4] == b"HEAD":
        head_at = start
    else:
        head_at = payload.find(b"HEAD", 20)
    if head_at < 0 or head_at + 8 > len(payload):
        return None
    data_at = head_at + 8 + _u32(payload, head_at + 4)
    if data_at + 8 > len(payload) or payload[data_at:data_at + 4] != b"DATA":
        data_at = payload.find(b"DATA", head_at + 8)
    if data_at < 0 or data_at + 8 > len(payload):
        return None
    name_at = data_at + 8 + _u32(payload, data_at + 4)
    if name_at + 8 > len(payload) or payload[name_at:name_at + 4] != b"NAME":
        name_at = payload.find(b"NAME", data_at + 8)
    if name_at < 0 or name_at + 8 > len(payload):
        return None
    return head_at, data_at, name_at


def _read_names(payload, name_at):
    count = _u32(payload, name_at + 8)
    if count <= 0 or count > 65536:
        return []
    pos = name_at + 12
    names = []
    for _ in range(count):
        if pos + 4 > len(payload):
            break
        size = _u32(payload, pos)
        pos += 4
        if size <= 0 or size > 1024 or pos + size > len(payload):
            break
        names.append(payload[pos:pos + size].decode("latin1", "replace"))
        pos += size
    return names


def _head_token(head, pos):
    size = struct.unpack_from("<I", head, pos)[0]
    if 2 <= size <= 64 and pos + 4 + size <= len(head):
        chunk = head[pos + 4:pos + 4 + size]
        if all(32 <= byte < 127 for byte in chunk):
            return "s", chunk.decode("latin1"), pos + 4 + size
    return "u", size, pos + 4


def _head_arrays(head):
    pos = 36
    index_count = 0
    tracks = []
    while pos + 4 <= len(head):
        kind, value, pos = _head_token(head, pos)
        if kind != "s":
            continue
        if value == "PoseTrack":
            count = struct.unpack_from("<I", head, pos)[0]
            pos += 4
            for _ in range(count):
                sub_kind, sub_value, pos = _head_token(head, pos)
                if sub_kind == "s" and sub_value == "LNode":
                    size, number = struct.unpack_from("<II", head, pos)
                    pos += 8
                    if not index_count:
                        index_count = number
                elif sub_kind == "s" and sub_value == "DNode":
                    _name_kind, _name, pos = _head_token(head, pos)
                    pos += 4
        elif value == "TransformTrack":
            count = struct.unpack_from("<I", head, pos)[0]
            pos += 4
            leaves = []
            for _ in range(count):
                sub_kind, sub_value, pos = _head_token(head, pos)
                if sub_kind == "s" and sub_value == "LNode":
                    size, number = struct.unpack_from("<II", head, pos)
                    pos += 8
                    leaves.append((size, number))
                else:
                    pos -= 4
                    break
            tracks.append(leaves)
        elif value == "Empty":
            break
    return index_count, tracks


def _decimate(times, values, max_points):
    count = len(times)
    if count <= max_points:
        return times, values, 0
    step = (count + max_points - 1) // max_points
    keep = list(range(0, count, step))
    if keep[-1] != count - 1:
        if len(keep) >= max_points:
            keep[-1] = count - 1
        else:
            keep.append(count - 1)
    return [times[index] for index in keep], [values[index] for index in keep], count


def _make_track(node, channel, axes, times, values, source, joint_index=None, max_points=MAX_SAMPLES):
    times, values, keys = _decimate(times, values, max_points)
    points = [[float(time)] + [float(value) for value in row] for time, row in zip(times, values)]
    track = animation_parse._make_track(node, channel, axes, points, source, joint_index)
    if track is None:
        return None
    if keys:
        track["keys"] = keys
        track["truncated"] = True
    return track


def _legacy_tracks(body, head, names, fps, duration, max_points=MAX_SAMPLES):
    index_count, arrays = _head_arrays(head)
    nb = len(names)
    if index_count <= 0 or index_count > nb or len(arrays) != index_count:
        raise ValueError("legacy track table mismatch")
    if index_count * 4 > len(body):
        raise ValueError("legacy bone table truncated")
    indexes = list(struct.unpack_from("<%dI" % index_count, body, 0))
    base = 0 if min(indexes) == 0 else 1
    for index in indexes:
        if index < base or index >= base + nb:
            raise ValueError("legacy bone index out of range")
    pos = _align16(index_count * 4)
    tracks = []
    samples = 0
    last_end = 0.0
    step = 0.0
    for group, index in enumerate(indexes):
        node = names[index - base]
        leaves = arrays[group]
        for channel_index, (channel, axes, _comps) in enumerate(CHANNEL_LAYOUT):
            if len(leaves) <= channel_index * 2 + 1:
                continue
            time_size, time_count = leaves[channel_index * 2]
            value_size, value_count = leaves[channel_index * 2 + 1]
            count = min(time_count, value_count)
            if count <= 0:
                continue
            comps = value_size // 4
            if time_size != 4 or comps != len(axes):
                raise ValueError("legacy channel layout mismatch")
            time_at = pos
            pos = _align16(pos + time_size * time_count)
            data_at = pos
            pos = _align16(pos + value_size * value_count)
            if pos > len(body):
                raise ValueError("legacy record truncated")
            times = struct.unpack_from("<%df" % count, body, time_at)
            values = struct.unpack_from("<%df" % (count * comps), body, data_at)
            rows = [values[offset:offset + comps] for offset in range(0, len(values), comps)]
            track = _make_track(node, channel, axes, times, rows, "rawanimation", group, max_points)
            if track:
                tracks.append(track)
            samples = max(samples, count)
            last_end = max(last_end, times[-1])
            if count >= 2 and step <= 0:
                step = times[1] - times[0]
    if pos != len(body):
        raise ValueError("legacy data size mismatch")
    if fps <= 0 and step > 0:
        fps = 1.0 / step
    if last_end > 0:
        duration = last_end
    return tracks, samples, fps, duration


def _acl_tracks(decoded, names, max_points=MAX_SAMPLES):
    data = decoded.get("data") or b""
    bones = int(decoded.get("bones") or 0)
    samples = int(decoded.get("samples") or 0)
    rate = float(decoded.get("rate") or 30.0)
    layout = int(decoded.get("layout") or 10)
    if bones <= 0 or samples <= 0 or rate <= 0 or len(data) != bones * samples * layout * 4:
        raise ValueError("acl frame data mismatch")
    floats = struct.unpack("<%df" % (len(data) // 4), data)
    scale_at = 8 if layout == 12 else 7
    offsets = (("translation", ("x", "y", "z"), 4, 3),
               ("rotation", ("x", "y", "z", "w"), 0, 4),
               ("scale", ("x", "y", "z"), scale_at, 3))
    times = [round(frame / rate, 6) for frame in range(samples)]
    tracks = []
    for bone in range(bones):
        node = names[bone] if bone < len(names) else "bone %d" % bone
        for channel, axes, begin, comps in offsets:
            values = []
            for frame in range(samples):
                index = (frame * bones + bone) * layout + begin
                values.append(floats[index:index + comps])
            track = _make_track(node, channel, axes, times, values, "rawanimation", bone, max_points)
            if track:
                tracks.append(track)
    return tracks, samples, rate


def _parse(payload, cache_dir, max_points):
    chunks = _find_chunks(payload)
    if not chunks:
        raise ValueError("RAWANIMA chunks missing")
    head_at, data_at, name_at = chunks
    head_size = _u32(payload, head_at + 4)
    head = payload[head_at + 8:head_at + 8 + head_size]
    if len(head) != head_size:
        raise ValueError("HEAD chunk truncated")
    fps = float(struct.unpack_from("<f", payload, head_at + 8)[0])
    duration = float(struct.unpack_from("<f", payload, head_at + 12)[0])
    data_size = _u32(payload, data_at + 4)
    body = payload[data_at + 8:data_at + 8 + data_size]
    if len(body) != data_size:
        raise ValueError("DATA chunk truncated")
    all_names = _read_names(payload, name_at)
    names = all_names[1:] if all_names else []
    version = None
    tag = _u32(body, 8) if len(body) >= 16 else 0
    if tag in ACL_TAGS:
        version = ACL_TAGS[tag]
        decoder = Path(__file__).with_name("acl_decode_v10.exe" if version == 10 else "acl_decode.exe")
        decoder_v10 = Path(__file__).with_name("acl_decode_v10.exe")
        decoded = neox_anim.decode_acl(body, cache_dir, decoder, decoder_v10, version)
        if not decoded:
            raise ValueError("acl decode failed (version %d)" % version)
        tracks, samples, rate = _acl_tracks(decoded, names, max_points)
        fps = rate
        duration = samples / rate if rate else duration
        compression = "acl"
    else:
        version = _u32(payload, 16) if len(payload) >= 20 else 0
        tracks, samples, fps, duration = _legacy_tracks(body, head, names, fps, duration, max_points)
        compression = "raw"
    skeleton = ""
    path_size = _u32(payload, 40) if len(payload) >= 44 else 0
    if 0 < path_size <= 512 and 44 + path_size <= len(payload):
        skeleton = payload[44:44 + path_size].decode("latin1", "replace")
    return {
        "format": "rawanimation",
        "name": all_names[0] if all_names else "",
        "skeleton": skeleton,
        "fps": fps if fps > 0 else 30.0,
        "duration": duration if duration > 0 else 0.0,
        "version": version,
        "compression": compression,
        "samples": samples,
        "track_count": len(tracks),
        "names": names,
        "tracks": tracks,
        "size": len(payload),
    }


def parse(payload: bytes, cache_dir: Path | None = None, max_points: int = MAX_SAMPLES) -> dict:
    try:
        limit = max(2, int(max_points))
        return _parse(payload, Path(cache_dir) if cache_dir else DEFAULT_CACHE, limit)
    except Exception as exc:
        return _error("%s: %s" % (type(exc).__name__, exc))
