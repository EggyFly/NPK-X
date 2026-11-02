#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build eid -> relative model path from the bindict tables in catalog.sqlite.

Table chain:
  bindict/client/unit_eid_2_model_id_data.bin  unit_eid -> model_id
  bindict/common/editor_model.bin              model_id -> {path}
"""

from __future__ import annotations

import os
import re
import sqlite3
import struct
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bindict_decode import Bindict, read_leb

EID_TABLE = "bindict/client/unit_eid_2_model_id_data.bin"
MODEL_TABLE = "bindict/common/editor_model.bin"
MAX_PAIRS = 400000
DRIVE_RE = re.compile(r"^[A-Za-z]:")

try:
    from compression.zstd import ZstdDecompressor as _StdZstd
    _STD_ZSTD = True
except ImportError:
    _StdZstd = None
    _STD_ZSTD = False

try:
    import zstandard as _zstandard
except ImportError:
    _zstandard = None


def default_catalog_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "EggyNpkBrowser" / "catalog.sqlite"
    return Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"


def fingerprint(catalog_path: Path) -> str:
    try:
        stat = Path(catalog_path).stat()
    except OSError:
        return "missing"
    return "%d:%d" % (stat.st_mtime_ns, stat.st_size)


def _decompress(raw: bytes, size: int) -> bytes:
    if _STD_ZSTD:
        return _StdZstd().decompress(raw, max_length=size)
    if _zstandard is not None:
        return _zstandard.ZstdDecompressor().decompress(raw, max_output_size=size)
    raise RuntimeError("zstd decoder unavailable")


def _read_entry(con, packages: dict, path: str):
    row = con.execute(
        "select package, offset, packed, unpacked, compression from files where path=?",
        (path,),
    ).fetchone()
    if row is None:
        raise KeyError(path)
    package, offset, packed, unpacked, compression = row
    package_path = packages.get(package)
    if not package_path:
        raise KeyError(package)
    with open(package_path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(packed)
    if compression == 3:
        return _decompress(raw, unpacked)
    return raw


def _table_header(data: bytes):
    count = struct.unpack_from("<I", data, 0)[0]
    if not count or count > MAX_PAIRS:
        raise ValueError("bad string count")
    strings_start = 8 + count * 4
    if strings_start > len(data):
        raise ValueError("bad string table")
    strings = []
    begin = 0
    for index in range(count):
        end = struct.unpack_from("<I", data, 8 + index * 4)[0]
        if end < begin or strings_start + end > len(data):
            raise ValueError("bad string offset")
        strings.append(data[strings_start + begin:strings_start + end].decode("utf-8", "replace"))
        begin = end
    row_start = strings_start + begin
    if row_start + 4 > len(data):
        raise ValueError("bad main area")
    main_len = struct.unpack_from("<I", data, row_start)[0]
    root = row_start + main_len
    if not 0 < main_len < len(data) or root >= len(data):
        raise ValueError("bad main length")
    return strings, row_start, main_len, root


def _root_pairs(data: bytes, root: int):
    tag = data[root]
    if (tag & 0x0F) != 6 or not tag & 0x10 or not tag & 0x20:
        raise ValueError("unexpected root tag")
    pos = root + 3
    count, pos = read_leb(data, pos, len(data))
    if not count or count > MAX_PAIRS:
        raise ValueError("bad root count")
    pos += count * 8
    if pos > len(data):
        raise ValueError("bad root entries")
    pairs = []
    while pos < len(data):
        key, pos = read_leb(data, pos, len(data))
        offset, pos = read_leb(data, pos, len(data))
        if key is None or offset is None:
            break
        pairs.append((key, offset))
        if len(pairs) > MAX_PAIRS:
            break
    return pairs


def _decode_rows(data: bytes, pairs):
    decoder = Bindict(data)
    rows = []
    for key, offset in pairs:
        if not 0 <= offset < decoder.main_len or offset >= len(decoder.body):
            continue
        try:
            value, _pos = decoder.value(offset, decoder.body[offset])
        except (ValueError, IndexError, struct.error, RecursionError):
            continue
        rows.append((key, value))
    return rows


def _clean_path(value):
    if not isinstance(value, str):
        return None
    path = value.strip().replace("\\", "/")
    if not path or not path.lower().endswith((".gim", ".mesh")):
        return None
    if path.startswith("/") or DRIVE_RE.match(path) or "://" in path:
        return None
    if ".." in path.split("/"):
        return None
    return path


def _model_map(data: bytes) -> dict:
    pairs = _root_pairs(data, _table_header(data)[3])
    mapping = {}
    for key, record in _decode_rows(data, pairs):
        if not isinstance(record, dict):
            continue
        path = _clean_path(record.get("path"))
        if path is None:
            continue
        row_key = record.get("key")
        if isinstance(row_key, int) and row_key != key:
            continue
        mapping[key] = path
    return mapping


def _eid_map(data: bytes) -> dict:
    pairs = _root_pairs(data, _table_header(data)[3])
    mapping = {}
    for key, record in _decode_rows(data, pairs):
        if not isinstance(record, dict):
            continue
        model_id = record.get("model_id")
        unit_eid = record.get("unit_eid")
        if not isinstance(model_id, int) or not isinstance(unit_eid, int):
            continue
        if unit_eid == key:
            mapping[key] = model_id
    return mapping


def build(catalog_path=None) -> dict:
    catalog = Path(catalog_path) if catalog_path else default_catalog_path()
    try:
        con = sqlite3.connect("file:%s?mode=ro" % catalog, uri=True)
        try:
            packages = dict(con.execute("select name, path from packages").fetchall())
            eid_data = _read_entry(con, packages, EID_TABLE)
            model_data = _read_entry(con, packages, MODEL_TABLE)
        finally:
            con.close()
    except Exception as exc:
        print("eid_models_build: read catalog failed: %s" % exc, file=sys.stderr)
        return {}
    try:
        model_ids = _model_map(model_data)
        unit_eids = _eid_map(eid_data)
    except Exception as exc:
        print("eid_models_build: decode table failed: %s" % exc, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return {}
    if not model_ids or not unit_eids:
        print("eid_models_build: empty table", file=sys.stderr)
        return {}
    result = {}
    for eid, model_id in unit_eids.items():
        path = model_ids.get(model_id)
        if path:
            result[str(eid)] = path
    if not result:
        print("eid_models_build: no eid joined to model path", file=sys.stderr)
    return result


def main() -> int:
    result = build()
    print("eids:", len(result))
    for key in ("100001", "100007", "100065"):
        print(key, result.get(key))
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
