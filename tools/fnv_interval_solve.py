#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enumerate path_hash_file gap records inside the lexicographic interval of
their nearest known anchors.

Every gap record is (fnv, len) and sits strictly between two already-named
paths A and B in the sorted table.  For each (dir, ext) pair consistent with
the interval the stem length is fully determined, so the stem is enumerated
by a depth-first walk over [a-z0-9_-.] with three prunes: the lexicographic
bounds, the exact remaining byte budget, and an inverse-FNV check on the
last 1-2 bytes.  A candidate only counts when FNV+length hit the gap record,
mmh3(backslash path) hits an unresolved signature, and the payload magic is
compatible with the candidate extension.
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
from array import array
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import external_pathtable_hunt as H
import npk_browser as B
import nxpk_mode3 as N

fnv1a = H.fnv1a
load_table = H.load_table
load_unresolved = H.load_unresolved
sniff = H.sniff
ext_ok = H.ext_ok

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
NEW_PATHS = SWEEP / "pathtable-new-paths.txt.gz"
CS = "0123456789_abcdefghijklmnopqrstuvwxyz"
CS_B = [ord(c) for c in CS]
FNV_P, FNV_MASK = 0x01000193, 0xFFFFFFFF
P_INV = pow(FNV_P, -1, 1 << 32)


def fnv_step(state: int, byte: int) -> int:
    return ((state ^ byte) * FNV_P) & FNV_MASK


def fnv_back(final: int, byte: int) -> int:
    return ((final * P_INV) & FNV_MASK) ^ byte


def load_paths(cache: Path, con: sqlite3.Connection | None):
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


def build_runs(records, known):
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
    return runs


def stem_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def ext_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[1] if "." in name else ""


def dir_compatible(directory: str, low: str, high: str) -> bool:
    for bound in (low, high):
        if bound and bound.startswith(directory) and len(bound) > len(directory) and bound[len(directory)] == "/":
            return True
        if bound and bound[: len(directory)] > directory:
            return True
    return False


def load_new_paths(path: Path, roots: set[str]):
    raw = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            item = line.strip()
            if item:
                raw.append(item)
    cleaned = set()
    for item in raw:
        for root in roots:
            index = item.find(root + "/")
            if index > 0:
                item = item[index:]
                break
        cleaned.add(item)
    return raw, cleaned


def interval_dirs(dirs_sorted, low, high):
    out = []
    if low:
        parent = low.rpartition("/")[0]
        while parent:
            out.append(parent)
            parent = parent.rpartition("/")[0]
    if high:
        parent = high.rpartition("/")[0]
        while parent:
            out.append(parent)
            parent = parent.rpartition("/")[0]
    if low and high:
        lo = bisect.bisect_right(dirs_sorted, low)
        hi = bisect.bisect_left(dirs_sorted, high)
        out.extend(dirs_sorted[lo:hi])
    return out


def numeric_candidates(low: str, high: str):
    out = []
    directory, low_file = low.rpartition("/")[0], low.rsplit("/", 1)[-1]
    directory2, high_file = high.rpartition("/")[0], high.rsplit("/", 1)[-1]
    if directory != directory2 or not directory:
        return out
    low_stem = low_file.rsplit(".", 1)[0] if "." in low_file else low_file
    high_stem = high_file.rsplit(".", 1)[0] if "." in high_file else high_file
    if len(low_stem) == len(high_stem):
        lcp = 0
        while lcp < len(low_stem) and low_stem[lcp] == high_stem[lcp]:
            lcp += 1
        cs = 0
        while cs < len(low_stem) - lcp and low_stem[len(low_stem) - 1 - cs] == high_stem[len(high_stem) - 1 - cs]:
            cs += 1
        mid_a, mid_b = low_stem[lcp: len(low_stem) - cs], high_stem[lcp: len(high_stem) - cs]
        if mid_a.isdigit() and mid_b.isdigit() and len(mid_a) == len(mid_b):
            width = len(mid_a)
            lo, hi = int(mid_a), int(mid_b)
            if hi - lo <= 200000:
                prefix, suffix = low_stem[:lcp], low_stem[len(low_stem) - cs:]
                for value in range(lo + 1, hi):
                    out.append(prefix + str(value).zfill(width) + suffix)
            return out
    base = re.match(r"^(.*?)(\d+)$", low_stem)
    base_b = re.match(r"^(.*?)(\d+)$", high_stem)
    if base and base_b and base.group(1) == base_b.group(1) and base.group(1):
        va, vb = int(base.group(2)), int(base_b.group(2))
        lo_v, hi_v = min(va, vb), max(va, vb)
        if hi_v - lo_v <= 200000:
            widths = {len(base.group(2)), len(base_b.group(2))}
            for value in range(lo_v + 1, hi_v):
                for width in widths:
                    if width >= len(str(value)):
                        out.append(base.group(1) + str(value).zfill(width))
    return out


