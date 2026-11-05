#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recover missing resource paths from the shipped path_hash_file table.

The ppk shard carries a CCZ!/zlib table of (FNV-1a(path), len(path)) records
sorted in the same order as the plaintext resource list.  Rows absent from the
current pathdict are exactly the unresolved paths.  Candidates are generated
from the two neighbouring known paths, checked against the FNV table first,
then confirmed with mmh3(path) and a content sniff of the unresolved entry.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
import sqlite3
import struct
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import npk_browser as B
import nxpk_mode3 as N

DEFAULT_DB = B.default_cache_dir() / "catalog.sqlite"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "external-pathtable-names.json"
FNV_OFFSET = 0x811C9DC5
FNV_PRIME = 0x01000193
ALPHA = "abcdefghijklmnopqrstuvwxyz0123456789_.-"

ALIASES = {
    "spr": {"atlas"},
    "skpatch": {"json"},
    "rscan": {"json"},
    "conf": {"json"},
    "lua": {"lua"},
    "skel": {"spine", "binary"},
    "blendspace1d": {"blendspace"},
    "surf": {"surface", "xml"},
    "fov": {"text", "binary"},
    "cube": {"dds"},
    "scn": {"gim_scene", "gim_material"},
    "sfx": {"gim_effect", "efx"},
}


def fnv1a(path: str) -> int:
    value = FNV_OFFSET
    for byte in path.encode("utf-8"):
        value = ((value ^ byte) * FNV_PRIME) & 0xFFFFFFFF
    return value


def sniff(data: bytes) -> str:
    head = data[:65536]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    head = head.lstrip()
    if head[:4] == b"\xc1\x59\x41\x0d":
        if any(key in head for key in (b"RoadGroup", b"SceneRoad", b"Lightmap", b"AmbientLight", b"BakeInfoTex")):
            return "gim_scene"
        if any(key in head for key in (b"ColorFrame", b"FxGroup", b"Emission", b"GravityFrame", b"AlphaMtl")):
            return "gim_effect"
        if any(key in head for key in (b"SubMesh", b"SkeletonFile", b"Sub0")):
            return "gim"
        if any(key in head for key in (b"LightingMaterial", b"MaterialGroup", b"Technique")):
            return "gim_material"
        return "gim"
    if head[:1] in (b"{", b"["):
        return "json"
    if head[:1] == b"<":
        if b"<BlendSpace" in head[:4096]:
            return "blendspace"
        if b"<Surface" in head[:4096]:
            return "surface"
        return "xml"
    if head[:4] == b"DDS ":
        return "dds"
    if head[:4] == b"RAWANIMA":
        return "rawanimation"
    if head[:4] == b"\x1bLua" or head.startswith(b"---AUTTO EXPORT") or b"\nreturn {" in head[:4096]:
        return "lua"
    if b".tga " in head[:4096] and b"\r\n" in head[:4096]:
        return "atlas"
    if b"3.8." in head[:128] and b"\x00" in head[:128]:
        return "spine"
    if head and all(32 <= byte < 127 or byte in (9, 10, 13, 59, 44) for byte in head[:512]):
        return "text"
    return "binary"


