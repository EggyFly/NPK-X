#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild the local path dictionary from the client's PPK shards.

The shipped path list is a large Zstandard frame inside ``ppk/<shard>``.  This
module finds a frame whose decompressed content is a newline-separated path
list, validates it, and optionally writes a fresh ``pathdict.txt.gz`` and a
minimal ``mode3_profile.json``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
from pathlib import Path

import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nxpk_mode3 as N

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
SCAN_BYTES = 16 * 1024 * 1024
MAX_FRAME_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
STEP = 1024 * 1024


def _looks_like_pathdict(data: bytes) -> bool:
    if len(data) < 1_000_000:
        return False
    lines = data.splitlines()
    if len(lines) < 100_000:
        return False
    sample = lines[:2000] + lines[-2000:]
    good = 0
    for line in sample:
        if not line or len(line) > 512 or b"/" not in line or b" " in line:
            continue
        if not all(32 <= byte < 127 for byte in line):
            continue
        if line.startswith((b"KTX", b"DDS ", b"RIFF", b"BKHD", b"PK\x03\x04")):
            continue
        good += 1
    if good < len(sample) * 0.97:
        return False
    roots = {line.split(b"/", 1)[0] for line in lines if b"/" in line}
    known_roots = {b"char", b"gui2", b"effect", b"props", b"scene_w", b"wwise"}
    if len(roots & known_roots) < 3:
        return False
    return len(set(lines)) > len(lines) * 0.98


def _decompress_frame(blob: bytes, start: int, end: int) -> bytes | None:
    decompressor = zstandard.ZstdDecompressor().decompressobj()
    output = bytearray()
    position = start
    end = min(end, start + MAX_FRAME_BYTES)
    while position < end and len(output) <= MAX_OUTPUT_BYTES:
        step = blob[position:min(position + STEP, end)]
        position += len(step)
        try:
            output.extend(decompressor.decompress(step))
        except zstandard.ZstdError:
            return None
        if decompressor.eof:
            break
    if not decompressor.eof:
        return None
    return bytes(output)


def _scan_blob(blob: bytes) -> bytes | None:
    positions = []
    position = 0
    while True:
        position = blob.find(ZSTD_MAGIC, position)
        if position < 0:
            break
        positions.append(position)
        position += 4
    for start in positions:
        next_start = blob.find(ZSTD_MAGIC, start + 4)
        end = next_start if next_start > 0 else len(blob)
        data = _decompress_frame(blob, start, end)
        if data is not None and _looks_like_pathdict(data):
            return data
    return None


def find_path_dictionary(game_root: Path, scan_bytes: int = SCAN_BYTES, log=print) -> bytes:
    ppk_dir = Path(game_root) / "ppk"
    shards = sorted(
        path for path in ppk_dir.glob("*")
        if path.is_file() and path.name not in {"assets.ppk", "assets.ppkn"}
    )
    if not shards:
        raise FileNotFoundError(f"no PPK shards under {ppk_dir}")
    for index, shard in enumerate(shards, 1):
        log(f"[{index}/{len(shards)}] scanning {shard.name}")
        with open(shard, "rb") as handle:
            blob = handle.read(scan_bytes)
        if len(blob) < 32:
            continue
        data = _scan_blob(blob)
        if data is not None:
            log(f"path dictionary found in {shard.name} ({len(data):,} bytes)")
            return data
    if any(shard.stat().st_size > scan_bytes for shard in shards):
        log("not found in the scanned prefix; checking the remaining shard data...")
        for index, shard in enumerate(shards, 1):
            if shard.stat().st_size <= scan_bytes:
                continue
            log(f"[{index}/{len(shards)}] full scan {shard.name}")
            data = _scan_blob(shard.read_bytes())
            if data is not None:
                log(f"path dictionary found in {shard.name} ({len(data):,} bytes)")
                return data
    raise FileNotFoundError(
        f"path dictionary frame not found in {ppk_dir}"
    )


