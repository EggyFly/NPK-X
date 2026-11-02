#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cocos Studio 2 .csb layout reader (flatbuffers)."""

from __future__ import annotations

import math
import struct

MAX_NODES = 12000
MAX_DEPTH = 64
MAX_VECTOR = 50000
MAX_CHANNELS = 4096
MAX_FRAMES = 4096
MAX_CHANNEL_FRAMES = 65536
IMAGE_EXTS = (
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".gif",
    ".ktx", ".dds", ".pvr", ".ccz",
)
SCALE9_SLOTS = {
    "Button": (9, 10, 11),
    "ImageView": (2, 3, 4),
    "Panel": (9, 10, 11),
    "ScrollView": (9, 10, 11),
    "PageView": (9, 10, 11),
    "ListView": (9, 10, 11),
}


class CsbError(ValueError):
    pass


def _number(data, pos, fmt):
    if pos is None or pos < 0 or pos + struct.calcsize(fmt) > len(data):
        return None
    return struct.unpack_from(fmt, data, pos)[0]


def _u32(data, pos):
    return _number(data, pos, "<I")


def _i32(data, pos):
    return _number(data, pos, "<i")


def _f32(data, pos):
    value = _number(data, pos, "<f")
    if value is None or not math.isfinite(value):
        return None
    return value


def _table(data, pos):
    if pos is None or pos < 0 or pos + 4 > len(data):
        return None
    back = _i32(data, pos)
    if back is None:
        return None
    vtable = pos - back
    if vtable < 0 or vtable + 4 > len(data):
        return None
    vtable_size, object_size = struct.unpack_from("<HH", data, vtable)
    if vtable_size < 4 or vtable_size > 512 or vtable_size % 2:
        return None
    if object_size < 4 or object_size > 4096 or vtable + vtable_size > len(data):
        return None
    if pos + object_size > len(data):
        return None
    count = (vtable_size - 4) // 2
    offsets = []
    for index in range(count):
        offset = struct.unpack_from("<H", data, vtable + 4 + index * 2)[0]
        if offset and offset >= object_size:
            return None
        offsets.append(offset)
    return {"pos": pos, "vtable": vtable, "size": object_size, "count": count, "offsets": offsets}


def _slot(data, table, index):
    if not table or index >= table["count"]:
        return None
    offset = table["offsets"][index]
    if not offset:
        return None
    return table["pos"] + offset


def _pointer(data, table, index):
    slot = _slot(data, table, index)
    if slot is None or slot % 4:
        return None
    value = _u32(data, slot)
    if value is None or not value:
        return None
    target = slot + value
    if target < 0 or target >= len(data):
        return None
    return target


def _string(data, pos):
    if pos is None or pos < 0 or pos + 4 > len(data):
        return None
    size = _u32(data, pos)
    if size is None or size > 1024 or pos + 4 + size + 1 > len(data):
        return None
    if data[pos + 4 + size] != 0:
        return None
    text = data[pos + 4:pos + 4 + size].decode("utf-8", "replace")
    for char in text:
        code = ord(char)
        if code == 127 or (code < 32 and char not in "\t\r\n"):
            return None
    return text


def _vector(data, pos, limit=MAX_VECTOR):
    if pos is None or pos < 0 or pos + 4 > len(data):
        return None
    count = _u32(data, pos)
    if count is None or count > limit or pos + 4 + count * 4 > len(data):
        return None
    items = []
    for index in range(count):
        slot = pos + 4 + index * 4
        value = _u32(data, slot)
        if value is None:
            return None
        target = slot + value
        if target < 0 or target >= len(data):
            return None
        items.append(target)
    return items


def _text(data, table, index):
    return _string(data, _pointer(data, table, index))


def _pair(data, table, index, default):
    pos = _slot(data, table, index)
    if pos is None:
        return list(default)
    first = _f32(data, pos)
    second = _f32(data, pos + 4)
    if first is None or second is None:
        return list(default)
    return [round(first, 3), round(second, 3)]


def _byte(data, table, index):
    pos = _slot(data, table, index)
    if pos is None or pos >= len(data):
        return None
    return data[pos]


def _cap_insets(data, table, index):
    pos = _slot(data, table, index)
    if pos is None:
        return None
    values = []
    for offset in (0, 4, 8, 12):
        value = _f32(data, pos + offset)
        if value is None:
            return None
        values.append(round(value, 3))
    if values[2] < 0 or values[3] < 0:
        return None
    return values


