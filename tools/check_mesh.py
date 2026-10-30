"""Parse selected catalog meshes and print a compact summary."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mesh_parse
import npk_browser


DEFAULT_DB = npk_browser.default_cache_dir() / "catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for path in args.paths:
            row = connection.execute(
                "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
                "f.compression, p.path AS package_path "
                "FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
                (path,),
            ).fetchone()
            if not row:
                print(f"{path}: not found")
                continue
            try:
                parsed = mesh_parse.parse(npk_browser.read_entry_bytes(row["package_path"], row))
                print(f"{path}: {parsed['variant']} v={parsed['vertex_count']} f={parsed['face_count']} "
                      f"sub={len(parsed['submeshes'])} uv={parsed['uv_layers']} "
                      f"bounds={parsed['bounds']}")
            except Exception as error:
                print(f"{path}: {type(error).__name__}: {error}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
