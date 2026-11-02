"""NeoX .animation reader for the viewer preview.

XML clips carry cached poses, optional rest/motion tracks and curve graphs.
RAWANIMA containers are read for header metadata and bone names only because
the ACL payload needs the external C++ decoder.
"""

from __future__ import annotations

import base64
import json
import math
import re
import struct
import xml.etree.ElementTree as ET

MAX_BYTES = 16 * 1024 * 1024
MAX_POINTS = 4000
MAX_KEYS = 20000
MAX_JOINTS = 4096
MAX_POSES = 64
MAX_REFS = 200
DEFAULT_FPS = 30.0
REF_EXTS = frozenset({
    "skeleton", "skel", "mesh", "gim", "ktx", "dds", "png", "jpg", "jpeg", "tga",
    "bmp", "webp", "gif", "mtg", "rawanimation", "animation", "animconfig",
    "trackgroup", "animgraph", "spr", "csb", "bin",
})
REF_BYTES = re.compile(
    rb"[A-Za-z0-9_./\\-]{2,240}\.(?:skeleton|skel|mesh|gim|ktx|dds|png|jpe?g|tga|bmp|"
    rb"webp|gif|mtg|rawanimation|animation|animconfig|trackgroup|animgraph|spr|csb|bin)\b",
    re.IGNORECASE,
)
XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")
BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
BOM = b"\xef\xbb\xbf"

CHANNELS = (
    ("translation", "TranslationTrack", ("x", "y", "z")),
    ("rotation", "RotationTrack", ("x", "y", "z", "w")),
    ("scale", "ScalingTrack", ("x", "y", "z")),
)


def _round(value, digits=6):
    number = round(float(value), digits)
    return 0.0 if number == 0 else number


def _number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text(value):
    if value is None:
        return ""
    return str(value).strip()


def _median(values):
    values = sorted(values)
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2.0


def _ref_ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in name:
        return ""
    ext = name.rsplit(".", 1)[-1].lower()
    return ext if ext in REF_EXTS else ""


def _ext_kind(path: str) -> str:
    ext = _ref_ext(path)
    if not ext:
        return "other"
    if ext in ("skeleton", "skel"):
        return "skeleton"
    if ext == "mesh":
        return "mesh"
    if ext in ("gim", "ktx", "dds", "png", "jpg", "jpeg", "tga", "bmp", "webp", "gif"):
        return "texture"
    if ext in ("rawanimation", "animation"):
        return "animation"
    if ext in ("trackgroup", "animconfig", "animgraph"):
        return "config"
    return ext


def _add_ref(refs, seen, path, source):
    text = _text(path).replace("\\", "/")
    if not text or len(text) > 300:
        return
    if not _ref_ext(text):
        return
    key = text.lower()
    if key in seen or len(refs) >= MAX_REFS:
        return
    seen.add(key)
    refs.append({"path": text, "kind": _ext_kind(text), "from": source})


def _payload_refs(payload: bytes, refs, seen, source="payload"):
    for match in REF_BYTES.finditer(payload[:4 * 1024 * 1024]):
        _add_ref(refs, seen, match.group().decode("ascii", "ignore"), source)


def _error(message, fmt, refs=None, **extra):
    result = {
        "error": message,
        "format": fmt,
        "duration": None,
        "tracks": [],
        "refs": list(refs or []),
        "sample_keys": {},
    }
    result.update(extra)
    return result


def _make_track(node, channel, axes, points, source, joint_index=None, extra=None):
    width = len(axes) + 1
    points = [point[:width] for point in points if len(point) >= width]
    if not points:
        return None
    points.sort(key=lambda point: point[0])
    deduped = {}
    for point in points:
        deduped[round(float(point[0]), 6)] = [round(float(value), 6) for value in point[1:]]
    times = sorted(deduped)
    keys = len(times)
    stored = [[time] + deduped[time] for time in times]
    truncated = keys > MAX_POINTS
    if truncated:
        step = max(1, int(math.ceil(keys / MAX_POINTS)))
        kept = stored[::step]
        if kept[-1][0] != stored[-1][0]:
            kept.append(stored[-1])
        stored = kept
    track = {
        "node": node,
        "channel": channel,
        "axes": list(axes),
        "source": source,
        "keys": keys,
        "start": times[0],
        "end": times[-1],
        "points": stored,
    }
    if joint_index is not None:
        track["joint_index"] = joint_index
    if truncated:
        track["truncated"] = True
    if extra:
        track.update(extra)
    return track


