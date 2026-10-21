#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify an extraction produced by extract_mode3.py."""

from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(r"D:\FeverApps\party_pc")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path("export_mode3"))
    parser.add_argument("--png-limit", type=int, default=0)
    args = parser.parse_args()
    root = args.root
    manifest = root / "_manifest.csv.gz"
    rows = []
    with gzip.open(manifest, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(row)
    statuses = collections.Counter(row["status"] for row in rows)
    ext_counts = collections.Counter()
    ext_bytes = collections.Counter()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name in ("_manifest.csv.gz", "_summary.json", "_failures.jsonl", "_verification.json"):
            continue
        suffix = path.suffix.lower() or "<none>"
        ext_counts[suffix] += 1
        ext_bytes[suffix] += path.stat().st_size

    output_paths = [row["output_path"] for row in rows if row["status"] not in ("failed", "cancelled")]
    duplicate_outputs = len(output_paths) - len({p.lower() for p in output_paths})
    missing = []
    for row in rows:
        if row["status"] in ("failed", "cancelled"):
            continue
        target = root / row["output_path"]
        if not target.is_file():
            missing.append(row["output_path"])

    pngs = list(root.rglob("*.png"))
    limit = args.png_limit if args.png_limit else len(pngs)
    bad_png = []
    for path in pngs[:limit]:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            bad_png.append((str(path), str(exc)))

    flacs = list(root.rglob("*.flac"))
    bad_flac = []
    for path in flacs:
        with open(path, "rb") as handle:
            if handle.read(4) != b"fLaC":
                bad_flac.append(str(path))

    flac_samples = []
    try:
        import soundfile as sf

        for path in flacs[:5]:
            info = sf.info(str(path))
            flac_samples.append((str(path), info.format == "FLAC", info.samplerate))
    except Exception as exc:
        ffmpeg = r"C:\ffmpeg\bin\ffmpeg.exe"
        for path in flacs[:5]:
            try:
                result = subprocess.run(
                    [ffmpeg, "-hide_banner", "-i", str(path)],
                    capture_output=True,
                    timeout=30,
                    text=True,
                    errors="ignore",
                )
                flac_samples.append((str(path), "flac" in result.stderr.lower(), len(result.stderr)))
            except Exception as inner:
                flac_samples.append((str(path), f"unavailable: {inner}", 0))

    summary = {
        "root": str(root),
        "manifest_rows": len(rows),
        "status_counts": dict(statuses),
        "extensions": {k: {"count": ext_counts[k], "bytes": ext_bytes[k]} for k in sorted(ext_counts)},
        "file_count": sum(ext_counts.values()),
        "total_bytes": sum(ext_bytes.values()),
        "real_path_rows": sum(1 for row in rows if row["real_path"]),
        "duplicate_output_paths": duplicate_outputs,
        "missing_output_files": len(missing),
        "missing_samples": missing[:20],
        "png_checked": min(limit, len(pngs)),
        "png_total": len(pngs),
        "bad_png": bad_png[:20],
        "bad_png_count": len(bad_png),
        "flac_total": len(flacs),
        "bad_flac": bad_flac[:20],
        "bad_flac_count": len(bad_flac),
        "flac_samples": flac_samples,
        "free_bytes": __import__("shutil").disk_usage(root).free,
    }
    (root / "_verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
