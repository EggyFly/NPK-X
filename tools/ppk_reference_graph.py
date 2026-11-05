#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name unresolved catalog entries from asset-to-asset reference strings.

The tool reads every reference-bearing entry (named or unresolved), extracts
path-like strings from text, flat buffers, msgpack, map frames and decrypted
XN scripts, then turns each reference into candidate client paths:

- the literal reference, with or without leading path segments
- the reference resolved against the referrer's own directory
- common top-level roots for the target extension
- extension variants (tga/png -> kta, gim <-> mesh, ...)
- directories that already contain a named file of the same stem family
- same-stem partners of packed asset pairs (atlas page, plist textureFileName,
  sfx/efx, gim/mesh/mtg/stb/nstb, spine json/skel, scn/postprocess)

Every candidate is hashed with MurmurHash3 x86_32 seed 0x9747B28C. A mapping is
accepted only when the hash equals the stored signature of an unresolved entry,
the extension matches the detected type, the entry payload passes the magic
check and no already-named signature or path owns the same slot.
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sqlite3
import sys
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mmh3
import zstandard

import npk_browser as B
import xn_crypto
from gmp_parse import _frames as gmp_frames
from image_convert import KTX2_ID
from ppk_ref_bruteforce import entry_magic

SEED = 0x9747B28C
CACHE_DIR = B.default_cache_dir()
DEFAULT_DB = CACHE_DIR / "catalog.sqlite"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "reference-graph-names.json"
XN_NAMES = CACHE_DIR / "xn_names.json"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

TOKEN = re.compile(rb"[A-Za-z0-9_][A-Za-z0-9_\-.\\/()\x80-\xff]{2,240}\.[A-Za-z0-9]{1,12}")

EXT_MAP = {
    "tga": ("kta",), "psd": ("kta",), "png": ("kta",), "jpg": ("kta",),
    "jpeg": ("kta",), "bmp": ("kta",), "dds": ("kta", "ktx"),
    "gim": ("mesh",), "mesh": ("gim",), "wav": ("wem",), "ogg": ("wem",),
    "mp3": ("wem",), "lua": ("luac",), "fbx": ("gim", "mesh"),
    "blendspace": ("blendspace1d",), "json": ("bin",), "bin": ("json",),
}
META_SRC = {
    "gim", "mesh", "mtg", "json", "bin", "csb", "efx", "sfx", "stb", "nstb",
    "animation", "trackgroup", "animconfig", "tga", "png", "psd", "dds",
    "hlsl", "scn", "gmp", "sfxmusic", "rawanimation", "skeleton", "trk",
}
REF_EXTS = set(EXT_MAP) | set(META_SRC) | {
    "kta", "ktx", "bnk", "wem", "skeleton", "rawanimation", "animconfig",
    "plist", "scn", "nstb", "trackgroup", "animresmap", "animgraph",
    "blendspace1d", "luac", "trk", "gmp", "hlsl", "sfx", "eggimonbin",
    "postprocess", "bin", "meta", "sfxmusic", "prefab", "spx", "surf", "lod",
    "atlas", "spr", "xml", "txt", "lang",
}
SCAN_EXTS = [
    ".mtg", ".gim", ".mesh", ".sfx", ".efx", ".csb", ".plist", ".json",
    ".bin", ".meta", ".stb", ".nstb", ".scn", ".gmp", ".trackgroup",
    ".animation", ".rawanimation", ".animconfig", ".skeleton", ".hlsl",
    ".luac", ".eggimonbin", ".sfxmusic", ".postprocess", ".prefab", ".spx",
    ".animgraph", ".blendspace", ".blendspace1d", ".animresmap", ".trk",
    ".lod", ".surf", ".atlas", ".spr", ".cache", ".xml", ".txt", ".lang",
]
PAIR_RULES = {
    ".sfx": (".efx", ".sfxmusic"),
    ".scn": (".postprocess",),
    ".postprocess": (".scn",),
    ".gim": (".mtg", ".mesh", ".stb", ".nstb", ".skeleton", ".animconfig", ".lod"),
    ".mesh": (".mtg", ".gim", ".skeleton", ".stb", ".nstb", ".lod"),
    ".skeleton": (".animconfig", ".stb", ".nstb"),
    ".stb": (".nstb",),
    ".nstb": (".stb",),
    ".animconfig": (".animgraph",),
    ".animgraph": (".animconfig",),
    ".mtg": (".gim", ".mesh"),
    ".efx": (".sfx", ".gim"),
    ".csb": (".plist",),
    ".plist": (".csb",),
    ".json": (".kta", ".csb", ".atlas", ".skel"),
    ".atlas": (".kta", ".skel"),
    ".skel": (".kta", ".atlas"),
    ".rawanimation": (".trackgroup",),
    ".animation": (".trackgroup",),
    ".trackgroup": (".animation", ".rawanimation"),
}
PLIST_TEX = re.compile(rb"<key>textureFileName</key>\s*<string>([^<]+)</string>")
STEM_SUFFIX = (
    "_basecolor", "_normal", "_convert", "_loading", "_lod1", "_lod2",
    "_lod3", "_lod4", "_lod", "_low", "_high", "_mid", "_nm", "_hc",
    "_d", "_n", "_m", "_e", "_a", "_s", "_r", "_c", "_h",
)
ROOT_MIN_COUNT = 20
FAMILY_DIR_CAP = 8
MAX_REF_LEN = 260

