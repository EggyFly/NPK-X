#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Name _unresolved entries by cross-set content twins.

Stage 1 hashes every unresolved catalog row with sha256 plus mmh3 of both the
unpacked payload and the stored bytes.  Stage 2 walks every npk index and every
ppk shard index, including copies hidden by patch-shard overwrite, resolves the
signature through the path dictionary, and collects named entries that can
share content with an unresolved row.  ppk zstd entries only store mmh3 of the
compressed bytes, so those are decompressed before matching.  Stage 3 keeps a
mapping only when the full sha256 of both payloads is equal.

Index hash semantics (verified by tools/validate_npk_mode3.py and this script):
  npk entry             unpacked_hash = mmh3(unpacked payload)
  ppk compression 0     content_hash  = mmh3(unpacked payload) == mmh3(stored)
  ppk compression 3     content_hash  = mmh3(stored zstd frame)
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import mmh3
import zstandard

import npk_browser as B
import nxpk_mode3 as N

DEFAULT_CACHE = B.default_cache_dir()
DEFAULT_DB = DEFAULT_CACHE / "catalog.sqlite"
DEFAULT_PROFILE = DEFAULT_CACHE / "mode3_profile.json"
DEFAULT_PATHDICT = DEFAULT_CACHE / "pathdict.generated.txt.gz"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "unresolved-sweep" / "07-cross-shard-twins.json"
SCRIPT_PACKAGES = {"script.npk", "script_patch.npk"}
FLUSH_EVERY = 5000
MAX_ALIASES = 25


def read_pathdict(path: Path, seed: int) -> dict[int, list[str]]:
    table: dict[int, list[str]] = {}
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            text = line.strip()
            if text:
                table.setdefault(N.path_hash(text, seed), []).append(text)
    return table


def load_catalog(db_path: Path):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    con.row_factory = sqlite3.Row
    packages = dict(con.execute("SELECT name, path FROM packages").fetchall())
    named_rows = []
    catalog_offset = {}
    named_sigs = set()
    for row in con.execute(
        "SELECT path, package, signature, offset, packed, unpacked, unpacked_hash, compression, ext, kind "
        "FROM files WHERE path NOT LIKE '_unresolved/%' AND path NOT LIKE '_script/%'"
    ):
        named_rows.append(row)
        catalog_offset[(row["package"], row["offset"])] = row
        named_sigs.add(row["signature"])
    unresolved = [
        dict(row)
        for row in con.execute(
            "SELECT id, path, package, signature, offset, packed, unpacked, unpacked_hash, compression, ext, kind "
            "FROM files WHERE path LIKE '_unresolved/%'"
        )
    ]
    con.close()
    return packages, named_rows, catalog_offset, named_sigs, unresolved


def read_payload(package_path: str, row) -> tuple[bytes, bytes]:
    offset = int(row["offset"])
    packed = int(row["packed"])
    unpacked = int(row["unpacked"])
    compression = int(row["compression"])
    with open(package_path, "rb") as handle:
        handle.seek(offset)
        raw = handle.read(packed)
    if len(raw) != packed:
        raise ValueError("short read from package")
    if compression == 3:
        obj = zstandard.ZstdDecompressor().decompressobj()
        data = obj.decompress(raw)
        if not obj.eof:
            raise ValueError("truncated Zstd frame")
        if obj.unused_data and any(obj.unused_data):
            raise ValueError("unexpected trailing Zstd data")
    elif compression == 0:
        data = raw
    else:
        raise ValueError("unsupported compression %d" % compression)
    if len(data) != unpacked:
        raise ValueError("unpacked size mismatch")
    return raw, data


def fingerprint(raw: bytes, data: bytes, seed: int) -> tuple[str, int, int]:
    return (
        hashlib.sha256(data).hexdigest(),
        mmh3.hash(data, seed, signed=False),
        mmh3.hash(raw, seed, signed=False),
    )


def index_hash_is_unpacked(package: str, compression: int) -> bool:
    return not (package.startswith("ppk/") and compression == 3)


