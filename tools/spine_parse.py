import json
import math
import struct

MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_ITEMS = 200000
MAX_FRAMES = 200000
MAX_MESH_POINTS = 30000
MAX_MESH_INDICES = 90000

TRANSFORM_MODES = ("normal", "onlyTranslation", "noRotationOrReflection", "noScale", "noScaleOrReflection")
BLEND_MODES = ("normal", "additive", "multiply", "screen")
ATTACHMENT_TYPES = ("region", "boundingbox", "mesh", "linkedmesh", "path", "point", "clipping")
PAGE_KEYS = ("size", "format", "filter", "repeat", "pma", "scale", "poweroftwo", "bounds")


class SpineParseError(Exception):
    pass


def _num(value, default=0.0):
    if isinstance(value, bool) or value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _int(value, default=0):
    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value, default=""):
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return default


def _seq(value):
    return value if isinstance(value, list) else []


def _bad_constant(value):
    raise ValueError("bad json constant: %s" % value)


def _color(value):
    if isinstance(value, str):
        text = value.strip().lstrip("#").lower()
        if len(text) in (6, 8) and all(char in "0123456789abcdef" for char in text):
            return text
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        number = value & 0xFFFFFFFF
        return "%02x%02x%02x%02x" % (
            (number >> 24) & 255, (number >> 16) & 255, (number >> 8) & 255, number & 255,
        )
    return None


def _rgb_color(value):
    if isinstance(value, int) and not isinstance(value, bool):
        number = value & 0xFFFFFF
        return "%06x" % number
    color = _color(value)
    return color[:6] if color else None


def _version_major(version):
    text = _text(version)
    head = text.split(".", 1)[0]
    return _int(head, 0)


def _atlas_pair(value):
    text = _text(value)
    parts = [part.strip() for part in text.split(",")]
    if len(parts) >= 2:
        return _int(parts[0], 0), _int(parts[1], 0)
    return 0, 0


def _atlas_quad(value):
    text = _text(value)
    parts = [part.strip() for part in text.split(",")]
    if len(parts) >= 4:
        return _int(parts[0], 0), _int(parts[1], 0), _int(parts[2], 0), _int(parts[3], 0)
    return 0, 0, 0, 0


def _atlas_degrees(value):
    text = _text(value).strip().lower()
    if text in ("true", "yes"):
        return 90.0
    if text in ("", "false", "no"):
        return 0.0
    try:
        degrees = float(text)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(degrees) or math.isinf(degrees):
        return 0.0
    return degrees % 360.0


def _atlas_rotate(value):
    return _atlas_degrees(value) not in (0.0,)


def _atlas_bool(value):
    return _text(value).strip().lower() in ("true", "1", "yes")


def _page_lookahead(lines, index):
    for cursor in range(index + 1, min(index + 9, len(lines))):
        raw = lines[cursor]
        text = raw.strip()
        if not text:
            continue
        if raw[:1].isspace():
            return False
        key = text.split(":", 1)[0].strip().lower()
        return key in PAGE_KEYS
    return True


def parse_atlas(payload):
    if payload is None:
        return {"pages": [], "regions": {}, "page_count": 0, "region_count": 0}
    if isinstance(payload, bytes):
        text = payload.decode("utf-8-sig", "replace")
    else:
        text = str(payload)
    lines = text.splitlines()
    pages = []
    regions = {}
    duplicates = []
    current_page = None
    current_region = None
    for index, raw in enumerate(lines):
        text = raw.strip()
        if not text:
            continue
        if not raw[:1].isspace():
            key_head = text.split(":", 1)[0].strip().lower()
            if current_page is not None and current_region is None and ":" in text and key_head in PAGE_KEYS:
                key, separator, value = text.partition(":")
                value = value.strip()
                if key_head == "size":
                    current_page["width"], current_page["height"] = _atlas_pair(value)
                elif key_head == "format":
                    current_page["format"] = value
                elif key_head == "filter":
                    current_page["filter"] = value
                elif key_head == "repeat":
                    current_page["repeat"] = value
                elif key_head == "pma":
                    current_page["pma"] = _atlas_bool(value)
                continue
            if not current_page or _page_lookahead(lines, index):
                current_page = {
                    "name": text, "texture": text, "width": 0, "height": 0,
                    "format": "", "filter": "", "repeat": "", "pma": False,
                    "regions": [],
                }
                pages.append(current_page)
                current_region = None
                if len(pages) > MAX_ITEMS:
                    raise SpineParseError("atlas page limit exceeded")
                continue
            current_region = {
                "name": text, "x": 0, "y": 0, "width": 0, "height": 0,
                "origWidth": 0, "origHeight": 0, "offsetX": 0, "offsetY": 0,
                "rotate": False, "degrees": 0.0, "index": -1, "page": len(pages) - 1,
            }
            if not current_page:
                continue
            current_page["regions"].append(current_region)
            if len(current_page["regions"]) > MAX_ITEMS:
                raise SpineParseError("atlas region limit exceeded")
            if text not in regions:
                regions[text] = current_region
            else:
                duplicates.append(text)
            continue
        key, separator, value = text.partition(":")
        if not separator:
            continue
        key = key.strip().lower()
        value = value.strip()
        if current_region is not None:
            if key == "rotate":
                degrees = _atlas_degrees(value)
                current_region["degrees"] = degrees
                current_region["rotate"] = degrees not in (0.0,)
            elif key == "xy":
                current_region["x"], current_region["y"] = _atlas_pair(value)
            elif key == "bounds":
                left, top, width, height = _atlas_quad(value)
                current_region["x"] = left
                current_region["y"] = top
                current_region["width"] = width
                current_region["height"] = height
            elif key == "size":
                current_region["width"], current_region["height"] = _atlas_pair(value)
            elif key == "orig":
                current_region["origWidth"], current_region["origHeight"] = _atlas_pair(value)
            elif key == "offset":
                current_region["offsetX"], current_region["offsetY"] = _atlas_pair(value)
            elif key == "offsets":
                offset_x, offset_y, orig_width, orig_height = _atlas_quad(value)
                current_region["offsetX"] = offset_x
                current_region["offsetY"] = offset_y
                current_region["origWidth"] = orig_width
                current_region["origHeight"] = orig_height
            elif key == "index":
                current_region["index"] = _int(value, -1)
            continue
        if current_page is not None:
            if key == "size":
                current_page["width"], current_page["height"] = _atlas_pair(value)
            elif key == "format":
                current_page["format"] = value
            elif key == "filter":
                current_page["filter"] = value
            elif key == "repeat":
                current_page["repeat"] = value
            elif key == "pma":
                current_page["pma"] = _atlas_bool(value)
    result = {"pages": pages, "regions": regions, "page_count": len(pages), "region_count": len(regions)}
    if duplicates:
        result["duplicate_regions"] = duplicates[:50]
    if not pages:
        result["error"] = "empty atlas"
    return result


