#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name unresolved catalog entries from path-like references inside entries.

Scans every text-ish entry across all packages (npk + ppk shards) plus
decrypted XN modules for path-like tokens, expands each token into candidate
client paths (lowercase fold, root prefix guessed by extension, same-directory
join, image-source extension mapping, .meta siblings), hashes them with the
client path hash (MurmurHash3 x86_32 seed 0x9747B28C) and accepts a mapping
only when the hash equals the stored signature of an unresolved entry and the
extension matches the catalog-detected type.
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
import mmh3

import npk_browser as B
import xn_crypto

DEFAULT_DB = B.default_cache_dir() / "catalog.sqlite"
SEED = 0x9747B28C
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "ppk-reference-names.json"

SCAN_EXTS = {
    ".gim", ".mtg", ".efx", ".csb", ".json", ".stb", ".nstb", ".scn", ".gmp",
    ".sfx", ".sfxmusic", ".trackgroup", ".animconfig", ".animgraph",
    ".blendspace", ".blendspace1d", ".eggimonbin", ".plist", ".meta", ".hlsl",
    ".xml", ".txt", ".lang", ".surf", ".spx", ".luac", ".lua", ".animation",
    ".rawanimation", ".skeleton", ".postprocess", ".trk", ".animresmap",
    ".prefab", ".cfg", ".ini", ".csv", ".xn", ".bin",
}
REF_EXTS = {
    "tga", "psd", "png", "jpg", "jpeg", "bmp", "dds", "kta", "ktx", "gim",
    "mesh", "mtg", "sfx", "efx", "bnk", "wem", "skeleton", "animation",
    "rawanimation", "animconfig", "plist", "csb", "json", "scn", "stb", "nstb",
    "trackgroup", "animresmap", "animgraph", "blendspace", "blendspace1d",
    "luac", "lua", "trk", "fbx", "lod", "gmp", "hlsl", "mp4", "eggimonbin",
    "postprocess", "bin", "meta", "sfxmusic", "prefab", "spx",
}
IMAGE_SRC_EXTS = {"tga", "psd", "png", "jpg", "jpeg", "bmp"}
EXT_MAP = {
    "tga": ("kta",), "psd": ("kta",), "png": ("kta",), "jpg": ("kta",),
    "jpeg": ("kta",), "bmp": ("kta",), "dds": ("kta", "ktx"),
    "wav": ("wem",), "ogg": ("wem",), "mp3": ("wem",),
    "lua": ("luac",), "gim": ("mesh",), "mesh": ("gim",),
    "fbx": ("gim", "mesh"), "blendspace": ("blendspace1d",),
}
META_SRC_EXTS = {
    "gim", "mesh", "mtg", "json", "bin", "csb", "efx", "sfx", "stb", "nstb",
    "animation", "trackgroup", "animconfig", "tga", "png", "psd", "dds",
    "hlsl", "scn", "gmp", "sfxmusic", "rawanimation", "skeleton", "trk",
}
TOKEN = re.compile(rb"[A-Za-z0-9_][A-Za-z0-9_\-.\\/()\x80-\xff]{2,240}\.[A-Za-z0-9]{1,12}")
TOKEN_CACHE_CAP = 1500000


def clean_token(raw: bytes):
    text = raw.strip(b" \t\r\n\"'=[],;")
    if not (5 <= len(text) <= 260):
        return None
    ext = text.rsplit(b".", 1)[-1].lower().decode("latin1")
    if not ext.isalpha() or len(ext) > 12 or ext not in REF_EXTS:
        return None
    if text[:8].lower().find(b"http") >= 0 or text.startswith(b"\\"):
        return None
    return text


def hash_forms(raw: bytes):
    forms = [raw]
    if any(b > 127 for b in raw):
        try:
            forms.append(raw.decode("latin1").encode("utf-8"))
        except UnicodeDecodeError:
            pass
    return forms


def split_ext(path: bytes):
    stem, dot, ext = path.rpartition(b".")
    if not dot:
        return path, b""
    return stem, ext.lower()


