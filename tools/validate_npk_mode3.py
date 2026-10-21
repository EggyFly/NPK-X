#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate every local NPK against the recovered mode-3 format."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import extract_mode3 as E
import nxpk_mode3 as N

RESOLVER = None
KEY = N.MODE3_KEY
HASH_SEED = N.PATH_HASH_SEED


def _init(paths: str | None, key_hex: str, seed: int, hash_seed: int, tables: str | None) -> None:
    global RESOLVER, KEY, HASH_SEED
    KEY = bytes.fromhex(key_hex)
    HASH_SEED = hash_seed
    if tables:
        N.install_tables(tables)
    RESOLVER = N.PathResolver.from_file(paths, seed) if paths else N.PathResolver([], seed)


def _run(path: str) -> dict:
    started = time.time()
    report = N.audit(path, RESOLVER, KEY, HASH_SEED)
    report["elapsed_seconds"] = round(time.time() - started, 2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Full-package validation of Eggy Party NPKs")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--paths", "--path-dict", dest="paths", type=Path, default=None, help="path dictionary (.txt or .txt.gz)")
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--key-hex", type=str, default=None)
    parser.add_argument("--tables", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("_validation_reports"))
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()

    profile_path = args.profile
    if profile_path is None:
        candidate = E.PACKAGE_ROOT / "data" / "mode3_profile.json"
        if candidate.is_file():
            profile_path = candidate
    profile = N.load_profile(profile_path)
    key = N.profile_key(profile, args.key_hex)
    seed = N.profile_seed(profile)
    hash_seed = int(profile.get("content_hash_seed", N.PATH_HASH_SEED), 0) if isinstance(profile.get("content_hash_seed", N.PATH_HASH_SEED), str) else int(profile.get("content_hash_seed", N.PATH_HASH_SEED))
    tables = args.tables or profile.get("tables_file")
    if tables:
        tables_path = Path(tables)
        if not tables_path.is_absolute() and profile_path:
            tables_path = Path(profile_path).parent / tables_path
        N.install_tables(tables_path)
        tables = str(tables_path)
    else:
        tables = None
    path_dict = E.find_path_dict(args.paths)
    game_root = E.find_game_root(args.game_root)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(str(p) for p in game_root.glob("*.npk"))
    if not files:
        print(f"no NPK files under {game_root}", file=sys.stderr)
        return 4
    print(f"profile={profile.get('name', 'custom')} key={key.hex()} packages={len(files)}", flush=True)

    reports = []
    with ProcessPoolExecutor(
        max_workers=args.jobs,
        initializer=_init,
        initargs=(str(path_dict) if path_dict else None, key.hex(), seed, hash_seed, tables),
    ) as pool:
        futures = {pool.submit(_run, f): f for f in files}
        for future in as_completed(futures):
            file = futures[future]
            try:
                report = future.result()
            except Exception as exc:
                report = {"file": Path(file).name, "valid": False, "exception": repr(exc)}
            name = Path(file).name
            (args.output_dir / f"{name}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            reports.append(report)
            print(
                f"{name:24s} valid={report.get('valid')} entries={report.get('entry_count')} "
                f"paths={report.get('paths', {}).get('resolved')}/{report.get('entry_count')} "
                f"failures={report.get('payload', {}).get('failure_count')} "
                f"{report.get('elapsed_seconds', '?')}s",
                flush=True,
            )

    reports.sort(key=lambda r: r.get("file", ""))
    script_names = {"script.npk", "script_patch.npk"}
    script_collisions = sum(r.get("paths", {}).get("resolved", 0) for r in reports if r.get("file") in script_names)
    effective_resolved = sum(
        r.get("paths", {}).get("resolved", 0) for r in reports if r.get("file") not in script_names
    )
    total_entries = sum(r.get("entry_count", 0) for r in reports)
    combined = {
        "profile": profile.get("name"),
        "packages": reports,
        "summary": {
            "packages": len(reports),
            "packages_valid": sum(1 for r in reports if r.get("valid")),
            "entries": total_entries,
            "paths_resolved": sum(r.get("paths", {}).get("resolved", 0) for r in reports),
            "paths_unresolved": sum(r.get("paths", {}).get("unresolved", 0) for r in reports),
            "script_collision_matches": script_collisions,
            "paths_resolved_effective": effective_resolved,
            "paths_unresolved_effective": total_entries - effective_resolved,
            "payload_entries": sum(
                r.get("payload", {}).get("zstd_entries", 0) + r.get("payload", {}).get("stored_entries", 0)
                for r in reports
            ),
            "failure_count": sum(r.get("payload", {}).get("failure_count", 0) for r in reports),
        },
    }
    summary_path = Path("nxpk_mode3_validation.json")
    summary_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(combined["summary"], ensure_ascii=False, indent=2))
    return 0 if combined["summary"]["packages_valid"] == len(reports) else 1


if __name__ == "__main__":
    sys.exit(main())
