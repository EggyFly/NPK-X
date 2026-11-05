#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-expand external candidate paths and hash-match unresolved signatures.

Collects candidate asset paths from every offline reference source (catalog
paths, pathdict files, XN string harvests, eid model tables, private-server
catalog.json, game Documents and logs), expands naming-convention variants
(lod/texture/loading/version suffixes, source-extension mapping, .meta
siblings), hashes each candidate with the client path hash (MurmurHash3
x86_32 seed 0x9747B28C) and accepts a mapping only when the hash equals the
stored signature of an unresolved entry, the extension matches the
catalog-detected type and the entry payload passes the magic check.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ppk_ref_scan as S

import mmh3

import npk_browser as B
from image_convert import classify

SEED = S.SEED
DEFAULT_DB = B.default_cache_dir() / "catalog.sqlite"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "ppk-reference-bruteforce.json"
GAME_ROOT = Path(r"D:\FeverApps\party_pc")
SHARE_CATALOG = Path(r"\\Win-g4cpd8te8po\n\蛋仔派对\server\server-src\res\catalog.json")

MESH_SUFFIX = ("_lod1", "_lod2", "_lod3", "_lod4", "_lod", "_low", "_high", "_mid", "_loading")
TEX_SUFFIX = ("_d", "_n", "_m", "_e", "_a", "_s", "_r", "_nm", "_h", "_c", "_basecolor", "_normal")
VER_SUFFIX = ("_v01", "_v02", "_v03", "_v04", "_v05", "_01", "_02", "_03")
SUFFIX_BY_EXT = {
    "gim": MESH_SUFFIX + VER_SUFFIX, "mesh": MESH_SUFFIX + VER_SUFFIX,
    "mtg": MESH_SUFFIX, "animation": MESH_SUFFIX, "trackgroup": MESH_SUFFIX,
    "kta": TEX_SUFFIX, "png": TEX_SUFFIX, "tga": TEX_SUFFIX, "dds": TEX_SUFFIX,
    "sfx": VER_SUFFIX, "efx": VER_SUFFIX,
}
SECOND_EXT = {
    "png": ("kta", "ktx"), "tga": ("kta", "ktx"), "psd": ("kta",),
    "jpg": ("kta",), "jpeg": ("kta",), "bmp": ("kta",), "dds": ("kta",),
    "kta": ("png", "tga", "dds"), "gim": ("mesh", "stb", "nstb"),
    "mesh": ("gim",), "stb": ("nstb",), "nstb": ("stb",),
    "skeleton": ("stb", "nstb"), "wav": ("wem",), "ogg": ("wem",),
    "mp3": ("wem",), "lua": ("luac",), "fbx": ("gim", "mesh"),
    "blendspace": ("blendspace1d",), "json": ("bin",), "bin": ("json",),
}
ASSET_EXTS = set(SUFFIX_BY_EXT) | set(SECOND_EXT)
REF_LINE = re.compile(rb"[A-Za-z0-9_][A-Za-z0-9_\-.\\/()\x80-\xff]{2,240}\.[A-Za-z0-9]{1,12}")
TEXT_SCAN_CAP = 8 * 1024 * 1024


def entry_magic(ext: str, body: bytes):
    if ext in ("kta", "ktx", "dds", "png", "jpg", "jpeg", "bmp", "tga", "webp"):
        return classify(body, "") is not None
    if ext == "mesh":
        return body[:4] == b"\x34\x80\xc8\xbb"
    if ext == "gim":
        return body[:4] == b"\xc1\x59\x41\x0d"
    if ext == "wem":
        return body[:4] == b"RIFF"
    if ext == "bnk":
        return body[:4] == b"BKHD"
    if ext == "rawanimation":
        return body[:7] == b"RAWANIM"
    if ext == "skeleton":
        return body[:8] == b"SKELETON"
    if ext in ("json",):
        head = body[:1] in (b"{", b"[") or body[:3] == b"\xef\xbb\xbf"
        return head
    if ext in ("stb", "nstb", "surf", "hlsl", "plist", "xml", "txt", "lang"):
        return body[:1] == b"<" or body[:1] == b"{"
    if ext == "gmp":
        return body[:2] == b"DZ" or len(body) > 54
    return True