def hash_unresolved(rows, packages, seed):
    started = time.time()
    done = 0
    failed = []
    total_bytes = 0
    mismatch = []
    for index, row in enumerate(rows, 1):
        package_path = packages.get(row["package"])
        if not package_path:
            failed.append(row["path"])
            continue
        try:
            raw, data = read_payload(package_path, row)
        except Exception:
            failed.append(row["path"])
            continue
        row["sha256"], row["unpacked_mmh3"], row["packed_mmh3"] = fingerprint(raw, data, seed)
        row["hash_kind"] = "unpacked" if index_hash_is_unpacked(row["package"], row["compression"]) else "packed"
        row["hash_ok"] = (
            row["unpacked_mmh3"] if row["hash_kind"] == "unpacked" else row["packed_mmh3"]
        ) == row["unpacked_hash"]
        if not row["hash_ok"]:
            mismatch.append(row["path"])
        done += 1
        total_bytes += len(data)
        if index % FLUSH_EVERY == 0:
            print("  unresolved %d/%d  %.0fMB  %.0fs" % (index, len(rows), total_bytes / 1e6, time.time() - started))
    print("  unresolved done: %d ok, %d failed, %.0fMB, %.0fs" % (
        done, len(failed), total_bytes / 1e6, time.time() - started))
    return done, failed, mismatch


def scan_named_refs(game_root: Path, key: bytes, sig_paths, wanted_keys, wanted_sizes,
                    catalog_offset, named_sigs, progress=20000):
    refs_by_offset = {}
    entries_scanned = 0
    resolved_entries = 0
    ambiguous_entries = 0
    renamed_entries = 0
    refs_npk = 0
    refs_ppk_stored = 0
    refs_ppk_zstd = 0

    def add_entry(package_name, entry, fallback_path):
        nonlocal ambiguous_entries, renamed_entries, refs_npk, refs_ppk_stored, refs_ppk_zstd
        row = catalog_offset.get((package_name, entry.offset))
        path = fallback_path
        renamed = row is not None and row["path"] != fallback_path
        if renamed:
            renamed_entries += 1
        if len(sig_paths[entry.signature]) > 1:
            ambiguous_entries += 1
        is_ppk = package_name.startswith("ppk/")
        if is_ppk and entry.compression == 3:
            if entry.unpacked_size not in wanted_sizes:
                return
            kind = "ppk_zstd"
        else:
            if (entry.unpacked_size, entry.unpacked_hash) not in wanted_keys:
                return
            kind = "ppk_stored" if is_ppk else "npk"
        marker = (package_name, entry.offset)
        if marker in refs_by_offset:
            return
        refs_by_offset[marker] = {
            "path": path,
            "package": package_name,
            "offset": entry.offset,
            "packed": entry.packed_size,
            "unpacked": entry.unpacked_size,
            "compression": entry.compression,
            "signature": entry.signature,
            "index_hash": entry.unpacked_hash,
            "index_packed_hash": entry.packed_hash,
            "in_catalog": row is not None,
            "sig_in_catalog": entry.signature in named_sigs,
            "renamed": renamed,
            "ambiguous": len(sig_paths[entry.signature]) > 1,
            "ref_kind": kind,
        }
        if kind == "npk":
            refs_npk += 1
        elif kind == "ppk_stored":
            refs_ppk_stored += 1
        else:
            refs_ppk_zstd += 1

    for npk in sorted(game_root.glob("*.npk")):
        if npk.name in SCRIPT_PACKAGES:
            continue
        header, entries = N.read_index(npk, key)
        entries_scanned += len(entries)
        for entry in entries:
            paths = sig_paths.get(entry.signature)
            if not paths:
                continue
            resolved_entries += 1
            add_entry(npk.name, entry, paths[0])
            if entries_scanned % progress == 0:
                print("  npk %d entries, refs=%d" % (entries_scanned, len(refs_by_offset)))

    for shard in B.ppk_shards(game_root):
        package_name = "ppk/" + shard.name
        entries = B.read_shard_entries(shard)
        entries_scanned += len(entries)
        for entry in entries:
            paths = sig_paths.get(entry.signature)
            if not paths:
                continue
            resolved_entries += 1
            add_entry(package_name, entry, paths[0])
        if entries_scanned % progress == 0:
            print("  all %d entries, refs=%d" % (entries_scanned, len(refs_by_offset)))

    stats = {
        "entries_scanned": entries_scanned,
        "resolved_entries": resolved_entries,
        "ambiguous_sig_entries": ambiguous_entries,
        "renamed_path_entries": renamed_entries,
        "refs_npk": refs_npk,
        "refs_ppk_stored": refs_ppk_stored,
        "refs_ppk_zstd": refs_ppk_zstd,
        "candidate_refs": len(refs_by_offset),
    }
    return list(refs_by_offset.values()), stats