def _looks_weighted(items):
    if len(items) < 5:
        return False
    position = 0
    while position < len(items):
        count = items[position]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 16:
            return False
        position += 1 + count * 4
        if position > len(items):
            return False
    return position == len(items)


def _json_points(values):
    items = _seq(values)
    if not items:
        return [], False
    if _looks_weighted(items):
        points = []
        position = 0
        while position < len(items) and len(points) < MAX_ITEMS:
            count = _int(items[position])
            position += 1
            if count < 0 or position + count * 4 > len(items):
                break
            for _ in range(count):
                points.append([_num(items[position + 1]), _num(items[position + 2])])
                position += 4
        return points, True
    points = []
    for position in range(0, len(items) - 1, 2):
        points.append([_num(items[position]), _num(items[position + 1])])
    return points, False


def _json_mesh_data(attachment):
    items = _seq(attachment.get("vertices"))
    uvs_raw = _seq(attachment.get("uvs"))
    uv_count = len(uvs_raw) // 2
    if uv_count > 0:
        weighted = len(items) != uv_count * 2
    else:
        weighted = _looks_weighted(items)
    result = {"points": [], "truncated": False}
    if weighted:
        weights = []
        points = []
        position = 0
        while position < len(items):
            count = _int(items[position])
            position += 1
            if count < 0 or position + count * 4 > len(items):
                result["truncated"] = True
                break
            group = []
            for _ in range(count):
                bone = _int(items[position])
                x = _num(items[position + 1])
                y = _num(items[position + 2])
                weight = _num(items[position + 3])
                group.extend([bone, x, y, weight])
                points.append([x, y])
                position += 4
            weights.append(group)
            if len(weights) > MAX_MESH_POINTS:
                result["truncated"] = True
                break
        result["weighted"] = True
        result["weights"] = weights[:MAX_MESH_POINTS]
    else:
        flat = []
        points = []
        for position in range(0, len(items) - 1, 2):
            x = _num(items[position])
            y = _num(items[position + 1])
            flat.extend([x, y])
            points.append([x, y])
            if len(points) > MAX_MESH_POINTS:
                result["truncated"] = True
                break
        result["weighted"] = False
        result["vertices"] = flat[:MAX_MESH_POINTS * 2]
    uvs = [_num(item) for item in _seq(attachment.get("uvs"))]
    if uvs:
        result["uvs"] = uvs[:MAX_MESH_POINTS * 2]
        if len(uvs) > MAX_MESH_POINTS * 2:
            result["truncated"] = True
    triangles = [_int(item) for item in _seq(attachment.get("triangles"))]
    if triangles:
        result["triangles"] = triangles[:MAX_MESH_INDICES]
        if len(triangles) > MAX_MESH_INDICES:
            result["truncated"] = True
    result["points"] = points[:MAX_MESH_POINTS]
    return result


def _bbox(points, width=0, height=0):
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        if max_x > min_x or max_y > min_y:
            return [min_x, min_y, max_x, max_y]
    if width or height:
        return [-width / 2.0, -height / 2.0, width / 2.0, height / 2.0]
    return None


def _normalize_attachment(name, attachment):
    kind = _text(attachment.get("type")).lower() or "region"
    path = _text(attachment.get("path")) or name
    result = {"type": kind, "name": name, "path": path}
    if kind == "region":
        result["x"] = _num(attachment.get("x"))
        result["y"] = _num(attachment.get("y"))
        result["rotation"] = _num(attachment.get("rotation"))
        result["scaleX"] = _num(attachment.get("scaleX"), 1.0)
        result["scaleY"] = _num(attachment.get("scaleY"), 1.0)
        result["width"] = _num(attachment.get("width"))
        result["height"] = _num(attachment.get("height"))
        color = _color(attachment.get("color"))
        if color:
            result["color"] = color
        return result
    if kind == "mesh":
        mesh = _json_mesh_data(attachment)
        width = _num(attachment.get("width"))
        height = _num(attachment.get("height"))
        result["width"] = width
        result["height"] = height
        result["vertex_count"] = len(mesh["weights"]) if mesh["weighted"] else len(mesh["points"])
        result["triangle_count"] = len(mesh.get("triangles", ())) // 3
        result["weighted"] = mesh["weighted"]
        result["bbox"] = _bbox(mesh["points"], width, height)
        if mesh["weighted"]:
            result["weights"] = mesh["weights"]
        else:
            result["vertices"] = mesh["vertices"]
        if "uvs" in mesh:
            result["uvs"] = mesh["uvs"]
        if "triangles" in mesh:
            result["triangles"] = mesh["triangles"]
        if mesh["truncated"]:
            result["data_truncated"] = True
        color = _color(attachment.get("color"))
        if color:
            result["color"] = color
        return result
    if kind == "linkedmesh":
        result["skin"] = _text(attachment.get("skin")) or None
        result["parent"] = _text(attachment.get("parent")) or None
        result["width"] = _num(attachment.get("width"))
        result["height"] = _num(attachment.get("height"))
        result["bbox"] = _bbox([], result["width"], result["height"])
        return result
    if kind in ("boundingbox", "path", "clipping"):
        points, weighted = _json_points(attachment.get("vertices"))
        result["vertex_count"] = len(points)
        result["weighted"] = weighted
        result["bbox"] = _bbox(points)
        if kind == "clipping":
            result["end"] = _text(attachment.get("end"))
        return result
    if kind == "point":
        result["x"] = _num(attachment.get("x"))
        result["y"] = _num(attachment.get("y"))
        result["rotation"] = _num(attachment.get("rotation"))
        return result
    for key, value in attachment.items():
        if key in result:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def _normalize_skin_attachments(raw):
    result = {}
    if not isinstance(raw, dict):
        return result
    for slot_name, items in raw.items():
        slot = _text(slot_name)
        if not slot:
            continue
        bucket = {}
        if isinstance(items, dict):
            for index, (attachment_name, attachment) in enumerate(items.items()):
                name = _text(attachment_name) or ("att_%d" % index)
                if isinstance(attachment, dict):
                    bucket[name] = _normalize_attachment(name, attachment)
        elif isinstance(items, list):
            for index, attachment in enumerate(items):
                if isinstance(attachment, dict):
                    name = _text(attachment.get("name")) or ("att_%d" % index)
                    bucket[name] = _normalize_attachment(name, attachment)
        if bucket:
            result[slot] = bucket
    return result