RULE_RANK = {
    "ref": 5, "join": 5, "root": 4, "ref-trim": 3, "family": 3, "meta": 2,
}


def ext_of(path: bytes):
    stem, dot, ext = path.rpartition(b".")
    return ext.decode("latin1") if dot else ""


def base_stem(stem: bytes):
    text = stem.decode("latin1")
    for _ in range(3):
        for suffix in STEM_SUFFIX:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                break
        else:
            break
    return text.encode("utf-8")


def collapse_slashes(text: bytes):
    while b"\\\\" in text:
        text = text.replace(b"\\\\", b"\\")
    return text


def norm_ref(raw: bytes):
    text = raw.strip(b" \t\r\n\"'=[],;").replace(b"/", b"\\").lower()
    text = collapse_slashes(text)
    while text.startswith(b".\\"):
        text = text[2:]
    return text


def resolve_path(base: bytes, rel: bytes):
    if not base:
        return rel
    parts = (base + b"\\" + rel).split(b"\\")
    out = []
    for part in parts:
        if part in (b"", b"."):
            continue
        if part == b"..":
            if out:
                out.pop()
            continue
        out.append(part)
    return b"\\".join(out)


def zstd_first(data: bytes):
    try:
        obj = zstandard.ZstdDecompressor().decompressobj()
        out = obj.decompress(data)
        return out or None
    except Exception:
        return None


def payload_pieces(ext: str, data: bytes):
    yield data
    if ext == ".gmp":
        for _start, _used, raw in gmp_frames(data):
            yield raw
    elif ext == ".bin":
        if data[:4] == ZSTD_MAGIC:
            raw = zstd_first(data)
            if raw:
                yield raw
        elif data[:2] == b"\x78\x9c":
            try:
                yield zlib.decompress(data)
            except Exception:
                pass
    elif ext == ".meta":
        start = data.find(ZSTD_MAGIC)
        if start >= 0:
            raw = zstd_first(data[start:])
            if raw:
                yield raw


def load_xn_names(path: Path):
    if not path.is_file():
        return {}
    data = json.loads(path.read_text("utf-8"))
    return {int(key): value for key, value in data.get("map", {}).items()}


