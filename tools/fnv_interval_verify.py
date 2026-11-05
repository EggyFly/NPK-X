#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independently re-verify every mapping produced by fnv_interval_solve.

For each mapping the checker recomputes the FNV-1a of the backslash path and
compares it against the evidence field, recomputes mmh3 and matches the
catalog unresolved entry by signature, re-checks the path length against the
record length, and re-reads the payload for the magic sniff.  Any mismatch is
listed; a clean run prints pass counts per check.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import external_pathtable_hunt as H
import npk_browser as B
import nxpk_mode3 as N

fnv1a = H.fnv1a
sniff = H.sniff
ext_ok = H.ext_ok

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
EVIDENCE_RE = re.compile(r"fnv=0x([0-9a-f]+) len=(\d+)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=SWEEP / "fnv-interval-names.json")
    parser.add_argument("--sample", type=int, default=0, help="verify only N mappings (0 = all)")
    args = parser.parse_args()

    started = time.time()
    data = json.loads(args.json.read_text(encoding="utf-8"))
    mappings = data["mappings"]
    items = list(mappings.items())
    if args.sample:
        items = items[: args.sample]
    print("verifying %d mappings from %s" % (len(items), args.json), flush=True)

    con = sqlite3.connect("file:%s?mode=ro" % (B.default_cache_dir() / "catalog.sqlite"), uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    unresolved = {}
    for row in con.execute("SELECT * FROM files WHERE path LIKE '_unresolved/%'"):
        unresolved.setdefault(row["signature"], []).append(row)
    con.close()

    checks = Counter()
    failures = []
    for target, item in items:
        path = item["path"]
        signature = item["signature"]
        match = EVIDENCE_RE.search(item.get("evidence", ""))
        fnv_field = int(match.group(1), 16) if match else None
        len_field = int(match.group(2)) if match else None
        fnv_recomputed = fnv1a(path.replace("/", "\\"))
        if fnv_field is not None and fnv_recomputed != fnv_field:
            checks["fnv_evidence_mismatch"] += 1
            failures.append({"target": target, "path": path, "check": "fnv_evidence", "detail": "0x%08x vs 0x%08x" % (fnv_field, fnv_recomputed)})
            continue
        if len_field is not None and len(path) != len_field:
            checks["length_evidence_mismatch"] += 1
            failures.append({"target": target, "path": path, "check": "length_evidence", "detail": "%d vs %d" % (len(path), len_field)})
            continue
        mmh3_recomputed = N.path_hash(path)
        if mmh3_recomputed != signature:
            checks["mmh3_mismatch"] += 1
            failures.append({"target": target, "path": path, "check": "mmh3", "detail": "%d vs %d" % (mmh3_recomputed, signature)})
            continue
        rows = unresolved.get(signature)
        if not rows:
            checks["signature_not_unresolved"] += 1
            failures.append({"target": target, "path": path, "check": "catalog", "detail": "signature not in _unresolved"})
            continue
        if target not in {row["path"] for row in rows}:
            checks["target_mismatch"] += 1
            failures.append({"target": target, "path": path, "check": "target_path", "detail": "target row missing"})
            continue
        entry = rows[0]
        try:
            payload = B.read_entry_bytes(packages[entry["package"]], entry)
        except Exception as exc:
            checks["payload_read_error"] += 1
            failures.append({"target": target, "path": path, "check": "payload", "detail": str(exc)})
            continue
        kind = sniff(payload)
        if not ext_ok(path, Path(entry["path"]).suffix, kind):
            checks["sniff_incompatible"] += 1
            failures.append({"target": target, "path": path, "check": "sniff", "detail": "kind=%s ext=%s" % (kind, Path(entry["path"]).suffix)})
            continue
        checks["pass"] += 1

    print("checks: %s  %.0fs" % (dict(checks), time.time() - started), flush=True)
    if failures:
        print("failures (up to 40):")
        for fail in failures[:40]:
            print("  %s" % json.dumps(fail, ensure_ascii=False))
    out = SWEEP / "fnv-interval-verify.json"
    out.write_text(json.dumps({
        "generated_by": "tools/fnv_interval_verify.py",
        "source": str(args.json),
        "verified": len(items),
        "checks": dict(checks),
        "failures": failures[:200],
        "stats": {"seconds": round(time.time() - started, 1)},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
