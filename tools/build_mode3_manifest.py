#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a path -> entry manifest for all local mode-3 NPKs."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import extract_mode3 as E
import nxpk_mode3 as N

SCRIPT_NAMESPACES = {"script.npk", "script_patch.npk"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a path -> entry manifest for Eggy Party NPKs")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--paths", "--path-dict", dest="paths", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--key-hex", type=str, default=None)
    parser.add_argument("--tables", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("nxpk_mode3_manifest.csv.gz"))
    parser.add_argument("--summary", type=Path, default=Path("nxpk_mode3_manifest_summary.json"))
    args = parser.parse_args()

    profile_path = args.profile
    if profile_path is None:
        candidate = E.PACKAGE_ROOT / "data" / "mode3_profile.json"
        if candidate.is_file():
            profile_path = candidate
    profile = N.load_profile(profile_path)
    key = N.profile_key(profile, args.key_hex)
    seed = N.profile_seed(profile)
    tables = args.tables or profile.get("tables_file")
    if tables:
        tables_path = Path(tables)
        if not tables_path.is_absolute() and profile_path:
            tables_path = Path(profile_path).parent / tables_path
        N.install_tables(tables_path)
    path_dict = E.find_path_dict(args.paths)
    resolver = N.PathResolver.from_file(path_dict, seed) if path_dict else N.PathResolver([], seed)
    game_root = E.find_game_root(args.game_root)
    packages = sorted(game_root.glob("*.npk"))
    if not packages:
        print(f"no NPK files under {game_root}")
        return 4

    summary = {
        "profile": profile.get("name"),
        "packages": {},
        "total_entries": 0,
        "resolved": 0,
        "unresolved": 0,
        "script_collision_matches": 0,
    }
    with gzip.open(args.out, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "package",
                "path",
                "signature",
                "offset",
                "packed_size",
                "unpacked_size",
                "compression",
                "flags",
                "packed_hash",
                "unpacked_hash",
            ]
        )
        for pkg in packages:
            header, entries = N.read_index(pkg, key, validate=True, resolver=resolver)
            resolved = 0
            for entry in entries:
                logical, _ = resolver.resolve(entry.signature)
                if pkg.name in SCRIPT_NAMESPACES:
                    if logical:
                        summary["script_collision_matches"] += 1
                    logical = None
                if logical:
                    resolved += 1
                writer.writerow(
                    [
                        pkg.name,
                        logical or "",
                        f"{entry.signature:08x}",
                        entry.offset,
                        entry.packed_size,
                        entry.unpacked_size,
                        entry.compression,
                        f"{entry.flags:#06x}",
                        f"{entry.packed_hash:08x}",
                        f"{entry.unpacked_hash:08x}",
                    ]
                )
            summary["packages"][pkg.name] = {
                "entries": len(entries),
                "resolved": resolved,
                "unresolved": len(entries) - resolved,
            }
            summary["total_entries"] += len(entries)
            summary["resolved"] += resolved
            summary["unresolved"] += len(entries) - resolved
            print(f"{pkg.name}: {len(entries)} entries, {resolved} resolved", flush=True)

    summary["note"] = (
        "script.npk/script_patch.npk use a separate script namespace; "
        "asset dictionary matches for those files are random collisions and are not path names."
    )
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "packages"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
