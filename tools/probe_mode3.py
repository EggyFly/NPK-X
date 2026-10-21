#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe local NPKs without writing anything (version/key compatibility check)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import extract_mode3 as E
import nxpk_mode3 as N


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Eggy Party NPK mode-3 indexes")
    parser.add_argument("path", nargs="?", type=Path, help="single NPK (default: all under --game-root)")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--paths", "--path-dict", dest="paths", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--key-hex", type=str, default=None)
    parser.add_argument("--tables", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
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
    resolver = N.PathResolver.from_file(path_dict, seed) if path_dict else None

    if args.path:
        packages = [args.path]
    else:
        packages = sorted(E.find_game_root(args.game_root).glob("*.npk"))
    if not packages:
        print("no NPK files found", file=sys.stderr)
        return 4
    if not args.json:
        print(f"profile : {profile.get('name', 'custom')}")
        print(f"key     : {key.hex()}")
        print(f"seed    : 0x{seed:08X}")
        print(f"paths   : {path_dict or '(none)'}")
        print("-" * 100)
    reports = []
    failed = 0
    for package in packages:
        try:
            report = N.probe_package(package, key, resolver, profile)
        except Exception as exc:
            failed += 1
            report = {"file": package.name, "error": str(exc), "valid": False}
        reports.append(report)
        if args.json:
            continue
        if "error" in report:
            print(f"{package.name:24s} FAIL  {report['error'][:160]}")
            continue
        known = report.get("known_package", {})
        known_text = ""
        if known:
            known_text = f" known={'OK' if known.get('match') else 'CHANGED'}"
        print(
            f"{package.name:24s} entries={report['entry_count']:7d} sorted={report['sorted']} "
            f"zstd={report['zstd_sample']:>8s} paths={report['path_sample']:>8s}"
            f" flags={report['flag_bits']}{known_text}"
        )
        for warning in report.get("warnings", []):
            print(f"  warning: {warning}")
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 5


if __name__ == "__main__":
    raise SystemExit(main())
