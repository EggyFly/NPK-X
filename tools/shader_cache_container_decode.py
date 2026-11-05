#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Decode the NeoX multi-frame zstd shader-cache containers.

Family (PC round 20): shader_cache/cross_compiled_glsl/<15-16hex>.cache is a
multi-frame zstd container whose frame 1+ need a raw-content dictionary.  The
dictionary is the container's own first decoded frame (NeoX
DecompressDataWithDict), so the whole family decodes without any external
dictionary.  The sibling <15-16hex>.meta is "NXSC" header + one zstd blob of
packed reflection records.  The unresolved mirror rows are
_unresolved/ppk/<8hex>.meta (NXSC, batch tag at offset 12) and
_unresolved/ppk/<8hex>.bin (same container, 783/784 decode).

The tool also reruns the content-to-filename self-hash matrix (12 x 64-bit
algorithms over uniform/vs/ps/whole shapes plus vectorized FNV-1a64) that
closes the "name derivable from payload" route: every hit count is 0.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import struct
import sys
import time
from collections import Counter
from pathlib import Path

import mmh3
import numpy as np
import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent))

import npk_browser as B
from _xxh_ref import xxh64

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
DEFAULT_OUT = SWEEP / "20-zstd-container-decode.json"

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
NXSC = b"NXSC"
M64 = 0xFFFFFFFFFFFFFFFF


def decode_container(data: bytes):
    """Walk real frames; dict = first decoded frame.  Return (frames, status)."""
    pos = 0
    rolling = None
    outs = []
    guard = 0
    while pos < len(data) and guard < 8192:
        guard += 1
        idx = data.find(ZSTD_MAGIC, pos)
        if idx < 0:
            break
        dctx = (zstandard.ZstdDecompressor(dict_data=zstandard.ZstdCompressionDict(rolling))
                if rolling is not None else zstandard.ZstdDecompressor())
        try:
            obj = dctx.decompressobj()
            payload = obj.decompress(data[idx:])
        except zstandard.ZstdError as exc:
            return outs, "%s@%d" % (str(exc).split(" (")[0].rstrip(), idx)
        if not obj.eof:
            return outs, "truncated@%d" % idx
        consumed = (len(data) - idx) - len(obj.unused_data)
        outs.append((idx, payload))
        if rolling is None:
            rolling = payload
        pos = idx + consumed
    return outs, ("done" if outs else "no_frames")


def parse_meta(data: bytes):
    if data[:4] != NXSC:
        return None
    info = {"head_u32": struct.unpack_from("<I", data, 4)[0], "version": hex(struct.unpack_from("<I", data, 8)[0])}
    info["off12_u64"] = struct.unpack_from("<Q", data, 12)[0]
    idx = data.find(ZSTD_MAGIC, 40)
    if idx < 0:
        info["blob"] = None
        info["blob_state"] = "no_frame"
        return info
    try:
        obj = zstandard.ZstdDecompressor().decompressobj()
        info["blob"] = obj.decompress(data[idx:])
        info["blob_eof"] = obj.eof
    except zstandard.ZstdError as exc:
        info["blob"] = None
        info["blob_err"] = str(exc)[:60]
    return info


def fnv1a64_py(b: bytes) -> int:
    h = 0xCBF29CE484222325
    for x in b:
        h = ((h ^ x) * 0x100000001B3) & M64
    return h


FN64_PRIME = np.uint64(0x100000001B3)


def fnv1a64_vec(blobs):
    arr = np.frombuffer(b"".join(blobs), dtype=np.uint8).reshape(len(blobs), len(blobs[0])).astype(np.uint64)
    h = np.full(len(blobs), 0xCBF29CE484222325, dtype=np.uint64)
    for c in range(arr.shape[1]):
        h = (h ^ arr[:, c]) * FN64_PRIME
    return h


