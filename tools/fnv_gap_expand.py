#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solve the remaining path_hash_file FNV gaps with an enriched candidate grammar.

Reuses tools/external_pathtable_hunt.py for the FNV table parsing, gap
construction, sniffing and alias checks, and extends the candidate grammar
with: the 143k newly mined path strings (glue-split and junk-stripped) as
direct candidates, per-gap anchors and sibling-stem transplants from the
combined vocabulary, plus richer stem mutations.  Every accepted mapping must
pass the FNV+length prefilter, the lexicographic gap interval, the mmh3
signature match against an unresolved entry and a content sniff of the
unpacked payload.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import io
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import external_pathtable_hunt as H

import npk_browser as B
import nxpk_mode3 as N

ext_ok = H.ext_ok
fnv1a = H.fnv1a
load_pathdict = H.load_pathdict
load_table = H.load_table
load_unresolved = H.load_unresolved
sniff = H.sniff

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
DEFAULT_OUT = SWEEP / "fnv-gap-expand-names.json"
NEW_PATHS = SWEEP / "pathtable-new-paths.txt.gz"
SOLVED_SOURCES = [
    SWEEP / "external-pathtable-names.json",
    SWEEP / "reference-graph-names.json",
    SWEEP / "04-pathtable-mined-names.json",
    SWEEP / "ppk-reference-verified.json",
]
NUM_RE = re.compile(r"\d+")
LOD_RE = re.compile(r"_lod([0-9])")


def load_new_paths(path: Path, roots: set[str]):
    raw = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            item = line.strip()
            if item:
                raw.append(item)
    pattern = re.compile("(?<=[A-Za-z0-9_])(?:" + "|".join(sorted(roots)) + ")/")
    cleaned = set()
    for item in raw:
        matches = [m.start() for m in pattern.finditer(item)]
        if matches:
            for start in matches:
                cleaned.add(item[start:])
        else:
            for root in roots:
                index = item.find(root + "/")
                if index > 0:
                    cleaned.add(item[index:])
                    break
            else:
                stripped = item.lstrip("0123456789_ ")
                if stripped[:1].isalpha() or stripped[:1] == ".":
                    cleaned.add(stripped)
    return raw, cleaned


def stem_mutations(stem: str):
    out = set()
    for match in NUM_RE.finditer(stem):
        width = len(match.group(0))
        base = int(match.group(0))
        head, tail = stem[: match.start()], stem[match.end():]
        for delta in (-10, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 10, 20, 100, 1000):
            if base + delta < 0:
                continue
            out.add(head + str(base + delta).zfill(width) + tail)
    if LOD_RE.search(stem):
        for digit in "0123456789":
            out.add(LOD_RE.sub("_lod" + digit, stem, count=1))
    else:
        for digit in "0123456789":
            out.add(stem + "_lod" + digit)
    parts = stem.split("_")
    for cut in (1, 2):
        if len(parts) > cut:
            out.add("_".join(parts[:-cut]))
    return out


