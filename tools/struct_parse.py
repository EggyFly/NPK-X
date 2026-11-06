#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""结构化预览解析器: .bin/.eggimonbin/.meta/.efx/.scn/.trk/.cache.

格式结论 (全部来自本目录样本实测):
  .bin        zstd+msgpack 双层 / 裸 msgpack / bindict 表 / 高度图(u32 w, u32 h, int16 高程) / zlib / 全零 / 多帧 zstd 着色器容器
  .eggimonbin msgpack, 顶层 uuid 键
  .meta       NXSC 头 0x28 + 可选 zstd 帧 + 哈希记录表 (记录内 16 字节处恒为 0x27 标记), 非 msgpack
  .cache      16hex 族 = 多帧 zstd (帧 0 自包含, 其输出即帧 1+ 的 raw-content 字典, NeoX DecompressDataWithDict);
              8hex 族 = 裸头 + 明文 GLSL 段 (#version 扫描分段)
  .efx        @BIN 头 + [u8 len+16][字符串] 属性流; 属性名/value 明文, 其余为哈希记录未解
  .scn        NeoX c159410d 容器, 值类型在 gim/mtg 基础上多 4=i32 与 7/8=8 字节定长结构 (仅 Probe.PixelFormat,
              73 文件实测值恒定且后接 ProbeOffset 全对齐, f64 解读数值荒谬故按原始字节展示);
              SHValue 类对象是裸属性记录 (fid+type+value, 无 prop_count 头也无 01 00 终止符),
              36 组×6 字节与 meta 表 36 个 SHValue 条目精确对齐 (057c29d9/06bca1d0 实测)
  .trk        NTRK 相机轨道: 32 字节名字段 + u32 帧数 + 帧数-1 个 f32 时间 + 6 字节通道标志 + 位置/四元数块
  .sfxmusic/.postprocess 为纯文本 XML, 已改由 /api/text 代码预览, 不再走结构化解析
"""

from __future__ import annotations

import re
import struct
import array
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
MAX_RECORDS = 400
MAX_OBJECTS = 4000
MAX_PAIRS = 500
MAX_REFS = 80
MAX_TIMES = 4000
MAX_SHADER_FRAMES = 4096
MAX_SHADER_SENT = 6
MAX_SHADER_TEXT = 64 * 1024
MAX_UNIFORM_ROWS = 500
MAX_FRAME_SUMMARY = 300
SHADER_CHUNK = 256 * 1024

REF_RE = re.compile(
    r"[A-Za-z0-9_./\\-]{2,240}\.(?:kta|dds|ktx|png|jpe?g|tga|bmp|mesh|skeleton|mtg|gim|"
    r"surf|shader|sfx|efx|animation|rawanimation|animgraph|csb|mp4|webm)\b",
    re.IGNORECASE,
)
OBJECT_NAME_RE = re.compile(r"^[A-Za-z_]\w*\(\d+\)$")

SCN_BARE_CLASSES = {"SHValue"}


class StructError(ValueError):
    pass


SUPPORTED_EXTS = {".bin", ".eggimonbin", ".meta", ".efx", ".scn", ".trk", ".cache"}


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
            shader = _shader_container_result(data)
            if shader:
                return shader
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


def _frame_text(payload: bytes) -> str | None:
    text = payload.rstrip(b"\x00")
    try:
        text = text.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    if printable / len(text) < 0.999:
        return None
    return text


def _glsl_role(text: str) -> str:
    if "gl_Position" in text:
        return "vs"
    if ("gl_FragColor" in text or "FragData" in text or "FragColor" in text
            or "entryPointOutput" in text
            or re.search(r"layout\(location\s*=\s*\d+\)\s*out\s+vec[234]", text)):
        return "ps"
    return "glsl"


def _uniform_rows(text: str) -> list | None:
    if text.lstrip()[:8] == "#version":
        return None
    rows = []
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        fields = entry.split(":")
        if len(fields) < 4 or not fields[1].isalpha():
            return None
        rows.append({
            "name": fields[0],
            "type": fields[1],
            "sampler": fields[2],
            "props": ":".join(fields[3:]),
        })
    return rows or None


def _decode_shader_frames(data: bytes):
    pos = 0
    outs = []
    dctx_dict = None
    while pos < len(data) and len(outs) < MAX_SHADER_FRAMES:
        idx = data.find(ZSTD_MAGIC, pos)
        if idx < 0:
            break
        dctx = dctx_dict if dctx_dict is not None else zstandard.ZstdDecompressor()
        obj = dctx.decompressobj()
        payload = b""
        fed = idx
        eof = False
        while fed < len(data):
            chunk = data[fed:fed + SHADER_CHUNK]
            payload += obj.decompress(chunk)
            fed += len(chunk)
            if obj.eof:
                eof = True
                break
        if not eof:
            return outs, "truncated@%d" % idx
        consumed = fed - idx - len(obj.unused_data)
        outs.append((idx, payload))
        if dctx_dict is None:
            dctx_dict = zstandard.ZstdDecompressor(dict_data=zstandard.ZstdCompressionDict(payload))
        pos = idx + consumed
    return outs, ("done" if outs else "no_frames")


def _shader_container_result(data: bytes) -> dict | None:
    frames, status = _decode_shader_frames(data)
    if status != "done" or len(frames) < 2:
        return None
    parsed = []
    counts = {"glsl": 0, "uniform": 0, "layout": 0, "text": 0}
    for index, (offset, payload) in enumerate(frames):
        text = _frame_text(payload)
        if text is None:
            role = "layout"
        elif text.lstrip()[:8] == "#version":
            role = "glsl"
        else:
            role = "uniform" if _uniform_rows(text) else "text"
        counts[role] += 1
        parsed.append({"index": index, "offset": offset, "size": len(payload), "role": role, "text": text})
    uniform_frame = next((item for item in parsed if item["role"] == "uniform"), None)
    glsl_sent = []
    for wanted in ("vs", "ps"):
        pick = next((item for item in parsed if item["role"] == "glsl" and _glsl_role(item["text"]) == wanted), None)
        if pick:
            glsl_sent.append(pick)
    sent_ids = {id(item) for item in glsl_sent}
    for item in parsed:
        if item["role"] != "glsl" or id(item) in sent_ids:
            continue
        if len(glsl_sent) >= MAX_SHADER_SENT:
            break
        glsl_sent.append(item)
        sent_ids.add(id(item))
    if uniform_frame:
        sent_ids.add(id(uniform_frame))
    more = [{"index": item["index"], "size": item["size"], "role": item["role"]}
            for item in parsed if id(item) not in sent_ids][:MAX_FRAME_SUMMARY]
    sent_glsl = []
    for item in glsl_sent:
        text = item["text"]
        sent_glsl.append({
            "index": item["index"],
            "size": item["size"],
            "role": _glsl_role(text) if text.lstrip()[:8] == "#version" else "glsl",
            "text": text[:MAX_SHADER_TEXT],
            "truncated": len(text) > MAX_SHADER_TEXT,
        })
    result = {
        "kind": "shader_cache",
        "layers": ["zstd", "dict"],
        "frame_count": len(frames),
        "glsl_frames": counts["glsl"],
        "uniform_frames": counts["uniform"],
        "layout_frames": counts["layout"],
        "dict_size": len(frames[0][1]),
        "more_frames": more,
        "more_total": len(frames) - (len(glsl_sent) + (1 if uniform_frame else 0)),
        "glsl": sent_glsl,
    }
    if uniform_frame:
        rows = _uniform_rows(uniform_frame["text"])
        result["uniform"] = {
            "frame": uniform_frame["index"],
            "rows": rows[:MAX_UNIFORM_ROWS],
            "truncated": len(rows) > MAX_UNIFORM_ROWS,
        }
    layout_frame = next((item for item in parsed if item["role"] == "layout"), None)
    if layout_frame:
        result["hex_head"] = _hex_head(frames[layout_frame["index"]][1])
    return result


def parse_cache(data: bytes) -> dict:
    if data[:4] == ZSTD_MAGIC:
        result = _shader_container_result(data)
        if result is None:
            raise StructError("not a shader zstd container")
        return result
    starts = []
    pos = 0
    while len(starts) < 512:
        idx = data.find(b"#version", pos)
        if idx < 0:
            break
        starts.append(idx)
        pos = idx + 8
    if not starts:
        raise StructError("no glsl in cache")
    bounds = starts + [len(data)]
    blocks = []
    for i in range(len(starts)):
        text = data[bounds[i]:bounds[i + 1]].decode("utf-8", "replace")
        blocks.append({
            "index": i,
            "size": bounds[i + 1] - bounds[i],
            "role": _glsl_role(text),
            "text": text[:MAX_SHADER_TEXT],
            "truncated": len(text) > MAX_SHADER_TEXT,
        })
    return {
        "kind": "cache_plain",
        "layers": ["raw"],
        "head_size": starts[0],
        "glsl_frames": len(blocks),
        "glsl": blocks[:MAX_SHADER_SENT],
        "more_frames": [{"index": item["index"], "size": item["size"], "role": item["role"]}
                        for item in blocks[MAX_SHADER_SENT:]][:MAX_FRAME_SUMMARY],
        "more_total": max(0, len(blocks) - MAX_SHADER_SENT),
        "hex_head": _hex_head(data[:starts[0]]),
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
        "off12": "0x%016x" % struct.unpack_from("<Q", data, 12)[0],
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
    if typ in (7, 8):
        return "0x" + data[pos:pos + 8].hex(), pos + 8
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
            props = {}
            if (index < len(meta) and 0 <= meta[index][0] < len(table)
                    and table[meta[index][0]] in SCN_BARE_CLASSES):
                fid, vpos = _read_varint(blob, vpos)
                typ, vpos = _read_varint(blob, vpos)
                types_used[typ] = types_used.get(typ, 0) + 1
                value, vpos = _read_value_ext(blob, vpos, typ)
                props[fields[fid] if fid < len(fields) else f"field{fid}"] = value
                objects.append(props)
                continue
            prop_count, vpos = _read_varint(blob, vpos)
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
    if ext == ".cache":
        return parse_cache(payload)
    if ext == ".scn":
        return parse_scn(payload)
    if ext == ".trk":
        return parse_trk(payload)
    raise StructError(f"unsupported ext {ext}")
