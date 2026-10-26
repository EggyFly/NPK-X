"""Find the per-vertex skin record layout in a NeoX mesh tail."""

from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mesh_parse
import npk_browser


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
            "f.compression, p.path AS package_path "
            "FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
            (args.path,),
        ).fetchone()
    finally:
        connection.close()
    data = npk_browser.read_entry_bytes(row["package_path"], row)
    info = mesh_parse._parse_bone_header(data)
    vcount = info["vcount"]
    end = info["data_end"]
    start = max(0, end - 16 * vcount - 64)
    print(f"{args.path}: vcount={vcount} data_end={end} search={start}..{end}")
    for stride in (8, 12):
        best = []
        for offset in range(start, min(end - stride, start + 4096), 2):
            good = 0
            total = 0
            for index in range(0, min(vcount, 256)):
                pos = offset + index * stride
                if pos + stride > end:
                    break
                total += 1
                if stride == 12:
                    joints = list(data[pos:pos + 4])
                    weights = struct.unpack_from("<4H", data, pos + 4)
                    wsum = sum(weights)
                else:
                    joints = list(data[pos:pos + 4])
                    weights = list(data[pos + 4:pos + 8])
                    wsum = sum(weights)
                if all(j < 64 for j in joints) and (abs(wsum - 65535) < 64 or abs(wsum - 255) < 2):
                    good += 1
            if total:
                best.append((good / total, offset, stride, total, good))
        best.sort(reverse=True)
        for score, offset, stride, total, good in best[:5]:
            print(f"  stride={stride} offset={offset} score={score:.3f} good={good}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
