#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local, read-only web browser for Eggy Party NPK packages.

Reads NPK indexes through the recovered mode-3 decoder, builds a searchable
virtual filesystem, and serves images/audio/text on demand.  No bulk export is
required.  Images are converted to PNG on first view (small disk cache); WEM/BNK
audio is decoded on demand and streamed as FLAC without creating a permanent
FLAC library.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import ipaddress
import json
import math
import glob
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
import zstandard
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from PIL import Image, ImageDraw

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import bnk_parse
import rebuild_data
import resource_inspect
import neox_object
import mesh_parse
import neox_pose
import neox_anim
import image_convert
import gmp_parse
import xn_crypto
import xn_parse
import xn_decompile
import xn_bootstrap
import cache_clean
import update_check
import startup_progress
import nxpk_mode3 as N
import extract_mode3 as E

try:
    import soundfile as _soundfile
except Exception:  # optional
    _soundfile = None

try:
    import numpy as _np
except Exception:  # optional
    _np = None

SCHEMA_VERSION = "1"
TOOLKIT_VERSION = "1.5-browser"
SORT_EXPRESSIONS = {
    "name_asc": "name COLLATE NOCASE ASC, id ASC",
    "name_desc": "name COLLATE NOCASE DESC, id ASC",
    "size_asc": "unpacked ASC, id ASC",
    "size_desc": "unpacked DESC, id ASC",
    "type": "kind ASC, name COLLATE NOCASE ASC, id ASC",
    "package": "package COLLATE NOCASE ASC, name COLLATE NOCASE ASC, id ASC",
}
KINDS = {"image", "audio", "bank", "video", "text", "script", "model", "binary"}
TOOLKIT_ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = E.PACKAGE_ROOT
WEB_DIR = PACKAGE_ROOT / "web"

