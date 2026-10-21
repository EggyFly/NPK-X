#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Readable inspection for NeoX resource formats that are not media files."""

from __future__ import annotations

import re
import struct
from typing import Any

NEOX_MAGIC = bytes.fromhex("c159410d")
MAX_TEXT_BYTES = 512 * 1024
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_STRINGS = 240
MAX_REFERENCES = 80

TEXT_EXTENSIONS = {
    ".animgraph", ".animation", ".animconfig", ".trackgroup", ".lod", ".spr",
    ".json", ".xml", ".plist", ".txt", ".ini", ".yaml", ".yml", ".csv", ".atlas",
    ".conf", ".lua", ".md", ".html", ".css", ".js",
}

FORMATS = {
    ".animgraph": (
        "NeoX 动画状态图",
        "XML 动画图/混合树，描述骨骼、变量、状态与过渡；本身不包含网格，需要配合模型和动画播放器才能显示动作。",
        "text",
    ),
    ".animation": (
        "NeoX 动画包装",
        "XML 动画条目，引用同目录的 .rawanimation，并附加属性/事件；动态播放需要骨骼和模型渲染器。",
        "text",
    ),
    ".animconfig": (
        "NeoX 动画配置",
        "XML 动画清单，列出角色可用的动画名称和文件名映射。",
        "text",
    ),
    ".trackgroup": (
        "NeoX 动作/特效轨道",
        "XML 时间轴，按时间触发粒子、音效或动作事件，常与角色的 .animation 配套。",
        "text",
    ),
    ".lod": (
        "LOD/渲染配置",
        "文本或 XML 的 LOD/材质/渲染参数，不是独立几何模型。",
        "text",
    ),
    ".spr": (
        "Sprite 图集描述",
        "文本图集清单，记录纹理文件名、区域和帧信息，可据此裁切对应 PNG/KTA。",
        "text",
    ),
    ".gim": (
        "NeoX 对象/预制体",
        "二进制对象配置，通常引用骨骼、子网格、材质和挂点/特效；它不是图片，也不能单独绘制。",
        "none",
    ),
    ".mtg": (
        "NeoX 材质配置",
        "二进制材质参数，包含材质槽、贴图引用和着色器技术名；需要材质渲染器才能显示。",
        "none",
    ),
    ".sfx": (
        "NeoX 特效参数",
        "二进制特效/关键帧参数，常引用粒子材质和贴图；需要特效播放器才能显示。",
        "none",
    ),
    ".efx": (
        "NeoX 粒子特效",
        "二进制粒子系统，包含粒子资源、着色器和贴图引用；需要粒子渲染器才能显示。",
        "none",
    ),
    ".gmp": (
        "NeoX 地图数据",
        "地图/场景二进制数据，不是独立模型文件；可视化需要地图地形、对象和材质格式。",
        "none",
    ),
    ".xn": (
        "NeoX 脚本容器",
        "受保护的脚本/字节码容器，不是可视化资源；只能查看字符串、十六进制或下载。",
        "none",
    ),
    ".qm": (
        "Qt 翻译表",
        "Qt Linguist 编译后的翻译文件，包含界面文案；不是游戏画面资源。",
        "none",
    ),
    ".csb": (
        "Cocos Studio UI",
        "Cocos Studio 二进制 UI 布局，包含节点树、图片和动画引用；渲染需要 Cocos UI 运行时。",
        "none",
    ),
    ".rawanimation": (
        "NeoX 原始动画",
        "二进制骨骼轨道关键帧，引用 .skeleton；动态显示需要骨骼蒙皮和动画播放器。",
        "none",
    ),
    ".skeleton": (
        "NeoX 骨骼",
        "二进制关节层级与绑定矩阵；需要网格蒙皮渲染器才能显示。",
        "none",
    ),
    ".mesh": (
        "NeoX 网格模型",
        "二进制网格，包含顶点、面、骨骼权重等；当前客户端的顶点数据有打包/量化变体，尚未接入通用 3D 预览。",
        "none",
    ),
}

