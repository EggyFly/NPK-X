#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name unresolved PPK entries from self-describing structures in their own bytes.

Reads each unresolved entry once, unwraps inner zstd/zlib, and pulls identity
hints out of format-specific structures: spine atlas texture line, eggimonbin
uuid field, msgpack meta schema type value, NeoX object Name pool, cocos plist
textureFileName, and Wwise BKHD bank id.  Candidate paths are built from those
stems plus directory pools (shard index neighbours, stem index, content paths,
whole catalog) and accepted only when MurmurHash3(path) equals the entry
signature.  Every rule is round-trip checked against already-named entries of
the same family before it touches unresolved rows.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import struct
import sys
import time
import zlib
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
import mmh3
import zstandard
import neox_object
import nxpk_mode3 as N

SEED = 0x9747B28C
ATLAS_HEAD = re.compile(rb"\r?\n*([A-Za-z0-9_\-]{3,120})\.(?:png|kta|jpg|jpeg)\r?\nsize: \d+,\d+\r?\nformat: ")
UUID_RE = re.compile(rb"uuid(?:[\xa0-\xbf]|\xc4[\x20-\xff])([A-Za-z0-9]{22})")
TYPE_RE = re.compile(rb"\xc4\x04type\xc4.([\x20-\x7e]{3,120})", re.DOTALL)
PLIST_TEX = re.compile(rb"textureFileName</key>\s*<string>([^<]{1,200}?)</string>")
FIELD_WORDS = {
    "name", "sub0", "sub1", "sub2", "sub3", "high", "low", "mid", "lod", "mesh", "mtg",
    "skeletonfile", "mtgfile", "filename", "meshfile", "animconfigfile", "blastfile",
    "softbone", "socketobject", "castshadow", "receiveshadow", "usefog", "object",
    "logicproperties", "neox", "boundingcenter", "boundinghalf", "boundinginfo",
    "geobatchhint", "animaccumenable", "materialgroup", "lightingmaterial", "technique",
    "material", "keyframe", "macro0", "root", "node", "true", "false", "submesh",
    "subresource", "track", "head", "lodpolicy", "colorframe", "roadgroup", "audio",
    "sceneroad", "lightmap",
}
NAME_FIELDS = ("name", "sub0", "sub1", "sub2", "sub3", "filename", "mtgfile", "skeletonfile", "meshfile")
NEOX_EXTS = (".gim", ".mtg", ".scn", ".lod")
TARGET_EXTS = (".bin", ".eggimonbin", ".gim", ".mtg", ".scn", ".lod", ".plist", ".bnk")
MAX_ALLDIR_STEMS = 2
MAX_BYTES = 256 * 1024 * 1024


def path_sig(path: str) -> int:
    return mmh3.hash(path.replace("/", "\\"), SEED, signed=False)