def load_catalog(db_path: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    named_sig = defaultdict(set)
    named_paths = set()
    named_by_ext = defaultdict(set)
    dirs_by_ext = defaultdict(set)
    root_by_ext = defaultdict(Counter)
    fam = defaultdict(set)
    root_count = Counter()
    for path, signature in con.execute(
        "SELECT path, signature FROM files WHERE path NOT LIKE '_unresolved/%' "
        "AND path NOT LIKE '_script/%'"
    ):
        named_sig[signature].add(path)
        named_paths.add(path)
        head, sep, rest = path.partition("/")
        if not sep:
            continue
        root_count[head] += 1
        ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
        named_by_ext[ext].add(path)
        dirs_by_ext[ext].add(path.rsplit("/", 1)[0])
        root_by_ext[ext][head] += 1
        name = path.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0] if "." in name else name
        fam[base_stem(stem.encode("utf-8"))].add(path.rsplit("/", 1)[0])
    roots = {head for head, count in root_count.items() if count >= ROOT_MIN_COUNT}
    root_guess = {}
    for ext, counter in root_by_ext.items():
        root_guess[ext] = [head for head, _ in counter.most_common(6) if head in roots]
    all_dirs = set()
    for ext_dirs in dirs_by_ext.values():
        all_dirs.update(ext_dirs)
    unresolved = con.execute(
        "SELECT path, signature, ext, offset, packed, unpacked, compression, package "
        "FROM files WHERE path LIKE '_unresolved/%'"
    ).fetchall()
    unres_by_sig = {}
    ambiguous = set()
    for row in unresolved:
        if row["signature"] in unres_by_sig:
            ambiguous.add(row["signature"])
        unres_by_sig[row["signature"]] = row
    for sig in ambiguous:
        unres_by_sig.pop(sig, None)
    unres_exts = {row["ext"].lstrip(".") for row in unresolved}
    return {
        "con": con,
        "packages": packages,
        "named_sig": named_sig,
        "named_paths": named_paths,
        "named_by_ext": named_by_ext,
        "dirs_by_ext": dirs_by_ext,
        "root_guess": root_guess,
        "roots": roots,
        "all_dirs": all_dirs,
        "family": fam,
        "unresolved": unresolved,
        "unres_by_sig": unres_by_sig,
        "unres_exts": unres_exts,
        "ambiguous": ambiguous,
    }


def source_rows(con):
    marks = ",".join("?" for _ in SCAN_EXTS)
    normal = con.execute(
        "SELECT path, signature, ext, offset, packed, unpacked, compression, package "
        "FROM files WHERE ext IN (" + marks + ")", SCAN_EXTS
    ).fetchall()
    scripts = con.execute(
        "SELECT path, signature, ext, offset, packed, unpacked, compression, package "
        "FROM files WHERE ext='.xn'"
    ).fetchall()
    return normal + scripts


def read_source(row, packages, xn_names):
    package_path = packages.get(row["package"])
    if not package_path:
        return None
    if row["ext"] == ".xn":
        salt = xn_names.get(row["signature"])
        if not salt:
            return None
        try:
            with open(package_path, "rb") as handle:
                handle.seek(int(row["offset"]))
                raw = handle.read(int(row["packed"]))
            return xn_crypto.decrypt(raw, salt)
        except Exception:
            return None
    try:
        return B.read_entry_bytes(package_path, row)
    except Exception:
        return None


def collect_refs(con, packages, xn_names, max_bytes, limit=0):
    refs = {}
    stats = Counter()
    rows = source_rows(con)
    if limit:
        rows = rows[:limit]
    started = time.time()
    for index, row in enumerate(rows, 1):
        if int(row["unpacked"]) > max_bytes:
            stats["skip_large"] += 1
            continue
        data = read_source(row, packages, xn_names)
        if not data:
            stats["read_fail"] += 1
            continue
        stats["files"] += 1
        source_path = row["path"]
        source_named = not source_path.startswith("_unresolved/") and not source_path.startswith("_script/")
        source_dir = source_path.rsplit("/", 1)[0].encode("utf-8") if source_named and "/" in source_path else b""
        seen = set()
        for piece in payload_pieces(row["ext"], data):
            for raw in TOKEN.findall(piece):
                text = norm_ref(raw)
                if not text or len(text) > MAX_REF_LEN:
                    continue
                ext = ext_of(text)
                if ext not in REF_EXTS:
                    continue
                if text in seen:
                    continue
                seen.add(text)
                item = refs.get(text)
                if item is None:
                    item = refs[text] = {"dirs": {}, "unres": None, "exts": set()}
                item["exts"].add(row["ext"])
                stats["ref_hits"] += 1
                if source_dir:
                    item["dirs"].setdefault(source_dir, source_path)
                else:
                    if item["unres"] is None:
                        item["unres"] = source_path
        if index % 50000 == 0:
            print("  scan %d/%d refs=%d %.0fs" % (
                index, len(rows), len(refs), time.time() - started), flush=True)
    stats["unique_refs"] = len(refs)
    return refs, stats