def norm(path: str):
    text = path.strip().replace("\\", "/")
    while text.startswith("./") or text.startswith("../"):
        text = text[3:] if text.startswith("../") else text[2:]
    try:
        return text.encode("latin1").lower().decode("latin1")
    except UnicodeEncodeError:
        return text.lower()


def expand(path: str):
    stem, dot, ext = path.rpartition(".")
    if not dot:
        return
    ext = ext.lower()
    out = []
    if ext in SUFFIX_BY_EXT:
        for suffix in SUFFIX_BY_EXT[ext]:
            out.append(stem + suffix + "." + ext)
        base = stem
        for suffix in MESH_SUFFIX:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        if base != stem:
            out.append(base + "." + ext)
    if ext in SECOND_EXT:
        for mapped in SECOND_EXT[ext]:
            out.append(stem + "." + mapped)
    if ext in S.META_SRC_EXTS:
        out.append(path + ".meta")
    yield path
    for item in out:
        yield item


def candidates_from_text(data: bytes, label: str, sink: dict):
    for raw in set(REF_LINE.findall(data)):
        text = raw.decode("latin1").strip(" \t\r\n\"'=[],;")
        ext = text.rpartition(".")[2].lower()
        if len(ext) > 12 or not ext.isalpha() or ext not in S.REF_EXTS:
            continue
        normed = norm(text)
        if 5 <= len(normed) <= 260 and normed not in sink:
            sink[normed] = label


