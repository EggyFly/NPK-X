#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name unresolved PPK entries from asset references embedded in file contents.

Scans text-ish PPK payloads (named and unnamed alike) for path-like strings,
maps referenced extensions to stored ones (.tga -> .kta, gui2-relative csb
references, effect Tex## prefix junk), and accepts a name only when
MurmurHash3(path) equals the target signature and the extension matches the
catalog-detected type.  Also names entries whose (size, content hash) is
identical to an already-named entry.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
import npk_browser as B
import nxpk_mode3 as N

DEFAULT_DB = B.default_cache_dir() / "catalog.sqlite"

SOURCE_EXTS = {
    ".mtg", ".gim", ".mesh", ".efx", ".csb", ".gmp", ".json", ".trackgroup",
    ".animconfig", ".animresmap", ".blendspace", ".animgraph", ".stb", ".nstb",
    ".sfxmusic", ".postprocess", ".plist", ".scn", ".eggimonbin", ".luac",
    ".trk", ".hlsl", ".fbx", ".lod", ".animation", ".rawanimation", ".skeleton",
    ".sfx", ".meta", ".bin",
}
REF_EXTS = {
    "tga", "psd", "png", "jpg", "jpeg", "bmp", "dds", "kta", "ktx", "gim",
    "mesh", "mtg", "sfx", "efx", "bnk", "wem", "skeleton", "animation",
    "rawanimation", "animconfig", "plist", "csb", "json", "scn", "stb", "nstb",
    "trackgroup", "animresmap", "animgraph", "blendspace", "blendspace1d",
    "luac", "lua", "trk", "fbx", "lod", "gmp", "hlsl", "mp4", "eggimonbin",
    "postprocess",
}
IMAGE_SRC_EXTS = {"tga", "psd", "png", "jpg", "jpeg", "bmp"}
TOKEN = re.compile(rb"[A-Za-z0-9_\-.\\/]{3,220}[.][A-Za-z0-9]{1,12}")
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def ref_variants(raw: str, roots: set[str]):
    text = raw.replace("\\", "/").strip()
    text = re.sub(r"^[.]/+", "", text)
    stem, dot, ext = text.rpartition(".")
    if not dot or ext.lower() not in REF_EXTS:
        return []
    low = ext.lower()
    out = []
    rooted = text.split("/", 1)[0] in roots
    bases = []
    if rooted:
        bases.append(text)
    else:
        bases.append("gui2/" + text)
        for cut in range(1, 4):
            trimmed = text[cut:]
            if trimmed.split("/", 1)[0] in roots:
                bases.append(trimmed)
    for base in bases:
        out.append(base)
        if low in IMAGE_SRC_EXTS:
            out.append(base[: base.rindex(".")] + ".kta")
        if low == "blendspace":
            out.append(base[: base.rindex(".")] + ".blendspace1d")
    seen = []
    for item in out:
        if item not in seen and "." in item and not item.endswith("."):
            seen.append(item)
    return seen


def load_catalog(db_path: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    named_by_sig = defaultdict(list)
    named_by_path = {}
    roots = Counter()
    for row in con.execute("SELECT path, signature, ext FROM files WHERE path NOT LIKE '_unresolved/%'"):
        named_by_sig[row["signature"]].append(row["path"])
        named_by_path[row["path"]] = row
        head = row["path"].split("/", 1)[0]
        if "/" in row["path"]:
            roots[head] += 1
    twin = {}
    for row in con.execute(
        "SELECT path, unpacked, unpacked_hash FROM files WHERE path NOT LIKE '_unresolved/%' "
        "AND unpacked_hash != 0"
    ):
        twin.setdefault((row["unpacked"], row["unpacked_hash"]), row["path"])
    unresolved = con.execute(
        "SELECT id, path, signature, offset, packed, unpacked, unpacked_hash, compression, package, ext "
        "FROM files WHERE path LIKE '_unresolved/ppk/%'"
    ).fetchall()
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    con.close()
    root_set = {root for root, count in roots.items() if count >= 20}
    return named_by_sig, named_by_path, twin, unresolved, packages, root_set


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--game-root", type=Path, default=Path(r"D:\FeverApps\party_pc"))
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "ppk-structural-names.json")
    parser.add_argument("--limit", type=int, default=0, help="scan at most N source entries (0 = all)")
    parser.add_argument("--max-mb", type=int, default=64)
    args = parser.parse_args()

    started = time.time()
    named_by_sig, named_by_path, twin, unresolved, packages, roots = load_catalog(args.db)
    print("named paths: %d  unresolved ppk: %d  roots: %d  %.0fs" % (
        len(named_by_path), len(unresolved), len(roots), time.time() - started))

    unresolved_by_sig = {}
    for row in unresolved:
        unresolved_by_sig.setdefault(row["signature"], row)

    results = {}
    conflicts = {}
    stats = Counter()

    unresolved_with_hash = [
        row for row in unresolved if row["unpacked_hash"]
    ]
    for row in unresolved_with_hash:
        named_path = twin.get((row["unpacked"], row["unpacked_hash"]))
        if named_path and N.path_hash(named_path) != row["signature"]:
            results[row["path"]] = {
                "rule": "content-identical",
                "path": named_path,
                "evidence": "same unpacked size and content hash as named entry",
            }
    stats["rule_identical"] = len(results)
    print("identical-content names: %d  %.0fs" % (len(results), time.time() - started))

    named_rows = []
    con3 = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    con3.row_factory = sqlite3.Row
    ext_list = ",".join("'%s'" % ext for ext in sorted(SOURCE_EXTS))
    where = "path NOT LIKE '_unresolved/%' AND package LIKE 'ppk/%' AND ext IN (" + ext_list + ")"
    named_rows = con3.execute(
        "SELECT path, signature, offset, packed, unpacked, compression, package, ext FROM files WHERE " + where
    ).fetchall()
    con3.close()

    sources = list(unresolved) + named_rows
    if args.limit:
        sources = sources[: args.limit]
    print("scan sources: %d" % len(sources))

    ref_total = 0
    ref_named_hits = 0
    for index, row in enumerate(sources, 1):
        if row["ext"] not in SOURCE_EXTS or row["unpacked"] > args.max_mb * 1024 * 1024:
            continue
        package_path = packages.get(row["package"])
        if not package_path:
            continue
        try:
            data = B.read_entry_bytes(package_path, row)
        except Exception:
            stats["source_read_fail"] += 1
            continue
        source_label = row["path"]
        for token in set(TOKEN.findall(data)):
            raw = token.decode("latin1")
            for candidate in ref_variants(raw, roots):
                ref_total += 1
                sig = N.path_hash(candidate)
                hit = named_by_sig.get(sig)
                target = unresolved_by_sig.get(sig)
                if target is not None and candidate.rsplit(".", 1)[-1].lower() == target["ext"].lstrip("."):
                    if hit and candidate not in hit:
                        stats["sig_collision_named"] += 1
                        continue
                    existing = results.get(target["path"])
                    if existing and existing.get("path") != candidate:
                        conflicts.setdefault(target["path"], set()).update([existing["path"], candidate])
                        continue
                    if target["path"] in conflicts:
                        continue
                    results[target["path"]] = {
                        "rule": "reference",
                        "path": candidate,
                        "evidence": "%s references %r" % (source_label, raw),
                    }
                elif hit:
                    ref_named_hits += 1
        if index % 20000 == 0:
            print("  %d/%d sources, refs=%d named-hits=%d named-new=%d %.0fs" % (
                index, len(sources), ref_total, ref_named_hits, stats["rule_reference"], time.time() - started))

    for path_key in conflicts:
        dropped = results.pop(path_key, None)
        if dropped:
            stats["conflict_dropped"] += 1

    stats["rule_reference"] = sum(1 for v in results.values() if v["rule"] == "reference")
    print("reference names: %d  conflicts dropped: %d  refs=%d verified-named-refs=%d  %.0fs" % (
        stats["rule_reference"], stats["conflict_dropped"], ref_total, ref_named_hits, time.time() - started))

    remaining = Counter()
    for row in unresolved:
        if row["path"] not in results:
            remaining[row["ext"]] += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_by": "tools/ppk_content_names.py",
        "total_unresolved": len(unresolved),
        "named": len(results),
        "mappings": results,
        "remaining_by_ext": dict(remaining.most_common()),
        "stats": dict(stats),
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  named %d / %d" % (args.out, len(results), len(unresolved)))
    for ext, count in remaining.most_common(15):
        print("  remaining %-14s %d" % (ext, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