def build_candidates(text, item, context):
    text = collapse_slashes(text)
    unres_exts = context["unres_exts"]
    roots = context["roots"]
    root_guess = context["root_guess"]
    family = context["family"]
    cands = {}

    def add(cand, rule):
        if not cand or len(cand) > MAX_REF_LEN:
            return
        ext = ext_of(cand)
        if ext not in unres_exts:
            return
        old = cands.get(cand)
        if old is None or RULE_RANK.get(rule, 0) > RULE_RANK.get(old, 0):
            cands[cand] = rule

    def add_tree(cand, rule):
        add(cand, rule)
        ext = ext_of(cand)
        if ext in EXT_MAP:
            stem = cand[: len(cand) - len(ext) - 1]
            for mapped in EXT_MAP[ext]:
                add(stem + b"." + mapped.encode("ascii"), rule)
        if ext in META_SRC:
            add(cand + b".meta", "meta")

    add_tree(text, "ref")
    parts = text.split(b"\\")
    for cut in (1, 2, 3, 4, 5):
        if len(parts) > cut:
            trimmed = b"\\".join(parts[cut:])
            if trimmed.split(b"\\")[0].decode("latin1") in roots:
                add_tree(trimmed, "ref-trim")
    has_root = parts[0].decode("latin1") in roots if parts else False
    for source_dir in item["dirs"]:
        if has_root:
            break
        add_tree(resolve_path(source_dir, text), "join")
    if not has_root:
        seen_ext = set()
        for cand in list(cands):
            ext = ext_of(cand)
            if ext in seen_ext:
                continue
            seen_ext.add(ext)
            for root in root_guess.get(ext, ())[:6]:
                add_tree(root.encode("utf-8") + b"\\" + cand, "root")
    base_cands = list(cands)
    for cand in base_cands:
        ext = ext_of(cand)
        name = cand.rsplit(b"\\", 1)[-1]
        stem = name.rsplit(b".", 1)[0] if b"." in name else name
        base = base_stem(stem)
        dirs = family.get(base)
        if not dirs or len(dirs) > 32:
            continue
        targets = [ext]
        targets.extend(EXT_MAP.get(ext, ()))
        for target in targets:
            if target not in unres_exts:
                continue
            for dir_path in list(dirs)[:FAMILY_DIR_CAP]:
                add(dir_path.encode("utf-8") + b"\\" + stem + b"." + target.encode("ascii"), "family")
    return cands


def pair_candidates(con, context, packages):
    out = {}
    unres_exts = context["unres_exts"]

    def add(text, rule, source):
        if not text or len(text) > MAX_REF_LEN:
            return
        cand = text.replace("/", "\\").encode("utf-8")
        if ext_of(cand) not in unres_exts:
            return
        out.setdefault(cand, (rule, source))

    for src_ext, targets in PAIR_RULES.items():
        for path in context["named_by_ext"].get(src_ext.lstrip("."), ()):
            if "/" not in path:
                continue
            folder, name = path.rsplit("/", 1)
            stem = name.rsplit(".", 1)[0]
            for target in targets:
                if target.lstrip(".") in unres_exts:
                    add(folder + "/" + stem + target, "pair:" + src_ext + ">" + target, path)
    rows = con.execute(
        "SELECT path, signature, ext, offset, packed, unpacked, compression, package "
        "FROM files WHERE ext='.plist' AND path NOT LIKE '_unresolved/%'"
    ).fetchall()
    for row in rows:
        data = read_source(row, packages, {})
        if not data:
            continue
        match = PLIST_TEX.search(data)
        if not match:
            continue
        name = match.group(1).decode("latin1", "ignore").strip()
        if not name.lower().endswith((".png", ".jpg", ".jpeg", ".tga", ".bmp", ".psd")):
            continue
        folder = row["path"].rsplit("/", 1)[0] if "/" in row["path"] else ""
        add(folder + "/" + name.rsplit(".", 1)[0] + ".kta", "pair:plist-tex", row["path"])
    rows = con.execute(
        "SELECT path, signature, ext, offset, packed, unpacked, compression, package "
        "FROM files WHERE ext='.atlas' AND path NOT LIKE '_unresolved/%'"
    ).fetchall()
    for row in rows:
        if "/" not in row["path"]:
            continue
        folder, name = row["path"].rsplit("/", 1)
        stem = name.rsplit(".", 1)[0]
        add(folder + "/" + stem + ".kta", "pair:atlas>kta", row["path"])
        data = read_source(row, packages, {})
        if not data:
            continue
        lines = [line.strip() for line in data.splitlines() if line.strip()]
        if lines:
            page = lines[0].decode("latin1", "ignore")
            if page.lower().endswith((".png", ".jpg", ".jpeg", ".tga", ".bmp")):
                add(folder + "/" + page.rsplit(".", 1)[0] + ".kta", "pair:atlas-page", row["path"])
    return out