def gen_rich(anchor: str, ext_list, suffix_list):
    directory, _, name = anchor.rpartition("/")
    prefix = (directory + "/") if directory else ""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    pool = set()
    for ext in ext_list:
        pool.add(prefix + stem + ext)
    pool.add(prefix + stem)
    wide_exts = ext_list[:14]
    for suffix in suffix_list[:40]:
        if not stem.endswith(suffix):
            for ext in wide_exts:
                pool.add(prefix + stem + suffix + ext)
    for word in ("new", "old", "high", "low", "mid", "common", "loading", "ui", "01", "02"):
        for ext in wide_exts:
            pool.add(prefix + stem + "_" + word + ext)
    for stem_variant in stem_mutations(stem):
        for ext in wide_exts:
            pool.add(prefix + stem_variant + ext)
        pool.add(prefix + stem_variant)
    return pool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--sibling-caps", type=int, default=400)
    args = parser.parse_args()

    started = time.time()
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    unresolved = load_unresolved(con, packages)
    records, table_info = load_table(con, packages)
    con.close()
    paths = load_pathdict(args.db.parent, None)
    print("unresolved sigs %d, table %d records, pathdict %d" % (
        len(unresolved), len(records), len(paths)), flush=True)

    solved_paths = {}
    for source in SOLVED_SOURCES:
        if source.is_file():
            data = json.loads(source.read_text("utf-8"))
            for target, item in data.get("mappings", {}).items():
                solved_paths[item["path"].replace("\\", "/").lower()] = target
    print("already-solved paths from previous batches: %d" % len(solved_paths), flush=True)

    known = {}
    known_set = set(paths)
    for path in paths:
        known.setdefault(fnv1a(path.replace("/", "\\")), path)
    roots = sorted({p.split("/", 1)[0] for p in paths if "/" in p})
    root_set = {r for r in roots if len(r) > 3}
    ext_counter = Counter()
    suffix_counter = Counter()
    dir_stems = defaultdict(set)
    for path in paths:
        directory, _, name = path.rpartition("/")
        if "." in name:
            ext_counter["." + name.rsplit(".", 1)[-1]] += 1
        stem = name.rsplit(".", 1)[0]
        if directory:
            dir_stems[directory].add(stem)
        parts = stem.split("_")
        suffix_counter["_" + parts[-1]] += 1
        if len(parts) >= 2:
            suffix_counter["_" + "_".join(parts[-2:])] += 1
    ext_list = [item for item, _ in ext_counter.most_common(100)]
    suffix_list = [item for item, _ in suffix_counter.most_common(80)]
    print("exts %d, suffixes %d, dirs %d, roots %d" % (
        len(ext_list), len(suffix_list), len(dir_stems), len(root_set)), flush=True)

    raw_new, cleaned_new = set(), set()
    if NEW_PATHS.is_file():
        raw_new, cleaned_new = load_new_paths(NEW_PATHS, root_set)
        for item in cleaned_new:
            directory, _, name = item.rpartition("/")
            if directory and "." in name:
                dir_stems[directory].add(name.rsplit(".", 1)[0])
        print("new paths: raw %d cleaned %d" % (len(raw_new), len(cleaned_new)), flush=True)

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
    print("runs %d, missing records %d  %.0fs" % (
        len(runs), unknown_total, time.time() - started), flush=True)

    solved = {}
    checked = 0

    def try_candidate(candidate, target_map, left, right, target_lengths=None):
        nonlocal checked
        if candidate in known_set or candidate in solved_paths or candidate in solved:
            return 0
        if left and right and not (left < candidate < right):
            return 0
        if target_lengths is not None and len(candidate) not in target_lengths:
            return 0
        checked += 1
        signature = fnv1a(candidate.replace("/", "\\"))
        if signature in known:
            return 0
        length = target_map.get(signature)
        if length is None or len(candidate) != length:
            return 0
        target_map.pop(signature, None)
        solved[candidate] = {"fnv": signature, "length": length, "after": left, "before": right}
        return 1

    extra_hits = 0
    global_targets = {}
    for _, items, _ in runs:
        for signature, length in items:
            global_targets.setdefault(signature, length)
    if cleaned_new:
        for candidate in cleaned_new:
            signature = fnv1a(candidate.replace("/", "\\"))
            if signature in known:
                continue
            length = global_targets.get(signature)
            if length is not None and len(candidate) == length and candidate not in known_set:
                if candidate in solved_paths or candidate in solved:
                    continue
                solved[candidate] = {"fnv": signature, "length": length, "after": None, "before": None}
                extra_hits += 1
    print("direct new-path hits: %d  %.0fs" % (extra_hits, time.time() - started), flush=True)

    sorted_new = sorted(cleaned_new)
    parent_dirs = defaultdict(set)
    for directory in dir_stems:
        parent, _, name = directory.rpartition("/")
        if parent:
            parent_dirs[parent].add(directory)
    limit = args.max_runs or len(runs)
    for index, (left, targets, right) in enumerate(runs[:limit]):
        target_map = {signature: length for signature, length in targets}
        target_lengths = set(target_map.values())
        pool = set()
        if left:
            pool |= gen_rich(left, ext_list, suffix_list)
        if right:
            pool |= gen_rich(right, ext_list, suffix_list)
        if left and right:
            low = bisect.bisect_right(sorted_new, left)
            high = bisect.bisect_left(sorted_new, right)
            for item in sorted_new[low:high][:2000]:
                pool |= gen_rich(item, ext_list, suffix_list)
        for directory in {item.rpartition("/")[0] for item in pool}:
            stems = dir_stems.get(directory)
            if not stems or len(stems) > args.sibling_caps:
                continue
            for stem in stems:
                for ext in ext_list:
                    pool.add(directory + "/" + stem + ext)
                for stem_variant in stem_mutations(stem):
                    for ext in ext_list[:14]:
                        pool.add(directory + "/" + stem_variant + ext)
        gap_dirs = {item.rpartition("/")[0] for item in pool}
        for directory in list(gap_dirs)[:8]:
            parent = directory.rpartition("/")[0]
            siblings = parent_dirs.get(parent, ())
            if not siblings or len(siblings) > args.sibling_caps:
                continue
            mid_exts = ext_list[:30]
            for sibling in siblings:
                if sibling == directory:
                    continue
                stems = dir_stems.get(sibling)
                if not stems or len(stems) > args.sibling_caps:
                    continue
                for stem in stems:
                    for ext in mid_exts:
                        candidate = directory + "/" + stem + ext
                        if len(candidate) in target_lengths:
                            pool.add(candidate)
                    for stem_variant in stem_mutations(stem):
                        for ext in mid_exts[:8]:
                            candidate = directory + "/" + stem_variant + ext
                            if len(candidate) in target_lengths:
                                pool.add(candidate)
        if left and right:
            pool = {item for item in pool if left < item < right}
        for candidate in pool:
            try_candidate(candidate, target_map, left, right, target_lengths)
        if index and index % 1500 == 0:
            print("  gap %d/%d checked=%d solved=%d %.0fs" % (
                index, limit, checked, len(solved), time.time() - started), flush=True)

    print("candidates solved %d (extra %d)  %.0fs" % (
        len(solved), extra_hits, time.time() - started), flush=True)

    mappings = {}
    rejected = []
    by_ext = Counter()
    unsolved_reason = Counter()
    unsolved_items = []
    named_sigs = set()
    con2 = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True, timeout=30)
    for (signature,) in con2.execute(
        "SELECT signature FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        named_sigs.add(signature)
    con2.close()
    for candidate, meta in solved.items():
        signature = N.path_hash(candidate)
        rows = unresolved.get(signature)
        if not rows:
            if signature in named_sigs:
                unsolved_reason["sig_already_named"] += 1
            else:
                unsolved_reason["no_catalog_entry"] += 1
                unsolved_items.append({"path": candidate, "reason": "no_catalog_entry"})
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
            unsolved_reason["sniff_incompatible"] += 1
            rejected.append({"path": candidate, "target": row["path"], "kind": kind, "ext": target_ext})
            continue
        evidence = "fnv=0x%08x len=%d mmh3=%d sniff=%s after=%s before=%s" % (
            meta["fnv"], meta["length"], signature, kind, meta["after"], meta["before"])
        mappings[row["path"]] = {
            "rule": "fnv-gap-expand",
            "path": candidate,
            "signature": signature,
            "evidence": evidence,
        }
        by_ext[target_ext] += 1
    print("mappings %d, rejected %d, unsolved reasons %s" % (
        len(mappings), len(rejected), dict(unsolved_reason)), flush=True)

    payload = {
        "generated_by": "tools/fnv_gap_expand.py",
        "total_unresolved": sum(len(items) for items in unresolved.values()),
        "named": len(mappings),
        "table": table_info,
        "pathdict": len(paths),
        "missing_records": unknown_total,
        "candidates_solved": len(solved),
        "rejected": len(rejected),
        "rejected_items": rejected[:200],
        "unsolved_reasons": dict(unsolved_reason),
        "unsolved_items": unsolved_items[:2000],
        "by_ext": dict(by_ext.most_common()),
        "stats": {
            "gaps": len(runs),
            "checked": checked,
            "direct_new_path_hits": extra_hits,
            "new_paths_raw": len(raw_new),
            "new_paths_cleaned": len(cleaned_new),
            "already_solved_excluded": len(solved_paths),
            "seconds": round(time.time() - started, 1),
        },
        "mappings": mappings,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  %d mappings  %.0fs" % (args.out, len(mappings), time.time() - started), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