def hash64_matrix(b: bytes) -> dict:
    out = {
        "fnv1a64": fnv1a64_py(b),
        "xxh64": xxh64(b),
        "md5le": struct.unpack("<Q", hashlib.md5(b).digest()[:8])[0],
        "md5be": struct.unpack(">Q", hashlib.md5(b).digest()[:8])[0],
        "sha1le": struct.unpack("<Q", hashlib.sha1(b).digest()[:8])[0],
        "sha256le": struct.unpack("<Q", hashlib.sha256(b).digest()[:8])[0],
    }
    h1, h2 = mmh3.hash64(b, signed=False)
    out["mmh64a"] = h1
    out["mmh64b"] = h2
    out["mmh128lo"] = mmh3.hash128(b, signed=False) & M64
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0, help="cap named-cache selfhash rows (0=all)")
    args = parser.parse_args()

    started = time.time()
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages"))

    stats = Counter()
    decode_fail = []
    named_fail = []
    frame_count = Counter()
    for row in con.execute("SELECT path, package, offset, packed, unpacked, compression, signature FROM files WHERE path LIKE '_unresolved/ppk/%.bin'"):
        try:
            data = B.read_entry_bytes(packages[row["package"]], row)
        except Exception as exc:
            stats["read_err"] += 1
            decode_fail.append({"entry": row["path"], "err": str(exc)[:60]})
            continue
        if not data.startswith(ZSTD_MAGIC):
            stats["non_container"] += 1
            continue
        outs, status = decode_container(data)
        if status == "done":
            stats["container_ok"] += 1
            frame_count[len(outs)] += 1
            allout = b"".join(p for _, p in outs)
            stats["container_ok_glsl" if b"#version" in allout else "container_ok_other"] += 1
        else:
            stats["container_fail"] += 1
            decode_fail.append({"entry": row["path"], "status": status})

    named_total = 0
    named_ok = 0
    for row in con.execute("SELECT path, package, offset, packed, unpacked, compression FROM files WHERE path LIKE 'shader_cache/cross_compiled_glsl/%.cache'"):
        data = B.read_entry_bytes(packages[row["package"]], row)
        if not data.startswith(ZSTD_MAGIC):
            continue
        named_total += 1
        outs, status = decode_container(data)
        if status == "done":
            named_ok += 1
        elif len(named_fail) < 10:
            named_fail.append({"entry": row["path"], "status": status})

    meta_named = Counter()
    meta_named_off12 = Counter()
    for row in con.execute("SELECT path, package, offset, packed, unpacked, compression FROM files WHERE path LIKE 'shader_cache/cross_compiled_glsl/%.meta'"):
        data = B.read_entry_bytes(packages[row["package"]], row)
        info = parse_meta(data)
        if not info:
            meta_named["not_nxsc"] += 1
            continue
        meta_named["nxsc"] += 1
        meta_named_off12["all_ff" if info["off12_u64"] == 0xFFFFFFFFFFFFFFFF else "real"] += 1
        if info.get("blob") is not None:
            meta_named["blob_ok"] += 1
        elif info.get("blob_state") == "no_frame":
            meta_named["blob_absent"] += 1
        else:
            meta_named["blob_fail"] += 1

    meta_unres = Counter()
    batch_tags = Counter()
    for row in con.execute("SELECT path, package, offset, packed, unpacked, compression FROM files WHERE path LIKE '_unresolved/ppk/%.meta'"):
        data = B.read_entry_bytes(packages[row["package"]], row)
        info = parse_meta(data)
        if not info:
            meta_unres["not_nxsc"] += 1
            continue
        meta_unres["nxsc"] += 1
        batch_tags[hex(info["off12_u64"])] += 1
        if info.get("blob") is not None:
            meta_unres["blob_ok"] += 1
        elif info.get("blob_state") == "no_frame":
            meta_unres["blob_absent"] += 1
        else:
            meta_unres["blob_fail"] += 1

    rows = list(con.execute("SELECT path, package, offset, packed, unpacked, compression FROM files WHERE path LIKE 'shader_cache/cross_compiled_glsl/%.cache'"))
    if args.limit:
        rows = rows[:args.limit]
    selfhash_blobs = 0
    selfhash_hits = Counter()
    shape_seen = Counter()
    vec_groups = {}
    for row in rows:
        stem = row["path"].rsplit("/", 1)[-1].split(".")[0]
        target = int(stem, 16)
        data = B.read_entry_bytes(packages[row["package"]], row)
        outs, status = decode_container(data)
        if status != "done":
            continue
        uni = outs[0][1] if outs else b""
        vs = outs[1][1] if len(outs) > 1 else b""
        ps = outs[2][1] if len(outs) > 2 else b""
        shapes = {"uni": uni, "vs": vs, "ps": ps, "whole": b"".join(p for _, p in outs)}
        for sname, blob in shapes.items():
            if not blob or len(blob) > 65536:
                continue
            shape_seen[sname] += 1
            selfhash_blobs += 1
            for algo, val in hash64_matrix(blob).items():
                if val == target:
                    selfhash_hits["%s/%s" % (sname, algo)] += 1
            vec_groups.setdefault((sname, len(blob)), []).append((target, blob))
    vec_blobs = 0
    for (sname, length), group in sorted(vec_groups.items()):
        if len(group) < 2:
            continue
        vals = fnv1a64_vec([g[1] for g in group])
        vec_blobs += len(group)
        for (target, _), v in zip(group, vals):
            if int(v) == target:
                selfhash_hits["%s/fnv1a64_vec" % sname] += 1
    con.close()

    result = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": "tools/shader_cache_container_decode.py",
        "container_format": {
            "cache": "zstd multi-frame; frame0 self-contained; frames 1+ raw-content dict = frame0 output",
            "meta": "NXSC u32 ver u64(tag) u64 u64 u32 flags + zstd reflection blob",
        },
        "unresolved_bins": dict(stats),
        "unresolved_frame_counts": {"entries": sum(frame_count.values()), "dist_top": dict(frame_count.most_common(8))},
        "decode_fail": decode_fail[:20],
        "named_16hex_cache": {"zstd_rows": named_total, "decoded": named_ok, "fail_sample": named_fail},
        "named_16hex_meta": dict(meta_named) | {"off12": dict(meta_named_off12)},
        "unresolved_meta": dict(meta_unres) | {"off12_batch_tags": dict(batch_tags)},
        "selfhash_matrix": {
            "named_rows_tested": len(rows),
            "blobs_direct": selfhash_blobs,
            "blobs_vectorized_fnv1a64": vec_blobs,
            "shapes": dict(shape_seen),
            "hits": dict(selfhash_hits),
            "verdict": "no payload hash reproduces the 15-16hex filename u64",
        },
        "elapsed_sec": round(time.time() - started, 1),
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1)
    print("container_ok=%s fail=%s non_container=%s named=%s/%s" % (
        stats["container_ok_glsl"] + stats["container_ok_other"],
        stats["container_fail"], stats["non_container"], named_ok, named_total))
    print("selfhash hits:", dict(selfhash_hits))
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
