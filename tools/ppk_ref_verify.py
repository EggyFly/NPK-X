#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independently re-verify hash-to-path mappings and merge them into one table.

Reads mapping JSONs produced by the reference sweep (ppk_ref_scan,
ppk_ref_bruteforce and the structural pass), recomputes the client path hash
for every proposed path, requires equality with the stored signature of the
unresolved entry, requires the extension to match the catalog-detected type,
checks for collisions against already-named signatures and unpacks the entry
payload to run the magic check.  Writes the verified mapping table.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ppk_ref_scan as S

import mmh3

import npk_browser as B
from ppk_ref_bruteforce import entry_magic, skel_magic, atlas_magic

DEFAULT_DB = S.DEFAULT_DB
SWEEP_DIR = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
SOURCES = [
    SWEEP_DIR / "ppk-reference-names.json",
    SWEEP_DIR / "ppk-reference-bruteforce.json",
    SWEEP_DIR / "ppk-structural-names.json",
]
DEFAULT_OUT = SWEEP_DIR / "ppk-reference-verified.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sources", nargs="*", default=[str(p) for p in SOURCES])
    args = parser.parse_args()

    started = time.time()
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    named_by_sig = {}
    for path, signature in con.execute(
        "SELECT path, signature FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        named_by_sig[signature] = path
    unresolved = con.execute(
        "SELECT path, signature, ext, offset, packed, unpacked, compression, package "
        "FROM files WHERE path LIKE '_unresolved/%'"
    ).fetchall()
    by_path = {row["path"]: row for row in unresolved}
    print("unresolved rows: %d  named sigs: %d" % (len(by_path), len(named_by_sig)))

    proposed = {}
    for source in args.sources:
        path = Path(source)
        if not path.is_file():
            print("skip missing source: %s" % path.name)
            continue
        data = json.loads(path.read_text("utf-8"))
        count = 0
        for target, item in data.get("mappings", {}).items():
            rule = item.get("rule", "unknown")
            entry = proposed.setdefault(target, {})
            entry.setdefault("rules", []).append(path.name + ":" + rule)
            if "path" in entry and entry["path"] != item["path"]:
                entry["conflict_with"] = entry.get("conflict_with", [])
                entry["conflict_with"].append(item["path"])
                continue
            entry["path"] = item["path"]
            entry["evidence"] = item.get("evidence", "")
            count += 1
        print("source %s: %d mappings" % (path.name, count))

    verified = {}
    stats = Counter()
    for target, item in proposed.items():
        if "conflict_with" in item:
            stats["conflict_dropped"] += 1
            continue
        row = by_path.get(target)
        if row is None:
            stats["target_gone"] += 1
            continue
        base = item["path"].replace("\\", "/")
        forms = [base]
        if base.lower() != base:
            forms.append(base.lower())
        matched = None
        for form in forms:
            cext = form.rpartition(".")[2].lower()
            if cext != row["ext"].lstrip(".") and not (
                row["ext"] == ".bin" and cext in ("skel", "atlas")
            ):
                continue
            try:
                raw = form.encode("latin1")
            except UnicodeEncodeError:
                raw = form.encode("utf-8")
            for blob in dict.fromkeys([raw, form.encode("utf-8")]):
                if mmh3.hash(blob.replace(b"/", b"\\"), S.SEED, signed=False) == row["signature"]:
                    matched = (form, cext, blob)
                    break
            if matched:
                break
        if matched is None:
            stats["hash_mismatch"] += 1
            continue
        cand, cext, blob = matched
        named = named_by_sig.get(row["signature"])
        if named is not None and named.lower() not in (cand, cand.lower()):
            stats["sig_collision_named"] += 1
            continue
        magic = "not-unpacked"
        package_path = packages.get(row["package"])
        if package_path:
            try:
                body = B.read_entry_bytes(package_path, row)
                if cext == "skel":
                    magic = "ok" if skel_magic(body) else "fail"
                elif cext == "atlas":
                    magic = "ok" if atlas_magic(body) else "fail"
                else:
                    magic = "ok" if entry_magic(cext, body) else "fail"
            except Exception:
                magic = "read-fail"
        if magic == "fail":
            stats["magic_fail"] += 1
            continue
        stats["magic_" + magic] += 1
        verified[target] = {
            "path": cand,
            "signature": "%08x" % row["signature"],
            "ext": row["ext"],
            "package": row["package"],
            "unpacked": row["unpacked"],
            "magic": magic,
            "rules": sorted(set(item["rules"])),
            "evidence": item["evidence"][:300],
        }

    remaining = Counter()
    for row in unresolved:
        if row["path"] not in verified:
            remaining[row["ext"]] += 1

    payload = {
        "generated_by": "tools/ppk_ref_verify.py",
        "sources": [Path(p).name for p in args.sources],
        "total_unresolved": len(by_path),
        "verified": len(verified),
        "mappings": verified,
        "remaining_by_ext": dict(remaining.most_common()),
        "stats": dict(stats),
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  verified %d / %d  %.0fs" % (args.out, len(verified), len(by_path), time.time() - started))
    print("stats:", dict(stats))
    for ext, count in remaining.most_common(12):
        print("  remaining %-14s %d" % (ext, count))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
