#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Map numeric Wwise WEM media IDs to the BGM banks that reference them.

Wwise media files are named by 32-bit Short IDs.  The client does not ship
SoundbanksInfo metadata, so original event names cannot be recovered from the
resources.  This tool exports a truthful alias map (WEM -> named BNK) and can
copy WEMs into per-bank folders for easier browsing.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bnk_parse
import extract_mode3 as E
import nxpk_mode3 as N


def read_entry(package: Path, entry: N.Entry) -> bytes:
    with open(package, "rb") as handle:
        handle.seek(entry.offset)
        raw = handle.read(entry.packed_size)
    if len(raw) != entry.packed_size:
        raise ValueError(f"short read for signature {entry.signature:08x}")
    if entry.compression == N.COMPRESSION_ZSTD:
        decompressor = zstandard.ZstdDecompressor().decompressobj()
        data = decompressor.decompress(raw)
        if not decompressor.eof:
            raise ValueError(f"truncated Zstd frame for signature {entry.signature:08x}")
    elif entry.compression == N.COMPRESSION_STORED:
        data = raw
    else:
        raise ValueError(f"unsupported compression {entry.compression}")
    if len(data) != entry.unpacked_size:
        raise ValueError(f"size mismatch for signature {entry.signature:08x}")
    return data


def safe_component(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or "unnamed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Map numeric Wwise WEM IDs to referencing BNK banks")
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--path-dict", "--paths", dest="path_dict", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument("--key-hex", type=str, default=None)
    parser.add_argument("--csv", type=Path, default=Path("wwise_media_map.csv"))
    parser.add_argument("--copy-to", type=Path, default=None, help="copy WEMs into <bank>/<id>.wem")
    args = parser.parse_args()

    game_root = E.find_game_root(args.game_root)
    package = game_root / "wwise.npk"
    if not package.is_file():
        print(f"wwise.npk not found under {game_root}", file=sys.stderr)
        return 2
    profile_path = args.profile
    if profile_path is None:
        candidate = E.PACKAGE_ROOT / "data" / "mode3_profile.json"
        if candidate.is_file():
            profile_path = candidate
    profile = N.load_profile(profile_path)
    key = N.profile_key(profile, args.key_hex)
    seed = N.profile_seed(profile)
    path_dict = E.find_path_dict(args.path_dict)
    if not path_dict:
        print("path dictionary not found; pass --path-dict", file=sys.stderr)
        return 3
    resolver = N.PathResolver.from_file(path_dict, seed)
    _, entries = N.read_index(package, key, validate=True, resolver=resolver)

    wems: dict[int, dict] = {}
    banks: dict[str, N.Entry] = {}
    for entry in entries:
        logical = resolver.resolve(entry.signature)[0]
        if not logical:
            continue
        rel = logical.replace("\\", "/").lstrip("/")
        suffix = Path(rel).suffix.lower()
        if suffix == ".wem":
            try:
                media_id = int(Path(rel).stem)
            except ValueError:
                continue
            wems[media_id] = {"path": rel, "entry": entry}
        elif suffix == ".bnk":
            banks[rel] = entry

    refs: dict[int, list[tuple[str, int]]] = defaultdict(list)
    for index, (bank_path, entry) in enumerate(sorted(banks.items()), 1):
        try:
            bank = bnk_parse.parse_bank(read_entry(package, entry))
        except Exception as exc:
            print(f"warning: {bank_path}: {exc}", file=sys.stderr)
            continue
        for track in bank.hirc.get("music_tracks", []):
            media_id = track.get("media_id")
            if media_id in wems:
                refs[media_id].append((bank_path, track.get("id") or 0))
        if index % 500 == 0:
            print(f"scanned {index}/{len(banks)} banks", flush=True)

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with open(args.csv, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["wem_path", "wem_id", "bank_path", "bank_name", "track_object_id"])
        for media_id in sorted(wems):
            item = wems[media_id]
            if refs.get(media_id):
                for bank_path, track_id in sorted(refs[media_id]):
                    writer.writerow(
                        [item["path"], media_id, bank_path, Path(bank_path).stem, f"{track_id:08x}"]
                    )
            else:
                writer.writerow([item["path"], media_id, "", "", ""])
    print(f"mapped {len(refs)}/{len(wems)} WEMs; wrote {args.csv}", flush=True)

    if args.copy_to:
        copied = 0
        for media_id in sorted(wems):
            item = wems[media_id]
            primary = sorted(refs[media_id])[0][0] if refs.get(media_id) else "_unmapped"
            folder = safe_component(Path(primary).stem)
            target_dir = args.copy_to / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{media_id}.wem"
            if not target.is_file():
                target.write_bytes(read_entry(package, item["entry"]))
            copied += 1
        print(f"copied {copied} WEMs under {args.copy_to}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