def load_pathdict(cache: Path, con: sqlite3.Connection):
    generated = cache / "pathdict.generated.txt.gz"
    paths = set()
    if generated.exists():
        with gzip.open(generated, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                item = line.strip().replace("\\", "/")
                if item:
                    paths.add(item)
    if not paths:
        for row in con.execute("SELECT path FROM files WHERE path NOT LIKE '_unresolved/%'"):
            paths.add(row[0])
        extra = Path(__file__).resolve().parent / "pathdict_extra.txt"
        if extra.exists():
            for line in extra.read_text(encoding="utf-8", errors="replace").splitlines():
                item = line.strip().replace("\\", "/")
                if item:
                    paths.add(item)
    return paths


def load_table(con: sqlite3.Connection, packages: dict):
    row = con.execute("SELECT * FROM files WHERE path='path_hash_file'").fetchone()
    if row is None:
        raise SystemExit("path_hash_file is not present in this catalog")
    raw = B.read_entry_bytes(packages[row["package"]], row)
    offset = raw.find(b"\x78\x9c")
    if offset < 0:
        raise SystemExit("path_hash_file has no zlib payload")
    plain = zlib.decompress(raw[offset:])
    if len(plain) % 8:
        raise SystemExit("path_hash_file payload is not 8-byte records")
    records = [struct.unpack_from("<II", plain, pos) for pos in range(0, len(plain), 8)]
    return records, {"package": row["package"], "packed": row["packed"], "raw": len(raw), "records": len(records)}


def load_unresolved(con: sqlite3.Connection, packages: dict):
    unresolved = {}
    for row in con.execute("SELECT * FROM files WHERE path LIKE '_unresolved/%'"):
        unresolved.setdefault(row["signature"], []).append(row)
    return unresolved


def gen(anchor: str, ext_list, suffix_list):
    out = set()
    directory, _, name = anchor.rpartition("/")
    prefix = (directory + "/") if directory else ""
    has_ext = "." in name
    stem = name.rsplit(".", 1)[0] if has_ext else name
    for ext in ext_list:
        out.add(prefix + stem + ext)
    out.add(prefix + stem)
    for match in re.finditer(r"\d+", stem):
        width = len(match.group(0))
        base = int(match.group(0))
        for delta in range(-20, 21):
            if delta == 0 or base + delta < 0:
                continue
            text = stem[: match.start()] + str(base + delta).zfill(width) + stem[match.end():]
            for ext in ext_list[:30]:
                out.add(prefix + text + ext)
            out.add(prefix + text)
    match = re.search(r"_lod([0-9])", stem)
    if match:
        for digit in "0123456789":
            text = stem[: match.start(1)] + digit + stem[match.end(1):]
            for ext in ext_list:
                out.add(prefix + text + ext)
    else:
        for digit in "0123456789":
            text = stem + "_lod" + digit
            for ext in ext_list:
                out.add(prefix + text + ext)
    for suffix in suffix_list:
        text = stem + suffix
        for ext in ext_list[:24]:
            out.add(prefix + text + ext)
    parts = stem.split("_")
    for cut in (1, 2):
        if len(parts) > cut:
            text = "_".join(parts[:-cut])
            for ext in ext_list[:30]:
                out.add(prefix + text + ext)
    return out


def ext_ok(path: str, target_ext: str, kind: str) -> bool:
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    target = target_ext.lower().lstrip(".")
    if suffix == target:
        return True
    if suffix in ALIASES and kind in ALIASES[suffix]:
        return True
    if not suffix and "physic_data/bin_convex_mesh/" in path and kind == "binary":
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--extra-candidates", type=Path, default=None, help="one candidate path per line")
    parser.add_argument("--max-runs", type=int, default=0, help="limit processed gaps (0 = all)")
    args = parser.parse_args()

    started = time.time()
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    unresolved = load_unresolved(con, packages)
    records, table_info = load_table(con, packages)
    paths = load_pathdict(args.db.parent, con)
    print("unresolved sigs %d, table %d records, pathdict %d" % (len(unresolved), len(records), len(paths)), flush=True)

    known = {}
    known_set = set()
    for path in paths:
        known.setdefault(fnv1a(path.replace("/", "\\")), path)
        known_set.add(path)
    ext_counter = Counter()
    suffix_counter = Counter()
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        if "." in name:
            ext_counter["." + name.rsplit(".", 1)[-1]] += 1
        stem = name.rsplit(".", 1)[0]
        parts = stem.split("_")
        suffix_counter["_" + parts[-1]] += 1
        if len(parts) >= 2:
            suffix_counter["_" + "_".join(parts[-2:])] += 1
    ext_list = [item for item, _ in ext_counter.most_common(100)]
    suffix_list = [item for item, _ in suffix_counter.most_common(80)]

    runs = []
    run = []
    previous = None
    for signature, length in records:
        anchor = known.get(signature)
        if anchor is not None and len(anchor) == length:
            if run:
                runs.append((previous, run, anchor))
                run = []
            previous = anchor
        else:
            run.append((signature, length))
    if run:
        runs.append((previous, run, None))
    unknown_total = sum(len(item[1]) for item in runs)
    print("runs %d, missing records %d" % (len(runs), unknown_total), flush=True)

    solved = {}
    rejected = []
    checked = 0
    limit = args.max_runs or len(runs)
    for index, (left, targets, right) in enumerate(runs[:limit]):
        target_map = {signature: length for signature, length in targets}
        pool = set()
        if left:
            pool |= gen(left, ext_list, suffix_list)
        if right:
            pool |= gen(right, ext_list, suffix_list)
        if left and right:
            pool = {item for item in pool if left < item < right}
        for candidate in pool:
            checked += 1
            signature = fnv1a(candidate.replace("/", "\\"))
            if signature in known:
                continue
            length = target_map.get(signature)
            if length is None or len(candidate) != length or candidate in known_set:
                continue
            target_map.pop(signature, None)
            solved[candidate] = {"fnv": signature, "length": length, "after": left, "before": right}
        if index and index % 1000 == 0:
            print("  gap %d/%d checked=%d solved=%d %.0fs" % (index, limit, checked, len(solved), time.time() - started), flush=True)

    extra_hits = 0
    if args.extra_candidates and args.extra_candidates.exists():
        global_targets = {signature: length for _, items, _ in runs for signature, length in items}
        for line in args.extra_candidates.read_text(encoding="utf-8", errors="replace").splitlines():
            candidate = line.strip().replace("\\", "/")
            if not candidate:
                continue
            signature = fnv1a(candidate.replace("/", "\\"))
            if signature in known:
                continue
            length = global_targets.get(signature)
            if length is not None and len(candidate) == length and candidate not in known_set:
                solved[candidate] = {"fnv": signature, "length": length, "after": None, "before": None}
                extra_hits += 1
    print("candidates solved %d (extra list %d) %.0fs" % (len(solved), extra_hits, time.time() - started), flush=True)

    mappings = {}
    by_ext = Counter()
    for candidate, meta in solved.items():
        signature = N.path_hash(candidate)
        rows = unresolved.get(signature)
        if not rows:
            continue
        row = rows[0]
        target_ext = Path(row["path"]).suffix
        try:
            data = B.read_entry_bytes(packages[row["package"]], row)
            kind = sniff(data)
        except Exception:
            data = b""
            kind = "unreadable"
        if not ext_ok(candidate, target_ext, kind):
            rejected.append({"path": candidate, "target": row["path"], "kind": kind, "ext": target_ext})
            continue
        evidence = "fnv=0x%08x len=%d mmh3=%d sniff=%s after=%s before=%s" % (
            meta["fnv"], meta["length"], signature, kind, meta["after"], meta["before"])
        mappings[row["path"]] = {
            "rule": "fnv-gap",
            "path": candidate,
            "signature": signature,
            "evidence": evidence,
        }
        by_ext[target_ext] += 1
    print("mappings %d, rejected %d" % (len(mappings), len(rejected)), flush=True)

    payload = {
        "generated_by": "tools/external_pathtable_hunt.py",
        "total_unresolved": sum(len(items) for items in unresolved.values()),
        "named": len(mappings),
        "table": table_info,
        "pathdict": len(paths),
        "missing_records": unknown_total,
        "candidates_solved": len(solved),
        "rejected": len(rejected),
        "rejected_items": rejected,
        "by_ext": dict(by_ext.most_common()),
        "stats": {
            "gaps": len(runs),
            "checked": checked,
            "extra_candidates": extra_hits,
            "seconds": round(time.time() - started, 1),
        },
        "sources": {
            "path_hash_file": "ppk/" + str(table_info["package"]).split("/", 1)[-1],
            "hash": "FNV-1a32 over backslash paths",
            "signature": "mmh3(path, 0x9747B28C)",
            "content": "npk_browser.read_entry_bytes + tools/external_pathtable_hunt.sniff",
        },
        "mappings": mappings,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  %d mappings  %.0fs" % (args.out, len(mappings), time.time() - started), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
