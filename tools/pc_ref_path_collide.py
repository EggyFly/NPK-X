#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collide resource-reference paths mined from PC-only containers against the gap.

Sources (round 16, all unused by earlier sweeps):
  gmp_refs      path-like strings inside every named .gmp map (raw bytes plus each
                zstd frame decompressed); unit ctx_model_path / mat_path / preload
                tables and section metadata all live in these frames
  hlsl_includes #include targets from named .hlsl shaders; relative targets are
                resolved against the includer directory, "res/"-prefixed targets are
                tried with and without the prefix
Gates: FNV-1a32(backslash path) + length on an unexplained path_hash_file record,
mmh3(backslash path) equal to an unresolved signature, entry sniff vs candidate ext
(external_pathtable_hunt.ext_ok).  mmh3-hit but sniff-incompatible candidates are kept
as pending with their sniff kind for merge-side magic adjudication.
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

import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent))

import external_pathtable_hunt as H
import fnv_interval_solve as S
import npk_browser as B
import nxpk_mode3 as N

SWEEP = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep"
DEFAULT_OUT = SWEEP / "16-pc-vocab-mining-hits.json"

RAW_PAT = re.compile(rb"[A-Za-z0-9_][A-Za-z0-9_.\-/\\]{4,180}")
INCLUDE_PAT = re.compile(rb"#include\s+[\"']([^\"']+)[\"']")
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def fnv_of(path: str) -> int:
    return H.fnv1a(path.replace("/", "\\"))


