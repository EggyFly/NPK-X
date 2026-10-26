"""Report connected components and joint/weight ranges for a mesh."""

from __future__ import annotations

import argparse
import sqlite3
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
    if not row:
        raise SystemExit(f"not found: {args.path}")
    data = npk_browser.read_entry_bytes(row["package_path"], row)
    parsed = mesh_parse.parse(data)
    verts = parsed["vertices"]
    indices = parsed["indices"]
    parent = list(range(len(verts)))

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for index in range(0, len(indices) - 2, 3):
        a, b, c = indices[index:index + 3]
        union(a, b)
        union(b, c)

    groups = {}
    for index in range(len(verts)):
        groups.setdefault(find(index), []).append(index)
    parts = sorted(groups.values(), key=len, reverse=True)
    print(f"{args.path}: {parsed['variant']} verts={len(verts)} faces={len(indices)//3} "
          f"components={len(parts)} bounds={parsed['bounds']}")
    for number, part in enumerate(parts[:15]):
        xs = [verts[i][0] for i in part]
        ys = [verts[i][1] for i in part]
        zs = [verts[i][2] for i in part]
        print(f"  part{number}: verts={len(part)} "
              f"x={min(xs):.3f}..{max(xs):.3f} y={min(ys):.3f}..{max(ys):.3f} "
              f"z={min(zs):.3f}..{max(zs):.3f}")
    if parsed["flag"]:
        info = mesh_parse._parse_bone_header(data)
        vcount = info["vcount"]
        tail = data[info["data_end"] - 12 * vcount - 32:info["data_end"] - 32]
        joints = [tuple(tail[index * 12:index * 12 + 4]) for index in range(vcount)]
        weights = [tuple(int.from_bytes(tail[index * 12 + 4:index * 12 + 6], "little") for _ in range(1))
                   for index in range(vcount)]
        print(f"  joints[0:8]={joints[:8]}")
        print(f"  weights[0:8]={weights[:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
