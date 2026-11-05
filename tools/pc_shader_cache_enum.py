#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enumerate short-hex shader cache names inside the path_hash_file gap.

Family (PC round 16): <shader_cache|shadercache>/cross_compiled_<glsl|vulkan_windows>/
<hex>{7,8}.{cache,meta}.  The runtime cache token is a 32-bit hex value (client log
"[CS] %llx.meta", named rows like shadercache/cross_compiled_glsl/6c357dcb.cache), so the
full 16^8 space is enumerable with a vectorized FNV-1a sweep.

Gates per candidate: FNV-1a32(backslash path) + exact length hit on an unexplained
path_hash_file record, mmh3(backslash path, 0x9747B28C) equal to an unresolved signature,
content sniff compatible with the candidate extension.  FNV-only 32-bit collision
candidates are all reported and adjudicated by the mmh3 gate.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import external_pathtable_hunt as H
import fnv_interval_solve as S
import npk_browser as B
import nxpk_mode3 as N

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
DEFAULT_OUT = SWEEP / "16-pc-vocab-mining-hits.json"

FNV_PRIME = np.uint32(0x01000193)
HEX_DIGITS = "0123456789abcdef"
HEX_CHARS = np.array([ord(c) for c in HEX_DIGITS], dtype=np.uint32)
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
BITMAP_BITS = 26
BITMAP_SIZE = 1 << BITMAP_BITS
BITMAP_MASK = BITMAP_SIZE - 1

COMBO_DIRS = (
    "shadercache/cross_compiled_glsl/",
    "shader_cache/cross_compiled_glsl/",
    "shadercache/cross_compiled_vulkan_windows/",
    "shader_cache/cross_compiled_vulkan_windows/",
)
COMBO_EXTS = (".cache", ".meta")
COMBO_WIDTHS = (7, 8)
ACCEPT_KINDS = {"binary", "text"}


def fnv_of(path: str) -> int:
    return H.fnv1a(path.replace("/", "\\"))


