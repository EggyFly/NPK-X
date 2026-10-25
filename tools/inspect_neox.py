"""Print the BXML object tree of a catalog .gim/.mtg/.animation file."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import neox_object
import npk_browser


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"


def get_row(db_path: Path, path: str):
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
            "f.compression, p.path AS package_path "
            "FROM files f JOIN packages p ON f.package=p.name WHERE f.path=?",
            (path,),
        ).fetchone()
    finally:
        connection.close()


def short(value, limit: int = 100):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "..."
    if isinstance(value, list) and len(value) > 12:
        return f"[{len(value)} items]"
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--match", default="")
    parser.add_argument("--depth", type=int, default=0)
    args = parser.parse_args()

    row = get_row(args.db, args.path)
    if not row:
        print(f"not found: {args.path}", file=sys.stderr)
        return 1
    data = neox_object.parse(npk_browser.read_entry_bytes(row["package_path"], row))
    table = data.get("table") or []
    meta = data.get("meta") or []
    objects = data.get("objects") or []
    children = neox_object.build_tree(data)
    roots = [index for index in range(len(objects)) if not any(index in kids for kids in children)]

    def class_name(index):
        table_index = meta[index].get("x") if index < len(meta) else None
        if isinstance(table_index, int) and 0 <= table_index < len(table):
            return table[table_index]
        return "?"

    def walk(index, depth):
        obj = objects[index] if index < len(objects) else {}
        name = class_name(index)
        if args.match and args.match.lower() not in name.lower():
            for child in children[index]:
                walk(child, depth)
            return
        values = {
            key: short(value)
            for key, value in obj.items()
            if key not in ("BoundingInfo",)
        }
        print(f"{'  ' * depth}{index} {name} {values}")
        if args.depth and depth >= args.depth:
            return
        for child in children[index]:
            walk(child, depth + 1)

    for root in roots:
        walk(root, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