def mid_bounds_stem(a: str, b: str):
    if len(a) != len(b):
        return None
    lcp = 0
    while lcp < len(a) and a[lcp] == b[lcp]:
        lcp += 1
    cs = 0
    while cs < len(a) - lcp and a[len(a) - 1 - cs] == b[len(b) - 1 - cs]:
        cs += 1
    return a[:lcp], a[lcp: len(a) - cs], b[lcp: len(b) - cs], a[len(a) - cs:]


def mid_bounds(low_file: str, high_file: str):
    return mid_bounds_stem(stem_of(low_file), stem_of(high_file))


class Vocab:
    def __init__(self, paths, new_paths=()):
        self.dir_stems = defaultdict(set)
        self.dir_names = defaultdict(list)
        self.ext_by_dir = defaultdict(Counter)
        self.stems_by_len = defaultdict(set)
        self.dirs_set = set()
        self.ext_global = Counter()
        self.tail_global = Counter()
        for path in paths:
            directory, _, name = path.rpartition("/")
            stem = name.rsplit(".", 1)[0] if "." in name else name
            ext = "." + name.rsplit(".", 1)[1] if "." in name else ""
            if directory:
                self.dirs_set.add(directory)
                self.dir_stems[directory].add(stem)
                self.dir_names[directory].append(name)
                self.ext_by_dir[directory][ext] += 1
            self.stems_by_len[len(stem)].add(stem)
            if ext:
                self.ext_global[ext] += 1
            if "_" in stem:
                self.tail_global["_" + stem.rsplit("_", 1)[-1]] += 1
        for directory in self.dir_names:
            self.dir_names[directory].sort()
        for item in new_paths:
            directory, _, name = item.rpartition("/")
            stem = name.rsplit(".", 1)[0] if "." in name else name
            if directory:
                self.dirs_set.add(directory)
                self.dir_stems[directory].add(stem)
        self.dirs_sorted = sorted(self.dirs_set)
        self.parent_dirs = defaultdict(set)
        for directory in self.dirs_set:
            parent, _, _ = directory.rpartition("/")
            if parent:
                self.parent_dirs[parent].add(directory)
        self.top_exts = [ext for ext, _ in self.ext_global.most_common(20)]
        self.tails = [tail for tail, _ in self.tail_global.most_common(300)]

    def exts_for(self, directory, low, high):
        out = []
        for bound in (low, high):
            if bound:
                ext = ext_of(bound)
                if ext and ext not in out:
                    out.append(ext)
        for ext, _ in self.ext_by_dir.get(directory, Counter()).most_common(2):
            if ext not in out:
                out.append(ext)
        for ext in self.top_exts:
            if ext not in out:
                out.append(ext)
        return out

    def stem_pool(self, directory, stem_len, with_global, global_cap=800):
        pool = {stem for stem in self.dir_stems.get(directory, ()) if len(stem) == stem_len}
        parent = directory.rpartition("/")[0]
        siblings = self.parent_dirs.get(parent, ())
        if len(pool) < 8 and 0 < len(siblings) <= 200:
            for sibling in siblings:
                if sibling == directory:
                    continue
                for stem in self.dir_stems.get(sibling, ()):
                    if len(stem) == stem_len:
                        pool.add(stem)
        if with_global and len(pool) < 32:
            take = 0
            for stem in self.stems_by_len.get(stem_len, ()):
                pool.add(stem)
                take += 1
                if take >= global_cap:
                    break
        return pool

    def dir_cands(self, low, high, cap=40):
        if low and high and low.rpartition("/")[0] == high.rpartition("/")[0]:
            return [low.rpartition("/")[0]]
        pool = interval_dirs(self.dirs_sorted, low, high)
        if len(pool) > cap:
            step = len(pool) / cap
            pool = [pool[int(i * step)] for i in range(cap)]
        return pool