REFERENCE_RE = re.compile(
    r"[A-Za-z0-9_./\\-]{2,240}\.(?:skeleton|mesh|mtg|kta|dds|ktx|png|jpe?g|tga|bmp|"
    r"sfx|efx|gim|animation|rawanimation|animconfig|trackgroup|spr|csb|surf|shader|"
    r"json|xml|plist|lua|mp4|webm)\b",
    re.IGNORECASE,
)


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _compact_count(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("count outside payload")
    value = data[offset]
    if value < 128:
        return value, offset + 1
    if offset + 1 >= len(data):
        raise ValueError("truncated compact count")
    return value | (data[offset + 1] << 8), offset + 2


def _header(payload: bytes, kind: str) -> dict[str, Any]:
    if payload.startswith(NEOX_MAGIC) and len(payload) >= 14:
        try:
            count, width = _compact_count(payload, 12)
            return {
                "magic": NEOX_MAGIC.hex(),
                "declared_size": _u32(payload, 4),
                "reserved": _u32(payload, 8),
                "name_count": count,
                "count_bytes": width,
            }
        except (struct.error, ValueError):
            pass
    if kind == ".mesh" and len(payload) >= 14:
        return {
            "magic": payload[:4].hex(),
            "type": struct.unpack_from("<H", payload, 4)[0],
            "subtype": struct.unpack_from("<H", payload, 6)[0],
            "bone_mode": _u32(payload, 8),
            "bone_count": struct.unpack_from("<H", payload, 12)[0],
        }
    if payload.startswith(b"RAWANIMA"):
        return {"magic": "RAWANIMA", "declared_size": _u32(payload, 8) if len(payload) >= 12 else None}
    if payload.startswith(b"SKELETON"):
        return {"magic": "SKELETON", "declared_size": _u32(payload, 8) if len(payload) >= 12 else None}
    if payload.startswith(b"@BIN"):
        return {"magic": "@BIN", "declared_size": _u32(payload, 4) if len(payload) >= 8 else None}
    if payload.startswith(b"DZ"):
        return {"magic": payload[:8].hex()}
    if payload.startswith(b"XN"):
        return {"magic": "XN", "header": payload[:16].hex()}
    return {"magic": payload[:16].hex()}


def _decode_text(payload: bytes) -> tuple[str, str, bool]:
    raw = payload[:MAX_TEXT_BYTES]
    truncated = len(payload) > len(raw)
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding), encoding, truncated
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1", truncated


def _plausible_string(text: str) -> bool:
    if len(text) < 4 or len(text) > 240:
        return False
    if any(char in "?]{}|^~`" for char in text):
        return False
    letters = sum(1 for char in text if char.isalpha())
    if letters < 4 or letters / len(text) < 0.55:
        return False
    symbols = sum(
        1 for char in text
        if not char.isalnum() and char not in " _./\\-()@:"
    )
    if symbols / len(text) > 0.18:
        return False
    return bool(re.search(r"[A-Za-z]{2,}", text))


def _neox_names(payload: bytes) -> list[str]:
    if not payload.startswith(NEOX_MAGIC) or len(payload) < 14:
        return []
    try:
        count, position = _compact_count(payload, 12)
    except ValueError:
        return []
    names = []
    for _ in range(min(count, 2000)):
        end = payload.find(b"\0", position)
        if end < 0:
            break
        text = payload[position:end].decode("utf-8", "ignore").strip()
        position = end + 1
        if _plausible_string(text) and text not in names:
            names.append(text)
            if len(names) >= MAX_STRINGS:
                break
    return names


def _extract_strings(payload: bytes) -> tuple[list[str], bool]:
    data = payload[:MAX_SCAN_BYTES]
    result: list[str] = []
    seen: set[str] = set()
    for text in _neox_names(payload):
        seen.add(text)
        result.append(text)
        if len(result) >= MAX_STRINGS:
            break
    for match in re.finditer(rb"[\x20-\x7e]{4,240}", data):
        text = match.group().decode("ascii")
        if text in seen or not _plausible_string(text):
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= MAX_STRINGS:
            break
    return result, len(payload) > len(data)


def _extract_references(strings: list[str], text: str | None) -> list[str]:
    source = "\n".join(strings)
    if text:
        source = text + "\n" + source
    result = []
    seen = set()
    for match in REFERENCE_RE.finditer(source):
        value = match.group().replace("\\", "/")
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= MAX_REFERENCES:
            break
    return result


def hex_lines(payload: bytes, limit: int = 4096) -> list[str]:
    lines = []
    head = payload[:limit]
    for offset in range(0, len(head), 16):
        chunk = head[offset:offset + 16]
        hx = " ".join(f"{byte:02x}" for byte in chunk)
        printable = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hx:<47}  {printable}")
    return lines


def inspect(payload: bytes, ext: str, name: str = "") -> dict[str, Any]:
    ext = (ext or "").lower()
    label, summary, renderable = FORMATS.get(
        ext,
        ("未知二进制资源", "暂未识别的资源格式；可以查看可读字符串和十六进制。", "none"),
    )
    looks_text = ext in TEXT_EXTENSIONS or (
        payload[:4096].lstrip().startswith((b"<", b"{", b"["))
        and b"\x00" not in payload[:4096]
    )
    result: dict[str, Any] = {
        "name": name,
        "ext": ext,
        "format": label,
        "summary": summary,
        "renderable": renderable,
        "size": len(payload),
        "header": {},
        "strings": [],
        "references": [],
        "text": None,
        "text_encoding": None,
        "text_truncated": False,
        "strings_truncated": False,
        "hex_lines": [],
        "hex_truncated": len(payload) > 4096,
    }
    if looks_text:
        text, encoding, truncated = _decode_text(payload)
        result.update(
            {
                "renderable": "text",
                "text": text,
                "text_encoding": encoding,
                "text_truncated": truncated,
                "references": _extract_references([], text),
            }
        )
        return result
    result["header"] = _header(payload, ext)
    if ext in {".gmp", ".xn"}:
        strings, strings_truncated = [], False
    else:
        strings, strings_truncated = _extract_strings(payload)
    result["strings"] = strings
    result["strings_truncated"] = strings_truncated
    result["references"] = _extract_references(strings, None)
    result["hex_lines"] = hex_lines(payload)
    return result