IMAGE_EXT = {".kta", ".dds", ".ktx", ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp", ".gif"}
MTG_RESOURCE_EXT = IMAGE_EXT | {".mesh", ".gim", ".mtg", ".sfx", ".efx", ".skeleton", ".animation", ".rawanimation", ".animgraph", ".animconfig", ".trackgroup", ".spr", ".csb", ".bin", ".lod"}
AUDIO_EXT = {".wem", ".bnk", ".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
TEXT_EXT = {
    ".json", ".xml", ".txt", ".conf", ".plist", ".atlas", ".mtg", ".gim", ".lod",
    ".ini", ".csv", ".yaml", ".yml", ".html", ".css", ".js", ".lua", ".py", ".md",
}
MODEL_EXT = {".mesh", ".skel", ".animgraph", ".animation", ".rawanimation", ".blendspace1d", ".trackgroup"}
MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".flac": "audio/flac", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".opus": "audio/ogg",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska",
    ".txt": "text/plain; charset=utf-8", ".json": "application/json",
}
IMAGE_FORMATS = {"png", "jpg", "ico"}
AUDIO_FORMATS = {"wav", "flac", "mp3"}
BATCH_RAW_MAX = 2000
BATCH_CONVERT_MAX = 200
BATCH_TIME_BUDGET = 600
BATCH_BODY_LIMIT = 2 * 1024 * 1024


def zip_arc_name(path: str) -> str:
    parts = []
    for part in str(path).replace("\\", "/").split("/"):
        clean = re.sub(r'[<>:"|?*\x00-\x1f]', "_", part.strip(" ."))
        if clean and clean not in (".", ".."):
            parts.append(clean)
    return "/".join(parts) or "entry.bin"


def unique_arc_name(name: str, used: set) -> str:
    if name not in used:
        used.add(name)
        return name
    root, ext = os.path.splitext(name)
    index = 2
    while f"{root}_{index}{ext}" in used:
        index += 1
    final = f"{root}_{index}{ext}"
    used.add(final)
    return final


def material_flags(objects) -> dict:
    lighting = True
    transparent = False
    light_found = False
    trans_found = False
    tint = [1.0, 1.0, 1.0, 1.0]
    tech = ""
    tint_found = False
    cull_back = False
    cull_found = False
    for obj in objects or []:
        if not tech:
            value = obj.get("TechName")
            if isinstance(value, str):
                tech = value
        if not tint_found:
            value = obj.get("Value")
            if (
                isinstance(value, list)
                and len(value) == 4
                and all(isinstance(item, (int, float)) for item in value)
            ):
                tint = [float(item) for item in value]
                tint_found = True
        if not light_found and "LightingEnable" in obj:
            value = obj.get("LightingEnable")
            if value is False or (isinstance(value, str) and value.strip().lower() == "false"):
                lighting = False
            light_found = True
        if not trans_found and "TransparentMode" in obj:
            value = obj.get("TransparentMode")
            try:
                if int(value) >= 2:
                    transparent = True
            except (TypeError, ValueError):
                pass
            trans_found = True
        if not cull_found:
            if "CullBack" in obj:
                value = obj.get("CullBack")
                cull_back = not (value is False or (isinstance(value, str) and value.strip().lower() == "false"))
                cull_found = True
            elif "CullMode" in obj:
                try:
                    cull_back = int(obj.get("CullMode")) != 0
                except (TypeError, ValueError):
                    pass
                cull_found = True
    return {
        "lighting": lighting,
        "transparent": transparent,
        "tint": tint,
        "tech": tech,
        "cull_back": cull_back,
        "textures": [],
    }


def submesh_materials(data: dict) -> dict:
    table = data.get("table") or []
    meta = data.get("meta") or []
    objects = data.get("objects") or []
    result = {}
    for index, obj in enumerate(objects):
        if not isinstance(obj, dict) or "MtlIdx" not in obj:
            continue
        table_index = None
        if index < len(meta) and isinstance(meta[index], dict):
            table_index = meta[index].get("x")
        if isinstance(table_index, int) and 0 <= table_index < len(table):
            name = table[table_index]
            if isinstance(name, str) and name.startswith("Sub") and name[3:].isdigit():
                try:
                    result[int(name[3:])] = int(obj["MtlIdx"])
                except (TypeError, ValueError):
                    pass
    return result


def material_groups(objects: list, textures: list) -> list:
    count = 0
    for obj in objects or []:
        if not isinstance(obj, dict):
            continue
        value = obj.get("MaterialCount")
        if isinstance(value, int) and value > 0:
            count = value
            break
    resolved = [item.get("resolved") for item in textures if isinstance(item, dict) and item.get("resolved")]
    if count <= 0 or len(resolved) % count:
        return []
    size = len(resolved) // count
    return [
        {"index": index, "textures": resolved[index * size:(index + 1) * size]}
        for index in range(count)
    ]


def material_tree(data: dict, resolve=None) -> list:
    children = neox_object.build_tree(data)
    table = data.get("table") or []
    meta = data.get("meta") or []
    objects = data.get("objects") or []

    def class_name(index):
        if index < 0 or index >= len(meta) or not isinstance(meta[index], dict):
            return ""
        table_index = meta[index].get("x")
        if isinstance(table_index, int) and 0 <= table_index < len(table):
            return table[table_index]
        return ""

    group = None
    for index in range(len(objects)):
        if class_name(index) == "MaterialGroup":
            group = index
            break
    if group is None:
        return []
    materials = []
    for node in children[group]:
        node_name = class_name(node)
        if not node_name.startswith("Material_"):
            continue
        try:
            material_index = int(node_name.rsplit("_", 1)[-1])
        except ValueError:
            continue
        material_node = None
        for child in children[node]:
            if class_name(child) == "Material":
                material_node = child
                break
        if material_node is None:
            continue
        nodes = []

        def walk(current):
            nodes.append(current)
            for child in children[current]:
                walk(child)

        walk(material_node)
        name = ""
        surface = ""
        transparent = False
        lighting = True
        cull_back = True
        textures = []
        tex0 = tex1 = tex2 = tex3 = tex4 = ""
        colors = [[1.0, 1.0, 1.0, 1.0] for _ in range(5)]
        design_color = [1.0, 1.0, 1.0, 1.0]
        emissive_color = [1.0, 1.0, 1.0, 1.0]
        emissive_intensity = [1.0, 1.0, 1.0, 1.0]
        intensity = [1.0, 1.0, 1.0, 1.0]
        base_color = [1.0, 1.0, 1.0, 1.0]
        tint_value = 1.0
        alpha_ref = 0.0
        axis = 0.0
        lerp_center = 0.0
        lerp_contrast = 0.5
        pattern_color_key = 0.0
        pattern_para = [1.0, 1.0, 0.0, 0.0]
        pattern_intensity = [0.0, 0.0, 0.0, 0.0]
        pattern_uv_key = 0.0
        pattern_depth = 0.0
        pattern_rotation = 0.0
        repeat_scale = 1.0
        texture_para = [1.0, 1.0, 0.0, 0.0]
        rotation_angle = 0.0
        tiling = False
        base_normal = ""
        pattern_tex = ""
        for item in nodes:
            obj = objects[item] if item < len(objects) else None
            if not isinstance(obj, dict):
                continue
            node_class = class_name(item)
            value = obj.get("Value")
            if not name and isinstance(obj.get("Name"), str):
                name = obj["Name"]
            if not surface and isinstance(obj.get("Surface"), str):
                surface = obj["Surface"]
            if "TransparentMode" in obj:
                try:
                    transparent = int(obj["TransparentMode"]) >= 2
                except (TypeError, ValueError):
                    pass
            if "LightingEnable" in obj:
                value2 = obj["LightingEnable"]
                lighting = not (value2 is False or (isinstance(value2, str) and value2.strip().lower() == "false"))
            if "CullBack" in obj:
                value2 = obj["CullBack"]
                cull_back = not (value2 is False or (isinstance(value2, str) and value2.strip().lower() == "false"))
            elif "CullMode" in obj:
                try:
                    cull_back = int(obj["CullMode"]) != 0
                except (TypeError, ValueError):
                    pass
            if node_class.startswith("MaskColor") and isinstance(value, list) and len(value) >= 4:
                suffix = node_class[len("MaskColor"):]
                if suffix.isdigit() and int(suffix) < len(colors):
                    colors[int(suffix)] = [float(x) for x in value[:4]]
            elif node_class == "DesignColor" and isinstance(value, list) and len(value) >= 4:
                design_color = [float(x) for x in value[:4]]
            elif node_class == "EmissiveColor" and isinstance(value, list) and len(value) >= 4:
                emissive_color = [float(x) for x in value[:4]]
            elif node_class == "EmissiveIntensity" and isinstance(value, list) and len(value) >= 4:
                emissive_intensity = [float(x) for x in value[:4]]
            elif node_class == "Intensity" and isinstance(value, list) and len(value) >= 4:
                intensity = [float(x) for x in value[:4]]
            elif node_class == "BaseColor" and isinstance(value, list) and len(value) >= 4:
                base_color = [float(x) for x in value[:4]]
            elif node_class == "TintValue" and isinstance(value, (int, float)):
                tint_value = float(value)
            elif "AlphaRef" in obj:
                try:
                    alpha_ref = float(obj["AlphaRef"])
                except (TypeError, ValueError):
                    pass
            elif node_class == "Axis" and isinstance(value, (int, float)):
                axis = float(value)
            elif node_class == "LerpCenter" and isinstance(value, (int, float)):
                lerp_center = float(value)
            elif node_class == "LerpContrast" and isinstance(value, (int, float)):
                lerp_contrast = float(value)
            elif node_class == "PatternColorKey" and isinstance(value, (int, float)):
                pattern_color_key = float(value)
            elif node_class == "PatternPara" and isinstance(value, list):
                pattern_para = [float(x) for x in value[:4]]
            elif node_class == "PatternIntensity" and isinstance(value, list):
                pattern_intensity = [float(x) for x in value[:4]]
            elif node_class == "PatternUVKey" and isinstance(value, (int, float)):
                pattern_uv_key = float(value)
            elif node_class == "PatternDeapthOffset" and isinstance(value, (int, float)):
                pattern_depth = float(value)
            elif node_class == "PatternRotationAngle" and isinstance(value, (int, float)):
                pattern_rotation = float(value)
            elif node_class == "RepeatScale" and isinstance(value, (int, float)):
                repeat_scale = float(value)
            elif node_class == "TexturePara" and isinstance(value, list):
                texture_para = [float(x) for x in value[:4]]
            elif node_class == "RotationAngle" and isinstance(value, (int, float)):
                rotation_angle = float(value)
            elif node_class in ("Tex0", "Tex1", "Tex2", "Tex3", "Tex4") and isinstance(value, str):
                slot = resolve(value) if resolve else value
                if node_class == "Tex0":
                    tex0 = slot
                elif node_class == "Tex1":
                    tex1 = slot
                elif node_class == "Tex2":
                    tex2 = slot
                elif node_class == "Tex3":
                    tex3 = slot
                else:
                    tex4 = slot
            elif node_class == "BaseNormal" and isinstance(value, str):
                base_normal = resolve(value) if resolve else value
            elif node_class == "PatternTex" and isinstance(value, str):
                pattern_tex = resolve(value) if resolve else value
            elif node_class == "Macro0" and obj.get("Key") == "ENABLE_TILING":
                try:
                    tiling = int(obj.get("Val") or 0) != 0
                except (TypeError, ValueError):
                    pass
            for value2 in obj.values():
                if isinstance(value2, str) and value2.lower().endswith((".tga", ".dds", ".kta", ".png")):
                    textures.append(resolve(value2) if resolve else value2)
        surface_lower = surface.lower()
        if "standard_change_color_lerp" in surface_lower:
            color_mode = 2
        elif any(token in surface_lower for token in (
            "standard_change_color_object", "standard_change_color_emissive",
            "standard_change_color_tiling_all", "standard_change_color_mix",
        )):
            color_mode = 3
        elif "change_color" in surface_lower:
            color_mode = 1
        else:
            color_mode = 0
        if color_mode == 2:
            opacity_tex = "tex0"
        elif color_mode == 3:
            opacity_tex = "tex1"
        elif color_mode == 1 and "change_color_pattern" not in surface_lower:
            opacity_tex = "tex1"
        else:
            opacity_tex = "tex0"
        if "standard_change_color_tiling_all" in surface_lower:
            opacity_tex = "none"
        normal_mode = 1 if color_mode == 2 else 0
        materials.append({
            "index": material_index,
            "name": name,
            "surface": surface,
            "transparent": transparent,
            "lighting": lighting,
            "cull_back": cull_back,
            "textures": textures,
            "tex0": tex0,
            "tex1": tex1,
            "tex2": tex2,
            "tex3": tex3,
            "tex4": tex4,
            "opacity_tex": opacity_tex,
            "normal_mode": normal_mode,
            "colors": colors,
            "design_color": design_color,
            "emissive_color": emissive_color,
            "emissive_intensity": emissive_intensity,
            "intensity": intensity,
            "base_color": base_color,
            "tint_value": tint_value,
            "alpha_ref": alpha_ref,
            "axis": axis,
            "lerp_center": lerp_center,
            "lerp_contrast": lerp_contrast,
            "pattern_color_key": pattern_color_key,
            "pattern_para": pattern_para,
            "pattern_intensity": pattern_intensity,
            "pattern_uv_key": pattern_uv_key,
            "pattern_depth": pattern_depth,
            "pattern_rotation": pattern_rotation,
            "repeat_scale": repeat_scale,
            "texture_para": texture_para,
            "rotation_angle": rotation_angle,
            "tiling": tiling,
            "base_normal": base_normal,
            "pattern_tex": pattern_tex,
            "color_mode": color_mode,
        })
    materials.sort(key=lambda item: item["index"])
    return materials


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "EggyNpkBrowser"
    return Path.home() / ".cache" / "eggy_npk_browser"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_entry_bytes(package_path: str, row) -> bytes:
    if hasattr(row, "packed_size"):
        offset, packed, unpacked, compression = row.offset, row.packed_size, row.unpacked_size, row.compression
    else:
        offset, packed, unpacked, compression = row["offset"], row["packed"], row["unpacked"], row["compression"]
    offset, packed, unpacked, compression = int(offset), int(packed), int(unpacked), int(compression)
    with open(package_path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(packed)
    if len(raw) != packed:
        raise ValueError("short read from package")
    if compression == 3:
        obj = zstandard.ZstdDecompressor().decompressobj()
        data = obj.decompress(raw)
        if not obj.eof:
            raise ValueError("truncated Zstd frame")
        if obj.unused_data and any(obj.unused_data):
            raise ValueError("unexpected trailing Zstd data")
    elif compression == 0:
        data = raw
    else:
        raise ValueError(f"unsupported compression {compression}")
    if len(data) != unpacked:
        raise ValueError("unpacked size mismatch")
    return data


def kind_for_ext(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".bnk":
        return "bank"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in TEXT_EXT:
        return "text"
    if ext == ".xn":
        return "script"
    if ext in MODEL_EXT:
        return "model"
    return "binary"


def detect_unresolved(package_path: str, entry) -> tuple[str, str]:
    """Return (extension, kind) for an entry without a logical path."""
    payload = read_entry_bytes(package_path, entry)
    kind = image_convert.classify(payload, "")
    ext = image_convert.image_extension(payload, "")
    if kind in ("ktx1", "ktx2", "dds", "png", "jpeg", "tga", "bmp", "webp", "gif"):
        return ext, "image"
    if ext == ".wem":
        return ".wem", "audio"
    if ext == ".bnk":
        return ".bnk", "bank"
    if ext == ".mesh":
        return ".mesh", "model"
    return ext or ".bin", "binary"


def fingerprint(game_root: Path, profile: dict, key: bytes, seed: int, path_dict: Path | None, packages: list[Path]) -> dict:
    path_dict_info = None
    if path_dict and path_dict.is_file():
        path_dict_info = {"name": path_dict.name, "size": path_dict.stat().st_size, "sha256": sha256_file(path_dict)}
    tables_hash = None
    try:
        tables_file = Path(N._t.__file__)  # type: ignore[attr-defined]
        tables_hash = sha256_file(tables_file)
    except Exception:
        pass
    return {
        "schema": SCHEMA_VERSION,
        "game_root": str(game_root.resolve()),
        "profile": profile.get("name"),
        "key": key.hex(),
        "path_hash_seed": f"0x{seed:08x}",
        "tables_sha256": tables_hash,
        "path_dict": path_dict_info,
        "packages": [
            {"name": p.name, "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
            for p in packages
        ],
    }


def build_catalog(
    db_path: Path,
    game_root: Path,
    profile: dict,
    key: bytes,
    seed: int,
    path_dict: Path | None,
    packages: list[Path],
    resolver: N.PathResolver,
) -> dict:
    meta = fingerprint(game_root, profile, key, seed, path_dict, packages)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp.sqlite")
    for path in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    connection = sqlite3.connect(tmp)
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE packages(name TEXT PRIMARY KEY, path TEXT, entries INTEGER, bytes INTEGER);
        CREATE TABLE files(
            id INTEGER PRIMARY KEY, package TEXT, path TEXT UNIQUE, dir TEXT, name TEXT,
            signature INTEGER, offset INTEGER, packed INTEGER, unpacked INTEGER,
            compression INTEGER, flags INTEGER, packed_hash INTEGER, unpacked_hash INTEGER,
            kind TEXT, ext TEXT
        );
        CREATE TABLE dirs(path TEXT PRIMARY KEY, parent TEXT, file_count INTEGER, bytes INTEGER);
        CREATE INDEX idx_files_dir ON files(dir);
        CREATE INDEX idx_files_path ON files(path);
        CREATE INDEX idx_dirs_parent ON dirs(parent);
        """
    )
    dir_stats: dict[str, list[int]] = {}
    seen: set[str] = set()
    total = 0
    package_total = len(packages)
    startup_progress.begin("catalog", f"0/{package_total} packages")
    for package_index, package in enumerate(packages, 1):
        startup_progress.update("catalog", package_index - 1, package_total, package.name)
        header, entries = N.read_index(package, key, validate=True, resolver=resolver)
        package_name = package.name
        package_bytes = 0
        rows = []
        entry_total = len(entries)
        entry_started = time.monotonic()
        for entry_index, entry in enumerate(entries, 1):
            if time.monotonic() - entry_started >= 0.05 or entry_index == entry_total:
                startup_progress.sub("catalog", entry_index, entry_total, package_name)
                entry_started = time.monotonic()
            real = resolver.resolve(entry.signature)[0]
            if package_name in E.SCRIPT_PACKAGES:
                real = None
            if real:
                rel = real.replace("\\", "/").lstrip("/")
                suffix = Path(rel).suffix.lower()
                kind = kind_for_ext(suffix)
                if suffix in (".jpg", ".jpeg"):
                    kind = "image"
                if suffix == ".wem":
                    kind = "audio"
            elif package_name in E.SCRIPT_PACKAGES:
                rel = f"_script/{Path(package_name).stem}/{entry.signature:08x}.xn"
                suffix = ".xn"
                kind = "script"
            else:
                try:
                    suffix, kind = detect_unresolved(str(package), entry)
                except Exception:
                    suffix, kind = ".bin", "binary"
                rel = f"_unresolved/{Path(package_name).stem}/{entry.signature:08x}{suffix}"
            if rel in seen:
                stem, dot, ext = rel.rpartition(".")
                rel = (stem + "~" + Path(package_name).stem + (dot + ext if dot else ""))
            seen.add(rel)
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            name = rel.rsplit("/", 1)[-1]
            rows.append(
                (
                    package_name, rel, parent, name, entry.signature, entry.offset,
                    entry.packed_size, entry.unpacked_size, entry.compression, entry.flags,
                    entry.packed_hash, entry.unpacked_hash, kind, suffix,
                )
            )
            package_bytes += entry.unpacked_size
            total += 1
            ancestor = parent
            while ancestor:
                stat = dir_stats.setdefault(ancestor, [0, 0])
                stat[0] += 1
                stat[1] += entry.unpacked_size
                ancestor = ancestor.rsplit("/", 1)[0] if "/" in ancestor else ""
            if len(rows) >= 5000:
                connection.executemany(
                    "INSERT INTO files(package,path,dir,name,signature,offset,packed,unpacked,compression,flags,packed_hash,unpacked_hash,kind,ext) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                rows.clear()
        if rows:
            connection.executemany(
                "INSERT INTO files(package,path,dir,name,signature,offset,packed,unpacked,compression,flags,packed_hash,unpacked_hash,kind,ext) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        connection.execute(
            "INSERT INTO packages(name,path,entries,bytes) VALUES (?,?,?,?)",
            (package_name, str(package), len(entries), package_bytes),
        )
        connection.commit()
        startup_progress.note(f"catalog: {package_name} {len(entries)} entries")
        startup_progress.sub("catalog", 0, 1, "")
    connection.executemany(
        "INSERT INTO dirs(path,parent,file_count,bytes) VALUES (?,?,?,?)",
        [(path, path.rsplit("/", 1)[0] if "/" in path else "", value[0], value[1]) for path, value in dir_stats.items()],
    )
    connection.execute("INSERT INTO meta(key,value) VALUES ('schema',?)", (SCHEMA_VERSION,))
    connection.execute("INSERT INTO meta(key,value) VALUES ('fingerprint',?)", (json.dumps(meta, ensure_ascii=False, sort_keys=True),))
    connection.execute("INSERT INTO meta(key,value) VALUES ('created',?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    connection.execute("INSERT INTO meta(key,value) VALUES ('toolkit',?)", (TOOLKIT_VERSION,))
    connection.commit()
    connection.execute("PRAGMA optimize")
    connection.close()
    os.replace(tmp, db_path)
    startup_progress.finish("catalog", f"{total} files")
    print(f"catalog ready: {db_path} ({total} files)", flush=True)
    return meta


def ensure_catalog(
    db_path: Path,
    game_root: Path,
    profile: dict,
    key: bytes,
    seed: int,
    path_dict: Path | None,
    packages: list[Path],
    resolver: N.PathResolver,
    rebuild: bool,
) -> dict:
    wanted = fingerprint(game_root, profile, key, seed, path_dict, packages)
    if not rebuild and db_path.is_file():
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = connection.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
            connection.close()
            if row:
                stored = json.loads(row[0])
                # Older catalogs also stored a toolkit version; ignore extra keys.
                if all(stored.get(key) == value for key, value in wanted.items()):
                    startup_progress.begin("catalog", "checking fingerprint")
                    startup_progress.finish("catalog", "reused")
                    print(f"catalog reused: {db_path}", flush=True)
                    return wanted
        except Exception:
            pass
    if db_path.exists():
        db_path.unlink()
    return build_catalog(db_path, game_root, profile, key, seed, path_dict, packages, resolver)


class BrowserServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    db_path: Path
    cache_root: Path
    artifacts: Path
    cache_limit: int
    vgmstream: str | None
    fingerprint: dict
    _cache_lock = threading.Lock()
    _thumb_sem = threading.Semaphore(3)
    _gmp_sem = threading.Semaphore(2)
    _gmp_cache_lock = threading.Lock()
    _gmp_cache: dict = {}
    _xn_sem = threading.Semaphore(2)
    _xn_cache_lock = threading.Lock()
    _xn_cache: dict = {}
    _xn_source_lock = threading.Lock()
    _xn_source_cache: dict = {}
    _xn_decompile_sem = threading.Semaphore(1)
    _uncompyle_cmd_cache = None
    _xn_names_lock = threading.Lock()
    _xn_names = None
    _xn_names_mtime = 0.0
    xn_names_path = None
    xn_opmap_path = None
    version: str | None = None
    update_state: dict = {}
    update_repo: str = update_check.DEFAULT_REPO
    update_proxy: str | None = None
    _update_lock = threading.Lock()
    _last_prune = 0.0

    def read_artifact(self, path: Path) -> bytes | None:
        with self._cache_lock:
            try:
                return path.read_bytes()
            except FileNotFoundError:
                return None

    def prune_cache(self) -> None:
        if self.cache_limit <= 0:
            return
        now = time.monotonic()
        with self._cache_lock:
            if now - self._last_prune < 15:
                return
            self._last_prune = now
        threading.Thread(target=self._prune_cache_worker, daemon=True).start()

    def _prune_cache_worker(self) -> None:
        files = []
        for path in self.artifacts.rglob("*"):
            if not path.is_file() or path.name.endswith(".tmp"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
        total = sum(item[1] for item in files)
        if total <= self.cache_limit:
            return
        files.sort(key=lambda item: item[0])
        target = int(self.cache_limit * 0.8)
        for _, size, path in files:
            if total <= target:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass

    def artifact(self, row, suffix: str, variant: str = "") -> Path:
        raw = f"{row['package']}|{row['path']}|{row['signature']}|{variant}".encode("utf-8")
        name = hashlib.sha256(raw).hexdigest()[:32] + suffix
        target = self.artifacts / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


def db_connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def find_vscode() -> str | None:
    for name in ("code", "code.cmd", "Code.exe"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft VS Code" / "Code.exe",
        Path("/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
        Path.home() / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code",
        Path("/usr/share/code/code"),
        Path("/snap/bin/code"),
        Path.home() / ".local" / "bin" / "code",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def find_idea() -> str | None:
    for name in ("idea64.exe", "idea.exe", "idea.bat", "idea"):
        found = shutil.which(name)
        if found:
            return found
    patterns = []
    local = os.environ.get("LOCALAPPDATA", "")
    programs = os.environ.get("ProgramFiles", "")
    if local:
        patterns.extend(
            [
                str(Path(local) / "Programs" / "IntelliJ IDEA*" / "bin" / "idea64.exe"),
                str(Path(local) / "JetBrains" / "Toolbox" / "apps" / "IDEA-U" / "ch-*" / "bin" / "idea64.exe"),
                str(Path(local) / "JetBrains" / "Toolbox" / "apps" / "IDEA-C" / "ch-*" / "bin" / "idea64.exe"),
            ]
        )
    if programs:
        patterns.append(str(Path(programs) / "JetBrains" / "IntelliJ IDEA*" / "bin" / "idea64.exe"))
    patterns.extend(
        [
            "/Applications/IntelliJ IDEA.app/Contents/MacOS/idea",
            "/Applications/IntelliJ IDEA CE.app/Contents/MacOS/idea",
            str(Path.home() / "Applications" / "IntelliJ IDEA.app" / "Contents" / "MacOS" / "idea"),
            "/opt/idea/bin/idea.sh",
            "/snap/bin/intellij-idea-ultimate",
            "/snap/bin/intellij-idea-community",
            str(Path.home() / ".local" / "share" / "JetBrains" / "Toolbox" / "apps" / "IDEA-U" / "ch-*" / "bin" / "idea.sh"),
            str(Path.home() / ".local" / "share" / "JetBrains" / "Toolbox" / "apps" / "IDEA-C" / "ch-*" / "bin" / "idea.sh"),
        ]
    )
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


def launch_app(app: str, target: Path) -> tuple[bool, str]:
    target_s = str(target)
    if app == "explorer":
        if sys.platform != "win32":
            return False, "backend.explorerWindowsOnly"
        subprocess.Popen(["explorer", f"/select,{target_s}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "backend.explorerOpened"
    if app == "finder":
        if sys.platform != "darwin":
            return False, "backend.finderMacOnly"
        subprocess.Popen(["open", "-R", target_s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "backend.finderOpened"
    if app == "vscode":
        executable = find_vscode()
        if not executable:
            return False, "backend.noVscode"
        command = [executable, target_s]
        if executable.lower().endswith((".cmd", ".bat")):
            command = ["cmd", "/c", executable, target_s]
        elif executable.lower().endswith(".sh"):
            command = ["bash", executable, target_s]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "backend.vscodeOpened"
    if app == "idea":
        executable = find_idea()
        if not executable:
            return False, "backend.noIdea"
        command = [executable, target_s]
        if executable.lower().endswith(".bat"):
            command = ["cmd", "/c", executable, target_s]
        elif executable.lower().endswith(".sh"):
            command = ["bash", executable, target_s]
        subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        return True, "backend.ideaOpened"
    return False, "backend.unsupported"


class Handler(BaseHTTPRequestHandler):
    server: BrowserServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def handle(self):
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _query(self):
        parsed = urlparse(self.path)
        return parsed.path, parse_qs(parsed.query)

    def _row(self, path: str):
        connection = db_connect(self.server.db_path)
        try:
            return connection.execute(
                "SELECT f.*, p.path AS package_path FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
                (path,),
            ).fetchone()
        finally:
            connection.close()

    def _send(self, data: bytes, content_type: str, status: int = 200, filename: str | None = None, download: bool = False, cache: str | None = None):
        total_size = len(data)
        start = 0
        end = len(data) - 1
        range_header = self.headers.get("Range")
        if range_header and len(data) > 0:
            match = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
            if match:
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else len(data) - 1
                elif match.group(2):
                    start = max(0, len(data) - int(match.group(2)))
                end = min(end, len(data) - 1)
                if start <= end:
                    status = 206
                    data = data[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        no_store = content_type.startswith("application/json") or content_type.startswith("audio/")
        self.send_header("Cache-Control", cache or ("no-store" if no_store else "private, max-age=3600"))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        if filename:
            disposition = "attachment" if download else "inline"
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, value, status: int = 200):
        self._send(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _error(self, message: str, status: int = 400, detail: str | None = None):
        payload = {"error": message}
        if detail:
            payload["detail"] = detail
        self._json(payload, status)

    def _send_file(self, path: Path, content_type: str, filename: str, download: bool = True):
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        disposition = "attachment" if download else "inline"
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(256 * 1024)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break

    def _entry_payload(self, row) -> bytes:
        return read_entry_bytes(row["package_path"], row)

    def _ensure_png(self, row) -> tuple[bytes, str]:
        payload = self._entry_payload(row)
        kind = image_convert.classify(payload, row["ext"] or "")
        if kind == "png":
            return payload, "image/png"
        if kind == "jpeg":
            return payload, "image/jpeg"
        if kind not in ("ktx1", "ktx2", "dds", "jpeg", "tga", "bmp", "webp", "gif"):
            raise ValueError("not a supported image")
        if self.server.cache_limit <= 0:
            tmp = self.server.cache_root / "tmp" / f"{uuid.uuid4().hex}.png"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            try:
                image_convert.convert_image(payload, row["ext"] or "", tmp)
                return tmp.read_bytes(), "image/png"
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        cache = self.server.artifact(row, ".png", "image")
        cached = self.server.read_artifact(cache)
        if cached is not None:
            return cached, "image/png"
        tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            image_convert.convert_image(payload, row["ext"] or "", tmp)
            data = tmp.read_bytes()
            os.replace(tmp, cache)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        self.server.prune_cache()
        return data, "image/png"

    def _decode_wwise(self, payload: bytes, ext: str) -> tuple[bytes, str, str]:
        if not self.server.vgmstream:
            raise ValueError("vgmstream-cli not available")
        tmpdir = self.server.cache_root / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        src = tmpdir / f"{token}{ext}"
        wav = tmpdir / f"{token}.wav"
        try:
            src.write_bytes(payload)
            result = subprocess.run(
                [self.server.vgmstream, "-o", str(wav), str(src)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=180,
            )
            if result.returncode != 0 or not wav.is_file() or wav.stat().st_size < 44:
                raise ValueError("audio decode failed")
            if wav.stat().st_size > 500 * 1024 * 1024:
                raise ValueError("audio too large for preview")
            if _soundfile is not None:
                samples, rate = _soundfile.read(str(wav), dtype="int16", always_2d=True)
                buffer = io.BytesIO()
                _soundfile.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
                return buffer.getvalue(), "audio/flac", "flac"
            data = wav.read_bytes()
            return data, "audio/wav", "wav"
        finally:
            for path in (src, wav):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

    def _audio(self, row, media: str | None = None) -> tuple[bytes, str, str]:
        ext = (row["ext"] or "").lower()
        payload = self._entry_payload(row)
        if ext in (".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".opus"):
            return payload, MIME.get(ext, "application/octet-stream"), ext[1:]
        if ext not in (".wem", ".bnk"):
            raise ValueError("not an audio entry")
        if ext == ".bnk":
            try:
                bank = bnk_parse.parse_bank(payload)
            except bnk_parse.BankFormatError:
                bank = None
            if bank is not None and bank.media:
                if media:
                    try:
                        media_id = int(media, 0)
                    except ValueError:
                        raise ValueError("invalid embedded media id") from None
                    _, payload = bank.media_payload(media_id=media_id)
                else:
                    _, payload = bank.media_payload(index=0)
                return self._decode_wwise(payload, ".wem")
            if media:
                raise ValueError("no embedded media in this bank")
        return self._decode_wwise(payload, ".bnk" if ext == ".bnk" else ".wem")

    def _image_bytes(self, row, fmt: str) -> tuple[bytes, str]:
        data, _ = self._ensure_png(row)
        if fmt == "png":
            return data, ".png"
        image = Image.open(io.BytesIO(data))
        image.load()
        buffer = io.BytesIO()
        if fmt == "jpg":
            if image.mode in ("RGBA", "LA", "P"):
                image = image.convert("RGBA")
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            image.save(buffer, "JPEG", quality=92)
            return buffer.getvalue(), ".jpg"
        if fmt == "ico":
            image.thumbnail((256, 256))
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGBA")
            image.save(buffer, "ICO")
            return buffer.getvalue(), ".ico"
        raise ValueError(f"unsupported image format {fmt}")

    def _encode_audio(self, wav_path: Path, fmt: str) -> bytes:
        if fmt == "wav":
            return wav_path.read_bytes()
        if _soundfile is None:
            raise ValueError("soundfile not available")
        samples, rate = _soundfile.read(str(wav_path), dtype="int16", always_2d=True)
        buffer = io.BytesIO()
        if fmt == "flac":
            _soundfile.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
        elif fmt == "mp3":
            _soundfile.write(buffer, samples, rate, format="MP3")
        else:
            raise ValueError(f"unsupported audio format {fmt}")
        return buffer.getvalue()

    def _audio_entries(self, row, fmt: str) -> list[tuple[str, bytes]]:
        ext = (row["ext"] or "").lower()
        base = Path(row["path"])
        payload = self._entry_payload(row)
        if ext in (".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".opus"):
            if ext == f".{fmt}" or ext in (".m4a", ".aac", ".opus") or _soundfile is None:
                return [(str(base), payload)]
            try:
                samples, rate = _soundfile.read(io.BytesIO(payload), dtype="int16", always_2d=True)
                buffer = io.BytesIO()
                if fmt == "flac":
                    _soundfile.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
                elif fmt == "mp3":
                    _soundfile.write(buffer, samples, rate, format="MP3")
                else:
                    _soundfile.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
                return [(str(base.with_suffix(f".{fmt}")), buffer.getvalue())]
            except Exception:
                return [(str(base), payload)]
        if ext not in (".wem", ".bnk"):
            raise ValueError("not an audio entry")
        wav = None
        try:
            if ext == ".bnk":
                try:
                    bank = bnk_parse.parse_bank(payload)
                except bnk_parse.BankFormatError:
                    bank = None
                if bank is not None and bank.media:
                    entries = []
                    single = len(bank.media) == 1
                    for index in range(len(bank.media)):
                        _, media_payload = bank.media_payload(index=index)
                        wav = self._decode_wav_file(media_payload, ".wem")
                        if single:
                            arc = str(base.with_suffix(f".{fmt}"))
                        else:
                            arc = str(base.with_name(f"{base.stem}_{bank.media[index].media_id}.{fmt}"))
                        entries.append((arc, self._encode_audio(wav, fmt)))
                        try:
                            wav.unlink()
                        except FileNotFoundError:
                            pass
                        wav = None
                    return entries
            wav = self._decode_wav_file(payload, ".bnk" if ext == ".bnk" else ".wem")
            return [(str(base.with_suffix(f".{fmt}")), self._encode_audio(wav, fmt))]
        finally:
            if wav is not None:
                try:
                    wav.unlink()
                except FileNotFoundError:
                    pass

    def _convert_entries(self, row, image_format: str, audio_format: str) -> list[tuple[str, bytes]]:
        ext = (row["ext"] or "").lower()
        if row["kind"] == "image" or ext in IMAGE_EXT:
            try:
                data, suffix = self._image_bytes(row, image_format)
                return [(str(Path(row["path"]).with_suffix(suffix)), data)]
            except Exception:
                return [(row["path"], self._entry_payload(row))]
        if row["kind"] in ("audio", "bank") or ext in AUDIO_EXT:
            try:
                return self._audio_entries(row, audio_format)
            except Exception:
                return [(row["path"], self._entry_payload(row))]
        return [(row["path"], self._entry_payload(row))]

    def _batch_zip(self, body) -> Path:
        paths = body.get("paths")
        mode = body.get("mode")
        image_format = body.get("img", "png")
        audio_format = body.get("aud", "wav")
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths required")
        if mode not in ("raw", "convert"):
            raise ValueError("invalid mode")
        if image_format not in IMAGE_FORMATS:
            raise ValueError("invalid image format")
        if audio_format not in AUDIO_FORMATS:
            raise ValueError("invalid audio format")
        limit = BATCH_RAW_MAX if mode == "raw" else BATCH_CONVERT_MAX
        if len(paths) > limit:
            raise ValueError(f"too many files (max {limit})")
        wanted = [str(item)[:1024] for item in paths if isinstance(item, str) and item][:limit]
        started = time.monotonic()
        tmpdir = self.server.cache_root / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        target = tmpdir / f"batch_{uuid.uuid4().hex}.zip"
        used: set = set()
        errors: list[str] = []
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
                for item in wanted:
                    row = self._row(item)
                    if row is None:
                        errors.append(f"{item}: not found")
                        continue
                    try:
                        if mode == "raw" or time.monotonic() - started > BATCH_TIME_BUDGET:
                            entries = [(row["path"], self._entry_payload(row))]
                        else:
                            entries = self._convert_entries(row, image_format, audio_format)
                    except Exception as exc:
                        try:
                            entries = [(row["path"], self._entry_payload(row))]
                        except Exception:
                            errors.append(f"{row['path']}: {exc}")
                            continue
                    for arc, data in entries:
                        bundle.writestr(unique_arc_name(zip_arc_name(arc), used), data)
                if errors:
                    bundle.writestr("_batch_errors.txt", "\n".join(errors) + "\n")
            return target
        except Exception:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            raise

    def _materialize(self, row) -> tuple[Path, bool]:
        payload = self._entry_payload(row)
        converted = False
        name = Path(row["path"]).name
        if row["kind"] == "image":
            try:
                data, ctype = self._ensure_png(row)
                payload = data
                if ctype == "image/png":
                    name = Path(row["path"]).stem + ".png"
                    converted = (row["ext"] or "").lower() != ".png"
                elif ctype == "image/jpeg":
                    name = Path(row["path"]).stem + ".jpg"
                else:
                    name = Path(row["path"]).name
            except Exception:
                pass
        name = re.sub(r'[<>:"/\|?*\x00-]', "_", name).strip(" .")
        if not name:
            name = f"entry_{row['signature']:08x}.bin"
        key = hashlib.sha1(row["path"].encode("utf-8", "ignore")).hexdigest()[:20]
        target_dir = self.server.cache_root / "open" / hashlib.sha1(
            json.dumps(self.server.fingerprint, sort_keys=True).encode()
        ).hexdigest()[:12] / key
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if not target.is_file() or target.stat().st_size != len(payload):
            tmp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
            try:
                tmp.write_bytes(payload)
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        return target, converted

    def _image_thumb(self, row, width: int) -> bytes:
        cache = self.server.artifact(row, f"_img_w{width}.png", "thumb")
        thumb = self.server.read_artifact(cache)
        if thumb is not None:
            return thumb
        data, _ = self._ensure_png(row)
        image = Image.open(io.BytesIO(data))
        image.load()
        image.thumbnail((width, width))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            image.save(tmp, "PNG")
            thumb = tmp.read_bytes()
            os.replace(tmp, cache)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        self.server.prune_cache()
        return thumb

    def _decode_wav_file(self, payload: bytes, ext: str) -> Path:
        if not self.server.vgmstream:
            raise ValueError("vgmstream-cli not available")
        tmpdir = self.server.cache_root / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        src = tmpdir / f"{token}{ext}"
        wav = tmpdir / f"{token}.wav"
        try:
            src.write_bytes(payload)
            result = subprocess.run(
                [self.server.vgmstream, "-o", str(wav), str(src)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=180,
            )
            if result.returncode != 0 or not wav.is_file() or wav.stat().st_size < 44:
                raise ValueError("audio decode failed")
            return wav
        except Exception:
            try:
                wav.unlink()
            except FileNotFoundError:
                pass
            raise
        finally:
            try:
                src.unlink()
            except FileNotFoundError:
                pass

    def _waveform_samples(self, row, max_seconds: int = 60):
        """Decode just enough audio for a waveform; no intermediate FLAC."""
        ext = (row["ext"] or "").lower()
        payload = self._entry_payload(row)
        source = None
        try:
            if ext == ".bnk":
                try:
                    bank = bnk_parse.parse_bank(payload)
                except bnk_parse.BankFormatError:
                    raise ValueError("bank has no embedded media") from None
                if not bank.media:
                    raise ValueError("bank has no embedded media")
                _, payload = bank.media_payload(index=0)
                source = self._decode_wav_file(payload, ".wem")
            elif ext in (".wav", ".flac", ".ogg", ".mp3", ".m4a", ".aac", ".opus"):
                info = _soundfile.info(io.BytesIO(payload))
                frames = min(int(info.frames), int(info.samplerate * max_seconds))
                samples, rate = _soundfile.read(
                    io.BytesIO(payload), dtype="float32", always_2d=True, frames=max(frames, 1)
                )
                return samples.mean(axis=1), rate
            else:
                source = self._decode_wav_file(payload, ext if ext.startswith(".") else ".wem")
            info = _soundfile.info(str(source))
            frames = min(int(info.frames), int(info.samplerate * max_seconds))
            samples, rate = _soundfile.read(
                str(source), dtype="float32", always_2d=True, frames=max(frames, 1)
            )
            return samples.mean(axis=1), rate
        finally:
            if source is not None:
                try:
                    source.unlink()
                except FileNotFoundError:
                    pass

    def _waveform_thumb(self, row, width: int) -> bytes:
        if _soundfile is None or _np is None:
            raise ValueError("waveform preview unavailable")
        cache = self.server.artifact(row, f"_wave3_w{width}.png", "thumb")
        thumb = self.server.read_artifact(cache)
        if thumb is not None:
            return thumb
        with BrowserServer._thumb_sem:
            thumb = self.server.read_artifact(cache)
            if thumb is not None:
                return thumb
            try:
                mono, _rate = self._waveform_samples(row)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"cannot decode audio: {exc}") from None
            if not mono.size:
                raise ValueError("audio has no samples")
            height = max(48, int(round(width * 0.625)))
            bins = _np.array_split(_np.abs(mono), width)
            peaks = _np.array(
                [float(part.max()) if part.size else 0.0 for part in bins], dtype="float32"
            )
            peak = float(peaks.max())
            peaks = _np.sqrt(peaks / peak) if peak > 0 else _np.zeros(width, dtype="float32")
            highlight = float(_np.percentile(peaks, 99)) if peak > 0 else 1.0
            if highlight >= 1.0:
                # Fully saturated tracks would otherwise paint every lane amber.
                highlight = 1.01
            image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            mid = height / 2
            top = []
            bottom = []
            for x, value in enumerate(peaks):
                amplitude = max(1.0, float(value) * height * 0.34)
                top.append((x, mid - amplitude))
                bottom.append((x, mid + amplitude))
            draw.polygon(top + list(reversed(bottom)), fill=(0, 140, 150, 42))
            draw.line((0, int(mid), width - 1, int(mid)), fill=(128, 128, 128, 60))
            normal = (0, 140, 150, 230)
            hot = (190, 120, 0, 240)
            for x in range(max(0, width - 1)):
                color = hot if max(float(peaks[x]), float(peaks[x + 1])) >= highlight else normal
                draw.line((top[x][0], top[x][1], top[x + 1][0], top[x + 1][1]), fill=color, width=2)
                draw.line((bottom[x][0], bottom[x][1], bottom[x + 1][0], bottom[x + 1][1]), fill=color, width=2)
            tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                image.save(tmp, "PNG")
                thumb = tmp.read_bytes()
                os.replace(tmp, cache)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
        self.server.prune_cache()
        return thumb

    def _thumbnail(self, row, width: int) -> bytes:
        ext = (row["ext"] or "").lower()
        kind = row["kind"] or ""
        if ext == ".gim":
            return self._gim_thumb(row, width)
        if ext == ".gmp":
            return self._gmp_thumb(row, width)
        if kind == "image" or ext in IMAGE_EXT:
            return self._image_thumb(row, width)
        if kind in ("audio", "bank") or ext in AUDIO_EXT:
            return self._waveform_thumb(row, width)
        raise ValueError("thumbnail not available for this type")

    def _gim_thumb(self, row, width: int) -> bytes:
        width = min(max(32, width), 512)
        cache = self.server.artifact(row, f"_gim_w{width}_v1.png", "thumb")
        thumb = self.server.read_artifact(cache)
        if thumb is not None:
            return thumb
        with BrowserServer._thumb_sem:
            thumb = self.server.read_artifact(cache)
            if thumb is not None:
                return thumb
            data = neox_object.parse(self._entry_payload(row))
            mesh_path = ""
            for obj in data.get("objects", []):
                value = obj.get("Mesh")
                if isinstance(value, str) and value:
                    mesh_path = self._resolve_ref(row["path"], value) or ""
                    break
            if not mesh_path:
                sibling = str(Path(row["path"]).with_suffix(".mesh")).replace("\\", "/")
                if self._row(sibling):
                    mesh_path = sibling
            mesh_row = self._row(mesh_path) if mesh_path else None
            if not mesh_row:
                raise ValueError("GIM mesh not found")
            mesh = mesh_parse.parse(self._entry_payload(mesh_row))
            if not mesh.get("packed"):
                center, half = self._gim_bounds(data)
                if center and half:
                    mesh = mesh_parse.denormalize(mesh, center, half)
            color = self._thumb_texture_color(row, data)
            image = self._render_model_thumb(mesh, width, color)
            tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                image.save(tmp, "PNG")
                thumb = tmp.read_bytes()
                os.replace(tmp, cache)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
            self.server.prune_cache()
            return thumb

    def _gmp_thumb(self, row, width: int) -> bytes:
        width = min(max(32, width), 512)
        cache = self.server.artifact(row, f"_gmp_w{width}_v1.png", "thumb")
        thumb = self.server.read_artifact(cache)
        if thumb is not None:
            return thumb
        with BrowserServer._thumb_sem:
            thumb = self.server.read_artifact(cache)
            if thumb is not None:
                return thumb
            try:
                data = self._gmp_data(row)
                image = self._render_gmp_thumb(data, width)
            except Exception as exc:
                raise ValueError(f"GMP thumbnail failed: {exc}") from exc
            tmp = cache.with_suffix(f".{uuid.uuid4().hex}.tmp")
            try:
                image.save(tmp, "PNG")
                thumb = tmp.read_bytes()
                os.replace(tmp, cache)
            finally:
                try:
                    tmp.unlink()
                except FileNotFoundError:
                    pass
            self.server.prune_cache()
            return thumb

    def _thumb_texture_color(self, row, data: dict):
        for obj in data.get("objects", []):
            for value in obj.values():
                if not isinstance(value, str):
                    continue
                if not value.lower().endswith((".tga", ".dds", ".kta", ".png", ".jpg", ".jpeg", ".webp")):
                    continue
                resolved = self._resolve_ref(row["path"], value)
                texture_row = self._row(resolved) if resolved else None
                if not texture_row:
                    continue
                try:
                    payload, _ = self._ensure_png(texture_row)
                    image = Image.open(io.BytesIO(payload)).convert("RGBA")
                    image.thumbnail((24, 24))
                    pixels = [pixel for pixel in image.getdata() if pixel[3] > 32]
                    if not pixels:
                        continue
                    color = tuple(sum(pixel[channel] for pixel in pixels) // len(pixels) for channel in range(3))
                    if max(color) - min(color) >= 18 and max(color) >= 45:
                        return color
                except Exception:
                    continue
        return None

    def _render_model_thumb(self, mesh: dict, width: int, base_color) -> Image.Image:
        vertices = mesh.get("vertices") or []
        indices = mesh.get("indices") or []
        if not vertices:
            raise ValueError("mesh has no vertices")
        width = max(32, int(width))
        height = max(20, round(width * 0.625))
        factor = 3 if width <= 128 else 2
        canvas_w = width * factor
        canvas_h = height * factor
        margin = max(8, round(min(canvas_w, canvas_h) * 0.06))

        xs = [vertex[0] for vertex in vertices]
        ys = [vertex[1] for vertex in vertices]
        zs = [vertex[2] for vertex in vertices]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        cz = (min(zs) + max(zs)) / 2
        yaw = 0.55
        pitch = 0.28
        cyaw, syaw = math.cos(yaw), math.sin(yaw)
        cpitch, spitch = math.cos(pitch), math.sin(pitch)
        rotated = []
        for x, y, z in vertices:
            x -= cx
            y -= cy
            z -= cz
            x, z = x * cyaw + z * syaw, -x * syaw + z * cyaw
            y, z = y * cpitch - z * spitch, y * spitch + z * cpitch
            rotated.append((x, y, z))
        min_x = min(vertex[0] for vertex in rotated)
        max_x = max(vertex[0] for vertex in rotated)
        min_y = min(vertex[1] for vertex in rotated)
        max_y = max(vertex[1] for vertex in rotated)
        span_x = max(1e-5, max_x - min_x)
        span_y = max(1e-5, max_y - min_y)
        scale = min((canvas_w - margin * 2) / span_x, (canvas_h - margin * 2) / span_y)
        offset_x = canvas_w / 2 - (min_x + max_x) / 2 * scale
        offset_y = canvas_h / 2 + (min_y + max_y) / 2 * scale
        projected = [(x * scale + offset_x, -y * scale + offset_y, z) for x, y, z in rotated]

        color = base_color or (112, 168, 202)
        image = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        shadow_w = canvas_w * 0.30
        shadow_h = canvas_h * 0.055
        draw.ellipse(
            [canvas_w / 2 - shadow_w / 2, canvas_h * 0.86, canvas_w / 2 + shadow_w / 2, canvas_h * 0.86 + shadow_h],
            fill=(0, 0, 0, 30),
        )

        light = (-0.4, 0.75, 0.52)
        light_len = math.sqrt(sum(value * value for value in light))
        light = tuple(value / light_len for value in light)
        triangles = []
        triangle_count = len(indices) // 3
        step = max(1, math.ceil(triangle_count / 120000))
        for index in range(0, triangle_count, step):
            base = index * 3
            try:
                a, b, c = indices[base], indices[base + 1], indices[base + 2]
                pa, pb, pc = rotated[a], rotated[b], rotated[c]
            except (IndexError, TypeError):
                continue
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            normal = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            lambert = abs(nx / normal * light[0] + ny / normal * light[1] + nz / normal * light[2])
            shade = 0.38 + 0.72 * lambert
            fill = tuple(min(255, int(channel * shade)) for channel in color)
            depth = (rotated[a][2] + rotated[b][2] + rotated[c][2]) / 3
            triangles.append((depth, (projected[a][0], projected[a][1], projected[b][0], projected[b][1], projected[c][0], projected[c][1]), fill))
        triangles.sort(key=lambda item: item[0])
        for _, points, fill in triangles:
            draw.polygon(
                [(points[0], points[1]), (points[2], points[3]), (points[4], points[5])],
                fill=fill + (255,), outline=fill + (255,),
            )
        if not triangles:
            radius = max(1, round(min(canvas_w, canvas_h) / 180))
            for x, y, _ in projected:
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color + (255,))
        return image.resize((width, height), Image.LANCZOS)

    def _render_gmp_thumb(self, data: dict, width: int) -> Image.Image:
        width = max(32, int(width))
        height = max(20, round(width * 0.625))
        factor = 2
        canvas_w = width * factor
        canvas_h = height * factor
        image = Image.new("RGBA", (canvas_w, canvas_h), (26, 30, 36, 255))
        draw = ImageDraw.Draw(image, "RGBA")
        step = max(12, round(canvas_w / 12))
        grid = (255, 255, 255, 18)
        for x in range(0, canvas_w + step, step):
            draw.line([(x, 0), (x, canvas_h)], fill=grid)
        for y in range(0, canvas_h + step, step):
            draw.line([(0, y), (canvas_w, y)], fill=grid)

        bounds = data.get("bounds")
        units = [unit for unit in (data.get("units") or []) if unit.get("position")]
        if not units or not bounds:
            draw.ellipse([canvas_w / 2 - 12, canvas_h / 2 - 12, canvas_w / 2 + 12, canvas_h / 2 + 12], outline=(255, 255, 255, 40), width=2)
        else:
            min_x, max_x = bounds["min_x"], bounds["max_x"]
            min_y, max_y = bounds["min_y"], bounds["max_y"]
            span_x = max(1e-5, max_x - min_x)
            span_y = max(1e-5, max_y - min_y)
            margin = max(6, round(min(canvas_w, canvas_h) * 0.07))
            scale = min((canvas_w - margin * 2) / span_x, (canvas_h - margin * 2) / span_y)
            offset_x = (canvas_w - span_x * scale) / 2 - min_x * scale
            offset_y = (canvas_h - span_y * scale) / 2 + max_y * scale
            axis = bounds.get("axis") or "xz"
            palette = [(255, 183, 77), (102, 187, 106), (79, 195, 247), (239, 83, 80), (171, 71, 188), (255, 241, 118)]
            radius = max(1, round(canvas_w / 180))
            for unit in units[:12000]:
                position = unit["position"]
                x = position[0]
                y = position[2] if axis == "xz" else position[1]
                color = unit.get("color")
                if not (isinstance(color, list) and len(color) >= 3):
                    key = unit.get("eid") or unit.get("id") or 0
                    color = palette[int(key) % len(palette)]
                px = x * scale + offset_x
                py = offset_y - y * scale
                draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=tuple(color[:3]) + (220,))
        return image.resize((width, height), Image.LANCZOS)

    def _sibling_bounds(self, mesh_path: str) -> tuple[list | None, list | None]:
        stem = str(Path(mesh_path).with_suffix(""))
        candidates = (
            stem + "_lod1.gim", stem + "_lod2.gim", stem + ".gim",
            stem[:-1] + ".gim" if stem and stem[-1].isdigit() else None,
        )
        for candidate in candidates:
            if not candidate:
                continue
            row = self._row(candidate)
            if not row or (row["ext"] or "").lower() != ".gim":
                continue
            try:
                payload = self._entry_payload(row)
                data = neox_object.parse(payload)
                center, half = self._gim_bounds(data)
                if center and half:
                    return center, half
            except Exception:
                continue
        return None, None

    def _mesh_animation_path(self, mesh_path: str) -> str | None:
        folder = Path(mesh_path).parent.as_posix()
        stem = Path(mesh_path).stem
        base = stem.split("_lod")[0]
        connection = db_connect(self.server.db_path)
        try:
            rows = connection.execute(
                "SELECT path FROM files WHERE lower(ext)='.animation' AND path LIKE ? ORDER BY path",
                (f"{folder}/%",),
            ).fetchall()
        finally:
            connection.close()
        paths = [row["path"] for row in rows]
        for path in paths:
            if f"animations_{base}/" in path and Path(path).name == "idle.animation":
                return path
        for path in paths:
            if Path(path).name == "idle.animation":
                return path
        for path in paths:
            if f"animations_{base}/" in path:
                return path
        return paths[0] if paths else None

    def _resolve_ref(self, base_path: str, ref: str) -> str | None:
        target = [p for p in ref.replace("\\", "/").split("/") if p not in ("", ".")]
        candidate = "/".join(target)
        if self._row(candidate):
            return candidate
        stack = [p for p in base_path.split("/") if p]
        if stack:
            stack.pop()
        for part in target:
            if part == "..":
                if stack:
                    stack.pop()
            else:
                stack.append(part)
        candidate = "/".join(stack)
        if self._row(candidate):
            return candidate
        overlap = candidate.rfind(target[0]) if target else -1
        if overlap > 0:
            candidate = candidate[overlap:]
            if self._row(candidate):
                return candidate
        # 贴图引用常写 .tga/.dds, 实际文件是同名 .kta
        stem = target[-1].rsplit(".", 1)[0] if target else ""
        if stem:
            for suffix in (".kta", ".dds", ".png"):
                candidate = "/".join(stack[:-1] if False else target[:-1] + [stem + suffix])
                if self._row(candidate):
                    return candidate
        return None

    def _gim_bounds(self, data: dict) -> tuple[list | None, list | None]:
        for obj in data.get("objects", []):
            text = obj.get("BoundingInfo")
            if not isinstance(text, str) or "),(" not in text:
                continue
            try:
                left, right = text.split("),(", 1)
                center = [float(v) for v in left.strip("()").split(",")]
                half_text = right.split("),")[0]
                half = [float(v) for v in half_text.strip("()").split(",")]
                if len(center) == 3 and len(half) == 3:
                    return center, half
            except ValueError:
                continue
        return None, None

    def _gmp_data(self, row) -> dict:
        key = (row["path"], row["signature"], row["packed_hash"])
        with BrowserServer._gmp_cache_lock:
            data = BrowserServer._gmp_cache.get(key)
        if data is not None:
            return data
        with BrowserServer._gmp_sem:
            data = gmp_parse.parse(self._entry_payload(row))
        data["path"] = row["path"]
        if not data.get("map_name"):
            data["map_name"] = Path(row["path"]).stem
        with BrowserServer._gmp_cache_lock:
            BrowserServer._gmp_cache[key] = data
            while len(BrowserServer._gmp_cache) > 8:
                BrowserServer._gmp_cache.pop(next(iter(BrowserServer._gmp_cache)))
        return data

    def _xn_name_map(self) -> dict:
        path = Path(self.server.xn_names_path) if self.server.xn_names_path else Path(__file__).resolve().parent.parent / "data" / "xn_names.json"
        try:
            mtime = path.stat().st_mtime if path.is_file() else 0.0
        except OSError:
            mtime = 0.0
        with BrowserServer._xn_names_lock:
            if BrowserServer._xn_names is None or mtime != BrowserServer._xn_names_mtime:
                mapping = {}
                try:
                    payload = json.loads(path.read_text("utf-8"))
                    mapping = {int(key): value for key, value in payload.get("map", {}).items()}
                except (OSError, ValueError, TypeError):
                    mapping = {}
                BrowserServer._xn_names = mapping
                BrowserServer._xn_names_mtime = mtime
            return BrowserServer._xn_names

    def _xn_data(self, row, module: str | None = None) -> dict:
        signature = int(row["signature"])
        override = (module or "").strip().replace("\\", ".").replace("/", ".")
        if override.endswith(".py"):
            override = override[:-3]
        if override.startswith("clients."):
            override = override[8:]
        salt = override or self._xn_name_map().get(signature)
        result = {"path": row["path"], "signature": f"{signature:08x}", "module": salt}
        payload = self._entry_payload(row)
        sizes = xn_crypto.header(payload)
        if not sizes:
            result.update({"decrypted": False, "reason": "backend.badContainer"})
            return result
        result["compressed"], result["original"] = sizes
        if not salt:
            result.update({"decrypted": False, "reason": "backend.moduleMissing"})
            return result
        key = (signature, salt, row["packed_hash"])
        with BrowserServer._xn_cache_lock:
            cached = BrowserServer._xn_cache.get(key)
        if cached is not None:
            return cached
        with BrowserServer._xn_sem:
            plain = xn_crypto.decrypt(payload, salt)
        if plain is None:
            result.update({"decrypted": False, "reason": "backend.decryptFailed"})
            return result
        try:
            preview = xn_parse.preview(plain)
        except Exception as exc:
            result.update({"decrypted": False, "reason": "backend.parseFailed", "detail": str(exc)})
            return result
        result["decrypted"] = True
        result["preview"] = preview
        with BrowserServer._xn_cache_lock:
            BrowserServer._xn_cache[key] = result
            while len(BrowserServer._xn_cache) > 32:
                BrowserServer._xn_cache.pop(next(iter(BrowserServer._xn_cache)))
        return result

    def _xn_disasm(self, row, func: str = "") -> dict:
        signature = int(row["signature"])
        salt = self._xn_name_map().get(signature)
        if not salt:
            return {"error": "backend.moduleNotIndexed", "signature": f"{signature:08x}"}
        payload = self._entry_payload(row)
        plain = xn_crypto.decrypt(payload, salt)
        if plain is None:
            return {"error": "backend.decryptFailed", "signature": f"{signature:08x}"}
        try:
            text = xn_parse.disasm_text(plain, func, 1600)
        except Exception as exc:
            return {"error": "backend.disasmFailed", "detail": str(exc), "signature": f"{signature:08x}"}
        return {
            "path": row["path"],
            "signature": f"{signature:08x}",
            "module": salt,
            "function": func or "<module>",
            "disassembly": text,
        }

    def _uncompyle_cmd(self) -> list | None:
        with BrowserServer._xn_source_lock:
            if BrowserServer._uncompyle_cmd_cache is not None:
                return BrowserServer._uncompyle_cmd_cache
        cmd = None
        bundled = Path(sys.executable).resolve()
        site_dir = bundled.parent / "Lib" / "site-packages" / "uncompyle6"
        if bundled.is_file() and site_dir.is_dir():
            probe = subprocess.run(
                [str(bundled), "-c", "import uncompyle6"],
                capture_output=True,
                timeout=30,
            )
            if probe.returncode == 0:
                cmd = [str(bundled), "-m", "uncompyle6.bin.uncompile"]
        if not cmd:
            exe = self._uncompyle_exe()
            if exe:
                cmd = [exe]
        with BrowserServer._xn_source_lock:
            BrowserServer._uncompyle_cmd_cache = cmd
        return cmd

    def _uncompyle_exe(self) -> str | None:
        candidates = [shutil.which("uncompyle6")]
        for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
            if not base:
                continue
            candidates.extend(glob.glob(str(Path(base) / "Python" / "Python*" / "Scripts" / "uncompyle6.exe")))
            candidates.extend(glob.glob(str(Path(base) / "Programs" / "Python" / "Python*" / "Scripts" / "uncompyle6.exe")))
        candidates.append(r"C:\Python314\Scripts\uncompyle6.exe")
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        return None

    def _xn_source(self, row) -> dict:
        signature = int(row["signature"])
        salt = self._xn_name_map().get(signature)
        if not salt:
            return {"error": "backend.moduleNotIndexed", "signature": f"{signature:08x}"}
        key = (signature, salt, row["packed_hash"])
        with BrowserServer._xn_source_lock:
            cached = BrowserServer._xn_source_cache.get(key)
        if cached is not None:
            return cached
        payload = self._entry_payload(row)
        with BrowserServer._xn_sem:
            plain = xn_crypto.decrypt(payload, salt)
        if plain is None:
            return {"error": "backend.decryptFailed", "signature": f"{signature:08x}"}
        try:
            pyc = xn_decompile.to_pyc(plain)
        except Exception as exc:
            return {"error": "backend.bytecodeFailed", "detail": str(exc), "signature": f"{signature:08x}"}
        cmd = self._uncompyle_cmd()
        if not cmd:
            return {"error": "backend.noUncompyle", "signature": f"{signature:08x}"}
        tmp = self.server.cache_root / "tmp" / f"{uuid.uuid4().hex}.pyc"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp.write_bytes(pyc)
            with BrowserServer._xn_decompile_sem:
                proc = subprocess.run(
                    cmd + [str(tmp)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    cwd=str(tmp.parent),
                )
        except subprocess.TimeoutExpired:
            return {"error": "backend.decompileTimeout", "signature": f"{signature:08x}"}
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
        source = proc.stdout or ""
        if not source.strip():
            message = (proc.stderr or "").strip().splitlines()
            if message:
                return {"error": "backend.decompileFailed", "detail": message[-1], "signature": f"{signature:08x}"}
            return {"error": "backend.noOutput", "signature": f"{signature:08x}"}
        result = {
            "path": row["path"],
            "signature": f"{signature:08x}",
            "module": salt,
            "source": source,
            "source_bytes": len(source.encode("utf-8")),
        }
        with BrowserServer._xn_source_lock:
            BrowserServer._xn_source_cache[key] = result
            while len(BrowserServer._xn_source_cache) > 16:
                BrowserServer._xn_source_cache.pop(next(iter(BrowserServer._xn_source_cache)))
        return result

    def _text_snippet(self, row, max_chars: int, max_lines: int) -> tuple[str, bool]:
        payload = self._entry_payload(row)
        head = payload[: 128 * 1024]
        if b"\x00" in head[:4096]:
            raise ValueError("binary data; no text preview")
        text = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = head.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            text = head.decode("latin-1")
        sample = text[:4096]
        printable = sum(1 for char in sample if char.isprintable() or char in "\r\n\t")
        if sample and printable < len(sample) * 0.9:
            raise ValueError("binary data; no text preview")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        out: list[str] = []
        used = 0
        for line in lines[:max_lines]:
            line = line.rstrip()
            if used >= max_chars:
                break
            room = max_chars - used
            if len(line) > room:
                out.append(line[:room])
                used = max_chars
                break
            out.append(line)
            used += len(line) + 1
        snippet = "\n".join(out)
        return snippet, len(text) > len(snippet)

    def do_POST(self):
        route, _ = self._query()
        if route == "/api/batch":
            origin = self.headers.get("Origin")
            if origin:
                try:
                    if urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                        self._error("origin not allowed", 403)
                        return
                except Exception:
                    self._error("bad origin", 403)
                    return
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                self._error("expected application/json", 415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > BATCH_BODY_LIMIT:
                    raise ValueError("invalid body length")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                zip_path = self._batch_zip(body)
            except ValueError as exc:
                self._error(str(exc), 400)
                return
            except Exception as exc:
                self._error(f"batch failed: {exc}", 500)
                return
            try:
                stamp = time.strftime("%Y%m%d_%H%M%S")
                self._send_file(zip_path, "application/zip", f"npkx_batch_{stamp}.zip")
            finally:
                try:
                    zip_path.unlink()
                except FileNotFoundError:
                    pass
            return
        if route != "/api/open":
            self._error("not found", 404)
            return
        origin = self.headers.get("Origin")
        if origin:
            try:
                if urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                    self._error("origin not allowed", 403)
                    return
            except Exception:
                self._error("bad origin", 403)
                return
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._error("expected application/json", 415)
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 65536:
                raise ValueError("invalid body length")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            path = str(body.get("path", "")).strip("/")
            app = str(body.get("app", "")).strip().lower()
            row = self._row(path)
            if not row:
                self._error("not found", 404)
                return
            target, converted = self._materialize(row)
            ok, message = launch_app(app, target)
            if not ok:
                self._error(message, 400)
                return
            self._json({
                "ok": True, "message": message, "app": app,
                "file": str(target), "converted": converted,
            })
        except Exception as exc:
            self._error("backend.openFailed", 500, str(exc))

    def do_GET(self):
        route, query = self._query()
        try:
            if route == "/" or route == "/index.html":
                self._send((WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8", cache="no-store")
                return
            if route.startswith("/static/"):
                name = route[len("/static/"):]
                if name in {"app.js", "style.css"}:
                    ctype = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/css; charset=utf-8"
                    self._send((WEB_DIR / name).read_bytes(), ctype, cache="no-store")
                    return
                if re.fullmatch(r"assets/[A-Za-z0-9._-]+", name):
                    path = WEB_DIR / name
                    if not path.is_file():
                        self._error("not found", 404)
                        return
                    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    self._send(path.read_bytes(), ctype, cache="no-store")
                    return
                self._error("not found", 404)
                return
            if route.startswith("/language/"):
                name = route[len("/language/"):]
                if not re.fullmatch(r"[a-z0-9_]+\.json", name):
                    self._error("not found", 404)
                    return
                path = WEB_DIR / "language" / name
                if not path.is_file():
                    self._error("not found", 404)
                    return
                self._send(path.read_bytes(), "application/json; charset=utf-8", cache="private, max-age=3600")
                return
            if route == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if route == "/api/update":
                self._json(dict(self.server.update_state))
                return
            if route == "/api/stats":
                connection = db_connect(self.server.db_path)
                try:
                    total, total_bytes = connection.execute("SELECT COUNT(*), COALESCE(SUM(unpacked),0) FROM files").fetchone()
                    dirs = connection.execute("SELECT COUNT(*) FROM dirs").fetchone()[0]
                    unresolved = connection.execute("SELECT COUNT(*) FROM files WHERE substr(path,1,12)='_unresolved/'").fetchone()[0]
                    scripts = connection.execute("SELECT COUNT(*) FROM files WHERE substr(path,1,8)='_script/'").fetchone()[0]
                    packages = [dict(row) for row in connection.execute("SELECT * FROM packages ORDER BY name")]
                    roots = [dict(row) for row in connection.execute("SELECT path,parent,file_count,bytes FROM dirs WHERE parent='' ORDER BY path")]
                    kinds = {row[0]: row[1] for row in connection.execute("SELECT kind,COUNT(*) FROM files GROUP BY kind")}
                finally:
                    connection.close()
                meta = self.server.fingerprint
                self._json(
                    {
                        "files": total,
                        "bytes": total_bytes,
                        "dirs": dirs,
                        "unresolved": unresolved,
                        "scripts": scripts,
                        "packages": packages,
                        "roots": roots,
                        "kinds": kinds,
                        "profile": meta.get("profile"),
                        "game_version": getattr(self.server, "profile", {}).get("tested_client"),
                        "version": self.server.version,
                        "platform": sys.platform,
                        "cache_mb": round(self.server.cache_limit / 1024 / 1024, 1),
                    }
                )
                return
            if route == "/api/list":
                path = (query.get("path", [""])[0] or "").strip("/").replace("\\", "/")
                sort_key = query.get("sort", ["name_asc"])[0]
                sort_expr = SORT_EXPRESSIONS.get(sort_key, SORT_EXPRESSIONS["name_asc"])
                kind_filter = (query.get("kind", [""])[0] or "").strip()
                if kind_filter not in KINDS:
                    kind_filter = ""
                offset = max(int(query.get("offset", ["0"])[0] or 0), 0)
                limit = min(max(int(query.get("limit", ["1500"])[0] or 1500), 1), 3000)
                connection = db_connect(self.server.db_path)
                try:
                    dirs = [
                        {"name": row["path"].rsplit("/", 1)[-1], "path": row["path"], "count": row["file_count"], "bytes": row["bytes"], "kind": "dir"}
                        for row in connection.execute("SELECT * FROM dirs WHERE parent=? ORDER BY path COLLATE NOCASE", (path,))
                    ]
                    if sort_key == "name_desc":
                        dirs.reverse()
                    elif sort_key in ("size_asc", "size_desc"):
                        dirs.sort(key=lambda item: (item["bytes"], item["path"]), reverse=sort_key == "size_desc")
                    where = "dir=?"
                    params = [path]
                    if kind_filter:
                        where += " AND kind=?"
                        params.append(kind_filter)
                    total_files = connection.execute(f"SELECT COUNT(*) FROM files WHERE {where}", params).fetchone()[0]
                    file_rows = connection.execute(
                        f"SELECT * FROM files WHERE {where} ORDER BY {sort_expr} LIMIT ? OFFSET ?",
                        params + [limit, offset],
                    ).fetchall()
                    kind_counts = {
                        row[0]: row[1]
                        for row in connection.execute("SELECT kind,COUNT(*) FROM files WHERE dir=? GROUP BY kind", (path,))
                    }
                    if path:
                        summary_row = connection.execute("SELECT file_count,bytes FROM dirs WHERE path=?", (path,)).fetchone()
                        dir_summary = {"files": summary_row[0], "bytes": summary_row[1]} if summary_row else {"files": 0, "bytes": 0}
                    else:
                        summary_row = connection.execute("SELECT COUNT(*),COALESCE(SUM(unpacked),0) FROM files").fetchone()
                        dir_summary = {"files": summary_row[0], "bytes": summary_row[1]}
                    files = [
                        {
                            "name": row["name"], "path": row["path"], "size": row["unpacked"],
                            "kind": row["kind"], "ext": row["ext"], "package": row["package"],
                            "signature": f"{row['signature']:08x}",
                        }
                        for row in file_rows
                    ]
                finally:
                    connection.close()
                crumbs = []
                current = ""
                for part in [p for p in path.split("/") if p]:
                    current = f"{current}/{part}" if current else part
                    crumbs.append({"name": part, "path": current})
                self._json({
                    "path": path, "dirs": dirs, "files": files, "breadcrumbs": crumbs,
                    "file_total": total_files, "offset": offset, "limit": limit,
                    "truncated": offset + len(files) < total_files,
                    "kind_counts": kind_counts, "kind_filter": kind_filter,
                    "dir_summary": dir_summary, "sort": sort_key,
                })
                return
            if route == "/api/search":
                query_text = (query.get("q", [""])[0] or "").strip()
                if not query_text:
                    self._error("empty query", 400)
                    return
                limit = min(max(int(query.get("limit", ["1500"])[0] or 1500), 1), 3000)
                offset = max(int(query.get("offset", ["0"])[0] or 0), 0)
                sort_key = query.get("sort", ["name_asc"])[0]
                sort_expr = SORT_EXPRESSIONS.get(sort_key, SORT_EXPRESSIONS["name_asc"])
                pattern = "%" + query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                connection = db_connect(self.server.db_path)
                try:
                    kind_counts = {
                        row[0]: row[1]
                        for row in connection.execute(
                            "SELECT kind,COUNT(*) FROM files WHERE lower(path) LIKE lower(?) ESCAPE '\\' GROUP BY kind",
                            (pattern,),
                        )
                    }
                    total_matches = sum(kind_counts.values())
                    rows = connection.execute(
                        f"SELECT name,path,unpacked,kind,ext,package,signature FROM files WHERE lower(path) LIKE lower(?) ESCAPE '\\' ORDER BY {sort_expr} LIMIT ? OFFSET ?",
                        (pattern, limit, offset),
                    )
                    files = [
                        {
                            "name": row["name"], "path": row["path"], "size": row["unpacked"],
                            "kind": row["kind"], "ext": row["ext"], "package": row["package"],
                            "signature": f"{row['signature']:08x}",
                        }
                        for row in rows
                    ]
                finally:
                    connection.close()
                self._json({
                    "query": query_text, "files": files, "sort": sort_key,
                    "total": total_matches, "offset": offset, "limit": limit,
                    "truncated": offset + len(files) < total_matches,
                    "kind_counts": kind_counts,
                })
                return
            if route == "/api/info":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                self._json(
                    {
                        "path": row["path"], "name": row["name"], "package": row["package"],
                        "package_path": row["package_path"], "signature": f"{row['signature']:08x}",
                        "offset": row["offset"], "packed": row["packed"], "unpacked": row["unpacked"],
                        "compression": row["compression"], "flags": f"{row['flags']:#06x}",
                        "packed_hash": f"{row['packed_hash']:08x}", "unpacked_hash": f"{row['unpacked_hash']:08x}",
                        "kind": row["kind"], "ext": row["ext"],
                    }
                )
                return
            if route == "/api/gmp":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() != ".gmp":
                    self._error("not a GMP file", 415)
                    return
                try:
                    data = self._gmp_data(row)
                except Exception as exc:
                    self._error(f"GMP parse failed: {exc}", 415)
                    return
                self._json(data)
                return
            if route == "/api/neox":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() not in (".gim", ".mtg", ".mesh"):
                    self._error("not a NeoX object file", 415)
                    return
                if (row["ext"] or "").lower() == ".mesh":
                    try:
                        payload = self._entry_payload(row)
                        parsed = mesh_parse.parse(payload)
                        if not parsed.get("packed"):
                            center, half = self._sibling_bounds(row["path"])
                            if center and half:
                                parsed = mesh_parse.denormalize(parsed, center, half)
                        data = parsed
                    except Exception as exc:
                        self._error(f"NeoX parse failed: {exc}", 415)
                        return
                    data.update(
                        {
                            "path": row["path"], "package": row["package"],
                            "kind": row["kind"], "ext": row["ext"], "size": row["unpacked"],
                            "material": material_flags([]),
                        }
                    )
                    self._json(data)
                    return
                try:
                    payload = self._entry_payload(row)
                    data = neox_object.parse(payload)
                except Exception as exc:
                    self._error(f"NeoX parse failed: {exc}", 415)
                    return
                data["material"] = material_flags(data.get("objects", []))
                material_objects = data.get("objects", [])
                textures = []
                for obj in data.get("objects", []):
                    for name, value in obj.items():
                        if (
                            isinstance(value, str)
                            and value
                            and value.lower().endswith((".tga", ".dds", ".kta", ".png"))
                        ):
                            textures.append({"slot": name, "path": value})
                if textures:
                    data["textures"] = textures
                mesh_ref = None
                for obj in data.get("objects", []):
                    value = obj.get("Mesh")
                    if isinstance(value, str) and value:
                        mesh_ref = value
                        break
                if textures:
                    for texture in textures:
                        texture["resolved"] = self._resolve_ref(row["path"], texture["path"])
                if mesh_ref:
                    mesh_path = self._resolve_ref(row["path"], mesh_ref)
                    data["mesh"] = mesh_path or mesh_ref.replace("\\", "/")
                else:
                    sibling = str(Path(row["path"]).with_suffix(".mesh")).replace("\\", "/")
                    if self._row(sibling):
                        mesh_path = sibling
                        data["mesh"] = sibling
                    else:
                        mesh_path = None
                tree_materials = []
                if (row["ext"] or "").lower() != ".mtg":
                    mtg_sibling = str(Path(row["path"]).with_suffix(".mtg")).replace("\\", "/")
                    mtg_row = self._row(mtg_sibling)
                    if mtg_row and (mtg_row["ext"] or "").lower() == ".mtg":
                        try:
                            mtg_data = neox_object.parse(self._entry_payload(mtg_row))
                            data["material"] = material_flags(mtg_data.get("objects", []))
                            material_objects = mtg_data.get("objects", [])
                            tree_materials = material_tree(mtg_data, lambda value: self._resolve_ref(mtg_sibling, value))
                            if not textures:
                                for obj in mtg_data.get("objects", []):
                                    for name, value in obj.items():
                                        if (
                                            isinstance(value, str)
                                            and value
                                            and value.lower().endswith((".tga", ".dds", ".kta", ".png"))
                                        ):
                                            textures.append({"slot": name, "path": value, "resolved": self._resolve_ref(mtg_sibling, value)})
                        except Exception:
                            pass
                    if textures:
                        data["textures"] = textures
                if tree_materials:
                    data["materials"] = tree_materials
                if (row["ext"] or "").lower() == ".mtg":
                    tree_materials = material_tree(data, lambda value: self._resolve_ref(row["path"], value))
                    if tree_materials:
                        data["materials"] = tree_materials
                if textures:
                    resolved = [item.get("resolved") for item in textures if item.get("resolved")]
                    if resolved:
                        data["material"]["textures"] = resolved
                        groups = material_groups(material_objects, textures)
                        if groups and not data.get("materials"):
                            data["materials"] = groups
                if mesh_path and not data.get("mesh_data"):
                    try:
                        mesh_row = self._row(mesh_path)
                        if mesh_row:
                            mesh_payload = self._entry_payload(mesh_row)
                            parsed = mesh_parse.parse(mesh_payload)
                            if not parsed.get("packed"):
                                center, half = self._gim_bounds(data)
                                if center and half:
                                    parsed = mesh_parse.denormalize(parsed, center, half)
                            if parsed.get("joints") and (query.get("pose", ["1"])[0] or "1") != "0":
                                anim_path = self._mesh_animation_path(mesh_path)
                                anim_row = self._row(anim_path) if anim_path else None
                                animation = None
                                if anim_path:
                                    data["animation_path"] = anim_path
                                    raw_path = Path(anim_path).with_suffix(".rawanimation").as_posix()
                                    raw_row = self._row(raw_path)
                                    data["raw_animation_path"] = raw_path
                                    data["raw_animation_found"] = bool(raw_row)
                                    if raw_row:
                                        try:
                                            skeleton = neox_pose.parse_skeleton(mesh_payload)
                                            animation = neox_anim.build_animation(
                                                mesh_payload, parsed, skeleton,
                                                self._entry_payload(raw_row),
                                                self.server.cache_root / "anim_frames",
                                                Path(__file__).parent / "acl_decode.exe",
                                                decoder_v10=Path(__file__).parent / "acl_decode_v10.exe",
                                                cached_animation=self._entry_payload(anim_row),
                                            )
                                            if not animation:
                                                data["animation_error"] = "build returned none"
                                        except Exception as exc:
                                            animation = None
                                            data["animation_error"] = f"{type(exc).__name__}: {exc}"
                                if animation:
                                    data["animation"] = animation
                                elif anim_row:
                                    try:
                                        posed, pose_report = neox_pose.apply_cached_pose(
                                            parsed, mesh_payload, self._entry_payload(anim_row)
                                        )
                                        if posed:
                                            old = parsed["bounds"]
                                            new = posed["bounds"]
                                            old_span = [max(1e-5, old["max"][axis] - old["min"][axis]) for axis in range(3)]
                                            new_span = [new["max"][axis] - new["min"][axis] for axis in range(3)]
                                            if all(0.5 <= new_span[axis] / old_span[axis] <= 3 for axis in range(3)):
                                                parsed = posed
                                                data["pose"] = pose_report
                                    except Exception:
                                        pass
                            data["mesh_data"] = parsed
                            if data.get("materials"):
                                mapping = submesh_materials(data)
                                if mapping:
                                    for index, sub in enumerate(parsed.get("submeshes", [])):
                                        if index in mapping:
                                            sub["material_index"] = mapping[index]
                            if textures:
                                data["texture_images"] = [
                                    t["resolved"] for t in textures if t.get("resolved")
                                ]
                    except Exception as exc:
                        data["mesh_error"] = str(exc)
                        data["mesh_data"] = None
                data.update(
                    {
                        "path": row["path"], "package": row["package"],
                        "kind": row["kind"], "ext": row["ext"], "size": row["unpacked"],
                    }
                )
                self._json(data)
                return
            if route == "/api/mtg":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() != ".mtg":
                    self._error("not an MTG file", 415)
                    return
                try:
                    data = neox_object.parse(self._entry_payload(row))
                except Exception as exc:
                    self._error(f"MTG parse failed: {exc}", 415)
                    return
                resources = []
                seen = set()
                for obj in data.get("objects", []):
                    for value in obj.values():
                        if not isinstance(value, str) or not value:
                            continue
                        suffix = Path(value.replace("\\", "/")).suffix.lower()
                        if suffix not in MTG_RESOURCE_EXT:
                            continue
                        resolved = self._resolve_ref(row["path"], value)
                        resource_path = (resolved or value.replace("\\", "/")).strip("/")
                        key = resource_path.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        ref_row = self._row(resource_path)
                        ref_ext = (ref_row["ext"] if ref_row else Path(resource_path).suffix).lower()
                        resources.append({
                            "path": resource_path,
                            "ext": ref_ext,
                            "kind": ref_row["kind"] if ref_row else "",
                        })
                total = len(resources)
                shown = resources[:5] if total > 6 else resources[:6]
                self._json({"total": total, "resources": shown})
                return
            if route == "/api/xn":
                client = (self.client_address[0] or "").lower()
                try:
                    address = ipaddress.ip_address(client)
                    mapped = getattr(address, "ipv4_mapped", None)
                    allowed = address.is_loopback or (mapped is not None and mapped.is_loopback)
                except ValueError:
                    allowed = client == "localhost"
                if not allowed:
                    self._error("XN preview is loopback-only", 403)
                    return
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() != ".xn":
                    self._error("not an XN file", 415)
                    return
                if (query.get("decompile", ["0"])[0] or "") == "1":
                    self._json(self._xn_source(row))
                    return
                if (query.get("disasm", ["0"])[0] or "") == "1":
                    self._json(self._xn_disasm(row, (query.get("func", [""])[0] or "")))
                    return
                try:
                    module = (query.get("module", [""])[0] or "").strip()
                    data = self._xn_data(row, module[:200])
                except Exception as exc:
                    self._error(f"XN decrypt failed: {exc}", 415)
                    return
                self._json(data)
                return
            if route == "/api/inspect":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                data = resource_inspect.inspect(payload, row["ext"] or "", row["name"] or "")
                data.update(
                    {
                        "path": row["path"], "package": row["package"],
                        "kind": row["kind"], "ext": row["ext"],
                    }
                )
                self._json(data)
                return
            if route == "/api/bank":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                if (row["ext"] or "").lower() != ".bnk":
                    self._error("not a BNK file", 415)
                    return
                payload = self._entry_payload(row)
                try:
                    bank = bnk_parse.parse_bank(payload)
                except bnk_parse.BankFormatError as exc:
                    self._error(str(exc), 415)
                    return
                data = bank.to_dict()
                data.update(
                    {
                        "path": row["path"], "name": row["name"], "package": row["package"],
                        "size": row["unpacked"],
                    }
                )
                self._json(data)
                return
            if route == "/api/image":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                try:
                    data, ctype = self._ensure_png(row)
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                suffix = ".png" if ctype == "image/png" else ".jpg" if ctype == "image/jpeg" else Path(row["path"]).suffix
                self._send(data, ctype, filename=Path(row["path"]).with_suffix(suffix).name)
                return
            if route == "/api/thumb":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                width = min(max(int(query.get("w", ["256"])[0] or 256), 32), 1024)
                try:
                    thumb = self._thumbnail(row, width)
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                self._send(thumb, "image/png")
                return
            if route == "/api/snippet":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                chars = min(max(int(query.get("chars", ["480"])[0] or 480), 80), 2000)
                lines = min(max(int(query.get("lines", ["9"])[0] or 9), 1), 30)
                try:
                    text, truncated = self._text_snippet(row, chars, lines)
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                self._json({"path": row["path"], "text": text, "truncated": truncated})
                return
            if route == "/api/audio":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                try:
                    data, ctype, ext = self._audio(row, query.get("media", [None])[0])
                except ValueError as exc:
                    self._error(str(exc), 415)
                    return
                filename = f"{Path(row['path']).stem}.{ext}"
                if query.get("media", [None])[0]:
                    filename = f"{Path(row['path']).stem}_{query['media'][0]}.{ext}"
                self._send(data, ctype, filename=filename, download=query.get("download", ["0"])[0] == "1")
                return
            if route == "/api/text":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                head = payload[: 2 * 1024 * 1024]
                if b"\x00" in head[:4096]:
                    self._error("binary data; use hex/download", 415)
                    return
                text = None
                for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
                    try:
                        text = head.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                if text is None:
                    self._error("cannot decode text", 415)
                    return
                self._send(text.encode("utf-8"), "text/plain; charset=utf-8")
                return
            if route == "/api/hex":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                head = payload[:4096]
                lines = []
                for offset in range(0, len(head), 16):
                    chunk = head[offset:offset + 16]
                    hexpart = " ".join(f"{b:02x}" for b in chunk)
                    asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                    lines.append(f"{offset:08x}  {hexpart:<47}  {asciipart}")
                self._json({"size": len(payload), "shown": len(head), "lines": lines, "truncated": len(payload) > len(head)})
                return
            if route == "/api/video":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                ext = (row["ext"] or "").lower()
                self._send(payload, MIME.get(ext, "video/mp4"), filename=row["name"])
                return
            if route == "/api/convert":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                image_format = query.get("img", ["png"])[0] or "png"
                audio_format = query.get("aud", ["wav"])[0] or "wav"
                if image_format not in IMAGE_FORMATS:
                    self._error("invalid image format", 400)
                    return
                if audio_format not in AUDIO_FORMATS:
                    self._error("invalid audio format", 400)
                    return
                try:
                    entries = self._convert_entries(row, image_format, audio_format)
                except Exception as exc:
                    self._error(f"convert failed: {exc}", 415)
                    return
                arc, data = entries[0]
                suffix = Path(arc).suffix.lower()
                self._send(data, MIME.get(suffix, "application/octet-stream"), filename=Path(arc).name, download=True)
                return
            if route == "/api/download":
                path = (query.get("path", [""])[0] or "").strip("/")
                row = self._row(path)
                if not row:
                    self._error("not found", 404)
                    return
                payload = self._entry_payload(row)
                self._send(payload, "application/octet-stream", filename=row["name"], download=True)
                return
            self._error("not found", 404)
        except BrokenPipeError:
            pass
        except (ConnectionAbortedError, ConnectionResetError):
            pass
        except Exception as exc:
            import traceback

            traceback.print_exc()
            try:
                self._error(f"internal error: {exc}", 500)
            except Exception:
                pass


def refresh_update(server: BrowserServer) -> None:
    try:
        info = update_check.check(
            server.cache_root / update_check.CACHE_NAME,
            server.version,
            repo=server.update_repo,
            proxy=server.update_proxy,
        )
    except Exception:
        info = update_check.initial_state(server.version, server.update_repo, False, "error")
    with BrowserServer._update_lock:
        server.update_state = info
    if info.get("available"):
        url = update_check.releases_url(server.update_repo)
        print("你正在运行过时的 NPK-X! 请前往Github下载最新版本: " + url, flush=True)
        print("You are running an outdated NPK-X! Please download the latest version from GitHub: " + url, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Eggy Party NPK browser")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--path-dict", "--paths", dest="path_dict", type=Path, default=None)
    parser.add_argument("--key-hex", type=str, default=None)
    parser.add_argument("--tables", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--cache-limit-mb", type=int, default=2048)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--rebuild-catalog", action="store_true")
    parser.add_argument("--refresh-pathdict", action="store_true")
    parser.add_argument("--force", action="store_true", help="clear cache and rebuild from the local client")
    parser.add_argument("--no-update-check", action="store_true", help="disable the GitHub release check")
    parser.add_argument("--update-repo", default=None, help="GitHub repository in owner/name form")
    parser.add_argument("--proxy", default=None, help="proxy URL used by the release check")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    repo_value = args.update_repo or os.environ.get("NPKX_UPDATE_REPO") or update_check.DEFAULT_REPO
    update_repo = update_check.normalize_repo(repo_value)
    if not update_repo:
        parser.error("--update-repo must look like owner/name")
    current_version = update_check.read_version(PACKAGE_ROOT)
    check_update = not args.no_update_check and bool(current_version)
    update_state = update_check.initial_state(
        current_version,
        update_repo,
        check_update,
        "pending" if check_update else "disabled",
    )
    if not args.no_update_check and not current_version:
        print("warning: VERSION file not found; update check disabled", file=sys.stderr)

    game_root = E.find_game_root(args.game_root)
    packages = sorted(game_root.glob("*.npk"))
    if not packages:
        print(f"no NPK files under {game_root}", file=sys.stderr)
        return 4
    cache_root = args.cache_dir or default_cache_dir()
    if args.force:
        try:
            cache_clean.clear_cache(cache_root, game_root, log=print)
        except (OSError, ValueError) as exc:
            print(f"ERROR: could not clear cache: {exc}", file=sys.stderr)
            return 2
    cache_root.mkdir(parents=True, exist_ok=True)

    profile_path = args.profile
    startup_progress.begin("profile", args.profile.name if profile_path else "auto from client")
    if profile_path is not None and not Path(profile_path).is_file():
        print(f"warning: profile not found: {profile_path}; generating from local client", file=sys.stderr)
        profile_path = None
    if profile_path is None:
        generated_profile = cache_root / "mode3_profile.json"
        try:
            generated_data = rebuild_data.build_profile(game_root, "pathdict.txt.gz")
            tmp_profile = generated_profile.with_name(generated_profile.name + ".tmp")
            tmp_profile.write_text(json.dumps(generated_data, ensure_ascii=False, indent=2) + "\n", "utf-8")
            tmp_profile.replace(generated_profile)
            startup_progress.note(f"profile={generated_data.get('name')} (generated from local client)")
            profile_path = generated_profile
        except (OSError, ValueError) as exc:
            print(f"warning: could not generate profile: {exc}", file=sys.stderr)
    startup_progress.finish("profile", profile_path.name if profile_path else "built-in defaults")
    if profile_path is None:
        candidate = E.PACKAGE_ROOT / "data" / "mode3_profile.json"
        if candidate.is_file():
            profile_path = candidate
    if profile_path is not None and not Path(profile_path).is_file():
        print("warning: generated profile unavailable; using built-in defaults", file=sys.stderr)
        profile_path = None
    profile = N.load_profile(profile_path)
    key = N.profile_key(profile, args.key_hex)
    seed = N.profile_seed(profile)
    tables = args.tables or profile.get("tables_file")
    if tables:
        tables_path = Path(tables)
        if not tables_path.is_absolute() and profile_path:
            tables_path = Path(profile_path).parent / tables_path
        N.install_tables(tables_path)
    path_dict = None if args.refresh_pathdict else E.find_path_dict(args.path_dict)
    startup_progress.begin("pathdict", "checking cache")
    if path_dict is None:
        generated = cache_root / "pathdict.generated.txt.gz"
        try:
            sources = list(game_root.glob("*.npk")) + [
                path for path in (game_root / "ppk").glob("*") if path.is_file()
            ]
            newest = max((path.stat().st_mtime for path in sources), default=0)
            if generated.is_file() and generated.stat().st_mtime >= newest:
                path_dict = generated
                startup_progress.finish("pathdict", "generated cache")
            else:
                with startup_progress.ticker("pathdict", "rebuilding from PPK shards"):
                    data = rebuild_data.find_path_dictionary(game_root, log=startup_progress.note)
                    path_dict = rebuild_data.write_path_dictionary(data, generated, log=startup_progress.note)
                startup_progress.finish("pathdict", "rebuilt")
        except (OSError, FileNotFoundError) as exc:
            startup_progress.finish("pathdict", "unavailable")
            print(f"warning: could not rebuild the path dictionary: {exc}", file=sys.stderr)
            print("warning: entries will use hash names", file=sys.stderr)
    else:
        startup_progress.finish("pathdict", path_dict.name)
    resolver = N.PathResolver.from_file(path_dict, seed) if path_dict else N.PathResolver([], seed)
    db_path = cache_root / "catalog.sqlite"
    print(f"game={game_root}")
    print(f"profile={profile.get('name', 'custom')} key={key.hex()}")
    print(f"cache={cache_root}")
    try:
        meta = ensure_catalog(db_path, game_root, profile, key, seed, path_dict, packages, resolver, args.rebuild_catalog)
    except (N.Mode3FormatError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    data_seed = E.PACKAGE_ROOT / "data" / "xn_names.json"
    if not data_seed.is_file():
        data_seed = E.PACKAGE_ROOT / "dataa" / "xn_names.json"
    opmap_path, names_path = xn_bootstrap.ensure(
        db_path,
        cache_root,
        data_seed if data_seed.is_file() else None,
        600.0,
        game_root,
        startup_progress.note,
        progress=startup_progress,
    )
    xn_parse.OPMAP_PATH = str(opmap_path)
    xn_parse._OPMAP_CACHE = None
    startup_progress.begin("artifacts", "preparing cache dirs")
    artifacts_root = cache_root / "artifacts"
    artifacts = artifacts_root / hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()[:16]
    artifacts.mkdir(parents=True, exist_ok=True)
    for old in artifacts_root.iterdir() if artifacts_root.exists() else []:
        if old.is_dir() and old.name != artifacts.name:
            shutil.rmtree(old, ignore_errors=True)
    startup_progress.finish("artifacts", artifacts.name)
    server_class = type(
        "ConfiguredBrowserServer",
        (BrowserServer,),
        {
            "db_path": db_path,
            "cache_root": cache_root,
            "artifacts": artifacts,
            "cache_limit": args.cache_limit_mb * 1024 * 1024,
            "vgmstream": E.find_vgmstream(),
            "fingerprint": meta,
            "profile": profile,
            "xn_names_path": str(names_path),
            "xn_opmap_path": str(opmap_path),
            "version": current_version,
            "update_state": update_state,
            "update_repo": update_repo,
            "update_proxy": args.proxy,
        },
    )
    server = None
    for port in range(args.port, args.port + 20):
        try:
            server = server_class((args.host, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("no free port", file=sys.stderr)
        return 6
    url = f"http://{args.host}:{server.server_port}/"
    print(f"NPK Browser: {url}")
    print("Press Ctrl+C to stop.")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: server is listening on a non-loopback address", file=sys.stderr)
    if check_update:
        threading.Thread(target=refresh_update, args=(server,), daemon=True).start()
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