def enum_gap(signature, length, low, high, vocab, max_free, with_global=False, with_mitm=True, free_hist=None, mitm_dir_exts=0, unresolved_sigs=None):
    candidates = []
    if not low or not high:
        return candidates
    directory = low.rpartition("/")[0]
    low_name, high_name = low.rsplit("/", 1)[-1], high.rsplit("/", 1)[-1]
    same_dir = directory == high.rpartition("/")[0] and bool(directory)
    if same_dir:
        candidates += enum_in_dir(
            signature, length, low, high, directory, low_name, high_name,
            vocab, max_free, with_global, with_mitm, free_hist, mitm_dir_exts, True)
    else:
        for cand_dir in vocab.dir_cands(low, high, cap=40):
            for ext in vocab.exts_for(cand_dir, low, high):
                stem_len = length - len(cand_dir) - 1 - len(ext)
                if stem_len <= 0:
                    continue
                for stem in vocab.stem_pool(cand_dir, stem_len, False):
                    path = cand_dir + "/" + stem + ext
                    if low < path < high:
                        fnv_check = fnv1a(path.replace("/", "\\"))
                        if fnv_check == signature and len(path) == length:
                            candidates.append(path)
            if not with_mitm:
                continue
            names = vocab.dir_names.get(cand_dir)
            if not names:
                continue
            lo_bound = low[len(cand_dir) + 1:] if low.startswith(cand_dir + "/") else ""
            hi_bound = high[len(cand_dir) + 1:] if high.startswith(cand_dir + "/") else None
            i = bisect.bisect_right(names, lo_bound)
            j = bisect.bisect_left(names, hi_bound) if hi_bound else len(names)
            pairs = []
            if i < j:
                for k in range(i, min(j, i + 3)):
                    pairs.append((names[k - 1] if k > 0 else None, names[k]))
                if j < len(names):
                    pairs.append((names[j - 1], names[j]))
            elif i > 0 and i < len(names):
                pairs.append((names[i - 1], names[i]))
            for prev_name, next_name in pairs[:4]:
                if prev_name is None:
                    continue
                ext_c = ext_of(next_name)
                stem_len_c = length - len(cand_dir) - 1 - len(ext_c)
                if stem_len_c <= 0:
                    continue
                prev_stem = prev_name.rsplit(".", 1)[0] if "." in prev_name else prev_name
                next_stem = next_name.rsplit(".", 1)[0] if "." in next_name else next_name
                if len(prev_stem) != stem_len_c and len(next_stem) != stem_len_c:
                    continue
                candidates += enum_in_dir(
                    signature, length, low, high, cand_dir, prev_name, next_name,
                    vocab, max_free, False, True, None, mitm_dir_exts, False)
    if unresolved_sigs is not None:
        candidates = [c for c in candidates if N.path_hash(c) in unresolved_sigs]
    return candidates


