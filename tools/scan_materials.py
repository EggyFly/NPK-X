"""Scan every .mtg in the local catalog and summarise material features."""

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

import neox_object
import npk_browser


DEFAULT_DB = Path.home() / "AppData" / "Local" / "EggyNpkBrowser" / "catalog.sqlite"
TEXTURE_SUFFIX = (".tga", ".dds", ".kta", ".png")


def load_paths(db_path: Path) -> set[str]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {row[0] for row in connection.execute("SELECT path FROM files")}
    finally:
        connection.close()


def make_resolver(paths: set[str]):
    def resolve(base_path: str, ref: str) -> str | None:
        if not isinstance(ref, str) or not ref:
            return None
        target = [part for part in ref.replace("\\", "/").split("/") if part not in ("", ".")]
        if not target:
            return None
        joined = "/".join(target)
        if joined in paths:
            return joined
        stack = [part for part in base_path.split("/") if part]
        if stack:
            stack.pop()
        for part in target:
            if part == "..":
                if stack:
                    stack.pop()
            else:
                stack.append(part)
        joined = "/".join(stack)
        if joined in paths:
            return joined
        stem = target[-1].rsplit(".", 1)[0]
        prefix = target[:-1]
        for suffix in (".kta", ".dds", ".png", ".tga"):
            candidate = "/".join(prefix + [stem + suffix])
            if candidate in paths:
                return candidate
        return None

    return resolve


def scan_one(row, resolve) -> dict:
    item = {"path": row["path"], "ok": False}
    try:
        payload = npk_browser.read_entry_bytes(row["package_path"], row)
        data = neox_object.parse(payload)
        materials = npk_browser.material_tree(data, lambda ref: resolve(row["path"], ref))
        item.update({
            "ok": True,
            "materials": len(materials),
            "color_mode": [m["color_mode"] for m in materials],
            "surface": [m["surface"] for m in materials],
            "transparent": sum(1 for m in materials if m["transparent"]),
            "no_cull": sum(1 for m in materials if not m["cull_back"]),
            "unlit": sum(1 for m in materials if not m["lighting"]),
            "pattern_tex": [m["pattern_tex"] for m in materials if m.get("pattern_tex")],
            "base_normal": [m["base_normal"] for m in materials if m.get("base_normal")],
            "design_color": [m["design_color"] for m in materials if m.get("design_color") != [1.0, 1.0, 1.0, 1.0]],
            "texture_counts": [len(m["textures"]) for m in materials],
            "colors_sets": len({tuple(tuple(c) for c in m["colors"]) for m in materials}),
        })
    except Exception as error:
        item["error"] = f"{type(error).__name__}: {error}"
    return item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=Path("material_scan.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()

    paths = load_paths(args.db)
    resolve = make_resolver(paths)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT f.path, f.package, f.offset, f.packed, f.unpacked, "
            "f.compression, p.path AS package_path "
            "FROM files f JOIN packages p ON f.package=p.name "
            "WHERE lower(f.ext)='.mtg' ORDER BY f.path"
        )
        if args.limit:
            sql += f" LIMIT {int(args.limit)}"
        rows = connection.execute(sql).fetchall()
    finally:
        connection.close()
    print(f"mtg: {len(rows)}", flush=True)

    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        for index, item in enumerate(pool.map(lambda row: scan_one(row, resolve), rows), 1):
            results.append(item)
            if index % 250 == 0:
                print(f"{index}/{len(rows)} {time.perf_counter() - started:.1f}s", flush=True)

    ok = [item for item in results if item["ok"]]
    materials = [m for item in ok for m in range(item["materials"])]
    color_modes = Counter()
    surfaces = Counter()
    for item in ok:
        for mode, surface in zip(item["color_mode"], item["surface"]):
            color_modes[mode] += 1
            surfaces[surface] += 1
    summary = {
        "total": len(results),
        "ok": len(ok),
        "failed": len(results) - len(ok),
        "materials": len(materials),
        "seconds": round(time.perf_counter() - started, 1),
        "color_modes": color_modes.most_common(),
        "surfaces": surfaces.most_common(25),
        "transparent": sum(item["transparent"] for item in ok),
        "no_cull": sum(item["no_cull"] for item in ok),
        "unlit": sum(item["unlit"] for item in ok),
        "pattern_tex": sum(len(item["pattern_tex"]) for item in ok),
        "base_normal": sum(len(item["base_normal"]) for item in ok),
        "design_color": sum(len(item["design_color"]) for item in ok),
        "texture_counts": Counter(
            count for item in ok for count in item["texture_counts"]
        ).most_common(15),
        "errors": Counter(
            item["error"].split(":")[-1].strip() for item in results if not item["ok"]
        ).most_common(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    args.out.write_text(json.dumps({"summary": summary, "items": results}, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