def _decode_xml(payload: bytes):
    candidates = [payload]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = payload.decode("gb18030")
        except UnicodeDecodeError:
            text = payload.decode("utf-8", "replace")
    text = XML_DECL.sub("", text, count=1)
    text = BAD_XML.sub("", text)
    candidates.append(text.encode("utf-8"))
    for candidate in candidates:
        try:
            return ET.fromstring(candidate)
        except Exception:
            continue
    return None


def _looks_path(value) -> bool:
    text = _text(value)
    if not text or len(text) > 300:
        return False
    return bool(_ref_ext(text))


def _xml_refs(root, payload, refs, seen):
    for node in root.iter():
        if node.tag == "FileName":
            _add_ref(refs, seen, node.get("Value"), node.tag)
            continue
        for value in node.attrib.values():
            if _looks_path(value):
                _add_ref(refs, seen, value, node.tag)
    _payload_refs(payload, refs, seen)


def _parse_events(root, start):
    events = []
    for track in root.findall("Annotation/Track"):
        points = []
        for key in track.findall("KeyFrame"):
            if len(points) >= MAX_KEYS:
                break
            time = _number(key.get("Time"))
            if time is None:
                continue
            value = key.get("Value")
            if value is None:
                parts = [key.get(axis) for axis in ("X", "Y", "Z", "W") if key.get(axis) is not None]
                value = ",".join(_text(part) for part in parts) if parts else ""
            points.append([_round(time - start), _text(value)])
        points.sort(key=lambda point: point[0])
        if not points:
            continue
        events.append({
            "node": _text(track.get("Name")) or "Track",
            "channel": "event",
            "type": _text(track.get("Type")) or "Custom",
            "keys": len(points),
            "start": points[0][0],
            "end": points[-1][0],
            "points": points,
        })
    return events


def _pose_layout(names, poses):
    tqs = []
    tsq = []
    for _time, values in poses:
        for index in range(len(names)):
            item = values[index * 10:(index + 1) * 10]
            if len(item) < 10:
                continue
            first = math.sqrt(sum(value * value for value in item[3:7]))
            second = math.sqrt(sum(value * value for value in item[6:10]))
            tqs.append(abs(first - 1.0))
            tsq.append(abs(second - 1.0))
    return "tqs" if _median(tqs) <= _median(tsq) else "tsq"