def verify_refs(refs, packages, seed):
    started = time.time()
    total_bytes = 0
    done = 0
    failed = []
    mismatch = []
    for index, ref in enumerate(refs, 1):
        package_path = packages.get(ref["package"])
        if not package_path:
            failed.append(ref["path"])
            continue
        try:
            raw, data = read_payload(package_path, ref)
        except Exception:
            failed.append(ref["path"])
            continue
        ref["sha256"], ref["unpacked_mmh3"], ref["packed_mmh3"] = fingerprint(raw, data, seed)
        expected = ref["packed_mmh3"] if ref["ref_kind"] == "ppk_zstd" else ref["unpacked_mmh3"]
        ref["hash_ok"] = expected == ref["index_hash"]
        if not ref["hash_ok"]:
            mismatch.append(ref["path"])
        ref["unpacked_key"] = (ref["unpacked"], ref["unpacked_mmh3"])
        done += 1
        total_bytes += len(data)
        if index % FLUSH_EVERY == 0:
            print("  refs %d/%d  %.0fMB  %.0fs" % (index, len(refs), total_bytes / 1e6, time.time() - started))
    print("  refs done: %d ok, %d failed, %.0fMB, %.0fs" % (
        done, len(failed), total_bytes / 1e6, time.time() - started))
    return done, failed, mismatch


def sample_named_check(named_rows, packages, seed, limit):
    import random

    random.seed(20261104)
    picked = [row for row in named_rows if row["unpacked"] <= 32 * 1024 * 1024 and row["unpacked_hash"]]
    random.shuffle(picked)
    picked = picked[:limit]
    checked = 0
    bad = []
    for row in picked:
        package_path = packages.get(row["package"])
        if not package_path:
            continue
        try:
            raw, data = read_payload(package_path, row)
        except Exception:
            continue
        checked += 1
        unpacked = mmh3.hash(data, seed, signed=False)
        packed = mmh3.hash(raw, seed, signed=False)
        expected = unpacked if index_hash_is_unpacked(row["package"], row["compression"]) else packed
        if expected != row["unpacked_hash"]:
            bad.append({"path": row["path"], "package": row["package"],
                        "compression": row["compression"], "index_hash": row["unpacked_hash"],
                        "unpacked_hash": unpacked, "packed_hash": packed})
    return {"checked": checked, "mismatch": len(bad), "examples": bad[:5]}