def accept_candidate(cand, rule, source, ref_text, context, tried, hits,
                     results, conflicts, magic_cache, stats):
    ext = ext_of(cand)
    tried[rule][ext] += 1
    signature = mmh3.hash(cand, SEED, signed=False)
    target = context["unres_by_sig"].get(signature)
    if target is None:
        return
    if target["ext"].lstrip(".") != ext:
        stats["ext_mismatch"] += 1
        return
    owners = context["named_sig"].get(signature)
    cand_text = cand.decode("utf-8", "replace").replace("\\", "/")
    if owners and cand_text not in owners:
        stats["sig_collision_named"] += 1
        return
    if cand_text in context["named_paths"]:
        stats["path_already_named"] += 1
        return
    if rule == "root":
        cand_dir = cand.rsplit(b"\\", 1)[0] if b"\\" in cand else b""
        if cand_dir.decode("utf-8", "replace").replace("\\", "/") not in context["all_dirs"]:
            stats["root_dir_missing"] += 1
            return
    magic = verify_magic(target, context["packages"], magic_cache)
    if magic != "ok":
        stats["magic_" + magic] += 1
        return
    hits[rule][ext] += 1
    key = target["path"]
    mapping = {
        "path": cand_text,
        "rule": rule,
        "source": source,
        "ref": ref_text,
        "signature": "%08x" % signature,
        "ext": target["ext"],
        "package": target["package"],
        "magic": magic,
        "_rank": RULE_RANK.get(rule, 0),
    }
    old = results.get(key)
    if old is None:
        results[key] = mapping
    elif old["path"] != cand_text:
        conflicts.setdefault(key, set()).update((old["path"], cand_text))
        if mapping["_rank"] > old["_rank"]:
            results[key] = mapping