def _decode_pose(text):
    try:
        raw = base64.b64decode(text or "", validate=False)
    except Exception:
        return None
    if not raw or len(raw) % 4:
        return None
    return struct.unpack("<%df" % (len(raw) // 4), raw)


def _cached_tracks(root, start, warnings):
    pose_root = root.find("CachedPose")
    if pose_root is None:
        return [], 0, 0, None
    name_node = pose_root.find("JointNames")
    names = []
    if name_node is not None:
        names = [name.strip() for name in _text(name_node.get("Value")).split(",") if name.strip()]
    poses = []
    for node in pose_root.findall("CachedPoseTrack/Pose"):
        time = _number(node.get("Time"))
        values = _decode_pose(node.get("Value"))
        if time is None or not values:
            continue
        poses.append((time, values))
    if not names or not poses:
        return [], len(names), len(poses), None
    if len(names) > MAX_JOINTS:
        names = names[:MAX_JOINTS]
        warnings.append("joint list truncated")
    if len(poses) > MAX_POSES:
        poses = poses[:MAX_POSES]
        warnings.append("pose list truncated")
    layout = _pose_layout(names, poses)
    tracks = []
    for index, name in enumerate(names):
        points_t = []
        points_r = []
        points_s = []
        for time, values in poses:
            item = values[index * 10:(index + 1) * 10]
            if len(item) < 10:
                continue
            if layout == "tqs":
                translation = item[0:3]
                rotation = item[3:7]
                scale = item[7:10]
            else:
                translation = item[0:3]
                scale = item[3:6]
                rotation = item[6:10]
            shifted = time - start
            points_t.append([shifted] + list(translation))
            points_r.append([shifted] + list(rotation))
            points_s.append([shifted] + list(scale))
        for channel, points, axes in (
            ("translation", points_t, ("x", "y", "z")),
            ("rotation", points_r, ("x", "y", "z", "w")),
            ("scale", points_s, ("x", "y", "z")),
        ):
            track = _make_track(name, channel, axes, points, "cached_pose", index)
            if track:
                tracks.append(track)
    if layout == "tsq":
        warnings.append("cached pose layout is T+S+Q")
    return tracks, len(names), len(poses), layout


def _numeric_tracks(root, tag, source, start, joint_names):
    node = root.find(tag)
    if node is None:
        return []
    index = -1
    parent = root.find("Property")
    if parent is not None:
        value = parent.get("ExtractedJointIndex")
        try:
            index = int(value)
        except (TypeError, ValueError):
            index = -1
    name = joint_names[index] if 0 <= index < len(joint_names) else tag
    tracks = []
    for channel, child_tag, axes in CHANNELS:
        child = node.find(child_tag)
        if child is None:
            continue
        points = []
        for key in child.findall("KeyFrame"):
            if len(points) >= MAX_KEYS:
                break
            time = _number(key.get("Time"))
            if time is None:
                continue
            values = [_number(key.get(axis.upper()), 0.0) for axis in axes]
            points.append([time - start] + values)
        track = _make_track(name, channel, axes, points, source, index if index >= 0 else None)
        if track:
            tracks.append(track)
    return tracks


def _curve_tracks(root, start):
    tracks = []
    for graph in root.findall("Curve/CurveGraph"):
        node = _text(graph.get("Name")) or "Curve"
        points = []
        for key in graph.findall("KeyFrame"):
            if len(points) >= MAX_KEYS:
                break
            time = _number(key.get("Time"))
            value = _number(key.get("Value"))
            if time is None or value is None:
                continue
            points.append([time - start, value])
        track = _make_track(node, "curve", ("value",), points, "curve")
        if track:
            tracks.append(track)
    curve = root.find("Curve")
    if curve is not None and not curve.findall("CurveGraph"):
        points = []
        for key in curve.findall("KeyFrame"):
            if len(points) >= MAX_KEYS:
                break
            time = _number(key.get("Time"))
            value = _number(key.get("Value"))
            if time is None or value is None:
                continue
            points.append([time - start, value])
        track = _make_track("Curve", "curve", ("value",), points, "curve")
        if track:
            tracks.append(track)
    return tracks


def _sample_keys(tracks):
    samples = {}
    for channel in ("translation", "rotation", "scale", "curve"):
        candidates = [item for item in tracks if item["channel"] == channel and item.get("points")]
        if not candidates:
            continue
        candidates.sort(key=lambda item: (0 if item["source"] in ("rest", "motion", "curve") else 1, -item["keys"]))
        track = candidates[0]
        points = track["points"]
        if len(points) == 1:
            picks = [0]
        else:
            picks = sorted({0, len(points) // 2, len(points) - 1})
        rows = []
        for pick in picks:
            point = points[pick]
            rows.append({
                "node": track["node"],
                "source": track["source"],
                "time": point[0],
                "values": point[1:],
            })
        samples[channel] = rows
    return samples


def _estimate_fps(tracks):
    deltas = []
    for track in tracks:
        points = track.get("points") or []
        for index in range(1, min(len(points), 64)):
            delta = points[index][0] - points[index - 1][0]
            if delta > 1e-7:
                deltas.append(delta)
    if not deltas:
        return DEFAULT_FPS
    value = 1.0 / _median(deltas)
    if value <= 0:
        return DEFAULT_FPS
    return round(value, 2)


def _parse_xml(root, payload):
    refs = []
    seen = set()
    warnings = []
    _xml_refs(root, payload, refs, seen)
    head = root.find("Head")
    name_node = root.find("Name")
    prop = root.find("Property")
    properties = dict(prop.attrib) if prop is not None else {}
    start = _number(properties.get("StartTime"), 0.0) or 0.0
    end = _number(properties.get("EndTime"))
    joint_node = root.find("CachedPose/JointNames")
    joint_names = []
    if joint_node is not None:
        joint_names = [name.strip() for name in _text(joint_node.get("Value")).split(",") if name.strip()]
    cached, joint_count, pose_count, layout = _cached_tracks(root, start, warnings)
    tracks = []
    tracks.extend(_numeric_tracks(root, "RestTrack", "rest", start, joint_names))
    tracks.extend(_numeric_tracks(root, "MotionTrack", "motion", start, joint_names))
    tracks.extend(_curve_tracks(root, start))
    tracks.extend(cached)
    events = _parse_events(root, start)
    signals = []
    for node in root.findall("Signal/EventName"):
        value = _text(node.get("Name"))
        if value and value not in signals:
            signals.append(value)
    points_end = max(
        [track["end"] for track in tracks if track.get("end") is not None] +
        [event["end"] for event in events if event.get("end") is not None] +
        [0.0]
    )
    if end is None or end <= start:
        end = start + points_end
    duration = max(0.0, end - start)
    if points_end > duration + 0.05:
        duration = points_end
        end = start + duration
        warnings.append("track end is longer than clip end")
    if duration <= 0:
        duration = points_end
        warnings.append("duration comes from key times")
    fps = _estimate_fps(tracks)
    return {
        "format": "xml",
        "name": _text(name_node.get("Name")) if name_node is not None else "",
        "head": dict(head.attrib) if head is not None else {},
        "start": _round(start),
        "end": _round(end),
        "duration": _round(duration),
        "fps": fps,
        "loop": _text(properties.get("DefaultLoop")).lower() == "true",
        "rate": _number(properties.get("DefaultRate"), 1.0),
        "category": _text(properties.get("Category")),
        "compression": _text(properties.get("CompressionLevel")),
        "compression_version": _text(properties.get("CompressionVersion")),
        "joint_count": joint_count,
        "pose_count": pose_count,
        "pose_layout": layout,
        "tracks": tracks,
        "track_count": len(tracks),
        "events": events,
        "event_count": len(events),
        "signals": signals,
        "refs": refs,
        "ref_count": len(refs),
        "sample_keys": _sample_keys(tracks),
        "warnings": warnings,
    }


def _rawanimation_names(payload, head):
    data_chunk = payload.find(b"DATA", head + 8 if head >= 0 else 8)
    if data_chunk < 0:
        return []
    name_chunk = payload.find(b"NAME", data_chunk + 8)
    if name_chunk < 0 or name_chunk + 12 > len(payload):
        return []
    count = struct.unpack_from("<I", payload, name_chunk + 8)[0]
    if count <= 0 or count > 100000:
        return []
    position = name_chunk + 12
    names = []
    for _index in range(count):
        if position + 4 > len(payload):
            break
        size = struct.unpack_from("<I", payload, position)[0]
        position += 4
        if size <= 0 or size > 512 or position + size > len(payload):
            break
        names.append(payload[position:position + size].decode("latin1", "replace"))
        position += size
    return names


def _parse_rawanimation(payload):
    refs = []
    seen = set()
    if len(payload) < 48:
        return _error("RAWANIMA header truncated", "binary", refs, kind="rawanimation")
    size = struct.unpack_from("<I", payload, 8)[0]
    track_count = struct.unpack_from("<I", payload, 20)[0]
    path_size = struct.unpack_from("<I", payload, 40)[0]
    skeleton = ""
    if 0 < path_size <= 512 and 44 + path_size <= len(payload):
        skeleton = payload[44:44 + path_size].decode("latin1", "replace")
        _add_ref(refs, seen, skeleton, "header")
    head = payload.find(b"HEAD", 40 + path_size)
    fps = 0.0
    duration = 0.0
    if head >= 0 and head + 16 <= len(payload):
        fps = _number(struct.unpack_from("<f", payload, head + 8)[0], 0.0) or 0.0
        duration = _number(struct.unpack_from("<f", payload, head + 12)[0], 0.0) or 0.0
    names = _rawanimation_names(payload, head)
    bones = names[1:] if names else []
    acl_version = None
    data_chunk = payload.find(b"DATA", head + 8 if head >= 0 else 8)
    if data_chunk >= 0 and data_chunk + 20 <= len(payload):
        acl_version = struct.unpack_from("<I", payload, data_chunk + 20)[0] & 0xFFFF
    warnings = []
    if bones:
        warnings.append("ACL keyframes not decoded, header and bone names only")
    return {
        "format": "binary",
        "kind": "rawanimation",
        "name": skeleton.rsplit("/", 1)[-1].rsplit(".", 1)[0] if skeleton else "",
        "duration": _round(duration) if duration > 0 else 0.0,
        "fps": fps if fps > 0 else DEFAULT_FPS,
        "loop": None,
        "size": size,
        "track_count": track_count or len(bones),
        "bone_names": bones,
        "acl_version": acl_version,
        "tracks": [],
        "events": [],
        "signals": [],
        "refs": refs,
        "ref_count": len(refs),
        "sample_keys": {},
        "warnings": warnings,
    }


def _json_tracks(value):
    tracks = []
    if not isinstance(value, list):
        return tracks
    for item in value:
        if not isinstance(item, dict):
            continue
        node = _text(item.get("node") or item.get("name") or item.get("bone")) or "Track"
        channel = _text(item.get("channel") or item.get("type")) or "curve"
        points = item.get("points") or item.get("keys")
        normalized = []
        if isinstance(points, list):
            for point in points:
                if isinstance(point, (list, tuple)) and point:
                    time = _number(point[0])
                    values = [_number(value) for value in point[1:]]
                    if time is not None and values and all(value is not None for value in values):
                        normalized.append([time] + values)
                elif isinstance(point, dict):
                    time = _number(point.get("time") or point.get("Time"))
                    values = [point.get(key) for key in ("x", "y", "z", "w", "value") if point.get(key) is not None]
                    values = [_number(value) for value in values]
                    if time is not None and values and all(value is not None for value in values):
                        normalized.append([time] + values)
        axes = item.get("axes")
        if not isinstance(axes, list) or not axes:
            count = max(0, len(normalized[0]) - 1) if normalized else 0
            if count == 1:
                axes = ["value"]
            else:
                axes = ["x", "y", "z", "w"][:count] or ["value"]
        track = _make_track(node, channel, tuple(axes), normalized, "json")
        if track:
            tracks.append(track)
        elif item.get("start") is not None:
            tracks.append({
                "node": node,
                "channel": channel,
                "axes": list(axes),
                "source": "json",
                "keys": int(item.get("keys") or 0),
                "start": _round(_number(item.get("start"), 0.0)),
                "end": _round(_number(item.get("end"), 0.0)),
                "points": [],
            })
    return tracks


def _parse_json(payload):
    refs = []
    seen = set()
    try:
        document = json.loads(payload.decode("utf-8", "replace"))
    except Exception as exc:
        return _error(f"json decode failed: {exc}", "json", refs)

    def walk(value):
        if isinstance(value, str):
            if _looks_path(value):
                _add_ref(refs, seen, value, "json")
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(document)
    if not isinstance(document, dict):
        return _error("json animation is not an object", "json", refs)
    tracks = _json_tracks(document.get("tracks") or document.get("channels"))
    duration = _number(document.get("duration") or document.get("length"), 0.0)
    if duration is None or duration <= 0:
        duration = max([track["end"] for track in tracks] + [0.0])
    return {
        "format": "json",
        "name": _text(document.get("name") or document.get("Name")),
        "duration": _round(duration),
        "fps": _number(document.get("fps"), DEFAULT_FPS),
        "loop": document.get("loop") if isinstance(document.get("loop"), bool) else None,
        "tracks": tracks,
        "track_count": len(tracks),
        "events": [],
        "signals": [],
        "refs": refs,
        "ref_count": len(refs),
        "sample_keys": _sample_keys(tracks),
        "warnings": [],
    }


def _unknown_binary(payload):
    refs = []
    seen = set()
    _payload_refs(payload, refs, seen)
    head = payload[:16]
    hint = "unknown"
    if head.startswith(b"\xc1\x59\x41\x0d") or head.startswith(b"\xc1YA"):
        hint = "mtg"
    elif b"KTX" in head:
        hint = "ktx"
    elif head.startswith(b"SKELETON"):
        hint = "skeleton"
    elif head.startswith(b"MESH") or head.startswith(b"\x34\x80\xc8\xbb"):
        hint = "mesh"
    return _error(
        f"not a NeoX animation payload ({hint})",
        "binary",
        refs,
        kind=hint,
        size=len(payload),
        magic=head.hex(),
    )


def _parse(payload):
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        return _error("payload must be bytes", "binary")
    data = bytes(payload)
    if not data:
        return _error("empty payload", "binary")
    if len(data) > MAX_BYTES:
        return _error("payload too large", "binary", size=len(data))
    head = data.lstrip(BOM + b" \t\r\n")
    if head.startswith(b"<"):
        if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data[:4096].upper():
            return _error("xml DTD is not allowed", "xml")
        root = _decode_xml(data)
        if root is None:
            refs = []
            seen = set()
            _payload_refs(data, refs, seen)
            return _error("xml parse failed", "xml", refs)
        return _parse_xml(root, data)
    if head.startswith(b"RAWANIMA"):
        return _parse_rawanimation(data)
    if head.startswith(b"{") or head.startswith(b"["):
        return _parse_json(data)
    return _unknown_binary(data)


def parse(payload: bytes) -> dict:
    try:
        result = _parse(payload)
    except Exception as exc:
        fmt = "binary"
        if isinstance(payload, (bytes, bytearray, memoryview)):
            head = bytes(payload[:16]).lstrip(BOM + b" \t\r\n")
            if head.startswith(b"<"):
                fmt = "xml"
            elif head.startswith(b"{") or head.startswith(b"["):
                fmt = "json"
        return _error(f"{type(exc).__name__}: {exc}", fmt)
    return result