def _scale9(data, node, table, node_type):
    slots = SCALE9_SLOTS.get(node_type)
    if not table or not slots:
        return
    cap = _cap_insets(data, table, slots[0])
    if cap is not None:
        node["capInsets"] = cap
    size_pos = _slot(data, table, slots[1])
    if size_pos is not None:
        width = _f32(data, size_pos)
        height = _f32(data, size_pos + 4)
        if width is not None and height is not None and (width or height):
            node["scale9Size"] = [round(width, 3), round(height, 3)]
    enabled = _byte(data, table, slots[2])
    if enabled is not None:
        node["scale9"] = enabled != 0


UNION_SLOTS = {
    "Position": 0,
    "Scale": 1,
    "RotationSkew": 1,
    "AnchorPoint": 1,
    "FileData": 3,
    "Alpha": 5,
    "VisibleForFrame": 6,
}


def _frame_value(data, frame_table, prop):
    slot = UNION_SLOTS.get(prop)
    if slot is None:
        return None
    target = _pointer(data, frame_table, slot)
    sub = _table(data, target)
    if not sub:
        return None
    index = _i32(data, _slot(data, sub, 0))
    if index is None:
        return None
    frame = {"frame": index}
    tween = _byte(data, sub, 1)
    if tween is not None and not tween:
        frame["tween"] = False
    if prop == "Position":
        frame["pos"] = _pair(data, sub, 2, (0.0, 0.0))
    elif prop == "Scale":
        frame["scale"] = _pair(data, sub, 2, (1.0, 1.0))
    elif prop == "RotationSkew":
        frame["rot"] = _pair(data, sub, 2, (0.0, 0.0))
    elif prop == "AnchorPoint":
        frame["anchor"] = _pair(data, sub, 2, (0.0, 0.0))
    elif prop == "VisibleForFrame":
        pos = _slot(data, sub, 2)
        frame["visible"] = bool(data[pos]) if pos is not None and pos < len(data) else True
    elif prop == "Alpha":
        value = _i32(data, _slot(data, sub, 2))
        frame["alpha"] = max(0, min(255, value if value is not None else 0))
    elif prop == "FileData":
        resource = _table(data, _pointer(data, sub, 2))
        path = _text(data, resource, 0) if resource else None
        if not path:
            return None
        frame["texture"] = path
    return frame


def _read_action(data, root_table):
    action = _table(data, _pointer(data, root_table, 4))
    if not action:
        return None
    duration = _i32(data, _slot(data, action, 0))
    if not duration or duration <= 0:
        return None
    speed = _f32(data, _slot(data, action, 1))
    state = {"channels": [], "budget": MAX_CHANNEL_FRAMES, "truncated": False}
    for timeline in _vector(data, _pointer(data, action, 2), MAX_CHANNELS) or []:
        if len(state["channels"]) >= MAX_CHANNELS or state["budget"] <= 0:
            state["truncated"] = True
            break
        table = _table(data, timeline)
        if not table:
            continue
        prop = _text(data, table, 0) or ""
        tag = _i32(data, _slot(data, table, 1)) or 0
        frames = []
        for pos in _vector(data, _pointer(data, table, 2), MAX_FRAMES) or []:
            if len(frames) >= MAX_FRAMES or state["budget"] <= 0:
                state["truncated"] = True
                break
            frame_table = _table(data, pos)
            if not frame_table:
                continue
            frame = _frame_value(data, frame_table, prop)
            if frame is not None:
                frames.append(frame)
                state["budget"] -= 1
        if frames:
            state["channels"].append({"property": prop, "tag": tag, "frames": frames})
    if not state["channels"]:
        return None
    return {
        "duration": duration,
        "speed": speed if speed and speed > 0 else 1.0,
        "current": _text(data, action, 3) or "",
        "channels": state["channels"],
        "truncated": state["truncated"],
    }


def _common(data, spec):
    table = _table(data, spec)
    if not table:
        return None
    child = _pointer(data, table, 0)
    child_table = _table(data, child)
    if child_table and _text(data, child_table, 0) is not None:
        return child
    if _text(data, table, 0) is not None:
        return spec
    if table["count"] >= 12 and _slot(data, table, 7) and _slot(data, table, 11):
        return spec
    return None


def _image_name(text):
    if not text or len(text) > 300:
        return None
    low = text.lower()
    for ext in IMAGE_EXTS:
        if low.endswith(ext):
            return text.replace("\\", "/")
    return None


def _collect_images(data, table, skip, depth=0, out=None):
    if out is None:
        out = []
    if not table or depth > 2 or len(out) >= 12:
        return out
    for index in range(table["count"]):
        if index == 0 and skip is not None:
            continue
        target = _pointer(data, table, index)
        if target is None:
            continue
        text = _string(data, target)
        if text is not None:
            name = _image_name(text)
            if name and name not in out:
                out.append(name)
            continue
        items = _vector(data, target, 256)
        if items is not None:
            for item in items:
                text = _string(data, item)
                if text is not None:
                    name = _image_name(text)
                    if name and name not in out:
                        out.append(name)
                elif depth < 2:
                    _collect_images(data, _table(data, item), None, depth + 1, out)
            continue
        if depth < 2:
            _collect_images(data, _table(data, target), None, depth + 1, out)
    return out


