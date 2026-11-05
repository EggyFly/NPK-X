#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构化预览解析器: .bin/.eggimonbin/.meta/.efx/.scn/.trk/.sfxmusic/.postprocess.

格式结论 (全部来自本目录样本实测):
  .bin        zstd+msgpack 双层 / 裸 msgpack / bindict 表 / 高度图(u32 w, u32 h, int16 高程) / zlib / 全零
  .eggimonbin msgpack, 顶层 uuid 键
  .meta       NXSC 头 0x28 + 可选 zstd 帧 + 哈希记录表 (记录内 16 字节处恒为 0x27 标记), 非 msgpack
  .efx        @BIN 头 + [u8 len+16][字符串] 属性流; 属性名/value 明文, 其余为哈希记录未解
  .scn        NeoX c159410d 容器, 值类型在 gim/mtg 基础上多 4=i32 与 7=f64 (PixelFormat 后接 ProbeOffset 佐证)
  .trk        NTRK 相机轨道: 32 字节名字段 + u32 帧数 + 帧数-1 个 f32 时间 + 6 字节通道标志 + 位置/四元数块
  .sfxmusic   XML <FxGroup>
  .postprocess XML <PostProcess>
"""

from __future__ import annotations

import re
import struct
import array
import xml.etree.ElementTree
import zlib

import zstandard

import bindict_decode
import gmp_parse
import neox_object

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
NEOX_MAGIC = 0xC159410D
MAX_NODES = 12000
MAX_DEPTH = 20
MAX_LIST = 300
MAX_STR = 400
MAX_TEXT = 160 * 1024
MAX_RECORDS = 400
MAX_OBJECTS = 1200
MAX_PAIRS = 500
MAX_REFS = 80
MAX_TIMES = 4000

REF_RE = re.compile(
    r"[A-Za-z0-9_./\\-]{2,240}\.(?:kta|dds|ktx|png|jpe?g|tga|bmp|mesh|skeleton|mtg|gim|"
    r"surf|shader|sfx|efx|animation|rawanimation|animgraph|csb|mp4|webm)\b",
    re.IGNORECASE,
)
OBJECT_NAME_RE = re.compile(r"^[A-Za-z_]\w*\(\d+\)$")


class StructError(ValueError):
    pass


SUPPORTED_EXTS = {".bin", ".eggimonbin", ".meta", ".efx", ".scn", ".trk", ".sfxmusic", ".postprocess"}


def _text(value: bytes) -> str:
    return value.decode("utf-8", "replace")


def _hex_head(data: bytes, limit: int = 4096) -> list[str]:
    lines = []
    head = data[:limit]
    for offset in range(0, len(head), 16):
        chunk = head[offset:offset + 16]
        hx = " ".join(f"{byte:02x}" for byte in chunk)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hx:<47}  {ascii_part}")
    return lines


def _refs(source: str) -> list[str]:
    out = []
    seen = set()
    for match in REF_RE.finditer(source):
        value = match.group().replace("\\", "/")
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= MAX_REFS:
            break
    return out


class _Budget:
    def __init__(self):
        self.nodes = 0

    def take(self) -> bool:
        self.nodes += 1
        return self.nodes <= MAX_NODES


def _sanitize(value, budget: _Budget, depth: int = 0):
    if not budget.take() or depth > MAX_DEPTH:
        return {"__cut__": True}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8")
            if "\x00" not in text:
                return text[:MAX_STR]
        except UnicodeDecodeError:
            pass
        return f"<binary {len(value)} bytes>"
    if isinstance(value, str):
        return value[:MAX_STR]
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:MAX_LIST]:
            if isinstance(key, bytes):
                try:
                    key = key.decode("utf-8")
                except UnicodeDecodeError:
                    key = key.hex()
            out[str(key)[:MAX_STR]] = _sanitize(item, budget, depth + 1)
        if len(value) > MAX_LIST:
            out["__more__"] = len(value) - MAX_LIST
        return out
    if isinstance(value, (list, tuple)):
        out = [_sanitize(item, budget, depth + 1) for item in list(value)[:MAX_LIST]]
        if len(value) > MAX_LIST:
            out.append({"__more__": len(value) - MAX_LIST})
        return out
    return str(value)[:MAX_STR]


def _stats(value) -> dict:
    counts = {"nodes": 0, "depth": 0, "strings": 0, "dicts": 0, "lists": 0}

    def walk(item, depth):
        counts["nodes"] += 1
        counts["depth"] = max(counts["depth"], depth)
        if isinstance(item, dict):
            counts["dicts"] += 1
            for key, child in list(item.items())[:64]:
                walk(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            counts["lists"] += 1
            for child in list(item)[:64]:
                walk(child, depth + 1)
        elif isinstance(item, str):
            counts["strings"] += 1

    walk(value, 1)
    return counts


def _decode_msgpack(data: bytes) -> dict:
    value, end = gmp_parse._decode(data, 0)
    if end != len(data):
        raise ValueError(f"msgpack trailing bytes: {end}/{len(data)}")
    budget = _Budget()
    tree = _sanitize(value, budget)
    return {
        "tree": tree,
        "top_keys": list(tree.keys())[:64] if isinstance(tree, dict) else None,
        "stats": _stats(value),
    }


def _try_heightmap(data: bytes) -> dict | None:
    if len(data) < 10:
        return None
    width, height = struct.unpack_from("<II", data, 0)
    if not 1 <= width <= 8192 or not 1 <= height <= 8192:
        return None
    if len(data) != 8 + width * height * 2:
        return None
    values = array.array("h")
    values.frombytes(data[8:8 + width * height * 2])
    return {
        "kind": "heightmap",
        "width": width,
        "height": height,
        "min": min(values),
        "max": max(values),
        "values": values[:262144].tolist(),
        "values_truncated": len(values) > 262144,
    }


def parse_bin(data: bytes) -> dict:
    if not any(data):
        return {"kind": "empty", "layers": [], "note": "inspect.binEmpty"}
    if data[:4] == ZSTD_MAGIC:
        inner = zstandard.ZstdDecompressor().decompressobj().decompress(data)
        try:
            tree_data = _decode_msgpack(inner)
        except (gmp_parse.MsgpackError, ValueError, RecursionError, MemoryError):
            return {
                "kind": "zstd",
                "layers": ["zstd"],
                "inner_size": len(inner),
                "hex_head": _hex_head(inner),
                "note": "inspect.binZstdOnly",
            }
        tree_data["kind"] = "msgpack"
        tree_data["layers"] = ["zstd", "msgpack"]
        tree_data["packed_size"] = len(data)
        return tree_data
    marker = data[0]
    if 0x80 <= marker <= 0x9F or marker in (0xDE, 0xDF, 0xDC, 0xDD):
        try:
            tree_data = _decode_msgpack(data)
        except (gmp_parse.MsgpackError, ValueError, RecursionError, MemoryError):
            tree_data = None
        if tree_data:
            tree_data["kind"] = "msgpack"
            tree_data["layers"] = ["msgpack"]
            return tree_data
    try:
        section = bindict_decode.decode_section(data)
    except (ValueError, IndexError, struct.error, RecursionError, MemoryError, TypeError):
        section = None
    if section and section.get("top_keys") is not None:
        rows = section.get("rows") or []
        budget = _Budget()
        budget.nodes = MAX_NODES - 4000
        return {
            "kind": "bindict",
            "layers": ["bindict"],
            "top_keys": list(section.get("top_keys") or [])[:64],
            "row_count": section.get("row_count"),
            "unit_count": section.get("unit_count"),
            "string_count": len(section.get("strings") or []),
            "strings": (section.get("strings") or [])[:200],
            "rows": _sanitize(rows, budget),
            "stats": _stats(rows),
        }
    heightmap = _try_heightmap(data)
    if heightmap:
        return heightmap
    if data[:2] == b"\x78\x9c" or data[:2] == b"\x78\x01" or data[:2] == b"\x78\xda":
        try:
            inner = zlib.decompress(data)
        except zlib.error:
            inner = None
        if inner is not None:
            return {
                "kind": "zlib",
                "layers": ["zlib"],
                "inner_size": len(inner),
                "hex_head": _hex_head(inner),
                "note": "inspect.binZlib",
            }
    return {
        "kind": "unknown",
        "layers": [],
        "hex_head": _hex_head(data),
        "note": "inspect.binUnknown",
    }


def parse_eggimonbin(data: bytes) -> dict:
    tree_data = _decode_msgpack(data)
    tree_data["kind"] = "msgpack"
    tree_data["layers"] = ["msgpack"]
    return tree_data


def parse_meta(data: bytes) -> dict:
    if data[:4] != b"NXSC":
        raise StructError("not a NXSC meta")
    hash_a, ver, flag = struct.unpack_from("<IHH", data, 4)
    guid = data[0x0C:0x14].hex()
    schema_id = struct.unpack_from("<I", data, 0x14)[0]
    size_field = struct.unpack_from("<I", data, 0x24)[0]
    body = data[0x28:]
    compressed = False
    if body[:4] == ZSTD_MAGIC:
        inner = zstandard.ZstdDecompressor().decompressobj().decompress(body)
        compressed = True
    else:
        inner = body
    result = {
        "kind": "meta",
        "hash": f"{hash_a:08x}",
        "version": ver,
        "flag": flag,
        "guid": guid,
        "schema_id": f"{schema_id:08x}",
        "size_field": size_field & 0x7FFFFFFF,
        "size_flag": bool(size_field & 0x80000000),
        "compressed": compressed,
        "body_size": len(body),
        "inner_size": len(inner),
    }
    records = None
    for stride in range(60, 73):
        if len(inner) < stride or len(inner) % stride:
            continue
        count = len(inner) // stride
        if count > 4096:
            continue
        marks = sum(1 for index in range(count) if inner[index * stride + 16] == 0x27)
        if marks >= count * 0.9:
            records = (stride, count)
            break
    if records:
        stride, count = records
        rows = []
        for index in range(min(count, MAX_RECORDS)):
            chunk = inner[index * stride:(index + 1) * stride]
            rows.append({
                "hash": chunk[:8].hex(),
                "flags": chunk[8:16].hex(),
                "tail": chunk[17:].hex(),
            })
        result["record_stride"] = stride
        result["record_count"] = count
        result["records"] = rows
        result["records_truncated"] = count > MAX_RECORDS
    else:
        result["hex_head"] = _hex_head(inner)
        result["note"] = "inspect.metaNoRecords"
    return result


def _efx_strings(data: bytes) -> tuple[list, float]:
    strings = []
    pos = 9
    covered = 0
    while pos < len(data):
        byte = data[pos]
        if 0x10 <= byte < 0xC0:
            length = byte - 16
            chunk = data[pos + 1:pos + 1 + length]
            if len(chunk) == length:
                try:
                    text = chunk.decode("utf-8")
                except UnicodeDecodeError:
                    text = None
                if text is not None and not any(ord(ch) < 9 or 11 <= ord(ch) < 32 for ch in text):
                    strings.append({"offset": pos, "value": text[:MAX_STR], "next": pos + 1 + length})
                    covered += length + 1
                    pos += 1 + length
                    continue
        pos += 1
    ratio = covered / len(data) if data else 0.0
    return strings, ratio


VALUE_LIKE_RE = re.compile(r"^[-+.:0-9]|[/\\]|^(?:true|false|TRUE|FALSE|yes|no|null|none)$")


def _efx_pairs(strings: list) -> list:
    pairs = []
    index = 0
    while index < len(strings) - 1:
        current, following = strings[index], strings[index + 1]
        if following["offset"] != current["next"] or len(current["value"]) > 80:
            index += 1
            continue
        if OBJECT_NAME_RE.match(current["value"]) or VALUE_LIKE_RE.match(current["value"]):
            index += 1
            continue
        if len(pairs) < MAX_PAIRS:
            pairs.append([current["value"], following["value"]])
        index += 2
    return pairs


def parse_efx(data: bytes) -> dict:
    if not data.startswith(b"@BIN"):
        raise StructError("not an @BIN efx")
    version = data[4]
    declared = struct.unpack_from("<I", data, 5)[0]
    strings, coverage = _efx_strings(data)
    objects = [item["value"] for item in strings if OBJECT_NAME_RE.match(item["value"])]
    source = "\n".join(item["value"] for item in strings)
    pairs = _efx_pairs(strings)
    result = {
        "kind": "efx",
        "version": f"0x{version:02x}",
        "declared": declared,
        "string_count": len(strings),
        "coverage": round(coverage * 100, 1),
        "objects": objects[:200],
        "pairs": pairs,
        "strings": [item["value"] for item in strings[:MAX_PAIRS * 2]],
        "references": _refs(source),
        "warnings": ["inspect.efxHashRecords"] if coverage < 0.9 else [],
    }
    if not strings:
        result["hex_head"] = _hex_head(data)
    return result


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _read_value_ext(data: bytes, pos: int, typ: int):
    if typ == 4:
        return struct.unpack_from("<i", data, pos)[0], pos + 4
    if typ == 7:
        return struct.unpack_from("<d", data, pos)[0], pos + 8
    return neox_object._read_value(data, pos, typ)


def parse_scn(data: bytes) -> dict:
    if len(data) < 13 or int.from_bytes(data[0:4], "big") != NEOX_MAGIC:
        raise StructError("not a NeoX container")
    declared = int.from_bytes(data[4:8], "little")
    pos = 12
    count, pos = _read_varint(data, pos)
    table = []
    for _ in range(count):
        end = data.find(b"\x00", pos)
        if end < 0:
            raise ValueError("unterminated string")
        table.append(_text(data[pos:end]))
        pos = end + 1
    count, pos = _read_varint(data, pos)
    fields = []
    for _ in range(count):
        end = data.find(b"\x00", pos)
        if end < 0:
            raise ValueError("unterminated field")
        fields.append(_text(data[pos:end]))
        pos = end + 1
    blob = data[pos:]
    obj_count, vpos = _read_varint(blob, 8)
    meta = []
    try:
        for _ in range(obj_count):
            x, vpos = _read_varint(blob, vpos)
            y, vpos = _read_varint(blob, vpos)
            meta.append([x, y])
    except (IndexError, ValueError):
        raise StructError("corrupt object meta table")
    objects = []
    warnings = []
    types_used = {}
    stop_reason = None
    for index in range(obj_count):
        if len(objects) >= MAX_OBJECTS:
            stop_reason = "object cap"
            warnings.append("inspect.scnObjectCap")
            break
        try:
            prop_count, vpos = _read_varint(blob, vpos)
            props = {}
            for _ in range(prop_count):
                fid, vpos = _read_varint(blob, vpos)
                typ, vpos = _read_varint(blob, vpos)
                types_used[typ] = types_used.get(typ, 0) + 1
                value, vpos = _read_value_ext(blob, vpos, typ)
                props[fields[fid] if fid < len(fields) else f"field{fid}"] = value
            if blob[vpos:vpos + 2] != b"\x01\x00":
                stop_reason = f"object {index} terminator mismatch at blob+{vpos}"
                warnings.append("inspect.scnStopEarly")
                break
            vpos += 2
        except (IndexError, ValueError, struct.error) as exc:
            stop_reason = f"object {index}: {type(exc).__name__}: {exc}"
            warnings.append("inspect.scnStopEarly")
            break
        objects.append(props)
    else:
        stop_reason = None
    budget = _Budget()
    sanitized = _sanitize(objects, budget)
    source = "\n".join(
        str(value) for obj in objects for value in obj.values() if isinstance(value, str)
    )
    if stop_reason:
        source += "\n" + _text(data)
    return {
        "kind": "scn",
        "declared": declared,
        "table": table[:200],
        "table_count": len(table),
        "fields": fields[:400],
        "field_count": len(fields),
        "object_count": obj_count,
        "parsed_count": len(objects),
        "objects": sanitized,
        "meta_sample": meta[:32],
        "types": {str(key): value for key, value in sorted(types_used.items())},
        "references": _refs(source),
        "warnings": warnings,
        "stop_reason": stop_reason,
    }


def _trk_block(body: bytes, offset: int, floats: int, quat: bool) -> list | None:
    if offset + floats * 4 > len(body):
        return None
    values = struct.unpack_from("<" + "f" * floats, body, offset)
    if quat:
        like = sum(1 for value in values if abs(value) <= 1.001)
        if floats and like / floats < 0.9:
            return None
    return [round(value, 4) for value in values]


def parse_trk(data: bytes) -> dict:
    if data[:4] != b"NTRK":
        raise StructError("not an NTRK track")
    ver_a, ver_b = struct.unpack_from("<HH", data, 4)
    name = _text(data[10:42].split(b"\x00")[0])
    count = struct.unpack_from("<I", data, 0x30)[0]
    pos = 0x36
    times = []
    prev = -1e30
    while pos + 4 <= len(data) and len(times) < min(count, MAX_TIMES):
        value = struct.unpack_from("<f", data, pos)[0]
        if value <= prev or value > 1e7:
            break
        times.append(round(value, 4))
        prev = value
        pos += 4
    flags_pos = 0x36 + (count - 1) * 4 if 0 < count <= 1_000_000 and 0x36 + (count - 1) * 4 + 6 <= len(data) else pos
    flag_a = data[flags_pos] if flags_pos < len(data) else 0
    pos_full = data[flags_pos + 2] if flags_pos + 2 < len(data) else 0
    rot_full = data[flags_pos + 3] if flags_pos + 3 < len(data) else 0
    duration = struct.unpack_from("<f", data, flags_pos - 4)[0] if count >= 2 and flags_pos >= 0x3A else 0
    body = data[flags_pos + 6:]
    warnings = []
    positions = None
    quats = None
    scales = None
    rot_keys = rot_full * count if rot_full else 1
    pos_keys = pos_full * count if pos_full else 1
    if count and flag_a in (1, 3, 5):
        if flag_a == 3:
            quats = _trk_block(body, 0, rot_keys * 4, True)
            if quats:
                positions = _trk_block(body, rot_keys * 16, pos_keys * 3, False)
        else:
            positions = _trk_block(body, 0, pos_keys * 3, False)
            if positions and flag_a == 1:
                quats = _trk_block(body, pos_keys * 12, rot_keys * 4, True)
                if quats:
                    scales = _trk_block(body, pos_keys * 12 + rot_keys * 16, 3, False)
            elif positions and flag_a == 5:
                quats = _trk_block(body, pos_keys * 12, rot_keys * 4, True)
        if positions is None and quats is None:
            warnings.append("inspect.trkChannelsSkip")
    else:
        warnings.append("inspect.trkChannelsSkip")
    if flag_a != 1:
        warnings.append("inspect.trkMultiChannel")
    if not times:
        warnings.append("inspect.trkNoTimes")
    return {
        "kind": "trk",
        "version": [ver_a, ver_b],
        "name": name,
        "flags": [flag_a, pos_full, rot_full],
        "key_count": count,
        "duration": round(duration, 4),
        "times": times,
        "positions": positions,
        "quats": quats,
        "scales": scales,
        "warnings": warnings,
        "trailing_bytes": len(data) - (flags_pos + 6),
    }


def parse_xml(data: bytes) -> dict:
    head = data[:MAX_TEXT]
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = head.decode("gb18030", "replace")
    truncated = len(data) > len(head)
    root = element_count = depth = 0
    root_tag = ""
    children = []
    try:
        root = xml.etree.ElementTree.fromstring(data if not truncated else head)
        root_tag = root.tag
        counter = {"count": 0, "depth": 0}

        def walk(element, level):
            counter["count"] += 1
            counter["depth"] = max(counter["depth"], level)
            for child in element:
                walk(child, level + 1)

        walk(root, 1)
        element_count = counter["count"]
        depth = counter["depth"]
        children = [child.tag for child in root][:40]
    except xml.etree.ElementTree.ParseError as exc:
        warnings = [f"xml parse: {exc}"]
    else:
        warnings = []
    return {
        "kind": "xml",
        "root": root_tag,
        "elements": element_count,
        "depth": depth,
        "children": children,
        "text": text,
        "truncated": truncated,
        "references": _refs(text),
        "warnings": warnings,
    }


def parse(payload: bytes, ext: str) -> dict:
    ext = (ext or "").lower()
    if ext == ".bin":
        return parse_bin(payload)
    if ext == ".eggimonbin":
        return parse_eggimonbin(payload)
    if ext == ".meta":
        return parse_meta(payload)
    if ext == ".efx":
        return parse_efx(payload)
    if ext == ".scn":
        return parse_scn(payload)
    if ext == ".trk":
        return parse_trk(payload)
    if ext in (".sfxmusic", ".postprocess"):
        return parse_xml(payload)
    raise StructError(f"unsupported ext {ext}")
