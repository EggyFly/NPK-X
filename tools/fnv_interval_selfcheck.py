#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check the interval enumerator by hiding known paths as fake gaps.

A sample of already-named paths is removed from the anchor set, turning them
into ordinary gap records of the FNV table.  The numeric, vocab and MITM
passes are then run on exactly these synthetic gaps; the recovery rate is a
direct measurement of enumerator completeness.  Recovery conditions mirror
the real solver: FNV+length hit, lexicographic interval, mmh3 against the
hidden signature and a payload sniff of the catalog entry.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import external_pathtable_hunt as H
import fnv_interval_solve as S
import npk_browser as B
import nxpk_mode3 as N

fnv1a = H.fnv1a


def explain_miss(true_path, low, high, max_free, vocab):
    reasons = []
    name = true_path.rsplit("/", 1)[-1]
    ext = S.ext_of(name)
    if low and high:
        directory = low.rpartition("/")[0]
        same = directory == high.rpartition("/")[0] and bool(directory)
        if not same:
            reasons.append("cross_dir")
        stem = name[: len(name) - len(ext)] if ext else name
        if ext.lower() != ext or any(ch not in S.CS + "." for ch in stem):
            reasons.append("charset")
        if ext and ext not in {S.ext_of(low.rsplit("/", 1)[-1]), S.ext_of(high.rsplit("/", 1)[-1])}:
            reasons.append("ext_outside_anchors")
        stem_len = len(name) - len(ext)
        if stem_len <= 0:
            reasons.append("free_negative")
            return "+".join(reasons)
        lo_stem = low.rsplit("/", 1)[-1][:stem_len].ljust(stem_len, "0")
        hi_stem = high.rsplit("/", 1)[-1][:stem_len].ljust(stem_len, "z")
        mb = S.mid_bounds_stem(lo_stem, hi_stem)
        if mb is None:
            reasons.append("no_mid_bounds")
        else:
            prefix, mid_a, mid_b, suffix = mb
            for suffix_len in (len(suffix), 0):
                free_len = stem_len - len(prefix) - suffix_len
                if free_len < 0:
                    reasons.append("free_negative")
                elif free_len > max_free:
                    reasons.append("free_%d" % free_len)
    return "+".join(reasons) or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=300)
    parser.add_argument("--max-free", type=int, default=6)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    started = time.time()
    random.seed(args.seed)
    db = B.default_cache_dir() / "catalog.sqlite"
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    records, table_info = H.load_table(con, packages)
    unresolved = H.load_unresolved(con, packages)
    con.close()
    paths = S.load_paths(db.parent, None)
    known = {}
    for path in paths:
        known.setdefault(fnv1a(path.replace("/", "\\")), path)

    hidden = []
    for path in sorted(paths):
        if path.startswith(("_unresolved/", "_script/")):
            continue
        fnv_sig = fnv1a(path.replace("/", "\\"))
        if known.get(fnv_sig) == path:
            hidden.append((path, fnv_sig, len(path)))
    random.shuffle(hidden)
    sample = hidden[: args.sample]
    sample_set = {signature for _, signature, _ in sample}
    known2 = {signature: path for path, signature, _ in hidden if signature not in sample_set}
    paths2 = paths - {path for path, _, _ in sample}
    print("hiding %d of %d anchors  %.0fs" % (len(sample), len(known), time.time() - started), flush=True)

    runs = S.build_runs(records, known2)
    gap_total = sum(len(items) for _, items, _ in runs)
    truth = {signature: path for path, signature, _ in sample}
    fake_sigs = {N.path_hash(path) for path, _, _ in sample}
    synthetic = []
    for low, items, high in runs:
        for signature, length in items:
            if signature in sample_set:
                synthetic.append((low, (signature, length), high))
    print("runs %d, gap records %d, synthetic gaps located %d  %.0fs" % (
        len(runs), gap_total, len(synthetic), time.time() - started), flush=True)

    new_cleaned = set()
    if S.NEW_PATHS.is_file():
        roots = {p.split("/", 1)[0] for p in paths2 if "/" in p}
        _raw, new_cleaned = S.load_new_paths(S.NEW_PATHS, roots)
    vocab = S.Vocab(paths2, new_cleaned)

    known_set = set(paths2)
    found = 0
    verdict = Counter()
    fails = []
    miss_reason = Counter()
    for low, (signature, length), high in synthetic:
        true_path = truth[signature]
        candidates = S.enum_gap(signature, length, low, high, vocab, args.max_free, False, True, None, 0, fake_sigs)
        candidates = [c for c in set(candidates) if c not in known_set]
        recovered = [c for c in candidates if c == true_path]
        if not candidates:
            verdict["no_candidate"] += 1
            miss_reason[explain_miss(true_path, low, high, args.max_free, vocab)] += 1
            fails.append({"true": true_path, "signature": "0x%08x" % signature, "low": low, "high": high, "len": length})
        elif recovered:
            if len(candidates) == 1:
                verdict["recovered"] += 1
            else:
                verdict["recovered_ambiguous"] += 1
            found += 1
        else:
            verdict["candidate_collision_only"] += 1
            fails.append({"true": true_path, "candidates": candidates[:4], "low": low, "high": high, "len": length})
        if sum(verdict.values()) % 50 == 0:
            print("  %d/%d recovered %d  %.0fs" % (
                sum(verdict.values()), len(synthetic), found, time.time() - started), flush=True)

    print("miss reasons: %s" % dict(miss_reason), flush=True)

    print("synthetic %d, recovered %d, verdicts %s  %.0fs" % (
        len(synthetic), found, dict(verdict), time.time() - started), flush=True)
    out = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "fnv-interval-selfcheck.json"
    payload = {
        "generated_by": "tools/fnv_interval_selfcheck.py",
        "sample": len(sample),
        "max_free": args.max_free,
        "synthetic": len(synthetic),
        "recovered": found,
        "verdicts": dict(verdict),
        "fail_sample": fails[:40],
        "stats": {"seconds": round(time.time() - started, 1)},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s" % out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