def snake_case(value: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1_\2", text)
    return text.replace("-", "_").lower()


def unwrap(data: bytes) -> bytes:
    if data[:4] == b"\x28\xb5\x2f\xfd":
        try:
            return zstandard.ZstdDecompressor().decompressobj().decompress(data)
        except Exception:
            return data
    if data[:1] == b"\x78" and data[1:2] in (b"\x01", b"\x5e", b"\x9c", b"\xda"):
        try:
            return zlib.decompress(data)
        except Exception:
            return data
    return data


def looks_like_stem(text: str) -> bool:
    if not 3 <= len(text) <= 64 or "/" in text or "\\" in text:
        return False
    if text.lower() in FIELD_WORDS:
        return False
    if text.isupper() or text.isdigit():
        return False
    return text.replace("_", "").replace("-", "").isalnum()


def extract_atlas(data: bytes):
    match = ATLAS_HEAD.match(data[:256])
    if not match:
        return [], [], ""
    return [match.group(1).decode("latin1")], [], "atlas"


def extract_eggimon(data: bytes):
    if b"uuid" not in data[:8192]:
        return [], [], ""
    match = UUID_RE.search(data[:131072])
    if not match:
        return [], [], ""
    uuid = match.group(1).decode("latin1")
    stems = [uuid.lower()]
    if uuid != uuid.lower():
        stems.append(uuid)
    return stems, [], "eggimon-uuid"


def extract_schema(data: bytes):
    if not data or data[0] > 0x8F and data[0] not in (0xDE, 0xDF):
        return [], [], ""
    stems = []
    for match in TYPE_RE.finditer(data[:256]):
        length = data[match.start(1) - 1]
        raw = match.group(1)
        if not 3 <= length <= len(raw):
            continue
        value = raw[:length].decode("latin1")
        if value[0].isupper() and value.replace("_", "").isalnum():
            stems.append(snake_case(value))
    if not stems:
        return [], [], ""
    return stems[:2], [], "meta-schema"


def extract_neox(data: bytes):
    try:
        parsed = neox_object.parse(data)
    except Exception:
        return [], [], ""
    stems = []
    content_dirs = []
    seen = set()

    def consider(text):
        text = text.strip().replace("\\", "/").strip("/")
        if not text or text in seen:
            return
        seen.add(text)
        if "/" in text or "." in text:
            head, _, tail = text.rpartition("/")
            tail = tail or head
            dot = tail.rfind(".")
            if dot > 0:
                tail = tail[:dot]
            if head and head != "." and "/" in head:
                content_dirs.append(head)
            if looks_like_stem(tail):
                stems.append(tail)
        elif looks_like_stem(text):
            stems.append(text)

    for obj in parsed.get("objects") or []:
        for field, value in obj.items():
            if not isinstance(value, str) or not value:
                continue
            if field.lower() in NAME_FIELDS or len(value) <= 64:
                consider(value)
            if len(stems) >= 14:
                break
        if len(stems) >= 14:
            break
    return stems[:14], content_dirs[:16], "neox-name"


def extract_plist(data: bytes):
    if b"<plist" not in data[:400]:
        return [], [], ""
    match = PLIST_TEX.search(data[:131072])
    if not match:
        return [], [], ""
    text = match.group(1).decode("latin1").replace("\\", "/")
    tail = text.rsplit("/", 1)[-1]
    dot = tail.rfind(".")
    if dot <= 0:
        return [], [], ""
    stem = tail[:dot]
    if not looks_like_stem(stem):
        return [], [], ""
    return [stem], [], "plist-texture"


def extract_bnk(data: bytes):
    if data[:4] != b"BKHD" or len(data) < 16:
        return [], [], "", 0
    return [], [], "bank-id", struct.unpack_from("<I", data, 12)[0]


EXTRACTORS = {
    ".bin": (extract_atlas, extract_eggimon, extract_schema),
    ".eggimonbin": (extract_eggimon,),
    ".gim": (extract_neox,),
    ".mtg": (extract_neox,),
    ".scn": (extract_neox,),
    ".lod": (extract_neox,),
    ".plist": (extract_plist,),
    ".bnk": (extract_bnk,),
}
RULE_EXTS = {
    "atlas": (".atlas",),
    "eggimon-uuid": (".eggimonbin",),
    "meta-schema": (".bin",),
    "plist-texture": (".plist",),
}
RULE_DIR_PREFIX = {
    "meta-schema": "luac/",
    "atlas": "gui2/",
    "plist-texture": "gui2/",
}


def candidate_dirs(stems, content_dirs, neighbours, stem_dirs, leaf_dirs, all_dirs, prefix=""):
    pool = []
    seen = set()

    def add(items):
        for item in items:
            if item and item not in seen:
                seen.add(item)
                pool.append(item)

    add(neighbours)
    for stem in stems:
        add(stem_dirs.get(stem.lower(), ()))
    for raw in content_dirs:
        text = raw.strip("/")
        if not text:
            continue
        head = text.rsplit("/", 1)[0] if "/" in text else text
        add((head, text))
    for stem in stems:
        low = stem.lower()
        tokens = {low, low[3:] if low.startswith("vx_") else low}
        for token in tokens:
            add(leaf_dirs.get(token, ()))
    if len(stems) <= MAX_ALLDIR_STEMS:
        if prefix:
            add(d for d in all_dirs if d.startswith(prefix))
        else:
            add(all_dirs)
    return pool


def build_candidates(stems, dirs, exts):
    out = []
    seen = set()
    for stem in stems:
        for directory in dirs:
            for ext in exts:
                path = "%s/%s%s" % (directory, stem, ext) if directory else stem + ext
                if path not in seen:
                    seen.add(path)
                    out.append(path)
    return out


def apply_rules(row, data, pools, bank_paths):
    stems = []
    content_dirs = []
    rule = ""
    bank_id = 0
    for func in EXTRACTORS.get(row["ext"], ()):
        got = func(data)
        if len(got) == 4:
            stems, content_dirs, rule, bank_id = got
        elif got[2]:
            stems, content_dirs, rule = got
        if rule:
            break
    if not rule:
        return None, "no-structure"
    if rule == "bank-id":
        paths = bank_paths.get(bank_id)
        if not paths:
            return None, "bank-id-unknown"
        hits = [path for path in sorted(paths) if path_sig(path) == row["signature"]]
        if len(hits) == 1:
            return {"rule": "bank-id", "path": hits[0],
                    "evidence": "BKHD bank id %d matches named bank" % bank_id}, None
        if hits:
            return None, "bank-id-collision"
        stems = sorted({path.rsplit("/", 1)[-1].rsplit(".", 1)[0] for path in paths})
        dirs = list({path.rsplit("/", 1)[0] for path in paths})
        dirs += pools["neighbour"]
        for stem in stems:
            dirs += pools["stem"].get(stem.lower(), ())
        dirs = list(dict.fromkeys(dirs))
        if len(stems) <= MAX_ALLDIR_STEMS:
            dirs += [d for d in pools["all"] if d.startswith("wwise")]
        hits = [path for path in build_candidates(stems, dirs, (".bnk",)) if path_sig(path) == row["signature"]]
        if len(hits) == 1:
            return {"rule": "bank-id", "path": hits[0],
                    "evidence": "BKHD bank id %d stem %r matched in %d dirs" % (bank_id, stems[0], len(dirs))}, None
        return None, "bank-id-collision" if hits else "bank-id-sig-miss"
    if not stems:
        return None, "no-stem"
    exts = RULE_EXTS.get(rule, (row["ext"],))
    if rule == "eggimon-uuid":
        dirs = pools["neighbour"] + pools["mont"]
    else:
        dirs = candidate_dirs(stems, content_dirs, pools["neighbour"], pools["stem"], pools["leaf"],
                              pools["all"], RULE_DIR_PREFIX.get(rule, ""))
    if not dirs:
        return None, "no-dirs"
    hits = []
    for path in build_candidates(stems, dirs, exts):
        if path_sig(path) == row["signature"]:
            hits.append(path)
    if not hits:
        return None, "sig-miss"
    if len(hits) > 1:
        return None, "collision"
    return {"rule": rule, "path": hits[0],
            "evidence": "%s stem %r matched in %d candidate dirs" % (rule, stems[0], len(dirs))}, None


def read_entry(packages, row):
    with open(packages[row["package"]], "rb") as handle:
        handle.seek(row["offset"])
        raw = handle.read(row["packed"])
    return raw if row["compression"] == 0 else zstandard.ZstdDecompressor().decompressobj().decompress(raw)


def validate_named(con, packages, sample_size):
    results = {}
    for ext in TARGET_EXTS:
        rows = con.execute(
            "SELECT path, signature, offset, packed, unpacked, compression, package FROM files "
            "WHERE ext=? AND path NOT LIKE '_unresolved/%' AND unpacked < 8388608 "
            "ORDER BY RANDOM() LIMIT ?", (ext, sample_size),
        ).fetchall()
        stats = Counter()
        for row in rows:
            stats["total"] += 1
            try:
                data = unwrap(read_entry(packages, row))
            except Exception:
                stats["read_fail"] += 1
                continue
            hit = False
            for func in EXTRACTORS.get(ext, ()):
                got = func(data)
                if len(got) == 4:
                    stems, dirs, rule, bank_id = got
                else:
                    stems, dirs, rule = got
                if not rule:
                    continue
                stats["rule_" + rule] += 1
                if rule == "bank-id":
                    hit = True
                    break
                real = row["path"]
                real_stem = real.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if any(stem.lower() == real_stem.lower() for stem in stems):
                    stats["stem_ok"] += 1
                exts = RULE_EXTS.get(rule, (ext,))
                cands = build_candidates(stems, [real.rsplit("/", 1)[0]], exts)
                if any(path_sig(c) == row["signature"] and c == real for c in cands):
                    stats["roundtrip_ok"] += 1
                else:
                    stats["roundtrip_miss"] += 1
                hit = True
                break
            if not hit:
                stats["no-rule"] += 1
        results[ext] = dict(stats)
    return results


def load_catalog(db_path: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    named_by_sig = defaultdict(list)
    for row in con.execute("SELECT path, signature FROM files WHERE path NOT LIKE '_unresolved/%'"):
        named_by_sig[row["signature"]].append(row["path"])
    dirs = [row["path"] for row in con.execute("SELECT path FROM dirs ORDER BY path")]
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    rows = con.execute(
        "SELECT rowid AS rid, path, signature, offset, packed, unpacked, compression, package, ext "
        "FROM files WHERE path LIKE '_unresolved/ppk/%' ORDER BY rowid"
    ).fetchall()
    named_order = defaultdict(list)
    for row in con.execute(
        "SELECT rowid AS rid, path, package FROM files WHERE path NOT LIKE '_unresolved/%' "
        "AND package LIKE 'ppk/%' ORDER BY rowid"
    ):
        head = row["path"].rsplit("/", 1)[0] if "/" in row["path"] else ""
        named_order[row["package"]].append((row["rid"], head))
    return con, named_by_sig, dirs, packages, rows, dict(named_order)


def build_indexes(named_order):
    out = {}
    for package, items in named_order.items():
        out[package] = ([item[0] for item in items], [item[1] for item in items])
    return out


def neighbours(package_index, rowid, window):
    rowids, heads = package_index
    pos = bisect_left(rowids, rowid)
    return heads[bisect_left(rowids, rowid - window):pos] + heads[pos:bisect_left(rowids, rowid + window + 1)]


def stem_dir_index(named_paths):
    stem_dirs = defaultdict(set)
    leaf_dirs = defaultdict(set)
    for path in named_paths:
        head, _, name = path.rpartition("/")
        if not head:
            continue
        stem = name.rsplit(".", 1)[0].lower()
        if 3 <= len(stem) <= 64:
            stem_dirs[stem].add(head)
        leaf = head.rsplit("/", 1)[-1].lower()
        if 2 <= len(leaf) <= 64:
            leaf_dirs[leaf].add(head)
    return stem_dirs, leaf_dirs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "ppk-structure-names.json")
    parser.add_argument("--window", type=int, default=48)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--validate", type=int, default=40)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    db_path = args.db or Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "EggyNpkBrowser" / "catalog.sqlite"
    started = time.time()
    con, named_by_sig, dirs, packages, rows, named_order = load_catalog(db_path)
    print("catalog: %d unresolved ppk rows, %d named sigs, %d dirs, %.0fs" % (
        len(rows), len(named_by_sig), len(dirs), time.time() - started))

    validation = validate_named(con, packages, args.validate)
    print("round-trip validation on named entries:")
    for ext, stats in sorted(validation.items()):
        print("  %-12s %s" % (ext, stats))

    stem_dirs, leaf_dirs = stem_dir_index(
        path for paths in named_by_sig.values() for path in paths)
    package_index = build_indexes(named_order)

    bank_paths = defaultdict(set)
    bank_rows = con.execute(
        "SELECT path, offset, packed, unpacked, compression, package FROM files "
        "WHERE ext='.bnk' AND path NOT LIKE '_unresolved/%'").fetchall()
    for row in bank_rows:
        try:
            with open(packages[row["package"]], "rb") as handle:
                handle.seek(row["offset"])
                raw = handle.read(min(row["packed"], 262144))
            data = raw if row["compression"] == 0 else zstandard.ZstdDecompressor().decompressobj().decompress(raw)
        except Exception:
            continue
        if data[:4] == b"BKHD" and len(data) >= 16:
            bank_paths[struct.unpack_from("<I", data, 12)[0]].add(row["path"])
    print("bank id index: %d ids from %d named bnks, %.0fs" % (len(bank_paths), len(bank_rows), time.time() - started))

    mont_dirs = [d for d in dirs if d == "mont" or d.startswith("mont/")]
    results = {}
    failures = Counter()
    rule_stats = Counter()
    fail_by_ext = defaultdict(Counter)
    processed = 0
    extra_named = defaultdict(list)
    pending = []
    workset = []

    for row in rows:
        if row["ext"] not in TARGET_EXTS:
            failures["ext-not-structural"] += 1
            fail_by_ext[row["ext"]]["ext-not-structural"] += 1
            continue
        processed += 1
        if args.limit and processed > args.limit:
            break
        try:
            data = unwrap(read_entry(packages, row))
        except Exception:
            failures["read-fail"] += 1
            fail_by_ext[row["ext"]]["read-fail"] += 1
            continue
        if len(data) > MAX_BYTES:
            failures["too-big"] += 1
            fail_by_ext[row["ext"]]["too-big"] += 1
            continue
        workset.append((row, data))
        if processed % 2000 == 0:
            print("  read %d/%d, %.0fs" % (processed, len(rows), time.time() - started))

    package_index = build_indexes(named_order)
    last_fail = {}
    for attempt in range(args.iterations):
        still = []
        gained = 0
        for row, data in workset:
            pools = {
                "neighbour": list(dict.fromkeys(neighbours(package_index[row["package"]], row["rid"], args.window)))
                if row["package"] in package_index else [],
                "stem": stem_dirs,
                "leaf": leaf_dirs,
                "all": dirs,
                "mont": mont_dirs,
            }
            mapping, failure = apply_rules(row, data, pools, bank_paths)
            if mapping:
                results[row["path"]] = mapping
                rule_stats[mapping["rule"]] += 1
                extra_named[row["package"]].append((row["rid"], mapping["path"].rsplit("/", 1)[0]))
                gained += 1
            else:
                last_fail[row["path"]] = (row, failure)
                if failure in ("sig-miss", "bank-id-sig-miss", "no-dirs"):
                    still.append((row, data))
        print("  pass %d: tried %d, gained %d, retry-pending %d, %.0fs" % (
            attempt + 1, len(workset), gained, len(still), time.time() - started))
        if not gained:
            break
        merged = {}
        for package, items in named_order.items():
            merged[package] = sorted(items + extra_named.get(package, []))
        for package, items in extra_named.items():
            if package not in merged:
                merged[package] = sorted(items)
        package_index = build_indexes(merged)
        workset = still
    for row, failure in last_fail.values():
        if row["path"] in results:
            continue
        failures[failure] += 1
        fail_by_ext[row["ext"]][failure] += 1

    remaining = Counter()
    for row in rows:
        if row["path"] not in results:
            remaining[row["ext"]] += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_by": "tools/ppk_structure_names.py",
        "total_unresolved_ppk": len(rows),
        "named": len(results),
        "rules": dict(rule_stats),
        "mappings": results,
        "remaining_by_ext": dict(remaining.most_common()),
        "failures": dict(failures),
        "failures_by_ext": {ext: dict(stats) for ext, stats in
                            sorted(fail_by_ext.items(), key=lambda kv: -sum(kv[1].values()))},
        "roundtrip_validation": validation,
    }
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("wrote %s  named %d / %d (%.1f%%)  %.0fs" % (
        args.out, len(results), len(rows), 100.0 * len(results) / max(1, len(rows)), time.time() - started))
    print("rules:", dict(rule_stats))
    print("failures:", failures.most_common(12))
    return 0


if __name__ == "__main__":
    sys.exit(main())