def _normalize_skins(raw):
    skins = []
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name")) or ("default" if index == 0 else "skin_%d" % index)
            skins.append({"name": name, "attachments": _normalize_skin_attachments(item.get("attachments"))})
    elif isinstance(raw, dict):
        for index, (name, item) in enumerate(raw.items()):
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("attachments"), dict):
                attachments = item.get("attachments")
            else:
                attachments = item
            skins.append({"name": _text(name) or ("skin_%d" % index), "attachments": _normalize_skin_attachments(attachments)})
    if not skins:
        skins.append({"name": "default", "attachments": {}})
    seen = {}
    for skin in skins:
        name = skin["name"]
        if name in seen:
            seen[name] += 1
            skin["name"] = "%s_%d" % (name, seen[name])
        else:
            seen[name] = 1
    return skins


def _resolve_linked(skins):
    by_name = {skin["name"]: skin for skin in skins}
    for skin in skins:
        for slot_name, attachments in skin["attachments"].items():
            for name, attachment in attachments.items():
                if attachment.get("type") != "linkedmesh":
                    continue
                parent_skin = by_name.get(attachment.get("skin") or "default") or skins[0]
                parent = (parent_skin.get("attachments") or {}).get(slot_name, {}).get(attachment.get("parent") or "")
                if not parent:
                    continue
                for key in ("width", "height", "bbox", "vertex_count", "triangle_count", "weighted",
                            "vertices", "weights", "uvs", "triangles", "data_truncated"):
                    if key in parent:
                        attachment[key] = parent[key]
                if "data_truncated" not in parent:
                    attachment.pop("data_truncated", None)
                attachment["type"] = "mesh"
                attachment["linked"] = True


def _count_meshes(skins):
    total = 0
    for skin in skins:
        for attachments in (skin.get("attachments") or {}).values():
            for attachment in attachments.values():
                if attachment.get("type") == "mesh":
                    total += 1
    return total


def _build_bones(raw):
    bones = []
    for index, item in enumerate(_seq(raw)):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name")) or ("bone_%d" % index)
        bone = {
            "name": name,
            "parent": _text(item.get("parent")) or None,
            "x": _num(item.get("x")),
            "y": _num(item.get("y")),
            "rotation": _num(item.get("rotation")),
            "scaleX": _num(item.get("scaleX"), 1.0),
            "scaleY": _num(item.get("scaleY"), 1.0),
            "shearX": _num(item.get("shearX")),
            "shearY": _num(item.get("shearY")),
            "length": _num(item.get("length")),
            "transform": _text(item.get("transform") or item.get("inherit")) or "normal",
            "index": len(bones),
        }
        color = _color(item.get("color"))
        if color:
            bone["color"] = color
        bones.append(bone)
        if len(bones) > MAX_ITEMS:
            raise SpineParseError("bone limit exceeded")
    index_by_name = {}
    for bone in bones:
        if bone["name"] in index_by_name:
            suffix = 2
            name = "%s_%d" % (bone["name"], suffix)
            while name in index_by_name:
                suffix += 1
                name = "%s_%d" % (bone["name"], suffix)
            bone["name"] = name
        index_by_name[bone["name"]] = bone["index"]
    for bone in bones:
        parent = bone["parent"]
        if parent not in index_by_name or parent == bone["name"]:
            bone["parent"] = None
    for bone in bones:
        seen = set()
        cursor = bone
        while cursor is not None:
            if cursor["index"] in seen:
                bone["parent"] = None
                break
            seen.add(cursor["index"])
            cursor = bones[index_by_name[cursor["parent"]]] if cursor["parent"] in index_by_name else None
    children = {bone["name"]: [] for bone in bones}
    for bone in bones:
        if bone["parent"]:
            children[bone["parent"]].append(bone["name"])
    depth = {}
    queue = []
    for bone in bones:
        bone["children"] = children[bone["name"]]
        if not bone["parent"]:
            depth[bone["name"]] = 0
            queue.append(bone["name"])
    cursor = 0
    while cursor < len(queue):
        name = queue[cursor]
        cursor += 1
        for child in children.get(name, []):
            if child not in depth:
                depth[child] = depth[name] + 1
                queue.append(child)
    for bone in bones:
        bone["depth"] = depth.get(bone["name"], 0)
    return bones


def _build_slots(raw, bone_names):
    slots = []
    for index, item in enumerate(_seq(raw)):
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name")) or ("slot_%d" % index)
        bone = _text(item.get("bone"))
        if bone not in bone_names:
            bone = ""
        slot = {
            "name": name,
            "bone": bone,
            "attachment": _text(item.get("attachment")),
            "color": _color(item.get("color")) or "ffffffff",
            "dark": _rgb_color(item.get("dark") or item.get("darkColor")),
            "blend": _text(item.get("blend")) or "normal",
            "index": len(slots),
        }
        slots.append(slot)
        if len(slots) > MAX_ITEMS:
            raise SpineParseError("slot limit exceeded")
    return slots


def _curve(value):
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("linear", "stepped"):
            return text
        return text or None
    if isinstance(value, list):
        numbers = [_num(item) for item in value[:4]]
        if len(numbers) == 4:
            return numbers
        return None
    if isinstance(value, dict):
        if "type" in value:
            return _curve(value.get("type"))
        keys = ("cx1", "cy1", "cx2", "cy2")
        if all(key in value for key in keys):
            return [_num(value.get(key)) for key in keys]
    return None


def _collect_frames(raw, mapper):
    frames = []
    previous = 0.0
    for index, item in enumerate(_seq(raw)):
        if not isinstance(item, dict):
            continue
        time = _num(item.get("time"), previous)
        if time < 0:
            time = 0.0
        frame = mapper(item)
        frame["t"] = time
        curve = _curve(item.get("curve"))
        if curve is not None:
            frame["curve"] = curve
        frames.append(frame)
        previous = time
        if len(frames) > MAX_FRAMES:
            raise SpineParseError("frame limit exceeded")
    frames.sort(key=lambda frame: frame["t"])
    cleaned = []
    for frame in frames:
        if cleaned and cleaned[-1]["t"] == frame["t"]:
            cleaned[-1] = frame
        else:
            cleaned.append(frame)
    return cleaned


