#!/usr/bin/env bash
# Запуск vkup. Аргументы передаются как есть:
#   ./run.sh                 — демо-режим (безопасно)
#   ./run.sh --backend browser
#   ./run.sh --backend browser --port 9000
set -euo pipefail
cd "$(dirname "$0")"

PY="../venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

if ! "$PY" -c "import selenium" >/dev/null 2>&1; then
  echo "Ставлю selenium…"
  "$PY" -m pip install --quiet -r requirements.txt
fi

if ! command -v chromedriver >/dev/null 2>&1 && [ ! -x /usr/bin/chromedriver ]; then
  echo "ВНИМАНИЕ: chromedriver не найден в PATH." >&2
  echo "Arch: sudo pacman -S chromium chromedriver" >&2
  echo "Debian/Ubuntu: sudo apt install chromium chromium-driver" >&2
fi

exec "$PY" vkup.py "$@"