def load_catalog_pool(con):
    pool = {}
    for path, in con.execute(
        "SELECT path FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        normed = norm(path)
        if normed not in pool:
            pool[normed] = "catalog"
    return pool


def load_pools(args, con, packages):
    pool = load_catalog_pool(con)
    print("pool catalog: %d (total %d)" % (len(pool), len(pool)))

    extra = Path(__file__).resolve().parent / "pathdict_extra.txt"
    if extra.is_file():
        before = len(pool)
        for line in extra.read_text("utf-8", errors="ignore").splitlines():
            normed = norm(line)
            if normed and normed not in pool:
                pool[normed] = "pathdict_extra"
        print("pool pathdict_extra: +%d (total %d)" % (len(pool) - before, len(pool)))

    generated = DEFAULT_DB.parent / "pathdict.generated.txt.gz"
    if generated.is_file():
        before = len(pool)
        with gzip.open(generated, "rt", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                normed = norm(line)
                if normed and normed not in pool:
                    pool[normed] = "pathdict.generated"
        print("pool pathdict.generated: +%d (total %d)" % (len(pool) - before, len(pool)))

    audit = Path(args.audit_dir)
    if audit.is_dir():
        for name, label in (("asset_paths.txt", "xn_memory_harvest"), ("ppk_path_tables.txt", "ppk_path_tables")):
            source = audit / name
            if source.is_file():
                before = len(pool)
                for line in source.read_text("utf-8", errors="ignore").splitlines():
                    normed = norm(line)
                    if normed and "/" in normed and normed not in pool:
                        pool[normed] = label
                print("pool %s: +%d (total %d)" % (label, len(pool) - before, len(pool)))

    eid = DEFAULT_DB.parent / "eid_models.json"
    if eid.is_file():
        before = len(pool)
        data = json.loads(eid.read_text("utf-8"))
        for value in data.get("map", {}).values():
            normed = norm(value)
            if normed and normed not in pool:
                pool[normed] = "eid_models"
        print("pool eid_models: +%d (total %d)" % (len(pool) - before, len(pool)))

    if args.share_catalog and SHARE_CATALOG.is_file():
        before = len(pool)
        raw = SHARE_CATALOG.read_bytes()
        candidates_from_text(raw, "server_catalog_json", pool)
        print("pool server_catalog_json: +%d (total %d)" % (len(pool) - before, len(pool)))

    for extra_file in args.extra:
        source = Path(extra_file)
        if source.is_file():
            before = len(pool)
            candidates_from_text(source.read_bytes(), source.name, pool)
            print("pool %s: +%d (total %d)" % (source.name, len(pool) - before, len(pool)))

    if args.game_docs:
        before = len(pool)
        for source in game_text_files():
            try:
                data = source.read_bytes()[:TEXT_SCAN_CAP]
            except OSError:
                continue
            candidates_from_text(data, "documents:" + source.name, pool)
        print("pool game_documents: +%d (total %d)" % (len(pool) - before, len(pool)))

    if args.gmp_frames:
        before = len(pool)
        import gmp_parse
        rows = con.execute(
            "SELECT path, offset, packed, unpacked, compression, package "
            "FROM files WHERE ext='.gmp'"
        ).fetchall()
        scanned = 0
        for row in rows:
            if row["unpacked"] > args.max_mb * 1024 * 1024:
                continue
            package_path = packages.get(row["package"])
            if not package_path:
                continue
            try:
                payload = B.read_entry_bytes(package_path, row)
            except Exception:
                continue
            for _start, _used, raw in gmp_parse._frames(payload):
                candidates_from_text(raw, "gmp_frames", pool)
            scanned += 1
        print("pool gmp_frames: +%d from %d gmp (total %d)" % (len(pool) - before, scanned, len(pool)))
    return pool


def game_text_files():
    docs = GAME_ROOT / "Documents"
    skip = {"NgVoice", "chat", "cloudmusic_songs", "video", "share_icon_season_33", "temp_archive", "kv_storage"}
    if docs.is_dir():
        for item in docs.iterdir():
            if item.name in skip:
                continue
            if item.is_file() and item.stat().st_size <= TEXT_SCAN_CAP:
                yield item
            elif item.is_dir():
                for child in item.rglob("*"):
                    if child.is_file() and child.stat().st_size <= TEXT_SCAN_CAP:
                        yield child
    for pattern in ("*.log", "log*.txt", "*.xml", "objectcounter.log"):
        for item in GAME_ROOT.glob(pattern):
            if item.is_file() and item.stat().st_size <= TEXT_SCAN_CAP:
                yield item


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--audit-dir", default=str(Path(__file__).resolve().parent.parent / "t3_audit" / "tmp_xn"))
    parser.add_argument("--extra", nargs="*", default=[])
    parser.add_argument("--share-catalog", action="store_true")
    parser.add_argument("--game-docs", action="store_true")
    parser.add_argument("--gmp-frames", action="store_true")
    parser.add_argument("--selfname-product", action="store_true")
    parser.add_argument("--max-mb", type=int, default=64)
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
    unresolved_by_sig = {}
    for row in unresolved:
        unresolved_by_sig.setdefault(row["signature"], row)
    root_set, ext_guesses = S.build_root_index(con)
    print("unresolved: %d  %.0fs" % (len(unresolved), time.time() - started))

    pool = load_pools(args, con, packages)
    print("candidates: %d  %.0fs" % (len(pool), time.time() - started))

    results = {}
    conflicts = {}
    stats = Counter()
    tried = 0
    base_count = 0
    for base, label in pool.items():
        base_count += 1
        head, sep, _rest = base.partition("/")
        rooted = bool(sep) and head in root_set
        for cand in expand(base):
            cand_bs = cand.replace("/", "\\")
            tried += 1
            if not rooted and sep:
                picks = ext_guesses.get(cand.rpartition(".")[2]) or []
                for pick in picks[:2]:
                    tried += 1
                    sig = mmh3.hash((pick + "\\" + cand_bs).encode("utf-8"), SEED, signed=False)
                    if sig in unresolved_by_sig:
                        note_hit(sig, pick + "\\" + cand_bs, label + "+root", results, conflicts,
                                 named_by_sig, unresolved_by_sig, packages, stats)
            sig = mmh3.hash(cand_bs.encode("utf-8"), SEED, signed=False)
            if sig in unresolved_by_sig:
                note_hit(sig, cand_bs, label, results, conflicts, named_by_sig,
                         unresolved_by_sig, packages, stats)
        if base_count % 300000 == 0:
            print("  %d/%d bases, tried %d, hits %d, %.0fs" % (
                base_count, len(pool), tried, len(results), time.time() - started))

    if args.selfname_product:
        before_hits = len(results)
        tried += spine_pair(con, packages, results, conflicts, named_by_sig,
                            unresolved_by_sig, stats)
        print("spine pair: hits %d, %.0fs" % (len(results) - before_hits, time.time() - started))
        bare_tried = selfname_product(con, packages, results, conflicts,
                                      named_by_sig, unresolved_by_sig, stats)
        tried += bare_tried
        print("selfname product: tried %d, new %d, %.0fs" % (
            bare_tried, len(results) - before_hits, time.time() - started))

    for path_key, names in conflicts.items():
        if len(names) > 1:
            if results.pop(path_key, None):
                stats["conflict_dropped"] += 1

    remaining = Counter()
    for row in unresolved:
        if row["path"] not in results:
            remaining[row["ext"]] += 1

    payload = {
        "generated_by": "tools/ppk_ref_bruteforce.py",
        "total_unresolved": len(unresolved),
        "named": len(results),
        "tried": tried,
        "mappings": results,
        "remaining_by_ext": dict(remaining.most_common()),
        "stats": dict(stats),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  named %d / %d  tried %d  %.0fs" % (
        args.out, len(results), len(unresolved), tried, time.time() - started))
    for ext, count in remaining.most_common(12):
        print("  remaining %-14s %d" % (ext, count))
    con.close()
    return 0


def skel_magic(body: bytes):
    return len(body) > 32 and body[0] == 0x1C and body[29:32] == b"3.8"


def atlas_magic(body: bytes):
    return body[:1] == b"\n" or (b".png" in body[:400] and body[:1] not in (b"\x1c",))


def spine_pair(con, packages, results, conflicts, named_by_sig, unresolved_by_sig, stats):
    tried = 0
    pairs = (
        (".atlas", ("skel", "bin"), "spine"),
        (".skel", ("atlas",), "spine"),
        (".gim", ("json",), "sibling"),
        (".mesh", ("json",), "sibling"),
        (".stb", ("json",), "sibling"),
    )
    for ext, others, kind in pairs:
        for (path,) in con.execute(
            "SELECT path FROM files WHERE ext=? AND path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'",
            (ext,),
        ):
            stem = path.rsplit(".", 1)[0]
            for other in others:
                cand = stem + "." + other
                tried += 1
                sig = mmh3.hash(cand.encode("utf-8").replace(b"/", b"\\"), SEED, signed=False)
                target = unresolved_by_sig.get(sig)
                if target is None:
                    continue
                if other != target["ext"].lstrip(".") and not (kind == "spine" and target["ext"] == ".bin"):
                    continue
                package_path = packages.get(target["package"])
                body = b""
                if package_path:
                    try:
                        body = B.read_entry_bytes(package_path, target)
                        if body[:4] == b"\x28\xb5\x2f\xfd":
                            import zstandard
                            body = zstandard.ZstdDecompressor().decompressobj().decompress(body)
                    except Exception:
                        body = b""
                if other == "skel" and body and not skel_magic(body):
                    stats["magic_reject"] += 1
                    continue
                if other == "atlas" and body and not atlas_magic(body):
                    stats["magic_reject"] += 1
                    continue
                if other == "json" and body and not entry_magic("json", body):
                    stats["magic_reject"] += 1
                    continue
                named = named_by_sig.get(sig)
                if named is not None and named.lower() != cand:
                    stats["sig_collision_named"] += 1
                    continue
                existing = results.get(target["path"])
                if existing and existing["path"] != cand:
                    conflicts.setdefault(target["path"], set()).update([existing["path"], cand])
                    continue
                if target["path"] in conflicts:
                    continue
                results[target["path"]] = {
                    "rule": "spine_pair" if kind == "spine" else "sibling_json",
                    "path": cand,
                    "evidence": "sibling stem of named %s, payload magic verified" % ext,
                }
    return tried


def selfname_product(con, packages, results, conflicts, named_by_sig, unresolved_by_sig, stats):
    dirs_by_ext = {}
    for dirpath, ext in con.execute(
        "SELECT DISTINCT dir, ext FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        dirs_by_ext.setdefault(ext.lstrip("."), []).append(dirpath.replace("/", "\\"))
    bare = {}
    rows = con.execute(
        "SELECT path, offset, packed, unpacked, compression, package "
        "FROM files WHERE path LIKE '_unresolved/%' AND ext IN "
        "('.gim','.mtg','.efx','.csb','.json','.meta','.mesh','.eggimonbin','.stb','.nstb')"
    ).fetchall()
    for row in rows:
        package_path = packages.get(row["package"])
        if not package_path:
            continue
        try:
            data = B.read_entry_bytes(package_path, row)
        except Exception:
            continue
        for raw in S.TOKEN.findall(data):
            cleaned = S.clean_token(raw)
            if cleaned is None or b"/" in cleaned or b"\\" in cleaned:
                continue
            bare.setdefault(cleaned, 0)
            bare[cleaned] += 1
    tried = 0
    for token in bare:
        stem, dot, ext = token.rpartition(b".")
        ext = ext.decode("latin1")
        forms = S.hash_forms(token)
        variants = [token]
        if ext in SUFFIX_BY_EXT:
            variants.extend(stem + suffix.encode("ascii") + b"." + ext.encode("ascii")
                            for suffix in SUFFIX_BY_EXT[ext])
        dirs = dirs_by_ext.get(ext) or []
        for cand in variants:
            for form in forms:
                for dirpath in dirs:
                    tried += 1
                    sig = mmh3.hash(dirpath.encode("utf-8") + b"\\" + form, SEED, signed=False)
                    if sig in unresolved_by_sig:
                        note_hit(sig, dirpath + "\\" + form.decode("latin1"), "selfname",
                                 results, conflicts, named_by_sig, unresolved_by_sig, packages, stats)
    return tried


def note_hit(sig, cand_bs, label, results, conflicts, named_by_sig, unresolved_by_sig, packages, stats):
    target = unresolved_by_sig[sig]
    cand_text = cand_bs.replace("\\", "/")
    cext = cand_text.rpartition(".")[2]
    if cext != target["ext"].lstrip("."):
        stats["ext_mismatch"] += 1
        return
    named = named_by_sig.get(sig)
    if named is not None and named.replace("\\", "/") != cand_text:
        stats["sig_collision_named"] += 1
        return
    package_path = packages.get(target["package"])
    if package_path:
        try:
            body = B.read_entry_bytes(package_path, target)
        except Exception:
            body = b""
        if body and not entry_magic(cext, body):
            stats["magic_reject"] += 1
            return
    existing = results.get(target["path"])
    if existing and existing["path"] != cand_text:
        conflicts.setdefault(target["path"], set()).update([existing["path"], cand_text])
        return
    if target["path"] in conflicts:
        return
    results[target["path"]] = {
        "rule": "bruteforce:" + label,
        "path": cand_text,
        "evidence": "mmh3(path)==signature and payload magic verified for %s" % cext,
    }


if __name__ == "__main__":
    raise SystemExit(main())