def write_path_dictionary(data: bytes, output: Path, log=print) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + f".{os.getpid()}.tmp")
    try:
        if output.suffix.lower() == ".gz":
            with open(tmp, "wb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as archive:
                    archive.write(data)
        else:
            tmp.write_bytes(data)
        os.replace(tmp, output)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    lines = len(data.splitlines())
    log(f"wrote {output} ({lines:,} paths, {output.stat().st_size:,} bytes)")
    return output


VERSION_PATTERNS = {
    "package_version": r"(?:package_version|PKG_REV)['\"]?\s*[:=]\s*['\"]?([0-9][\w.]*)",
    "engine_ver": r"(?:ENGINE_VERSION|engine_ver|#ENGINE_VER)['\"]?\s*[:=]\s*['\"]?(\d+)",
    "a50_version": r"a50_version['\"]?\s*[:=]\s*['\"]?([0-9][0-9\- :]*)",
    "res_rev": r"(?:RES_VERSION|res_rev|RS_VER)['\"]?\s*[:=]\s*['\"]?(\d+)",
    "client_ver": r"client_ver['\"]?\s*[:=]\s*['\"]?(\d+)",
    "svn_branch": r"svn_branch['\"]?\s*[:=]\s*['\"]?([0-9A-Za-z_.-]+)",
    "game_ver_str": r"game_ver_str['\"]?\s*[:=]\s*['\"]?([0-9A-Za-z_.()-]+)",
}


def detect_client_version(game_root: Path) -> tuple[str, dict, dict]:
    game_root = Path(game_root)
    logs = []
    main_log = game_root / "log.txt"
    if main_log.is_file():
        logs.append(main_log)
    old_logs = sorted(game_root.glob("log_old_*.txt"), key=lambda path: path.stat().st_mtime, reverse=True)
    logs.extend(old_logs)
    text = ""
    for log in logs:
        try:
            text = log.read_text("utf-8", errors="replace")
        except OSError:
            continue
        if "package_version" in text or "PKG_REV" in text or "ENGINE_VER" in text:
            break
    fields = {}
    for key, pattern in VERSION_PATTERNS.items():
        matches = list(re.finditer(pattern, text))
        if matches:
            fields[key] = matches[-1].group(1).strip()
    package = fields.get("package_version")
    engine = fields.get("engine_ver")
    a50 = fields.get("a50_version")
    if package and engine:
        name = "pc-%s-engine-%s" % (package, engine)
        if a50:
            tag = a50.split(":")[-1].strip()[-4:]
            if tag:
                name += "-a50-%s" % tag
    else:
        name = "generated-local-client"
    tested = {
        "engine_ver": engine or "",
        "package_version": package or "",
        "a50_version": a50 or "",
        "res_rev": int(fields["res_rev"]) if str(fields.get("res_rev", "")).isdigit() else None,
        "client_ver": int(fields["client_ver"]) if str(fields.get("client_ver", "")).isdigit() else None,
        "svn_branch": fields.get("svn_branch", ""),
        "game_ver_str": fields.get("game_ver_str", ""),
    }
    return name, tested, fields


def build_profile(game_root: Path, path_dict_name: str = "pathdict.txt.gz") -> dict:
    name, tested, version = detect_client_version(game_root)
    profile = N.load_profile(None)
    known = {}
    for package in sorted(Path(game_root).glob("*.npk")):
        try:
            with open(package, "rb") as handle:
                header = N.read_header(handle.read(N.HEADER_SIZE))
            known[package.name] = {
                "entry_count": header.entry_count,
                "index_offset": header.index_offset,
                "size": package.stat().st_size,
            }
        except Exception:
            continue
    profile.update(
        {
            "name": name,
            "format": "NXPK mode 3, 28-byte entries",
            "tested_client": tested,
            "path_dict": path_dict_name,
            "known_packages": known,
            "version": version,
            "notes": "Generated from the local client. The mode-3 key is build-bound; refresh it if index validation fails.",
        }
    )
    return profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild data/pathdict.txt.gz from the game's PPK shards")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("data/pathdict.txt.gz"))
    parser.add_argument("--scan-mb", type=int, default=16)
    parser.add_argument("--write-profile", action="store_true")
    parser.add_argument("--profile-output", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        print(f"refusing to overwrite {args.output}; pass --force", file=sys.stderr)
        return 2
    if args.write_profile and args.profile_output is None:
        print("--write-profile requires --profile-output <path>", file=sys.stderr)
        return 2
    if args.write_profile and args.profile_output.exists() and not args.force:
        print(f"refusing to overwrite {args.profile_output}; pass --force", file=sys.stderr)
        return 2
    game_root = args.game_root
    if game_root is None:
        import extract_mode3 as E

        game_root = E.find_game_root(None)
    try:
        data = find_path_dictionary(Path(game_root), args.scan_mb * 1024 * 1024)
        write_path_dictionary(data, args.output)
        if args.write_profile:
            profile_path = args.profile_output
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile = build_profile(Path(game_root), args.output.name)
            profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {profile_path}")
    except (OSError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