def load_context(db: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages"))
    records, table_info = H.load_table(con, packages)
    paths = S.load_paths(db.parent, con)
    unresolved = H.load_unresolved(con, packages)
    con.close()
    known = {}
    for path in paths:
        known.setdefault(fnv_of(path), path)
    hidden = hide_selfcheck_anchors(known)
    seen = set()
    gap = set()
    for fnv, length in records:
        if (fnv, length) in seen:
            continue
        seen.add((fnv, length))
        anchor = known.get(fnv)
        if anchor is not None and len(anchor) == length:
            continue
        gap.add((fnv, length))
    return {"packages": packages, "records": seen, "paths": paths,
            "known": known, "unresolved": unresolved, "gap": gap,
            "hidden": hidden}


def hide_selfcheck_anchors(known):
    hidden = []
    for fnv, path in known.items():
        if "/cross_compiled_" not in path:
            continue
        name = path.rsplit("/", 1)[-1]
        stem = name.split(".")[0]
        ext = name[len(stem):]
        if ext not in (".cache", ".meta") or not (6 <= len(stem) <= 9):
            continue
        hidden.append((path, fnv, len(path)))
        if len(hidden) >= 24:
            break
    return hidden


def token_of(index: int, lead: int) -> str:
    digits = []
    for _ in range(lead):
        digits.append(HEX_DIGITS[index & 0xF])
        index >>= 4
    return "".join(reversed(digits))


def sweep_combo(prefix_bs, ext, width, targets):
    """Return [(fnv, hex_token)] whose full-path FNV lands on a target record."""
    if not targets:
        return []
    bitmap = np.zeros(BITMAP_SIZE, dtype=bool)
    for fnv in targets:
        bitmap[fnv & BITMAP_MASK] = True
    lead = width - 2
    states = np.array([H.fnv1a(prefix_bs)], dtype=np.uint32)
    for _ in range(lead):
        states = ((states[:, None] ^ HEX_CHARS[None, :]).reshape(-1) * FNV_PRIME)
    hits = []
    ext_bytes = [np.uint32(b) for b in ext.encode("ascii")]
    for d1, c1 in enumerate(HEX_CHARS):
        s1 = (states ^ c1) * FNV_PRIME
        for d2, c2 in enumerate(HEX_CHARS):
            s2 = (s1 ^ c2) * FNV_PRIME
            for byte in ext_bytes:
                s2 = (s2 ^ byte) * FNV_PRIME
            mask = bitmap[s2 & BITMAP_MASK]
            if mask.any():
                tail = HEX_DIGITS[d1] + HEX_DIGITS[d2]
                for idx in np.nonzero(mask)[0]:
                    value = int(s2[idx])
                    if value in targets:
                        hits.append((value, token_of(int(idx), lead) + tail))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--selfcheck-only", action="store_true")
    args = parser.parse_args()

    started = time.time()
    ctx = load_context(args.db)
    print("gap %d, known %d, unresolved sigs %d, hidden selfcheck %d" % (
        len(ctx["gap"]), len(ctx["known"]), len(ctx["unresolved"]), len(ctx["hidden"])), flush=True)

    passes = []
    for prefix_fwd in COMBO_DIRS:
        prefix_bs = prefix_fwd.replace("/", "\\")
        for ext in COMBO_EXTS:
            for width in COMBO_WIDTHS:
                passes.append((prefix_fwd, prefix_bs, ext, width))

    verified = {}
    collisions = []
    verdict = Counter()
    selfcheck_hits = set()
    for prefix_fwd, prefix_bs, ext, width in passes:
        length = len(prefix_bs) + width + len(ext)
        targets = {}
        if not args.selfcheck_only:
            for fnv, rec_len in ctx["gap"]:
                if rec_len == length:
                    targets[fnv] = rec_len
        for path, fnv, rec_len in ctx["hidden"]:
            if rec_len == length:
                targets[fnv] = rec_len
        if not targets:
            continue
        hits = sweep_combo(prefix_bs, ext, width, targets)
        for fnv, token in hits:
            cand_fwd = prefix_fwd + token + ext
            cand_bs = prefix_bs + token + ext
            key = (fnv, length)
            is_hidden = cand_fwd in selfcheck_known(ctx)
            sig = N.path_hash(cand_bs)
            if not is_hidden and sig not in ctx["unresolved"]:
                collisions.append({"fnv": "0x%08x" % fnv, "len": length,
                                   "path": cand_fwd, "mmh3": "0x%08x" % sig,
                                   "verdict": "mmh3_miss"})
                verdict["fnv_collision_mmh3_miss"] += 1
                continue
            if is_hidden:
                selfcheck_hits.add(cand_fwd)
                verified[key] = {"path": cand_fwd, "rule": "selfcheck", "kind": "selfcheck",
                                 "mmh3": "0x%08x" % sig, "fnv": "0x%08x" % fnv, "len": length}
                continue
            accepted = None
            for row in ctx["unresolved"].get(sig, []):
                try:
                    data = B.read_entry_bytes(ctx["packages"][row["package"]], row)
                except Exception:
                    continue
                kind = H.sniff(data)
                if kind not in ACCEPT_KINDS and (
                        ZSTD_MAGIC in data[:4096] or b"#version" in data[:4096]):
                    kind = "binary"
                if kind in ACCEPT_KINDS:
                    accepted = (row, kind)
                    break
            if accepted is None:
                verdict["sniff_reject"] += 1
                collisions.append({"fnv": "0x%08x" % fnv, "len": length,
                                   "path": cand_fwd, "mmh3": "0x%08x" % sig,
                                   "verdict": "sniff_reject"})
                continue
            row, kind = accepted
            verified[key] = {"path": cand_fwd, "rule": "shader_hex_enum", "kind": kind,
                             "entry": row["path"], "mmh3": "0x%08x" % sig,
                             "fnv": "0x%08x" % fnv, "len": length}
        print("combo %s%s w%d: targets %d, fnv hits %d, verified %d, collisions +%d, %.0fs" % (
            prefix_fwd, ext, width, len(targets), len(hits),
            sum(1 for v in verified.values() if v["rule"] == "shader_hex_enum"),
            verdict["fnv_collision_mmh3_miss"], time.time() - started), flush=True)

    total_hidden = len(ctx["hidden"])
    print("selfcheck: %d/%d recovered" % (len(selfcheck_hits), total_hidden), flush=True)

    real = {key: info for key, info in verified.items() if info["rule"] == "shader_hex_enum"}
    ext_count = Counter(Path(info["path"]).suffix for info in real.values())
    by_family = Counter("/".join(info["path"].split("/")[:2]) for info in real.values())
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": "tools/pc_shader_cache_enum.py",
        "gap_records": len(ctx["gap"]),
        "mappings": {info["entry"]: {"path": info["path"], "rule": info["rule"],
                                     "kind": info["kind"],
                                     "evidence": {"fnv": info["fnv"], "len": info["len"],
                                                  "mmh3": info["mmh3"], "sniff": info["kind"]}}
                     for key, info in sorted(real.items())},
        "stats": {"solved": len(real), "by_ext": dict(ext_count), "by_family": dict(by_family),
                  "verdict": dict(verdict),
                  "selfcheck": "%d/%d" % (len(selfcheck_hits), total_hidden),
                  "collision_total": len(collisions),
                  "collisions": collisions[:4000]},
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written %s: %d mappings, %d collisions" % (
        args.out, len(real), len(collisions)), flush=True)
    return 0


def selfcheck_known(ctx):
    return {path for path, _fnv, _length in ctx["hidden"]}


if __name__ == "__main__":
    sys.exit(main())