def build_root_index(con):
    roots = Counter()
    ext_roots = defaultdict(Counter)
    for path, ext in con.execute(
        "SELECT path, ext FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        head, _, rest = path.partition("/")
        if rest:
            roots[head] += 1
            ext_roots[ext.lstrip(".")][head] += 1
    root_set = {head for head, count in roots.items() if count >= 20}
    guesses = {}
    for ext, counter in ext_roots.items():
        guesses[ext] = [head for head, _ in counter.most_common(4) if head in root_set]
    return root_set, guesses


def variant_paths(form: bytes, source_dir: bytes, root_set, ext_guesses):
    text = form.replace(b"/", b"\\").lower()
    while text.startswith(b".\\") or text.startswith(b"..\\"):
        text = text[3:] if text.startswith(b"..\\") else text[2:]
    if not text:
        return []
    stem, ext = split_ext(text)
    out = []
    rooted = text.split(b"\\", 1)[0].decode("latin1") in root_set
    if rooted:
        out.append(text)
    for cut in (1, 2, 3):
        trimmed = text[cut:]
        if len(trimmed) > 4 and trimmed.split(b"\\", 1)[0].decode("latin1") in root_set:
            out.append(trimmed)
    if source_dir:
        out.append(source_dir + b"\\" + text)
    if not rooted:
        picks = ext_guesses.get(ext.decode("latin1")) or []
        for pick in picks[:3]:
            out.append(pick.encode("utf-8") + b"\\" + text)
        if not picks:
            out.append(b"gui2\\" + text)
    extra = []
    for item in out:
        istem, iext = split_ext(item)
        iext = iext.decode("latin1")
        if iext in EXT_MAP:
            for mapped in EXT_MAP[iext]:
                extra.append(istem + b"." + mapped.encode("ascii"))
        if iext in META_SRC_EXTS:
            extra.append(item + b".meta")
    out.extend(extra)
    seen = []
    for item in out:
        if item not in seen and len(item) <= 260:
            seen.append(item)
    return seen


def load_catalog(db_path: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    named_by_sig = {}
    for path, signature in con.execute(
        "SELECT path, signature FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        named_by_sig[signature] = path.encode("utf-8")
    unresolved = con.execute(
        "SELECT path, signature, ext, package FROM files WHERE path LIKE '_unresolved/%'"
    ).fetchall()
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    xn_names = {}
    names_file = db_path.parent / "xn_names.json"
    if names_file.is_file():
        xn_names = {int(k): v for k, v in json.loads(names_file.read_text("utf-8"))["map"].items()}
    return con, named_by_sig, unresolved, packages, xn_names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--max-mb", type=int, default=64)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    started = time.time()
    con, named_by_sig, unresolved, packages, xn_names = load_catalog(args.db)
    root_set, ext_guesses = build_root_index(con)
    unresolved_by_sig = {}
    for row in unresolved:
        unresolved_by_sig.setdefault(row["signature"], row)
    unresolved_exts = {row["ext"].lstrip(".") for row in unresolved}
    print("named: %d  unresolved: %d  roots: %d  %.0fs" % (
        len(named_by_sig), len(unresolved), len(root_set), time.time() - started))

    where = "ext IN (%s)" % ",".join("'%s'" % ext for ext in sorted(SCAN_EXTS))
    sources = con.execute(
        "SELECT path, signature, offset, packed, unpacked, compression, package, ext "
        "FROM files WHERE " + where
    ).fetchall()
    if args.limit:
        sources = sources[: args.limit]
    con.close()
    print("scan sources: %d" % len(sources))

    results = {}
    conflicts = {}
    stats = Counter()
    token_cache = {}
    ref_total = 0

    def accept(cands, row, token):
        for cand in cands:
            cext = split_ext(cand)[1].decode("latin1")
            if cext not in unresolved_exts:
                continue
            sig = mmh3.hash(cand, SEED, signed=False)
            target = unresolved_by_sig.get(sig)
            if target is None or cext != target["ext"].lstrip("."):
                continue
            named = named_by_sig.get(sig)
            if named is not None and named != cand:
                stats["sig_collision_named"] += 1
                continue
            cand_text = cand.decode("latin1").replace("\\", "/")
            existing = results.get(target["path"])
            if existing and existing["path"] != cand_text:
                conflicts.setdefault(target["path"], set()).update([existing["path"], cand_text])
                continue
            if target["path"] in conflicts:
                continue
            results[target["path"]] = {
                "rule": "reference",
                "path": cand_text,
                "evidence": "%s references %r" % (row["path"], token.decode("latin1")),
            }

    for index, row in enumerate(sources, 1):
        if row["unpacked"] > args.max_mb * 1024 * 1024:
            continue
        package_path = packages.get(row["package"])
        if not package_path:
            continue
        if row["ext"] == ".xn":
            salt = xn_names.get(row["signature"])
            if not salt:
                continue
            try:
                with open(package_path, "rb") as handle:
                    handle.seek(int(row["offset"]))
                    raw = handle.read(int(row["packed"]))
                data = xn_crypto.decrypt(raw, salt)
            except Exception:
                data = None
            if not data:
                stats["xn_decrypt_fail"] += 1
                continue
        else:
            try:
                data = B.read_entry_bytes(package_path, row)
            except Exception:
                stats["source_read_fail"] += 1
                continue
        source_dir = row["path"].rsplit("/", 1)[0].encode("utf-8") if "/" in row["path"] else b""
        for token in set(TOKEN.findall(data)):
            cleaned = clean_token(token)
            if cleaned is None:
                continue
            cached = token_cache.get(cleaned)
            if cached is None:
                cached = []
                for form in hash_forms(cleaned):
                    cached.extend(variant_paths(form, b"", root_set, ext_guesses))
                if len(token_cache) < TOKEN_CACHE_CAP:
                    token_cache[cleaned] = cached
            local = []
            if source_dir:
                for form in hash_forms(cleaned):
                    local.extend(variant_paths(form, source_dir, root_set, ext_guesses))
            ref_total += len(cached) + len(local)
            accept(cached, row, token)
            accept(local, row, token)
        if index % 20000 == 0:
            print("  %d/%d sources, refs=%d named=%d %.0fs" % (
                index, len(sources), ref_total, len(results), time.time() - started))

    for path_key, names in conflicts.items():
        if len(names) > 1:
            if results.pop(path_key, None):
                stats["conflict_dropped"] += 1

    remaining = Counter()
    for row in unresolved:
        if row["path"] not in results:
            remaining[row["ext"]] += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_by": "tools/ppk_ref_scan.py",
        "total_unresolved": len(unresolved),
        "named": len(results),
        "mappings": results,
        "remaining_by_ext": dict(remaining.most_common()),
        "stats": dict(stats),
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  named %d / %d  refs=%d  %.0fs" % (
        args.out, len(results), len(unresolved), ref_total, time.time() - started))
    for ext, count in remaining.most_common(12):
        print("  remaining %-14s %d" % (ext, count))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
