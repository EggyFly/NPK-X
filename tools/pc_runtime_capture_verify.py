#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify hex names harvested from the live client memory against the package.

Takes every 15-16 hex string found in the runtime capture JSONs, builds the
candidate shader cache paths (both shipped dirs x both extensions), and gates
each candidate: FNV-1a32(backslash path) + exact length on a path_hash_file
record, mmh3(backslash path, 0x9747B28C) on an unresolved signature, and a
content magic check (NXSC meta / zstd container) of the unresolved entry.

Output: docs/unresolved-sweep/23-runtime-capture-verify.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import external_pathtable_hunt as H
import npk_browser as B
import mmh3

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
DEFAULT_STRINGS = SWEEP / "23-runtime-capture-strings.json"
DEFAULT_HEXNAMES = SWEEP / "23-runtime-capture-hexnames.json"
DEFAULT_OUT = SWEEP / "23-runtime-capture-verify.json"

DIRS = ("shader_cache/cross_compiled_glsl", "shadercache/cross_compiled_glsl",
        "shader_cache/cross_compiled_vulkan_windows", "shadercache/cross_compiled_vulkan_windows")
EXTS = (".meta", ".cache")
HEX_RE_CHARS = set("0123456789abcdef")


def load_names(strings_path: Path, hexnames_path: Path) -> dict:
    names = {}
    if hexnames_path.exists():
        data = json.loads(hexnames_path.read_text(encoding="utf-8"))
        for name, count in data.get("names", {}).items():
            names[name] = names.get(name, 0) + count
        src_meta = {"hexnames": {"count": len(data.get("names", {})), "scan": data.get("stats", {})}}
    else:
        src_meta = {}
    strings_hits = {}
    if strings_path.exists():
        data = json.loads(strings_path.read_text(encoding="utf-8"))
        for text, item in data.get("strings", {}).items():
            for hn in item.get("hexnames", []):
                strings_hits[hn] = strings_hits.get(hn, 0) + item.get("count", 0)
        src_meta["strings"] = {"unique_strings": data.get("unique_strings"), "pid": data.get("pid")}
    for name, count in strings_hits.items():
        names.setdefault(name, 0)
        names[name] += count
    return names, src_meta


def magic_of(data: bytes) -> dict:
    head = data[:16]
    info = {"size": len(data), "head": head.hex()}
    if head[:4] == b"NXSC":
        info["kind"] = "NXSC_meta"
    elif head[:4] == b"\x28\xb5\x2f\xfd":
        info["kind"] = "zstd_container"
    else:
        info["kind"] = "other"
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--strings", type=Path, default=DEFAULT_STRINGS)
    parser.add_argument("--hexnames", type=Path, default=DEFAULT_HEXNAMES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    started = time.time()
    names, src_meta = load_names(args.strings, args.hexnames)
    clean = {n: c for n, c in names.items() if len(n) in (15, 16) and set(n) <= HEX_RE_CHARS}
    print("harvested names %d, clean 15-16hex %d" % (len(names), len(clean)), flush=True)

    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages"))
    records, table_info = H.load_table(con, packages)
    recset = set(records)
    unresolved = H.load_unresolved(con, packages)
    con.close()

    import gzip
    dict_paths = set()
    generated = B.default_cache_dir() / "pathdict.generated.txt.gz"
    with gzip.open(generated, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            item = line.strip()
            if item:
                dict_paths.add(item)

    results = []
    hit_fnv = hit_mmh3 = 0
    for name, count in sorted(clean.items(), key=lambda kv: -kv[1]):
        for d in DIRS:
            for ext in EXTS:
                path = d + "/" + name + ext
                bs = path.replace("/", "\\")
                f = H.fnv1a(bs)
                m = mmh3.hash(bs.encode(), 0x9747B28C, signed=False)
                in_fnv = (f, len(bs)) in recset
                in_dict = path in dict_paths
                sig_rows = unresolved.get(m)
                if not (in_fnv or sig_rows):
                    continue
                row = {"name": name, "mem_count": count, "path": path,
                       "fnv": "0x%08x" % f, "path_len": len(bs),
                       "fnv_len_hit": in_fnv, "in_pathdict": in_dict,
                       "mmh3": m, "mmh3_unresolved_hit": bool(sig_rows)}
                if sig_rows:
                    row["entries"] = [r["path"] for r in sig_rows]
                    mags = []
                    for r in sig_rows[:4]:
                        try:
                            blob = B.read_entry_bytes(packages[r["package"]], r)
                        except Exception as exc:
                            mags.append({"entry": r["path"], "error": str(exc)})
                            continue
                        mags.append(dict(magic_of(blob[:65536] if len(blob) > 65536 else blob), entry=r["path"]))
                    row["content"] = mags
                results.append(row)
                hit_fnv += 1 if in_fnv else 0
                hit_mmh3 += 1 if sig_rows else 0

    gap_hits = [r for r in results if r["fnv_len_hit"] and not r["in_pathdict"]]
    report = {
        "sources": src_meta,
        "names_total": len(names),
        "names_clean": len(clean),
        "candidates_checked": len(clean) * len(DIRS) * len(EXTS),
        "candidates_hit_fnv": hit_fnv,
        "candidates_hit_mmh3": hit_mmh3,
        "gap_hits": gap_hits,
        "results": results,
        "elapsed": round(time.time() - started, 1),
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("candidates checked %d, fnv hits %d, mmh3 unresolved hits %d" % (report["candidates_checked"], hit_fnv, hit_mmh3))
    print("gap hits (fnv+len not yet in pathdict): %d" % len(gap_hits))
    for r in gap_hits[:20]:
        print("  %s  %s  mem x%d  mmh3_hit=%s" % (r["path"], r["fnv"], r["mem_count"], r["mmh3_unresolved_hit"]))
    print("saved %s (%.1fs)" % (args.out, report["elapsed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