def _frame_color(item):
    for key in ("color", "rgba", "rgb"):
        value = item.get(key)
        color = _color(value)
        if color:
            return color
        if isinstance(value, list) and len(value) >= 3:
            numbers = [_num(part) for part in value[:4]]
            if numbers and max(numbers) <= 1.0:
                numbers = [int(round(part * 255)) for part in numbers]
            if len(numbers) >= 4:
                alpha = numbers[3] / 255.0
            else:
                alpha = _num(item.get("alpha"), 1.0)
            while len(numbers) < 3:
                numbers.append(0)
            return "%02x%02x%02x%02x" % (
                max(0, min(255, int(numbers[0]))), max(0, min(255, int(numbers[1]))),
                max(0, min(255, int(numbers[2]))), max(0, min(255, int(round(alpha * 255)))),
            )
    rgb = _color(item.get("rgb"))
    alpha = _num(item.get("alpha"), 1.0)
    if rgb:
        base = rgb[:6] if len(rgb) == 6 else rgb[:6]
        return base + "%02x" % max(0, min(255, int(round(alpha * 255))))
    return None


def _bone_channels(target, tracks):
    channels = []
    for kind in ("rotate", "translate", "scale", "shear"):
        if kind not in tracks:
            continue
        if kind == "rotate":
            frames = _collect_frames(tracks.get(kind), lambda item: {"v": _num(item.get("angle", item.get("value")), 0.0)})
        else:
            default = 1.0 if kind == "scale" else 0.0
            frames = _collect_frames(tracks.get(kind), lambda item: {
                "x": _num(item.get("x"), default), "y": _num(item.get("y"), default),
            })
        if frames:
            channels.append({"kind": "bone", "target": target, "type": kind, "frames": frames})
    return channels


def _slot_channels(target, tracks):
    channels = []
    if "attachment" in tracks:
        frames = _collect_frames(tracks.get("attachment"), lambda item: {"name": _text(item.get("name"))})
        if frames:
            channels.append({"kind": "slot", "target": target, "type": "attachment", "frames": frames})
    for source in ("color", "rgba", "rgb"):
        if source in tracks:
            frames = _collect_frames(tracks.get(source), lambda item: {"color": _frame_color(item) or "ffffffff"})
            if frames:
                channels.append({"kind": "slot", "target": target, "type": "color", "frames": frames})
            break
    for source in ("twoColor", "two_color"):
        if source in tracks:
            frames = _collect_frames(tracks.get(source), lambda item: {
                "light": _frame_color({"color": item.get("light") or item.get("color"), "rgb": item.get("rgb")}) or "ffffffff",
                "dark": _rgb_color(item.get("dark")) or "000000",
            })
            if frames:
                channels.append({"kind": "slot", "target": target, "type": "twoColor", "frames": frames})
            break
    return channels


def _constraint_channels(group, kind, keys):
    channels = []
    if not isinstance(group, dict):
        return channels
    for target, tracks in group.items():
        if not isinstance(tracks, dict):
            continue
        for type_name, raw in tracks.items():
            def mapper(item, names=keys):
                frame = {}
                for key in names:
                    if key in item:
                        value = item.get(key)
                        frame[key] = _num(value) if not isinstance(value, bool) else value
                return frame
            frames = _collect_frames(raw, mapper)
            if frames:
                channels.append({"kind": kind, "target": _text(target), "type": _text(type_name), "frames": frames})
    return channels


def _deform_channels(group):
    channels = []
    if not isinstance(group, dict):
        return channels
    for skin_name, slots in group.items():
        if not isinstance(slots, dict):
            continue
        for slot_name, attachments in slots.items():
            if not isinstance(attachments, dict):
                continue
            for attachment_name, raw in attachments.items():
                frames = _collect_frames(raw, lambda item: {})
                if frames:
                    channels.append({
                        "kind": "deform", "skin": _text(skin_name), "target": _text(slot_name),
                        "attachment": _text(attachment_name), "type": "deform", "frames": frames,
                    })
    return channels


def _draw_order_channels(raw):
    channels = []
    frames = []
    for index, item in enumerate(_seq(raw)):
        if not isinstance(item, dict):
            continue
        time = _num(item.get("time"), 0.0)
        offsets = []
        for offset in _seq(item.get("offsets")):
            if isinstance(offset, dict):
                offsets.append({"slot": _text(offset.get("slot")), "offset": _int(offset.get("offset"))})
        frames.append({"t": time, "offsets": offsets})
    if frames:
        channels.append({"kind": "drawOrder", "type": "drawOrder", "frames": frames})
    return channels


def _event_channels(raw):
    frames = []
    for item in _seq(raw):
        if not isinstance(item, dict):
            continue
        frame = {"t": _num(item.get("time"), 0.0), "name": _text(item.get("name"))}
        for key in ("int", "float", "string"):
            if key in item:
                frame[key] = item.get(key)
        frames.append(frame)
    return [{"kind": "event", "type": "event", "frames": frames}] if frames else []


def _normalize_animations(raw):
    animations = []
    items = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                items.append((_text(item.get("name")) or ("animation_%d" % index), item))
    for name, animation in items:
        if not isinstance(animation, dict):
            continue
        channels = []
        bones = animation.get("bones")
        if isinstance(bones, dict):
            for target, tracks in bones.items():
                if isinstance(tracks, dict):
                    channels.extend(_bone_channels(_text(target), tracks))
        slots = animation.get("slots")
        if isinstance(slots, dict):
            for target, tracks in slots.items():
                if isinstance(tracks, dict):
                    channels.extend(_slot_channels(_text(target), tracks))
        channels.extend(_constraint_channels(animation.get("ik"), "ik", (
            "mix", "softness", "bendPositive", "compress", "stretch", "uniform",
        )))
        channels.extend(_constraint_channels(animation.get("transform"), "transform", (
            "rotateMix", "translateMix", "scaleMix", "shearMix",
        )))
        channels.extend(_constraint_channels(animation.get("path"), "path", (
            "position", "spacing", "mix", "rotateMix", "translateMix",
        )))
        channels.extend(_deform_channels(animation.get("deform")))
        channels.extend(_draw_order_channels(animation.get("drawOrder")))
        channels.extend(_event_channels(animation.get("events")))
        duration = _num(animation.get("duration"))
        for channel in channels:
            frames = channel.get("frames") or []
            if frames:
                duration = max(duration, _num(frames[-1].get("t")))
        animations.append({"name": _text(name), "duration": duration, "channels": channels})
        if len(animations) > MAX_ITEMS:
            raise SpineParseError("animation limit exceeded")
    return animations