def _spec(data, node):
    options = _pointer(data, node, 2)
    table = _table(data, options)
    if not table:
        return None
    if table["count"] <= 1:
        return _pointer(data, table, 0)
    return options


def _walk(data, pos, depth, seen, state):
    if pos is None or depth > MAX_DEPTH or len(state["nodes"]) >= MAX_NODES:
        state["truncated"] = True
        return None
    if pos in seen:
        return None
    table = _table(data, pos)
    if not table:
        return None
    seen.add(pos)
    node_type = _text(data, table, 0) or "Node"
    spec = _spec(data, table)
    common = _common(data, spec)
    name = ""
    pos_pair = [0.0, 0.0]
    size_pair = [0.0, 0.0]
    scale_pair = [1.0, 1.0]
    anchor_pair = [0.0, 0.0]
    rotate = 0.0
    visible = True
    alpha = 255
    tag = 0
    action_tag = 0
    if common:
        common_table = _table(data, common)
        name = _text(data, common_table, 0) or ""
        pos_pair = _pair(data, common_table, 7, (0.0, 0.0))
        size_pair = _pair(data, common_table, 11, (0.0, 0.0))
        scale_pair = _pair(data, common_table, 8, (1.0, 1.0))
        anchor_pair = _pair(data, common_table, 9, (0.0, 0.0))
        rotate_pos = _slot(data, common_table, 2)
        if rotate_pos is not None:
            value = _f32(data, rotate_pos)
            if value is not None:
                rotate = round(value, 3)
        visible_byte = _byte(data, common_table, 4)
        if visible_byte is not None:
            visible = visible_byte != 0
        alpha_byte = _byte(data, common_table, 5)
        if alpha_byte is not None:
            alpha = alpha_byte
        tag_value = _i32(data, _slot(data, common_table, 6))
        if tag_value is not None:
            tag = tag_value
        action_value = _i32(data, _slot(data, common_table, 1))
        if action_value is not None:
            action_tag = action_value
    images = _collect_images(data, _table(data, spec), common)
    for image in images:
        if image not in state["textures"]:
            state["textures"].append(image)
    node = {
        "name": name or node_type,
        "type": node_type,
        "pos": pos_pair,
        "size": size_pair,
        "scale": scale_pair,
        "rotate": rotate,
        "visible": visible,
        "anchor": anchor_pair,
        "alpha": alpha,
        "tag": tag,
        "actionTag": action_tag,
        "texture": images[0] if images else "",
        "children": [],
    }
    _scale9(data, node, _table(data, spec), node_type)
    state["nodes"].append(node)
    kids = _vector(data, _pointer(data, table, 1), MAX_NODES)
    for child in kids or []:
        item = _walk(data, child, depth + 1, seen, state)
        if item is not None:
            node["children"].append(item)
    return node


def _root_textures(data, root):
    table = _table(data, root)
    result = []
    if not table:
        return result
    for index in range(1, table["count"]):
        items = _vector(data, _pointer(data, table, index), 4096)
        if items is None:
            continue
        for item in items:
            name = _image_name(_string(data, item))
            if name and name not in result:
                result.append(name)
    return result


def parse(payload: bytes) -> dict:
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 16:
        raise CsbError("payload too small")
    root = _u32(payload, 0)
    table = _table(payload, root)
    if not table:
        raise CsbError("not a cocos studio flatbuffer")
    version = _text(payload, table, 0)
    state = {"nodes": [], "textures": [], "truncated": False}
    node = _walk(payload, _pointer(payload, table, 3), 0, set(), state)
    if node is None:
        raise CsbError("node tree missing")
    root_textures = _root_textures(payload, root)
    for name in root_textures:
        if name not in state["textures"]:
            state["textures"].append(name)
    state["textures"] = root_textures + [name for name in state["textures"] if name not in root_textures]
    scene = node["name"]
    width, height = node["size"]
    result = {
        "header": {
            "format": "cocos-studio-2-flatbuffer",
            "version": version or "",
            "size": len(payload),
            "node_count": len(state["nodes"]),
            "texture_count": len(state["textures"]),
            "scene": scene,
            "width": width,
            "height": height,
            "truncated": state["truncated"],
        },
        "nodes": [node],
        "textures": state["textures"],
    }
    animation = _read_action(payload, table)
    if animation is not None:
        result["animation"] = animation
    return result