def verify_magic(target, packages, cache):
    key = (target["path"], target["signature"])
    if key in cache:
        return cache[key]
    package_path = packages.get(target["package"])
    if not package_path:
        cache[key] = "no-package"
        return cache[key]
    try:
        body = B.read_entry_bytes(package_path, target)
    except Exception:
        cache[key] = "read-fail"
        return cache[key]
    ok = entry_magic(target["ext"].lstrip("."), body)
    if target["ext"] in (".kta", ".ktx"):
        ok = body.startswith(KTX2_ID) or body.startswith(b"\xabKTX 11\xbb\r\n\x1a\n")
    cache[key] = "ok" if ok else "fail"
    return cache[key]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--refs-cache", type=Path, default=None)
    parser.add_argument("--max-mb", type=int, default=64)
    parser.add_argument("--no-xn", action="store_true")
    parser.add_argument("--reuse-refs", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    started = time.time()
    context = load_catalog(args.db)
    print("named paths: %d  unresolved: %d  unique sigs: %d  ambiguous: %d  roots: %d" % (
        len(context["named_paths"]), len(context["unresolved"]),
        len(context["unres_by_sig"]), len(context["ambiguous"]), len(context["roots"])), flush=True)

    if args.reuse_refs and args.reuse_refs.is_file():
        with open(args.reuse_refs, "rb") as handle:
            cached = pickle.load(handle)
        refs = cached["refs"]
        scan_stats = Counter(cached["stats"])
        print("loaded refs cache: %d refs" % len(refs), flush=True)
    else:
        xn_names = {} if args.no_xn else load_xn_names(XN_NAMES)
        refs, scan_stats = collect_refs(
            context["con"], context["packages"], xn_names,
            args.max_mb * 1024 * 1024, args.limit)
        print("scanned files: %d  ref occurrences: %d  unique refs: %d  %.0fs" % (
            scan_stats["files"], scan_stats["ref_hits"], len(refs), time.time() - started), flush=True)
        if args.refs_cache:
            with open(args.refs_cache, "wb") as handle:
                pickle.dump({"refs": refs, "stats": dict(scan_stats)}, handle)

    tried = defaultdict(Counter)
    hits = defaultdict(Counter)
    magic_cache = {}
    results = {}
    conflicts = {}
    stats = Counter()
    processed = 0
    for text, item in refs.items():
        processed += 1
        cands = build_candidates(text, item, context)
        source = item["dirs"][next(iter(item["dirs"]))] if item["dirs"] else item["unres"]
        for cand, rule in cands.items():
            accept_candidate(cand, rule, source, text.decode("utf-8", "replace"),
                             context, tried, hits, results, conflicts, magic_cache, stats)
        if processed % 100000 == 0:
            print("  match %d/%d named=%d %.0fs" % (
                processed, len(refs), len(results), time.time() - started), flush=True)

    pair_total = 0
    for cand, (rule, source) in pair_candidates(context["con"], context, context["packages"]).items():
        pair_total += 1
        accept_candidate(cand, rule, source, "sibling:" + source,
                         context, tried, hits, results, conflicts, magic_cache, stats)
    print("  pair candidates: %d  named=%d  %.0fs" % (
        pair_total, len(results), time.time() - started), flush=True)

    for key in conflicts:
        results.pop(key, None)
    if conflicts:
        stats["conflict_dropped"] = len(conflicts)

    verified = {}
    for key, mapping in results.items():
        target = context["unres_by_sig"].get(int(mapping["signature"], 16))
        if target is None or target["path"] != key:
            stats["verify_target_gone"] += 1
            continue
        cand = mapping["path"].replace("/", "\\").encode("utf-8")
        if mmh3.hash(cand, SEED, signed=False) != target["signature"]:
            stats["verify_hash_fail"] += 1
            continue
        if ext_of(cand) != target["ext"].lstrip("."):
            stats["verify_ext_fail"] += 1
            continue
        if mapping["path"] in context["named_paths"]:
            stats["verify_named_path"] += 1
            continue
        owners = context["named_sig"].get(target["signature"])
        if owners and mapping["path"] not in owners:
            stats["verify_named_sig"] += 1
            continue
        mapping.pop("_rank", None)
        verified[key] = mapping

    remaining = Counter()
    for row in context["unresolved"]:
        if row["path"] not in verified:
            remaining[row["ext"]] += 1
    by_ext = Counter(item["ext"] for item in verified.values())
    by_rule = Counter(item["rule"] for item in verified.values())
    by_pair = Counter("%s -> %s" % (item["source"].rsplit(".", 1)[-1], item["ext"])
                      for item in verified.values())
    budget = {}
    sig_space = float(2 ** 32)
    for rule, counter in tried.items():
        total = sum(counter.values())
        rule_hits = sum(hits[rule].values())
        budget[rule] = {
            "tried": total,
            "hits": rule_hits,
            "expected_random": round(total * len(context["unres_by_sig"]) / sig_space, 2),
        }

    payload = {
        "generated_by": "tools/ppk_reference_graph.py",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_unresolved": len(context["unresolved"]),
        "unique_unresolved_sigs": len(context["unres_by_sig"]),
        "ambiguous_sigs": len(context["ambiguous"]),
        "named": len(verified),
        "scan": dict(scan_stats),
        "by_ext": dict(by_ext.most_common()),
        "by_rule": dict(by_rule.most_common()),
        "by_pair": dict(by_pair.most_common(20)),
        "collision_budget": budget,
        "remaining_by_ext": dict(remaining.most_common()),
        "mappings": verified,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  named %d / %d  %.0fs" % (
        args.out, len(verified), len(context["unresolved"]), time.time() - started), flush=True)
    print("by_rule:", dict(by_rule.most_common()), flush=True)
    print("by_ext:", dict(by_ext.most_common(12)), flush=True)
    print("stats:", dict(stats), flush=True)
    for rule, info in sorted(budget.items(), key=lambda kv: -kv[1]["hits"]):
        print("  budget %-10s tried %-10d hits %-6d expected_random %.1f" % (
            rule, info["tried"], info["hits"], info["expected_random"]), flush=True)
    for ext, count in remaining.most_common(12):
        print("  remaining %-14s %d" % (ext, count), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