def pick_canonical(refs, unresolved_ext):
    def score(ref):
        suffix = ref["path"].rsplit(".", 1)[-1].lower() if "." in ref["path"] else ""
        same_ext = 0 if suffix == unresolved_ext.lstrip(".") else 1
        return (0 if ref["sig_in_catalog"] else 1, 0 if ref["in_catalog"] else 1,
                1 if ref["renamed"] else 0, same_ext, len(ref["path"]), ref["path"])
    return sorted(refs, key=score)[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--game-root", type=Path, default=Path(r"D:\FeverApps\party_pc"))
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--pathdict", type=Path, default=DEFAULT_PATHDICT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sample-named", type=int, default=300)
    parser.add_argument("--hash-limit", type=int, default=0, help="hash at most N unresolved rows (debug)")
    args = parser.parse_args()

    started = time.time()
    profile = N.load_profile(args.profile)
    seed = int(profile["path_hash_seed"])
    key = bytes.fromhex(profile["key_hex"])
    print("profile %s  seed=0x%08x" % (profile.get("name"), seed))

    sig_paths = read_pathdict(args.pathdict, seed)
    print("pathdict sigs: %d  %.0fs" % (len(sig_paths), time.time() - started))

    packages, named_rows, catalog_offset, named_sigs, unresolved = load_catalog(args.db)
    catalog_by_sig = defaultdict(list)
    for row in named_rows:
        catalog_by_sig[row["signature"]].append(row)
    print("catalog: named=%d  named-sigs=%d  unresolved=%d  packages=%d" % (
        len(named_rows), len(named_sigs), len(unresolved), len(packages)))

    if args.hash_limit:
        unresolved = unresolved[: args.hash_limit]
    ok, failed, mismatched = hash_unresolved(unresolved, packages, seed)
    actual_rows = [row for row in unresolved if "sha256" in row]
    print("unresolved hashed=%d  read-fail=%d  index-hash mismatch=%d" % (
        len(actual_rows), len(failed), len(mismatched)))

    wanted_keys = {(row["unpacked"], row["unpacked_mmh3"]) for row in actual_rows}
    wanted_sizes = {row["unpacked"] for row in actual_rows}

    refs, scan_stats = scan_named_refs(
        args.game_root, key, sig_paths, wanted_keys, wanted_sizes, catalog_offset, named_sigs)
    print("named scan: %s  %.0fs" % (scan_stats, time.time() - started))

    ref_ok, ref_failed, ref_mismatched = verify_refs(refs, packages, seed)
    ref_rows = [row for row in refs if "sha256" in row]
    print("refs verified=%d  read-fail=%d  index-hash mismatch=%d" % (
        len(ref_rows), len(ref_failed), len(ref_mismatched)))

    refs_by_key = defaultdict(list)
    for ref in ref_rows:
        refs_by_key[ref["unpacked_key"]].append(ref)

    mappings = {}
    mapping_aliases = {}
    rejected = []
    confirmed_pairs = 0
    packed_identical_pairs = 0
    for row in actual_rows:
        key2 = (row["unpacked"], row["unpacked_mmh3"])
        same_content = [ref for ref in refs_by_key.get(key2, []) if ref["sha256"] == row["sha256"]]
        if not same_content:
            if refs_by_key.get(key2):
                rejected.append({
                    "path": row["path"], "package": row["package"],
                    "sha256": row["sha256"], "reason": "mmh3_collision",
                })
            continue
        confirmed_pairs += len(same_content)
        for ref in same_content:
            if ref["packed"] == row["packed"] and ref["packed_mmh3"] == row["packed_mmh3"]:
                packed_identical_pairs += 1
        unique_paths = {}
        for ref in same_content:
            unique_paths.setdefault(ref["path"], ref)
        canonical = pick_canonical(same_content, row["ext"])
        alias_count = len(unique_paths)
        if alias_count == 1:
            specificity = "unique"
        elif alias_count <= 10:
            specificity = "few"
        elif alias_count <= 100:
            specificity = "shared"
        else:
            specificity = "boilerplate"
        alias_order = sorted(
            unique_paths,
            key=lambda item: (item != canonical["path"], len(item), item),
        )
        mapping_aliases[row["path"]] = set(unique_paths)
        mappings[row["path"]] = {
            "rule": "content-twin",
            "confidence": "confirmed",
            "signature": row["signature"],
            "signature_hex": "%08x" % row["signature"],
            "package": row["package"],
            "ext": row["ext"],
            "kind": row["kind"],
            "size": row["unpacked"],
            "sha256": row["sha256"],
            "named_path": canonical["path"],
            "named_package": canonical["package"],
            "named_offset": canonical["offset"],
            "named_signature": canonical["signature"],
            "named_signature_hex": "%08x" % canonical["signature"],
            "named_source": canonical["ref_kind"],
            "named_compression": canonical["compression"],
            "unresolved_source": "ppk_zstd" if (
                row["package"].startswith("ppk/") and row["compression"] == 3
            ) else "ppk_stored" if row["package"].startswith("ppk/") else "npk",
            "named_in_catalog": canonical["in_catalog"],
            "named_sig_in_catalog": canonical["sig_in_catalog"],
            "historical_version": canonical["sig_in_catalog"] and not canonical["in_catalog"],
            "catalog_stale": not canonical["sig_in_catalog"],
            "ambiguous_signature": canonical["ambiguous"],
            "packed_identical": any(
                ref["path"] == canonical["path"] and ref["packed"] == row["packed"]
                and ref["packed_mmh3"] == row["packed_mmh3"] for ref in same_content),
            "named_alias_count": alias_count,
            "specificity": specificity,
            "named_paths_truncated": alias_count > MAX_ALIASES,
            "named_paths": alias_order[:MAX_ALIASES],
            "evidence": "full sha256 equal (%d bytes); payload hashes verified" % row["unpacked"],
        }

    groups = defaultdict(list)
    for row in actual_rows:
        groups[row["sha256"]].append(row)

    historical_checked = 0
    historical_differs = 0
    for mapping in mappings.values():
        if not mapping["historical_version"]:
            continue
        candidates = catalog_by_sig.get(mapping["named_signature"], [])
        current = [row for row in candidates if row["path"] == mapping["named_path"]] or candidates
        if not current:
            mapping["current_version_status"] = "missing"
            continue
        row = current[0]
        package_path = packages.get(row["package"])
        try:
            raw, data = read_payload(package_path, row)
        except Exception:
            mapping["current_version_status"] = "read_fail"
            continue
        current_sha = hashlib.sha256(data).hexdigest()
        mapping["current_version_package"] = row["package"]
        mapping["current_version_offset"] = row["offset"]
        mapping["current_version_sha256"] = current_sha
        mapping["current_version_differs"] = current_sha != mapping["sha256"]
        mapping["current_version_status"] = "checked"
        historical_checked += 1
        mapping["historical_version"] = current_sha != mapping["sha256"]
        if not mapping["historical_version"]:
            mapping["duplicate_of_current"] = True
        if current_sha != mapping["sha256"]:
            historical_differs += 1

    equivalence = []
    for sha, members in groups.items():
        if len(members) < 2:
            continue
        aliases = set()
        for member in members:
            aliases.update(mapping_aliases.get(member["path"], ()))
        equivalence.append({
            "sha256": sha,
            "size": members[0]["unpacked"],
            "member_count": len(members),
            "members": [
                {
                    "path": member["path"],
                    "package": member["package"],
                    "signature_hex": "%08x" % member["signature"],
                    "named": member["path"] in mappings,
                }
                for member in sorted(members, key=lambda item: item["path"])
            ],
            "named_alias_count": len(aliases),
            "named_aliases": sorted(aliases)[:MAX_ALIASES],
        })
    equivalence.sort(key=lambda item: (-item["member_count"], item["sha256"]))

    sample = sample_named_check(named_rows, packages, seed, args.sample_named) if args.sample_named else {
        "checked": 0, "mismatch": 0, "examples": []}

    by_package = Counter(mapping["package"] for mapping in mappings.values())
    by_ext = Counter(mapping["ext"] for mapping in mappings.values())
    by_source = Counter(mapping["named_source"] for mapping in mappings.values())
    by_unresolved = Counter(mapping["unresolved_source"] for mapping in mappings.values())
    by_specificity = Counter(mapping["specificity"] for mapping in mappings.values())
    historical = sum(1 for mapping in mappings.values() if mapping["historical_version"])
    ambiguous = sum(1 for mapping in mappings.values() if mapping["ambiguous_signature"])
    packed_identical = sum(1 for mapping in mappings.values() if mapping["packed_identical"])
    alias_total = sum(mapping["named_alias_count"] for mapping in mappings.values())
    catalog_stale = sum(1 for mapping in mappings.values() if mapping["catalog_stale"])
    catalog_baseline = len(mappings) - catalog_stale
    recommended = {
        path: mapping["named_path"]
        for path, mapping in sorted(mappings.items())
        if not mapping["historical_version"] and mapping["named_alias_count"] <= 10
    }
    recommended_baseline = {
        path: mapping["named_path"]
        for path, mapping in sorted(mappings.items())
        if not mapping["historical_version"] and not mapping["catalog_stale"]
        and mapping["named_alias_count"] <= 10
    }
    pathdict_stat = args.pathdict.stat()
    pathdict_info = {
        "path": str(args.pathdict),
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pathdict_stat.st_mtime)),
        "size": pathdict_stat.st_size,
        "sha256": B.sha256_file(args.pathdict),
        "sigs": len(sig_paths),
    }

    payload = {
        "generated_by": "tools/cross_shard_dedupe.py",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "catalog": str(args.db),
        "game_root": str(args.game_root),
        "seed": seed,
        "pathdict": pathdict_info,
        "hash_semantics": {
            "npk": "index unpacked_hash = mmh3(unpacked payload)",
            "ppk_stored": "index content_hash = mmh3(unpacked payload)",
            "ppk_zstd": "index content_hash = mmh3(stored zstd frame)",
        },
        "total_unresolved": len(unresolved),
        "unresolved_hashed": len(actual_rows),
        "unresolved_read_fail": len(failed),
        "unresolved_index_hash_mismatch": len(mismatched),
        "named_entries_scanned": scan_stats["entries_scanned"],
        "named_candidate_refs": len(ref_rows),
        "confirmed_unresolved": len(mappings),
        "confirmed_pairs": confirmed_pairs,
        "packed_identical_mappings": packed_identical,
        "packed_identical_pairs": packed_identical_pairs,
        "historical_version_mappings": historical,
        "historical_version_checked": historical_checked,
        "historical_version_differs": historical_differs,
        "catalog_stale_mappings": catalog_stale,
        "catalog_baseline_mappings": catalog_baseline,
        "ambiguous_signature_mappings": ambiguous,
        "named_alias_paths_total": alias_total,
        "equivalence_group_count": len(equivalence),
        "equivalence_group_members": sum(item["member_count"] for item in equivalence),
        "rejected_candidates": rejected,
        "scan_stats": scan_stats,
        "verify_stats": {"refs_verified": len(ref_rows), "read_fail": len(ref_failed),
                         "index_hash_mismatch": len(ref_mismatched)},
        "sample_named_check": sample,
        "stats": {
            "by_package": dict(by_package.most_common()),
            "by_ext": dict(by_ext.most_common()),
            "by_named_source": dict(by_source.most_common()),
            "by_unresolved_source": dict(by_unresolved.most_common()),
            "by_specificity": dict(by_specificity.most_common()),
        },
        "backfill_map": {path: mapping["named_path"] for path, mapping in sorted(mappings.items())},
        "backfill_map_recommended": recommended,
        "backfill_map_catalog_baseline": recommended_baseline,
        "mappings": mappings,
        "equivalence_groups": equivalence,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("confirmed unresolved: %d / %d  pairs=%d  packed-identical=%d  historical=%d  groups=%d  aliases=%d" % (
        len(mappings), len(unresolved), confirmed_pairs, packed_identical, historical,
        len(equivalence), alias_total))
    print("recommended backfill (non-historical, alias<=10): %d  baseline-only: %d  specificity=%s  sources=%s" % (
        len(recommended), len(recommended_baseline), dict(by_specificity.most_common()),
        dict(by_source.most_common())))
    print("rejected collisions: %d  sample named check: %s" % (len(rejected), sample))
    print("wrote %s  %.0fs" % (args.out, time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