def _parse_json(payload, atlas):
    if len(payload) > MAX_JSON_BYTES:
        raise SpineParseError("json size limit exceeded")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("utf-8", "replace")
    try:
        document = json.loads(text, parse_constant=_bad_constant)
    except (ValueError, RecursionError) as exc:
        raise SpineParseError("invalid json: %s" % exc)
    if not isinstance(document, dict):
        raise SpineParseError("json root is not an object")
    skeleton_raw = document.get("skeleton")
    if not isinstance(skeleton_raw, dict):
        skeleton_raw = {}
    version = _text(skeleton_raw.get("spine") or document.get("version"))
    bones = _build_bones(document.get("bones"))
    if not bones:
        raise SpineParseError("no bones")
    bone_names = {bone["name"] for bone in bones}
    slots = _build_slots(document.get("slots"), bone_names)
    skins = _normalize_skins(document.get("skins"))
    _resolve_linked(skins)
    animations = _normalize_animations(document.get("animations"))
    default_skin = "default" if any(skin["name"] == "default" for skin in skins) else (skins[0]["name"] if skins else None)
    skeleton = {
        "hash": _text(skeleton_raw.get("hash")),
        "fps": _num(skeleton_raw.get("fps"), 30.0),
        "x": _num(skeleton_raw.get("x")),
        "y": _num(skeleton_raw.get("y")),
        "width": _num(skeleton_raw.get("width")),
        "height": _num(skeleton_raw.get("height")),
        "images": _text(skeleton_raw.get("images")),
        "audio": _text(skeleton_raw.get("audio")),
        "bones": bones,
        "skins": skins,
        "default_skin": default_skin,
    }
    result = {
        "format": "json",
        "version": version,
        "version_major": _version_major(version),
        "skeleton": skeleton,
        "slots": slots,
        "animations": animations,
        "mesh_count": _count_meshes(skins),
        "atlas": parse_atlas(atlas) if atlas is not None else {"pages": [], "regions": {}, "page_count": 0, "region_count": 0},
        "source_size": len(payload),
    }
    warnings = []
    if result["version_major"] not in (0, 3, 4):
        warnings.append("unknown spine version")
    for bone in bones:
        if bone["transform"] not in TRANSFORM_MODES:
            warnings.append("unknown transform mode")
            break
    if warnings:
        result["warnings"] = warnings
    return result


