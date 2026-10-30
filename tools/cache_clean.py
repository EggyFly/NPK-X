"""Clear NPK-X browser caches without touching the game or repository data."""

import argparse
import os
import shutil
from pathlib import Path

CACHE_FILES = (
    "catalog.sqlite",
    "catalog.tmp.sqlite",
    "pathdict.generated.txt.gz",
    "xn_names.json",
    "xn_opmap.json",
    "mode3_profile.json",
    "update_check.json",
)
CACHE_DIRS = ("artifacts", "open", "tmp")


def default_cache_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "EggyNpkBrowser"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "npk-x"


def clear_cache(cache_root: Path, game_root: Path | None = None, log=print) -> list[str]:
    root = Path(cache_root).expanduser().resolve()
    if root == root.parent:
        raise ValueError("refusing to clear a drive root")
    if root == Path(__file__).resolve().parent.parent:
        raise ValueError("refusing to clear the project directory")
    if game_root is not None and root == Path(game_root).expanduser().resolve():
        raise ValueError("refusing to clear the game directory")
    removed = []
    if not root.is_dir():
        return removed
    for name in CACHE_FILES:
        path = root / name
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    for path in root.glob("catalog.sqlite-*"):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    for name in CACHE_DIRS:
        path = root / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(str(path))
    for item in removed:
        log("removed %s" % item)
    log("cache cleared: %s (%d items)" % (root, len(removed)))
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear NPK-X browser cache")
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--game-root", type=Path, default=None)
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    cache_root = args.cache_dir or default_cache_dir()
    if not args.yes:
        answer = input("clear cache %s? [y/N] " % cache_root).strip().lower()
        if answer not in ("y", "yes"):
            print("cancelled")
            return 1
    try:
        clear_cache(cache_root, args.game_root)
    except (OSError, ValueError) as exc:
        print("ERROR: %s" % exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