def enum_in_dir(signature, length, low, high, directory, ref_lo_name, ref_hi_name, vocab, max_free, with_global, with_mitm, free_hist, mitm_dir_exts, with_vocab):
    candidates = []
    ref_low = directory + "/" + ref_lo_name
    ref_high = directory + "/" + ref_hi_name
    ref_exts = sorted({ext_of(ref_lo_name), ext_of(ref_hi_name)})
    for stem in numeric_candidates(ref_low, ref_high):
        for ext in ref_exts:
            path = directory + "/" + stem + ext
            if low < path < high:
                fnv_check = fnv1a(path.replace("/", "\\"))
                if fnv_check == signature and len(path) == length:
                    candidates.append(path)
    if with_vocab:
        for ext in vocab.exts_for(directory, low, high):
            stem_len = length - len(directory) - 1 - len(ext)
            if stem_len <= 0:
                continue
            for stem in vocab.stem_pool(directory, stem_len, with_global):
                path = directory + "/" + stem + ext
                if low < path < high:
                    fnv_check = fnv1a(path.replace("/", "\\"))
                    if fnv_check == signature and len(path) == length:
                        candidates.append(path)
    ref_lo_stem = ref_lo_name.rsplit(".", 1)[0] if "." in ref_lo_name else ref_lo_name
    segs = ref_lo_stem.split("_")
    prefix_stems = {"_".join(segs[:k]) for k in range(1, min(len(segs), 6) + 1)}
    prefix_stems.add(ref_lo_stem)
    prefix_stems.discard("")
    for base_stem in prefix_stems:
        for tail in vocab.tails:
            stem = base_stem + tail
            for ext in ref_exts:
                path = directory + "/" + stem + ext
                if low < path < high:
                    fnv_check = fnv1a(path.replace("/", "\\"))
                    if fnv_check == signature and len(path) == length:
                        candidates.append(path)
    if with_mitm:
        mitm_exts = list(ref_exts)
        for ext, _ in vocab.ext_by_dir.get(directory, Counter()).most_common(mitm_dir_exts):
            if ext not in mitm_exts:
                mitm_exts.append(ext)
        for ext in mitm_exts:
            stem_len = length - len(directory) - 1 - len(ext)
            if stem_len <= 0:
                continue
            lo_stem = ref_lo_name[:stem_len].ljust(stem_len, "0")
            hi_stem = ref_hi_name[:stem_len].ljust(stem_len, "z")
            mb = mid_bounds_stem(lo_stem, hi_stem)
            if mb is None:
                continue
            prefix, mid_a, mid_b, suffix_stem = mb
            for suffix_len, mid_lo, mid_hi in (
                (len(suffix_stem), mid_a, mid_b),
                (0, lo_stem[len(prefix):], hi_stem[len(prefix):]),
            ):
                free_len = stem_len - len(prefix) - suffix_len
                if free_len < 0 or free_len > max_free:
                    if free_hist is not None:
                        free_hist[free_len if 0 <= free_len <= 64 else 65] += 1
                    continue
                h_start = fnv1a((directory + "/" + prefix).replace("/", "\\"))
                h_end = signature
                for byte in reversed(ext.encode()):
                    h_end = fnv_back(h_end, byte)
                for byte in reversed(lo_stem[len(prefix): len(prefix) + suffix_len].encode()):
                    h_end = fnv_back(h_end, byte)
                frees = mitm_free(h_start, h_end, free_len, mid_lo, mid_hi)
                if frees is None:
                    if free_hist is not None:
                        free_hist[-2] += 1
                    continue
                for free in frees:
                    stem = prefix + free + (suffix_stem if suffix_len else "")
                    path = directory + "/" + stem + ext
                    if low < path < high:
                        fnv_check = fnv1a(path.replace("/", "\\"))
                        if fnv_check == signature and len(path) == length:
                            candidates.append(path)
    return candidates


