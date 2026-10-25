"""Scan every .mesh in the local catalog and report parser coverage."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mesh_parse
import npk_browser


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"


def mesh_multiple(data: bytes, parsed: dict) -> int | None:
    if parsed.get("flag", 0) != 0:
        return None
    try:
        data_end = int.from_bytes(data[12:16], "little")
        if data_end + 6 <= len(data):
            return int.from_bytes(data[data_end:data_end + 2], "little")
    except Exception:
        pass
    return None


def scan_one(row) -> dict:
    path = row["path"]
    started = time.perf_counter()
    item = {"path": path, "package": row["package_path"], "size": row["unpacked"]}
    try:
        payload = npk_browser.read_entry_bytes(row["package_path"], row)
        parsed = mesh_parse.parse(payload)
        item.update({
            "ok": True,
            "version": parsed["version"],
            "flag": parsed["flag"],
            "variant": parsed["variant"],
            "vcount": parsed["vertex_count"],
            "fcount": parsed["face_count"],
            "submeshes": len(parsed["submeshes"]),
            "uv": parsed["uv_layers"],
            "color": parsed["color_len"],
            "multiple": mesh_multiple(payload, parsed),
            "bounds": parsed["bounds"],
        })
        del parsed
    except Exception as error:
        item.update({"ok": False, "error": f"{type(error).__name__}: {error}"})
    item["ms"] = round((time.perf_counter() - started) * 1000, 1)
    return item


def open_rows(db_path: Path, limit: int | None, wanted: set[str] | None = None) -> list:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
            "f.compression, p.path AS package_path "
            "FROM files f JOIN packages p ON f.package=p.name "
            "WHERE lower(f.ext)='.mesh' ORDER BY f.path"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = connection.execute(sql).fetchall()
        if wanted is not None:
            rows = [row for row in rows if row["path"] in wanted]
        return rows
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=Path("mesh_scan.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--only-fail", action="store_true")
    parser.add_argument("--retry", type=Path, default=None)
    args = parser.parse_args()

    if not args.db.is_file():
        print(f"catalog not found: {args.db}", file=sys.stderr)
        return 1
    wanted = None
    if args.retry:
        previous = json.loads(args.retry.read_text(encoding="utf-8"))
        wanted = {item["path"] for item in previous.get("items", []) if not item.get("ok")}
        print(f"retry: {len(wanted)}", flush=True)
    rows = open_rows(args.db, args.limit or None, wanted)
    print(f"meshes: {len(rows)}", flush=True)

    results = []
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for index, item in enumerate(pool.map(scan_one, rows), 1):
            results.append(item)
            if index % 250 == 0:
                print(f"{index}/{len(rows)} {time.perf_counter() - started:.1f}s", flush=True)

    ok = [item for item in results if item["ok"]]
    failed = [item for item in results if not item["ok"]]
    errors = Counter(item["error"].split(":")[-1].strip() for item in failed)
    variants = Counter(item["variant"] for item in ok)
    versions = Counter(str(item["version"]) for item in ok)
    submeshes = Counter(item["submeshes"] for item in ok)
    multiples = Counter(str(item.get("multiple")) for item in ok)
    summary = {
        "total": len(results),
        "ok": len(ok),
        "failed": len(failed),
        "seconds": round(time.perf_counter() - started, 1),
        "errors": errors.most_common(),
        "variants": variants.most_common(),
        "versions": versions.most_common(),
        "submeshes": submeshes.most_common(20),
        "multiples": multiples.most_common(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "items": failed if args.only_fail else results}
    args.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
