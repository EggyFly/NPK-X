"""Scan GIM prefabs for SubMesh fields that affect preview geometry."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import neox_object
import npk_browser


DEFAULT_DB = npk_browser.default_cache_dir() / "catalog.sqlite"


def scan_one(row) -> dict:
    item = {"path": row["path"], "ok": False}
    try:
        data = neox_object.parse(npk_browser.read_entry_bytes(row["package_path"], row))
        subs = [obj for obj in data.get("objects", []) if isinstance(obj, dict) and "MtlIdx" in obj and "BoundingHalf" in obj]
        offsets = [sub.get("RenderOffset") for sub in subs if "RenderOffset" in sub]
        item.update({
            "ok": True,
            "submeshes": len(subs),
            "nonzero_offset": sum(1 for value in offsets if isinstance(value, (int, float)) and abs(value) > 1e-6),
            "offset_values": sorted({round(float(value), 4) for value in offsets if isinstance(value, (int, float))})[:20],
            "skin4s": sum(1 for sub in subs if str(sub.get("IsSkin4S", "")).lower() == "true"),
            "names": [sub.get("Name") for sub in subs][:20],
        })
    except Exception as error:
        item["error"] = f"{type(error).__name__}: {error}"
    return item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=Path("gim_scan.json"))
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
            "f.compression, p.path AS package_path "
            "FROM files f JOIN packages p ON f.package=p.name "
            "WHERE lower(f.ext)='.gim' ORDER BY f.path"
        ).fetchall()
    finally:
        connection.close()
    print(f"gims: {len(rows)}", flush=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for index, item in enumerate(pool.map(scan_one, rows), 1):
            results.append(item)
            if index % 10000 == 0:
                print(f"{index}/{len(rows)}", flush=True)
    ok = [item for item in results if item["ok"]]
    summary = {
        "total": len(results),
        "ok": len(ok),
        "failed": len(results) - len(ok),
        "submeshes": Counter(item["submeshes"] for item in ok).most_common(15),
        "nonzero_offset_gims": sum(1 for item in ok if item["nonzero_offset"]),
        "nonzero_offset_subs": sum(item["nonzero_offset"] for item in ok),
        "skin4s_gims": sum(1 for item in ok if item["skin4s"]),
        "offset_values": Counter(value for item in ok for value in item["offset_values"]).most_common(20),
        "errors": Counter(item["error"].split(":")[-1] for item in results if not item["ok"]).most_common(10),
        "samples": [item["path"] for item in ok if item["nonzero_offset"]][:20],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    args.out.write_text(json.dumps({"summary": summary, "items": results}, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