def load_context(db: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages"))
    records, table_info = H.load_table(con, packages)
    paths = S.load_paths(db.parent, con)
    unresolved = H.load_unresolved(con, packages)
    gmp_rows = list(con.execute(
        "SELECT path, package, offset, packed, unpacked, compression FROM files "
        "WHERE ext='.gmp' AND path NOT LIKE '_unresolved/%'"))
    unres_gmp_rows = list(con.execute(
        "SELECT path, package, offset, packed, unpacked, compression FROM files "
        "WHERE ext='.gmp' AND path LIKE '_unresolved/%'"))
    unres_bin_rows = list(con.execute(
        "SELECT path, package, offset, packed, unpacked, compression FROM files "
        "WHERE ext='.bin' AND path LIKE '_unresolved/%' AND unpacked <= 16777216"))
    hlsl_rows = list(con.execute(
        "SELECT path, package, offset, packed, unpacked, compression FROM files "
        "WHERE ext='.hlsl' AND path NOT LIKE '_unresolved/%'"))
    con.close()
    known = {}
    for path in paths:
        known.setdefault(fnv_of(path), path)
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
    return {"packages": packages, "records": seen, "paths": set(paths),
            "known": known, "unresolved": unresolved, "gap": gap,
            "gmp_rows": gmp_rows, "unres_gmp_rows": unres_gmp_rows,
            "unres_bin_rows": unres_bin_rows, "hlsl_rows": hlsl_rows}


def entry_bytes(ctx, row):
    return B.read_entry_bytes(ctx["packages"][row["package"]], row)


def frame_payloads(data: bytes):
    yield data
    pos = 0
    while True:
        pos = data.find(ZSTD_MAGIC, pos)
        if pos < 0:
            return
        obj = zstandard.ZstdDecompressor().decompressobj()
        try:
            plain = obj.decompress(data[pos:])
        except Exception:
            pos += 4
            continue
        if plain:
            yield plain
        rest = obj.unused_data
        if not rest:
            return
        offset = rest.find(ZSTD_MAGIC)
        if offset < 0:
            return
        pos = len(data) - len(rest) + offset


def normalize_ref(text: str) -> str | None:
    text = text.replace("\\", "/").strip("./").lower()
    if text.count("/") < 1 or " " in text or "%" in text:
        return None
    if "." not in text.rsplit("/", 1)[-1]:
        return None
    if len(text) > 200:
        return None
    return text


def _printable(data: bytes) -> bool:
    return all(32 <= b < 127 for b in data)


TOP_DIR_MARKERS = (
    "effect/", "gui2/", "char/", "props/", "scene_w/", "shader/", "maps/",
    "luac/", "bindict/", "lightmap/", "wwise/", "trigger_data", "ogc/",
    "audio/", "video/", "battleui/", "common/", "effect_h52/", "char_mdd/",
    "ui/", "font/", "model/", "texture/", "textures/", "sound/", "script/",
)
PATH_EXT_RE = re.compile(
    r"\.(?:gim|mesh|mtg|kta|sfx|sfxmusic|json|bin|csb|atlas|plist|lod|stb|scn|efx|"
    r"trk|trackgroup|animation|rawanimation|animgraph|animconfig|hlsl|png|tga|dds|"
    r"bnk|wem|cache|meta|info|skel|spr|conf|lua|xml|surf|blendspace1d|eggimonbin|"
    r"animresmap|postprocess|skeleton|nstb|ttf|otf|csv|gmp|nx|nxpk)(?![a-z0-9])")


def segmented_candidates(text: str):
    """Split a glued string-pool dump into path candidates by top-dir markers and exts."""
    out = set()
    starts = {0}
    for marker in TOP_DIR_MARKERS:
        pos = text.find(marker)
        while 0 <= pos:
            starts.add(pos)
            pos = text.find(marker, pos + 1)
    for start in sorted(starts):
        match = PATH_EXT_RE.search(text, start)
        if match:
            cand = text[start:match.end()]
            if 5 <= len(cand) <= 200 and "/" in cand:
                out.add(cand)
    return out


def mp_strings(raw: bytes):
    """Yield msgpack length-prefixed strings; adjacent fixstr pools stay separate."""
    n = len(raw)
    i = 0
    while i < n:
        b = raw[i]
        if 0xA0 <= b <= 0xBF:
            ln = b & 0x1F
            end = i + 1 + ln
            if end <= n and _printable(raw[i + 1:end]):
                yield raw[i + 1:end]
            i = end if end > i + 1 else i + 1
        elif b == 0xD9 and i + 1 < n:
            ln = raw[i + 1]
            end = i + 2 + ln
            if end <= n and _printable(raw[i + 2:end]):
                yield raw[i + 2:end]
            i = max(end, i + 2)
        elif b == 0xDA and i + 2 < n:
            ln = int.from_bytes(raw[i + 1:i + 3], "big")
            end = i + 3 + ln
            if end <= n and ln <= 4096 and _printable(raw[i + 3:end]):
                yield raw[i + 3:end]
            i = max(end, i + 3)
        elif b == 0xDB and i + 4 < n:
            ln = int.from_bytes(raw[i + 1:i + 5], "big")
            end = i + 5 + ln
            if end <= n and ln <= 65536 and _printable(raw[i + 5:end]):
                yield raw[i + 5:end]
            i = max(end, i + 5)
        else:
            i += 1


def scan_strings(data: bytes):
    for payload in frame_payloads(data):
        for match in RAW_PAT.findall(payload):
            text = normalize_ref(match.decode("latin-1"))
            if text is None:
                continue
            for cand in segmented_candidates(text):
                yield cand
        for value in mp_strings(payload):
            text = normalize_ref(value.decode("latin-1"))
            if text is None:
                continue
            yield text
            for cand in segmented_candidates(text):
                yield cand


def collect_gmp_pool(ctx, verdict, rows=None):
    pool = set()
    for row in (rows if rows is not None else ctx["gmp_rows"]):
        try:
            data = entry_bytes(ctx, row)
        except Exception:
            verdict["gmp_read_fail"] += 1
            continue
        for text in scan_strings(data):
            if text not in pool:
                pool.add(text)
                verdict["gmp_strings"] += 1
    return pool


def collect_bin_pool(ctx, verdict):
    pool = set()
    for row in ctx["unres_bin_rows"]:
        try:
            data = entry_bytes(ctx, row)
        except Exception:
            verdict["bin_read_fail"] += 1
            continue
        for text in scan_strings(data):
            if text not in pool:
                pool.add(text)
                verdict["bin_strings"] += 1
    return pool


def collect_hlsl_pool(ctx, verdict):
    pool = set()
    for row in ctx["hlsl_rows"]:
        try:
            data = entry_bytes(ctx, row)
        except Exception:
            verdict["hlsl_read_fail"] += 1
            continue
        directory = row["path"].rsplit("/", 1)[0]
        for target in INCLUDE_PAT.findall(data):
            text = target.decode("latin-1").replace("\\", "/").strip().lower()
            variants = []
            if text.startswith("res/"):
                variants.append(text[4:])
            elif text.startswith("/"):
                variants.append(text.lstrip("/"))
            else:
                base = directory
                for part in text.split("/"):
                    if part == "..":
                        base = base.rsplit("/", 1)[0] if "/" in base else ""
                    elif part and part != ".":
                        base = base + "/" + part if base else part
                variants.append(base)
                variants.append("shader/" + text)
            for variant in variants:
                if variant.endswith(".hlsl") and variant not in pool:
                    pool.add(variant)
                    verdict["hlsl_includes"] += 1
    return pool


def collide(ctx, pool, verdict):
    hits = {}
    pending = []
    for cand in pool:
        if cand in ctx["paths"]:
            continue
        key = (fnv_of(cand), len(cand))
        if key not in ctx["gap"]:
            continue
        sig = N.path_hash(cand.replace("/", "\\"))
        rows = ctx["unresolved"].get(sig)
        if not rows:
            verdict["mmh3_miss"] += 1
            continue
        attempts = []
        accepted = None
        for row in rows:
            try:
                data = entry_bytes(ctx, row)
            except Exception:
                continue
            kind = H.sniff(data)
            if H.ext_ok(cand, row["ext"], kind):
                accepted = (row, kind)
                break
            attempts.append({"path": cand, "entry": row["path"], "kind": kind,
                             "mmh3": "0x%08x" % sig, "fnv": "0x%08x" % key[0],
                             "len": key[1]})
        if accepted is None:
            verdict["sniff_pending"] += 1
            pending.extend(attempts)
            continue
        row, kind = accepted
        hits[key] = {"path": cand, "kind": kind, "entry": row["path"],
                     "mmh3": "0x%08x" % sig, "fnv": "0x%08x" % key[0], "len": key[1]}
    return hits, pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=B.default_cache_dir() / "catalog.sqlite")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sources", default="unresolved_gmp_refs,unresolved_bin_refs,hlsl_includes,gmp_refs")
    args = parser.parse_args()

    started = time.time()
    ctx = load_context(args.db)
    print("gap %d, gmp %d, hlsl %d, unresolved sigs %d" % (
        len(ctx["gap"]), len(ctx["gmp_rows"]), len(ctx["hlsl_rows"]),
        len(ctx["unresolved"])), flush=True)

    verdict = Counter()
    rule_hits = {}
    pending = []
    used_paths = set()
    for source in ("gmp_refs", "unresolved_gmp_refs", "unresolved_bin_refs", "hlsl_includes"):
        if source not in {s.strip() for s in args.sources.split(",") if s.strip()}:
            continue
        if source == "gmp_refs":
            pool = collect_gmp_pool(ctx, verdict)
        elif source == "unresolved_gmp_refs":
            pool = collect_gmp_pool(ctx, verdict, rows=ctx["unres_gmp_rows"])
        elif source == "unresolved_bin_refs":
            pool = collect_bin_pool(ctx, verdict)
        else:
            pool = collect_hlsl_pool(ctx, verdict)
        pool -= used_paths
        print("%s pool %d, %.0fs" % (source, len(pool), time.time() - started), flush=True)
        hits, pend = collide(ctx, pool, verdict)
        for key, info in hits.items():
            info["rule"] = source
            rule_hits.setdefault(key, info)
            used_paths.add(info["path"])
        pending.extend(pend)
        print("%s hits +%d, pending +%d, %.0fs" % (
            source, len(hits), len(pend), time.time() - started), flush=True)

    rule_count = Counter(info["rule"] for info in rule_hits.values())
    ext_count = Counter(Path(info["path"]).suffix for info in rule_hits.values())
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": "tools/pc_ref_path_collide.py",
        "gap_records": len(ctx["gap"]),
        "mappings": {info["entry"]: {"path": info["path"], "rule": info["rule"],
                                     "kind": info["kind"],
                                     "evidence": {"fnv": info["fnv"], "len": info["len"],
                                                  "mmh3": info["mmh3"], "sniff": info["kind"]}}
                     for key, info in sorted(rule_hits.items())},
        "stats": {"solved": len(rule_hits), "by_rule": dict(rule_count),
                  "by_ext": dict(ext_count), "verdict": dict(verdict),
                  "pending": pending[:2000], "pending_total": len(pending)},
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written %s: %d mappings" % (args.out, len(rule_hits)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
