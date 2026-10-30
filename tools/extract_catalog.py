"""Write catalog entries to disk (or stdout) for local inspection."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import npk_browser


DEFAULT_DB = npk_browser.default_cache_dir() / "catalog.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--text", action="store_true")
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
        print(f"not found: {args.path}", file=sys.stderr)
        return 1
    payload = npk_browser.read_entry_bytes(row["package_path"], row)
    if args.text:
        sys.stdout.write(payload.decode("utf-8", errors="replace"))
        return 0
    target = args.out or Path(args.path).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    print(f"{target} ({len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
