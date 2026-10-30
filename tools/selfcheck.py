#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-test the portable toolkit: cipher, tables, path hash and fixtures."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if (HERE.parent / "fixtures").is_dir() or (HERE.parent / "data").is_dir() else HERE
sys.path.insert(0, str(HERE))

import nxpk_mode3 as N  # noqa: E402


def main() -> int:
    fixtures = ROOT / "fixtures"
    vectors = json.loads((fixtures / "mode3_vectors.json").read_text(encoding="utf-8"))
    failures = []

    def check(name: str, actual, expected) -> None:
        if actual != expected:
            failures.append(f"{name}: got {actual!r}, expected {expected!r}")
        print(f"[{'PASS' if actual == expected else 'FAIL'}] {name}")

    check("runtime key", N.MODE3_KEY.hex(), vectors["key_hex"])
    schedule = N.prepare_schedule(N.MODE3_KEY)
    check("schedule words", ["0x%08x" % x for x in schedule[:8]], vectors["schedule_first8"])
    check("round count", schedule[60], vectors["round_count"])
    for number, item in enumerate(vectors["block_vectors"], 1):
        actual = N.decrypt_block(bytes.fromhex(item["input"]), schedule).hex()
        check(f"block vector {number}", actual, item["plain"])
    path = vectors["path_hash"]["path"]
    check("path hash", "0x%08x" % N.path_hash(path), vectors["path_hash"]["value"])
    sample = (fixtures / "index_sample.bin").read_bytes()
    plain = N.decrypt_index(sample)
    check("index sample prefix", plain.hex(), vectors["index_sample"]["plain_prefix"])

    for module in ("zstandard", "PIL", "mmh3", "texture2ddecoder"):
        try:
            __import__(module)
            print(f"[PASS] import {module}")
        except Exception as exc:
            failures.append(f"import {module}: {exc}")
            print(f"[FAIL] import {module}: {exc}")
    try:
        import soundfile  # noqa: F401
        print("[PASS] import soundfile (FLAC encoder)")
    except Exception:
        print("[INFO] soundfile missing; WEM->FLAC will need ffmpeg")

    try:
        import uncompyle6  # noqa: F401
        print("[PASS] import uncompyle6 (XN source decompiler)")
    except Exception as exc:
        failures.append(f"import uncompyle6: {exc}")
        print("[FAIL] import uncompyle6 (XN source view unavailable)")

    vgm_name = "vgmstream-cli.exe" if os.name == "nt" else "vgmstream-cli"
    vgm = None
    for candidate in (
        ROOT / "bin" / "vgmstream" / vgm_name,
        ROOT / "_tools" / "vgmstream" / vgm_name,
    ):
        if candidate.is_file():
            vgm = candidate
            break
    if vgm is None:
        vgm = shutil.which(vgm_name)
    if vgm:
        print(f"[PASS] vgmstream: {vgm}")
    else:
        failures.append("vgmstream-cli not found (WEM decoding unavailable)")
        print("[FAIL] vgmstream-cli not found")

    pathdict = ROOT / "data" / "pathdict.txt.gz"
    if pathdict.is_file():
        print(f"[PASS] path dictionary: {pathdict}")
    else:
        print("[WARN] path dictionary missing; assets will be named by hash")

    print()
    if failures:
        print(f"SELF-CHECK FAILED ({len(failures)} issue(s))")
        for item in failures:
            print(" -", item)
        return 1
    print("SELF-CHECK OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
