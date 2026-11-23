#!/usr/bin/env python3
"""Сборка Windows-exe. На Linux — через Wine с установленным Windows-Python.

Обычно запускается так (из корня репозитория):

    python3 build_exe.py

Скрипт сам:
  1. находит Windows-Python (по умолчанию C:\\Py311 под Wine);
  2. ставит PyInstaller/selenium, если их нет;
  3. собирает один exe в dist/;
  4. копирует рядом README и пример списка файлов.

На Windows можно просто: pyinstaller vkup.spec
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def run(cmd: list[str], env: dict | None = None) -> int:
    print("+", " ".join(str(c) for c in cmd[:3]), "…")
    return subprocess.run(cmd, cwd=str(ROOT), env=env).returncode


def windows_python() -> tuple[list[str], dict] | None:
    """Как вызвать Windows-Python: напрямую (Windows) или через Wine (Linux)."""
    if sys.platform == "win32":
        return ([sys.executable], dict(os.environ))

    wine = shutil.which("wine")
    if not wine:
        return None

    candidates = [r"C:\Py311\python.exe", r"C:\Python311\python.exe",
                  r"C:\Python312\python.exe", r"C:\Python313\python.exe"]
    env = dict(os.environ)
    env["WINEDEBUG"] = "-all"
    for exe in candidates:
        r = subprocess.run([wine, exe, "--version"], capture_output=True, text=True,
                           env=env)
        if r.returncode == 0:
            print(f"Windows-Python найден: {exe} ({r.stdout.strip() or r.stderr.strip()})")
            return ([wine, exe], env)
    return None


def main() -> int:
    print("=" * 62)
    print("  Сборка Windows-exe: VK Video Uploader")
    print("=" * 62)

    py = windows_python()
    if py is None:
        print("Не найден Windows-Python.")
        print("Установите Python для Windows (например, в C:\\Py311) или")
        print("соберите на Windows командой:  pyinstaller vkup.spec")
        return 2
    pycmd, env = py

    # зависимости
    r = subprocess.run(pycmd + ["-c", "import PyInstaller, selenium"],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print("Ставлю PyInstaller и selenium…")
        if run(pycmd + ["-m", "pip", "install", "--disable-pip-version-check",
                        "pyinstaller", "selenium"], env) != 0:
            return 3

    # чистая сборка
    for d in (DIST, BUILD):
        if d.exists():
            shutil.rmtree(d)

    r = run(pycmd + ["-m", "PyInstaller", "--noconfirm", "--clean",
                     "--distpath", str(DIST), "--workpath", str(BUILD),
                     str(ROOT / "vkup.spec")], env)
    if r != 0:
        return r

    exe = next(DIST.glob("VK-Video-Uploader*"), None)
    if exe is None:
        print("exe не найден в dist/")
        return 4

    # сопроводительные файлы
    for name in ("README.md",):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, DIST / name)
    (DIST / "СНАЧАЛА-ПРОЧТИ.txt").write_text(
        "VK Video Uploader\n"
        "=================\n\n"
        "1. Запустите VK-Video-Uploader.exe двойным кликом.\n"
        "2. Откроется окно консоли и браузер с интерфейсом.\n"
        "   Если браузер не открылся — скопируйте адрес из консоли\n"
        "   (вида http://127.0.0.1:8765/) в адресную строку браузера.\n"
        "3. Для проверки без VK оставьте режим «Демо» и нажмите «Старт».\n"
        "4. Для настоящего VK: Настройки → Режим работы → «Боевой»,\n"
        "   затем «Открыть браузер» и войдите в VK вручную (один раз).\n\n"
        "Нужен установленный браузер: Chrome или Edge (Windows 10/11).\n"
        "Python и chromedriver не требуются — всё внутри exe.\n\n"
        "Окно консоли закрывать нельзя, пока работаете: это и есть сервер.\n"
        "Все данные (настройки, очередь, профиль браузера) — в папке data/\n"
        "рядом с exe.\n\n"
        "Проверка подлинности: файл собран локально из исходников в этом\n"
        "репозитории, см. build_exe.py и vkup.spec.\n",
        encoding="utf-8",
    )

    size = exe.stat().st_size / (1024 * 1024)
    print()
    print(f"готово: {exe}  ({size:.1f} МБ)")
    print(f"рядом: {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