class _Bin:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def need(self, count):
        if count < 0 or self.pos + count > len(self.data):
            raise SpineParseError("truncated skel data")

    def byte(self):
        self.need(1)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def sbyte(self):
        value = self.byte()
        return value - 256 if value >= 128 else value

    def short(self):
        self.need(2)
        value = struct.unpack_from(">h", self.data, self.pos)[0]
        self.pos += 2
        return value

    def int32(self):
        self.need(4)
        value = struct.unpack_from(">i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def float32(self):
        self.need(4)
        value = struct.unpack_from(">f", self.data, self.pos)[0]
        self.pos += 4
        return _num(value)

    def boolean(self):
        return self.byte() != 0

    def count(self, limit=MAX_ITEMS):
        value = self.varint()
        if value < 0 or value > limit:
            raise SpineParseError("count limit exceeded")
        return value

    def varint(self, optimize_positive=True):
        value = self.byte()
        result = value & 0x7F
        shift = 7
        count = 1
        while value & 0x80:
            if count >= 5:
                raise SpineParseError("bad varint")
            value = self.byte()
            result |= (value & 0x7F) << shift
            shift += 7
            count += 1
        return result if optimize_positive else ((result >> 1) ^ -(result & 1))

    def string(self):
        length = self.varint()
        if length == 0:
            return None
        if length == 1:
            return ""
        length -= 1
        self.need(length)
        raw = self.data[self.pos:self.pos + length]
        self.pos += length
        return raw.decode("utf-8", "replace")

    def string_ref(self, strings):
        index = self.varint()
        if index == 0:
            return None
        if index - 1 >= len(strings):
            raise SpineParseError("bad string ref")
        return strings[index - 1]

    def floats(self, count):
        return [self.float32() for _ in range(count)]

    def shorts(self):
        count = self.varint()
        if count < 0 or count > MAX_ITEMS:
            raise SpineParseError("bad short array")
        return [self.short() for _ in range(count)]


def _binary_points_detail(reader, vertex_count):
    if vertex_count < 0 or vertex_count > MAX_ITEMS:
        raise SpineParseError("vertex limit exceeded")
    weighted = reader.boolean()
    if not weighted:
        values = reader.floats(vertex_count * 2)
        points = [[values[index], values[index + 1]] for index in range(0, len(values) - 1, 2)]
        return points, False, None, values
    points = []
    weights = []
    for _ in range(vertex_count):
        count = reader.varint()
        if count < 0 or count > 64:
            raise SpineParseError("bad vertex weights")
        group = []
        for _ in range(count):
            bone = reader.varint()
            x = reader.float32()
            y = reader.float32()
            weight = reader.float32()
            group.extend([bone, x, y, weight])
            points.append([x, y])
        weights.append(group)
    return points, True, weights, None


def _binary_points(reader, vertex_count):
    points, weighted, _, _ = _binary_points_detail(reader, vertex_count)
    return points, weighted


def _binary_attachment(reader, strings, attachment_name, nonessential, slot_names):
    name = reader.string_ref(strings) or attachment_name
    kind_index = reader.sbyte()
    if kind_index < 0 or kind_index >= len(ATTACHMENT_TYPES):
        raise SpineParseError("bad attachment type")
    kind = ATTACHMENT_TYPES[kind_index]
    result = {"type": kind, "name": name, "path": name}
    if kind == "region":
        path = reader.string_ref(strings) or name
        rotation = reader.float32()
        x = reader.float32()
        y = reader.float32()
        scale_x = reader.float32()
        scale_y = reader.float32()
        width = reader.float32()
        height = reader.float32()
        color = _color(reader.int32())
        result.update({
            "path": path, "rotation": rotation, "x": x, "y": y,
            "scaleX": scale_x, "scaleY": scale_y, "width": width, "height": height,
        })
        if color:
            result["color"] = color
        return result
    if kind == "boundingbox":
        vertex_count = reader.count()
        points, weighted = _binary_points(reader, vertex_count)
        if nonessential:
            reader.int32()
        result["vertex_count"] = len(points)
        result["weighted"] = weighted
        result["bbox"] = _bbox(points)
        return result
    if kind == "mesh":
        path = reader.string_ref(strings) or name
        color = _color(reader.int32())
        vertex_count = reader.count()
        uvs = reader.floats(vertex_count * 2)
        triangles = reader.shorts()
        points, weighted, weights, flat = _binary_points_detail(reader, vertex_count)
        reader.varint()
        width = 0.0
        height = 0.0
        if nonessential:
            reader.shorts()
            width = reader.float32()
            height = reader.float32()
        result.update({
            "path": path, "width": width, "height": height,
            "vertex_count": len(weights) if weighted and weights is not None else len(points),
            "triangle_count": len(triangles) // 3, "weighted": weighted,
            "bbox": _bbox(points, width, height),
        })
        if weighted:
            result["weights"] = weights[:MAX_MESH_POINTS]
        elif flat:
            result["vertices"] = flat[:MAX_MESH_POINTS * 2]
        if uvs:
            result["uvs"] = uvs[:MAX_MESH_POINTS * 2]
        if triangles:
            result["triangles"] = triangles[:MAX_MESH_INDICES]
        if (len(points) > MAX_MESH_POINTS or len(uvs) > MAX_MESH_POINTS * 2
                or len(triangles) > MAX_MESH_INDICES):
            result["data_truncated"] = True
        if color:
            result["color"] = color
        return result
    if kind == "linkedmesh":
        path = reader.string_ref(strings) or name
        color = _color(reader.int32())
        skin_name = reader.string_ref(strings)
        parent = reader.string_ref(strings)
        reader.boolean()
        width = 0.0
        height = 0.0
        if nonessential:
            width = reader.float32()
            height = reader.float32()
        result.update({
            "path": path, "width": width, "height": height,
            "_linked": True, "skin": skin_name, "parent": parent,
            "bbox": _bbox([], width, height),
        })
        if color:
            result["color"] = color
        return result
    if kind == "path":
        reader.boolean()
        reader.boolean()
        vertex_count = reader.count()
        points, weighted = _binary_points(reader, vertex_count)
        reader.floats(vertex_count // 3)
        if nonessential:
            reader.int32()
        result["vertex_count"] = len(points)
        result["weighted"] = weighted
        result["bbox"] = _bbox(points)
        return result
    if kind == "point":
        result["rotation"] = reader.float32()
        result["x"] = reader.float32()
        result["y"] = reader.float32()
        if nonessential:
            reader.int32()
        return result
    if kind == "clipping":
        end_slot = reader.varint()
        if 0 <= end_slot < len(slot_names):
            result["end"] = slot_names[end_slot]
        vertex_count = reader.count()
        points, weighted = _binary_points(reader, vertex_count)
        if nonessential:
            reader.int32()
        result["vertex_count"] = len(points)
        result["weighted"] = weighted
        result["bbox"] = _bbox(points)
        return result
    return result


def _binary_read_skin(reader, strings, slots, bones, nonessential, default_skin):
    if default_skin:
        slot_count = reader.count()
        if slot_count == 0:
            return None
        skin = {"name": "default", "attachments": {}}
    else:
        name = reader.string_ref(strings) or ("skin_%d" % len(bones))
        bone_count = reader.count()
        for _ in range(bone_count):
            reader.varint()
        for _ in range(reader.count()):
            reader.varint()
        for _ in range(reader.count()):
            reader.varint()
        for _ in range(reader.count()):
            reader.varint()
        slot_count = reader.count()
        skin = {"name": name, "attachments": {}}
    if slot_count > MAX_ITEMS:
        raise SpineParseError("skin slot limit exceeded")
    for _ in range(slot_count):
        slot_index = reader.varint()
        if slot_index < 0 or slot_index >= len(slots):
            raise SpineParseError("bad slot index")
        slot_name = slots[slot_index]
        count = reader.count()
        if count > MAX_ITEMS:
            raise SpineParseError("skin attachment limit exceeded")
        for _ in range(count):
            attachment_name = reader.string_ref(strings)
            attachment = _binary_attachment(reader, strings, attachment_name, nonessential, slots)
            key = attachment_name or attachment.get("name") or "attachment"
            skin["attachments"].setdefault(slot_name, {})[key] = attachment
    return skin


def _binary_curve(reader):
    kind = reader.sbyte()
    if kind == 1:
        return "stepped"
    if kind == 2:
        return [reader.float32(), reader.float32(), reader.float32(), reader.float32()]
    return None


def _binary_frames(reader, count, mapper, curve=True):
    frames = []
    for index in range(count):
        time = reader.float32()
        frame = mapper()
        frame["t"] = time
        if curve and index < count - 1:
            value = _binary_curve(reader)
            if value is not None:
                frame["curve"] = value
        frames.append(frame)
    return frames


def _binary_read_animation(reader, name, strings, slots, bones, skin_names, events, ik_names, transform_names, path_names):
    channels = []
    duration = 0.0

    def pick(names, index):
        if 0 <= index < len(names) and names[index]:
            return names[index]
        return str(index)
    for _ in range(reader.count()):
        slot_index = reader.varint()
        if slot_index < 0 or slot_index >= len(slots):
            raise SpineParseError("bad slot index")
        slot_name = slots[slot_index]
        for _ in range(reader.count()):
            timeline_type = reader.sbyte()
            frame_count = reader.count(MAX_FRAMES)
            if frame_count < 0 or frame_count > MAX_FRAMES:
                raise SpineParseError("bad frame count")
            if timeline_type == 0:
                frames = _binary_frames(reader, frame_count, lambda: {"name": reader.string_ref(strings) or ""}, False)
                channels.append({"kind": "slot", "target": slot_name, "type": "attachment", "frames": frames})
            elif timeline_type == 1:
                frames = _binary_frames(reader, frame_count, lambda: {"color": _color(reader.int32()) or "ffffffff"})
                channels.append({"kind": "slot", "target": slot_name, "type": "color", "frames": frames})
            elif timeline_type == 2:
                frames = _binary_frames(reader, frame_count, lambda: {
                    "light": _color(reader.int32()) or "ffffffff", "dark": _rgb_color(reader.int32()) or "000000",
                })
                channels.append({"kind": "slot", "target": slot_name, "type": "twoColor", "frames": frames})
            else:
                raise SpineParseError("bad slot timeline")
            if frames:
                duration = max(duration, frames[-1]["t"])
    for _ in range(reader.count()):
        bone_index = reader.varint()
        if bone_index < 0 or bone_index >= len(bones):
            raise SpineParseError("bad bone index")
        bone_name = bones[bone_index]
        for _ in range(reader.count()):
            timeline_type = reader.sbyte()
            frame_count = reader.count(MAX_FRAMES)
            if frame_count < 0 or frame_count > MAX_FRAMES:
                raise SpineParseError("bad frame count")
            if timeline_type == 0:
                frames = _binary_frames(reader, frame_count, lambda: {"v": reader.float32()})
                channels.append({"kind": "bone", "target": bone_name, "type": "rotate", "frames": frames})
            elif timeline_type in (1, 2, 3):
                kind = ("translate", "scale", "shear")[timeline_type - 1]
                frames = _binary_frames(reader, frame_count, lambda: {"x": reader.float32(), "y": reader.float32()})
                channels.append({"kind": "bone", "target": bone_name, "type": kind, "frames": frames})
            else:
                raise SpineParseError("bad bone timeline")
            if frames:
                duration = max(duration, frames[-1]["t"])
    for _ in range(reader.count()):
        index = reader.varint()
        frames = _binary_frames(reader, reader.count(MAX_FRAMES), lambda: {
            "mix": reader.float32(), "softness": reader.float32(), "bend": reader.sbyte(),
            "compress": reader.boolean(), "stretch": reader.boolean(),
        })
        channels.append({"kind": "ik", "target": pick(ik_names, index), "type": "mix", "frames": frames})
        if frames:
            duration = max(duration, frames[-1]["t"])
    for _ in range(reader.count()):
        index = reader.varint()
        frames = _binary_frames(reader, reader.count(MAX_FRAMES), lambda: {
            "rotateMix": reader.float32(), "translateMix": reader.float32(), "scaleMix": reader.float32(),
            "shearMix": reader.float32(),
        })
        channels.append({"kind": "transform", "target": pick(transform_names, index), "type": "mix", "frames": frames})
        if frames:
            duration = max(duration, frames[-1]["t"])
    for _ in range(reader.count()):
        index = reader.varint()
        for _ in range(reader.count()):
            timeline_type = reader.sbyte()
            frame_count = reader.count(MAX_FRAMES)
            if timeline_type in (0, 1):
                kind = "position" if timeline_type == 0 else "spacing"
                frames = _binary_frames(reader, frame_count, lambda: {"value": reader.float32()})
                channels.append({"kind": "path", "target": pick(path_names, index), "type": kind, "frames": frames})
            elif timeline_type == 2:
                frames = _binary_frames(reader, frame_count, lambda: {
                    "rotateMix": reader.float32(), "translateMix": reader.float32(),
                })
                channels.append({"kind": "path", "target": pick(path_names, index), "type": "mix", "frames": frames})
            else:
                raise SpineParseError("bad path timeline")
            if frames:
                duration = max(duration, frames[-1]["t"])
    for _ in range(reader.count()):
        skin_index = reader.varint()
        skin_name = skin_names[skin_index] if 0 <= skin_index < len(skin_names) else ""
        for _ in range(reader.count()):
            slot_index = reader.varint()
            slot_name = slots[slot_index] if 0 <= slot_index < len(slots) else ""
            for _ in range(reader.count()):
                attachment_name = reader.string_ref(strings) or ""
                frame_count = reader.count(MAX_FRAMES)
                frames = []
                for _ in range(frame_count):
                    time = reader.float32()
                    count = reader.varint()
                    if count != 0:
                        start = reader.varint()
                        for _ in range(count):
                            reader.float32()
                    frames.append({"t": time})
                    if frame_count > 1 and len(frames) < frame_count:
                        _binary_curve(reader)
                channels.append({
                    "kind": "deform", "skin": skin_name, "target": slot_name, "attachment": attachment_name,
                    "type": "deform", "frames": frames,
                })
                if frames:
                    duration = max(duration, frames[-1]["t"])
    draw_frames = []
    for _ in range(reader.count()):
        time = reader.float32()
        offsets = []
        for _ in range(reader.count()):
            slot_index = reader.varint()
            offset = reader.varint()
            slot_name = slots[slot_index] if 0 <= slot_index < len(slots) else ""
            offsets.append({"slot": slot_name, "offset": offset})
        draw_frames.append({"t": time, "offsets": offsets})
    if draw_frames:
        channels.append({"kind": "drawOrder", "type": "drawOrder", "frames": draw_frames})
        duration = max(duration, draw_frames[-1]["t"])
    event_frames = []
    for _ in range(reader.count()):
        time = reader.float32()
        event_index = reader.varint()
        event_data = events[event_index] if 0 <= event_index < len(events) else {}
        reader.varint(False)
        reader.float32()
        if reader.boolean():
            reader.string()
        if event_data.get("audio") is not None:
            reader.float32()
            reader.float32()
        event_frames.append({"t": time, "name": event_data.get("name") or ""})
    if event_frames:
        channels.append({"kind": "event", "type": "event", "frames": event_frames})
        duration = max(duration, event_frames[-1]["t"])
    return {"name": name, "duration": duration, "channels": channels}


def _parse_skel(payload, atlas):
    reader = _Bin(payload)
    skeleton_hash = reader.string()
    version = reader.string() or ""
    major = _version_major(version)
    if major != 3:
        raise SpineParseError("skel unsupported: version %s" % (version or "unknown"))
    x = reader.float32()
    y = reader.float32()
    width = reader.float32()
    height = reader.float32()
    nonessential = reader.boolean()
    fps = 30.0
    images = ""
    audio = ""
    if nonessential:
        fps = reader.float32()
        images = reader.string() or ""
        audio = reader.string() or ""
    strings = []
    for _ in range(reader.count()):
        strings.append(reader.string())
    bones = []
    for index in range(reader.count()):
        name = reader.string() or ("bone_%d" % index)
        parent = None if index == 0 else reader.varint()
        bone = {
            "name": name, "parent": bones[parent]["name"] if parent is not None and 0 <= parent < len(bones) else None,
            "rotation": reader.float32(), "x": reader.float32(), "y": reader.float32(),
            "scaleX": reader.float32(), "scaleY": reader.float32(), "shearX": reader.float32(), "shearY": reader.float32(),
            "length": reader.float32(), "index": index,
        }
        mode = reader.varint()
        bone["transform"] = TRANSFORM_MODES[mode] if 0 <= mode < len(TRANSFORM_MODES) else "normal"
        reader.boolean()
        if nonessential:
            color = _color(reader.int32())
            if color:
                bone["color"] = color
        bones.append(bone)
        if len(bones) > MAX_ITEMS:
            raise SpineParseError("bone limit exceeded")
    slots = []
    for index in range(reader.count()):
        name = reader.string() or ("slot_%d" % index)
        bone_index = reader.varint()
        color = _color(reader.int32()) or "ffffffff"
        dark_value = reader.int32()
        dark = None if dark_value == -1 else _rgb_color(dark_value)
        attachment = reader.string_ref(strings) or ""
        blend = reader.varint()
        slot = {
            "name": name, "bone": bones[bone_index]["name"] if 0 <= bone_index < len(bones) else "",
            "attachment": attachment, "color": color, "dark": dark,
            "blend": BLEND_MODES[blend] if 0 <= blend < len(BLEND_MODES) else "normal", "index": index,
        }
        slots.append(slot)
        if len(slots) > MAX_ITEMS:
            raise SpineParseError("slot limit exceeded")
    ik_names = []
    for _ in range(reader.count()):
        ik_names.append(reader.string() or "")
        reader.varint()
        reader.boolean()
        for _ in range(reader.count()):
            reader.varint()
        reader.varint()
        reader.float32()
        reader.float32()
        reader.sbyte()
        reader.boolean()
        reader.boolean()
        reader.boolean()
    transform_names = []
    for _ in range(reader.count()):
        transform_names.append(reader.string() or "")
        reader.varint()
        reader.boolean()
        for _ in range(reader.count()):
            reader.varint()
        reader.varint()
        reader.boolean()
        reader.boolean()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
    path_names = []
    for _ in range(reader.count()):
        path_names.append(reader.string() or "")
        reader.varint()
        reader.boolean()
        for _ in range(reader.count()):
            reader.varint()
        reader.varint()
        reader.varint()
        reader.varint()
        reader.varint()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
        reader.float32()
    skins = []
    default_skin = _binary_read_skin(reader, strings, [slot["name"] for slot in slots], bones, nonessential, True)
    if default_skin:
        skins.append(default_skin)
    for _ in range(reader.count()):
        skin = _binary_read_skin(reader, strings, [slot["name"] for slot in slots], bones, nonessential, False)
        if skin:
            skins.append(skin)
    for skin in skins:
        for slot_name, attachments in skin["attachments"].items():
            for name, attachment in attachments.items():
                if not attachment.get("_linked"):
                    continue
                parent_skin = next((item for item in skins if item["name"] == (attachment.get("skin") or "default")), None)
                if parent_skin is None and skins:
                    parent_skin = skins[0]
                parent = (parent_skin or {}).get("attachments", {}).get(slot_name, {}).get(attachment.get("parent") or "")
                if parent:
                    for key in ("width", "height", "bbox", "vertex_count", "triangle_count", "weighted",
                                "vertices", "weights", "uvs", "triangles", "data_truncated"):
                        if key in parent:
                            attachment[key] = parent[key]
                    if "data_truncated" not in parent:
                        attachment.pop("data_truncated", None)
                    attachment["type"] = "mesh"
                    attachment["linked"] = True
                attachment.pop("_linked", None)
    events = []
    for _ in range(reader.count()):
        event = {"name": reader.string_ref(strings) or ""}
        reader.varint(False)
        reader.float32()
        reader.string()
        event["audio"] = reader.string()
        if event["audio"] is not None:
            reader.float32()
            reader.float32()
        events.append(event)
    animations = []
    for _ in range(reader.count()):
        name = reader.string() or ("animation_%d" % len(animations))
        animations.append(_binary_read_animation(reader, name, strings, [slot["name"] for slot in slots], [bone["name"] for bone in bones], [skin["name"] for skin in skins], events, ik_names, transform_names, path_names))
        if len(animations) > MAX_ITEMS:
            raise SpineParseError("animation limit exceeded")
    if reader.pos != len(payload):
        trailing = len(payload) - reader.pos
    else:
        trailing = 0
    skeleton = {
        "hash": skeleton_hash or "",
        "fps": _num(fps, 30.0),
        "x": _num(x),
        "y": _num(y),
        "width": _num(width),
        "height": _num(height),
        "images": images,
        "audio": audio,
        "bones": bones,
        "skins": skins,
        "default_skin": "default" if any(skin["name"] == "default" for skin in skins) else (skins[0]["name"] if skins else None),
    }
    for bone in bones:
        bone.setdefault("children", [])
        bone.setdefault("depth", 0)
    for bone in bones:
        parent = next((item for item in bones if item["name"] == bone["parent"]), None) if bone["parent"] else None
        bone["depth"] = 0 if parent is None else parent.get("depth", 0) + 1
    children = {bone["name"]: [] for bone in bones}
    for bone in bones:
        if bone["parent"]:
            children[bone["parent"]].append(bone["name"])
    for bone in bones:
        bone["children"] = children[bone["name"]]
    result = {
        "format": "skel",
        "version": version,
        "version_major": major,
        "skeleton": skeleton,
        "slots": slots,
        "animations": animations,
        "mesh_count": _count_meshes(skins),
        "atlas": parse_atlas(atlas) if atlas is not None else {"pages": [], "regions": {}, "page_count": 0, "region_count": 0},
        "source_size": len(payload),
    }
    if trailing:
        result["trailing_bytes"] = trailing
    return result


def parse(payload, atlas=None):
    try:
        atlas_data = parse_atlas(atlas) if atlas is not None else {"pages": [], "regions": {}, "page_count": 0, "region_count": 0}
    except SpineParseError as exc:
        atlas_data = {"pages": [], "regions": {}, "page_count": 0, "region_count": 0, "error": str(exc)}
    empty = {
        "format": "", "version": "", "version_major": 0,
        "skeleton": {"bones": [], "skins": [], "default_skin": None},
        "slots": [], "animations": [], "mesh_count": 0, "atlas": atlas_data,
    }
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        empty["error"] = "empty payload"
        return empty
    payload = bytes(payload)
    prefix = payload[:64]
    if prefix.startswith(b"\xef\xbb\xbf"):
        prefix = prefix[3:]
    prefix = prefix.lstrip()
    try:
        if prefix.startswith(b"{"):
            return _parse_json(payload, atlas)
        if prefix.startswith(b"["):
            empty["error"] = "json root is not an object"
            return empty
        return _parse_skel(payload, atlas)
    except SpineParseError as exc:
        empty["format"] = "skel" if not prefix.startswith(b"{") else "json"
        empty["error"] = str(exc)
        return empty
    except (struct.error, IndexError, ValueError, RecursionError, MemoryError) as exc:
        empty["format"] = "skel" if not prefix.startswith(b"{") else "json"
        empty["error"] = "%s: %s" % (type(exc).__name__, exc)
        return empty
