#!/usr/bin/env bash
set -euo pipefail
PKG="$(cd "$(dirname "$0")" && pwd)"
VENV="$PKG/.venv"
PY="$PKG/bin/python/python"
if [ ! -x "$PY" ]; then
  SYS_PY="$(command -v python3 || true)"
  if [ -z "$SYS_PY" ]; then
    echo "[ERROR] python3 not found"
    exit 1
  fi
  if [ ! -f "$VENV/.deps_ok" ]; then
    if [ ! -x "$VENV/bin/python" ]; then
      echo "[SETUP] creating venv at $VENV"
      "$SYS_PY" -m venv "$VENV" || {
        echo "[ERROR] venv creation failed; Debian/Ubuntu needs: sudo apt install python3-venv"
        exit 1
      }
    fi
    echo "[SETUP] installing dependencies from requirements.txt"
    "$VENV/bin/python" -m pip install -q --upgrade pip || true
    "$VENV/bin/python" -m pip install -r "$PKG/requirements.txt" || {
      echo "[ERROR] dependency install failed; delete $VENV and retry"
      exit 1
    }
    touch "$VENV/.deps_ok"
  fi
  PY="$VENV/bin/python"
fi
GAME_ROOT="${1:-}"
if [ -z "$GAME_ROOT" ]; then
  read -r -p "Game folder containing char.npk: " GAME_ROOT
fi
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/npk-x"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
exec "$PY" "$PKG/tools/npk_browser.py" --game-root "$GAME_ROOT" --cache-dir "$CACHE_DIR" "${@:2}"