def _code_str(code: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(CS[code % 38])
        code //= 38
    return "".join(reversed(out))


def mitm_free(h_start: int, h_end: int, free_len: int, mid_lo: str, mid_hi: str, budget=2500000, back_max=4):
    if free_len <= 0:
        return []
    target_d0 = max((free_len + 1) // 2, free_len - back_max)
    n_lo, n_hi = len(mid_lo), len(mid_hi)
    states = array("Q", [h_start])
    codes = array("Q", [0])
    flags = array("B", [3])
    chosen = target_d0
    for depth in range(1, target_d0 + 1):
        d1 = depth - 1
        if d1 > 0 and len(states) * 37 > budget and d1 >= free_len - back_max:
            chosen = d1
            break
        if len(states) * 37 > 4000000:
            return None
        nxt_states = array("Q")
        nxt_codes = array("Q")
        nxt_flags = array("B")
        for pos in range(len(states)):
            state = states[pos]
            flag = flags[pos]
            if flag & 1 and d1 < n_lo:
                lo_i = bisect.bisect_left(CS_B, ord(mid_lo[d1]))
                if lo_i >= len(CS_B):
                    continue
            else:
                lo_i = 0
            if flag & 2 and d1 < n_hi:
                hi_i = bisect.bisect_right(CS_B, ord(mid_hi[d1])) - 1
                if hi_i < 0:
                    continue
            else:
                hi_i = len(CS_B) - 1
            for index in range(lo_i, hi_i + 1):
                byte = CS_B[index]
                nxt_states.append(((state ^ byte) * FNV_P) & FNV_MASK)
                nxt_codes.append(codes[pos] * 38 + index)
                nf = 0
                if flag & 1 and d1 < n_lo and byte == ord(mid_lo[d1]):
                    nf = 1
                if flag & 2 and d1 < n_hi and byte == ord(mid_hi[d1]):
                    nf |= 2
                nxt_flags.append(nf)
        states, codes, flags = nxt_states, nxt_codes, nxt_flags
        if not len(states):
            return []
    if free_len - chosen > back_max:
        return None
    fwd = {}
    for pos in range(len(states)):
        s = states[pos]
        if s not in fwd:
            fwd[s] = codes[pos]
    back_depth = free_len - chosen
    hits = []
    bwd_states = array("Q", [h_end])
    bwd_codes = array("Q", [0])
    for _depth in range(back_depth):
        nxt_states = array("Q")
        nxt_codes = array("Q")
        for pos in range(len(bwd_states)):
            rotated = (bwd_states[pos] * P_INV) & FNV_MASK
            base_code = bwd_codes[pos] * 38
            for index, byte in enumerate(CS_B):
                nxt_states.append(rotated ^ byte)
                nxt_codes.append(base_code + index)
        bwd_states, bwd_codes = nxt_states, nxt_codes
    for pos in range(len(bwd_states)):
        fcode = fwd.get(bwd_states[pos])
        if fcode is None:
            continue
        tail_chars = []
        tc = bwd_codes[pos]
        for _ in range(back_depth):
            tail_chars.append(CS[tc % 38])
            tc //= 38
        free = _code_str(fcode, chosen) + "".join(tail_chars)
        if mid_lo <= free <= mid_hi:
            hits.append(free)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["recon", "solve"], default="recon")
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--out", type=Path, default=SWEEP / "fnv-interval-names.json")
    parser.add_argument("--max-free", type=int, default=6, help="max free chars for the stem DFS")
    parser.add_argument("--num-range", type=int, default=2000, help="numeric sweep width per group")
    parser.add_argument("--max-runs", type=int, default=0)
    args = parser.parse_args()

    started = time.time()
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    unresolved = load_unresolved(con, packages)
    records, table_info = load_table(con, packages)
    named_sigs = set()
    for (signature,) in con.execute(
        "SELECT signature FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        named_sigs.add(signature)
    con.close()

    paths = load_paths(args.db.parent, None)
    known = {}
    for path in paths:
        known.setdefault(fnv1a(path.replace("/", "\\")), path)
    print("unresolved sigs %d, table %d records, pathdict %d, named anchors %d  %.0fs" % (
        len(unresolved), len(records), len(paths), len(known), time.time() - started), flush=True)

    runs = build_runs(records, known)
    gap_total = sum(len(items) for _, items, _ in runs)
    print("runs %d, gap records %d  %.0fs" % (len(runs), gap_total, time.time() - started), flush=True)

    if args.phase == "recon":
        recon(runs, args)
        print("seconds %.0f" % (time.time() - started))
        return 0
    known_set = set(paths)
    payload = solve_all(args, runs, unresolved, named_sigs, packages, paths, known, known_set, table_info, started)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  %d mappings  %.0fs" % (args.out, len(payload["mappings"]), time.time() - started), flush=True)
    return 0


def solve_all(args, runs, unresolved, named_sigs, packages, paths, known, known_set, table_info, started):
    new_cleaned = set()
    if NEW_PATHS.is_file():
        roots = {p.split("/", 1)[0] for p in paths if "/" in p}
        raw_new, new_cleaned = load_new_paths(NEW_PATHS, roots)
        print("new-path vocab: raw %d  %.0fs" % (len(new_cleaned), time.time() - started), flush=True)
    vocab = Vocab(paths, new_cleaned)
    unresolved_sigs = set(unresolved.keys())
    print("vocab dirs %d  %.0fs" % (len(vocab.dirs_set), time.time() - started), flush=True)

    solutions = defaultdict(list)
    seen_candidates = set()
    free_hist = Counter()

    def accept(path, signature, low, high):
        if path in seen_candidates or path in known_set:
            return False
        seen_candidates.add(path)
        solutions[signature].append((path, low, high))
        return True

    def sweep_pass(label, max_free, with_global, with_mitm=True, track_free=False, mitm_dir_exts=0):
        hits = 0
        for index, (low, items, high) in enumerate(runs):
            for signature, length in items:
                if signature in solutions:
                    continue
                for candidate in enum_gap(
                    signature, length, low, high, vocab, max_free,
                    with_global, with_mitm, free_hist if track_free else None,
                    mitm_dir_exts, unresolved_sigs,
                ):
                    if accept(candidate, signature, low, high):
                        hits += 1
            if index and index % 1000 == 0:
                print("  %s %d/%d records-hit %d  %.0fs" % (
                    label, index, len(runs), len(solutions), time.time() - started), flush=True)
        return hits

    print("== sweep 1 (local vocab + numeric + mitm<=%d)  %.0fs" % (args.max_free, time.time() - started), flush=True)
    hits1 = sweep_pass("sweep1", args.max_free, False, track_free=True)
    print("sweep1 records %d  %.0fs" % (hits1, time.time() - started), flush=True)
    print("== sweep 2 (+global stem pool)  %.0fs" % (time.time() - started), flush=True)
    hits2 = sweep_pass("sweep2", args.max_free, True, track_free=False)
    print("sweep2 records %d  %.0fs" % (hits2, time.time() - started), flush=True)
    print("== sweep 3 (mitm<=%d, exts+dir, +global)  %.0fs" % (args.max_free + 2, time.time() - started), flush=True)
    hits3 = sweep_pass("sweep3", args.max_free + 2, True, mitm_dir_exts=6)
    print("sweep3 records %d  %.0fs" % (hits3, time.time() - started), flush=True)

    mappings = {}
    verdict = Counter()
    ambiguous = []
    for record_fnv, cands in solutions.items():
        passed = []
        for candidate, low, high in cands:
            signature = N.path_hash(candidate)
            rows = unresolved.get(signature)
            if not rows:
                verdict["no_catalog_entry"] += 1
                continue
            entry = rows[0]
            target_ext = Path(entry["path"]).suffix
            try:
                data = B.read_entry_bytes(packages[entry["package"]], entry)
                kind = sniff(data)
            except Exception:
                kind = "unreadable"
            if not ext_ok(candidate, target_ext, kind):
                verdict["sniff_rejected"] += 1
                continue
            passed.append((candidate, signature, entry, kind, low, high))
        if len(passed) == 1:
            candidate, signature, entry, kind, low, high = passed[0]
            evidence = "fnv=0x%08x len=%d mmh3=%d sniff=%s after=%s before=%s" % (
                record_fnv, len(candidate), signature, kind, low or "", high or "")
            mappings[entry["path"]] = {
                "rule": "fnv-interval",
                "path": candidate,
                "signature": signature,
                "evidence": evidence,
            }
            verdict["named"] += 1
        elif len(passed) > 1:
            sigs = {item[1] for item in passed}
            if len(sigs) == 1:
                verdict["ambiguous"] += 1
                ambiguous.append({"fnv": "0x%08x" % record_fnv, "candidates": [item[0] for item in passed]})
            else:
                for candidate, signature, entry, kind, low, high in passed:
                    mappings[entry["path"]] = {
                        "rule": "fnv-interval-collision",
                        "path": candidate,
                        "signature": signature,
                        "evidence": "fnv=0x%08x collision %d candidates sniff=%s" % (record_fnv, len(passed), kind),
                    }
                verdict["collision_named"] += 1
    return build_payload(
        args, runs, solutions, mappings, verdict, ambiguous, free_hist,
        table_info, paths, started)


def build_payload(args, runs, solutions, mappings, verdict, ambiguous, free_hist, table_info, paths, started):
    bucket_unsolved = Counter()
    bucket_reason = defaultdict(Counter)
    for low, items, high in runs:
        low_name = low.rsplit("/", 1)[-1] if low else ""
        high_name = high.rsplit("/", 1)[-1] if high else ""
        hood_ext = ""
        for name in (low_name, high_name):
            ext = Path(name).suffix
            if ext:
                hood_ext = ext
                break
        for signature, length in items:
            if solutions.get(signature):
                continue
            reason = classify_unsolved(low, high, length, args.max_free)
            bucket_unsolved[hood_ext] += 1
            bucket_reason[reason][hood_ext] += 1
    named_by_ext = Counter()
    for target_path in mappings:
        named_by_ext[Path(target_path).suffix] += 1
    payload = {
        "generated_by": "tools/fnv_interval_solve.py",
        "table": table_info,
        "pathdict": len(paths),
        "gap_records": sum(len(items) for _, items, _ in runs),
        "runs": len(runs),
        "named": len(mappings),
        "verdict": dict(verdict),
        "bucket_named": dict(named_by_ext.most_common()),
        "bucket_unsolved_neighborhood": dict(bucket_unsolved.most_common()),
        "unsolved_reason_by_bucket": {
            reason: dict(counter.most_common()) for reason, counter in bucket_reason.items()
        },
        "free_hist_mitm_skipped": {str(k): v for k, v in sorted(free_hist.items())},
        "ambiguous": ambiguous[:500],
        "stats": {"seconds": round(time.time() - started, 1), "max_free": args.max_free},
        "mappings": mappings,
    }
    return payload


def classify_unsolved(low, high, length, max_free):
    if not low or not high:
        return "table_edge"
    directory = low.rpartition("/")[0]
    if directory != high.rpartition("/")[0] or not directory:
        return "cross_dir"
    low_name, high_name = low.rsplit("/", 1)[-1], high.rsplit("/", 1)[-1]
    for ext in {Path(low_name).suffix, Path(high_name).suffix}:
        stem_len = length - len(directory) - 1 - len(ext)
        if stem_len <= 0:
            return "stem_len_negative"
    ext = Path(low_name).suffix
    stem_len = length - len(directory) - 1 - len(ext)
    lo_stem = low_name[:stem_len].ljust(stem_len, "0")
    hi_stem = high_name[:stem_len].ljust(stem_len, "z")
    mb = mid_bounds_stem(lo_stem, hi_stem)
    if mb is None:
        return "no_mid_bounds"
    prefix, _mid_a, _mid_b, suffix = mb
    if stem_len - len(prefix) - len(suffix) > max_free:
        return "free_over_max_free"
    return "enum_missed"


def recon(runs, args):
    run_len = Counter(len(items) for _, items, _ in runs)
    gap_records = sum(len(items) for _, items, _ in runs)
    same_dir = 0
    numeric = 0
    numeric_gaps = 0
    numeric_span_ok = 0
    free_hist = Counter()
    anchor_ext_pairs = Counter()
    cross_dirs = 0
    ext_modes = Counter()
    for low, items, high in runs:
        if low and high:
            dlow, dhigh = low.rpartition("/")[0], high.rpartition("/")[0]
            exts = sorted({ext_of(low), ext_of(high)})
            ext_modes[tuple(exts)] += 1
            if dlow == dhigh and dlow:
                same_dir += 1
                slow, shigh = stem_of(low), stem_of(high)
                lcp = 0
                while lcp < min(len(slow), len(shigh)) and slow[lcp] == shigh[lcp]:
                    lcp += 1
                cs = 0
                while (
                    cs < min(len(slow), len(shigh)) - lcp
                    and slow[len(slow) - 1 - cs] == shigh[len(shigh) - 1 - cs]
                ):
                    cs += 1
                sa, sb = slow[lcp: len(slow) - cs], shigh[lcp: len(shigh) - cs]
                if sa.isdigit() and sb.isdigit():
                    numeric += 1
                    lo, hi = int(sa), int(sb)
                    numeric_gaps += len(items)
                    if hi - lo - 1 >= len(items):
                        numeric_span_ok += len(items)
                for _, length in items:
                    for ext in exts:
                        stem_len = length - len(dlow) - 1 - len(ext)
                        if stem_len >= lcp + cs:
                            free_hist[stem_len - lcp - cs] += 1
                        else:
                            free_hist[-1] += 1
            else:
                cross_dirs += 1
    print("runs %d, gap records %d, run sizes %s" % (len(runs), gap_records, run_len.most_common(8)))
    print("same-dir runs %d, cross-dir runs %d, numeric-diff runs %d (records %d, span-covered %d)" % (
        same_dir, cross_dirs, numeric, numeric_gaps, numeric_span_ok))
    print("anchor ext pairs top: %s" % ext_modes.most_common(12))
    total_free = sum(free_hist.values())
    cum = 0
    for width in sorted(free_hist):
        cum += free_hist[width]
        print("free<=%d: %d / %d (%.1f%%)" % (width, cum, total_free, 100.0 * cum / total_free))
    print("seconds recon")


if __name__ == "__main__":
    sys.exit(main())
