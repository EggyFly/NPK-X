#!/usr/bin/env bash
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
PY="$PKG/bin/python/python"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "${PY:-}" ]; then
  echo "[ERROR] python3 not found"
  exit 1
fi
GAME_ROOT="${1:-}"
if [ -z "$GAME_ROOT" ]; then
  read -r -p "Game folder containing char.npk: " GAME_ROOT
fi
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/npk-x"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec "$PY" "$PKG/tools/npk_browser.py" --game-root "$GAME_ROOT" --cache-dir "$CACHE_DIR" "${@:2}"
