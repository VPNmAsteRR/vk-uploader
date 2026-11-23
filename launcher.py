#!/usr/bin/env python3
"""Точка входа для сборки в exe (Windows).

Отличия от ``vkup.py``:

* запускает локальный сервер, сам подбирает свободный порт;
* сразу открывает интерфейс в браузере по умолчанию;
* по умолчанию работает в демо-режиме (безопасно);
* при двойном клике на exe ничего не нужно вводить в консоли;
* при ошибке не закрывает окно мгновенно, чтобы можно было прочитать текст.

Этот файл используется PyInstaller'ом; на Linux он тоже запускается.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser


def _free_port(preferred: int = 8765) -> int:
    """Любимый порт, если свободен, иначе случайный свободный."""
    for candidate in (preferred, 0):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    return 8765


def _pause_if_console() -> None:
    """Чтобы окно консоли не исчезало до того, как прочитают ошибку."""
    if sys.platform == "win32" and sys.stdout and sys.stdout.isatty():
        try:
            input("\nНажмите Enter, чтобы закрыть…")
        except (EOFError, KeyboardInterrupt):
            pass


def main() -> int:
    # на всякий случай: данные кладём рядом с exe, а не в системный temp
    if getattr(sys, "frozen", False) and not os.environ.get("VKUP_DATA_DIR"):
        os.environ["VKUP_DATA_DIR"] = str(
            __import__("pathlib").Path(sys.executable).parent / "data"
        )

    from vkup.server import App, build_server

    host = "127.0.0.1"
    port = _free_port()
    url = f"http://{host}:{port}/"

    app = App()
    # если пользователь ранее не выбирал режим — стартуем в демо, это безопасно
    httpd = build_server(app, host, port)

    print("=" * 62)
    print("  VK Video Uploader — локальная загрузка видео")
    print("=" * 62)
    print(f"  Интерфейс:  {url}")
    print(f"  Режим:      {app.cfg.get('backend')}")
    print(f"  Данные:     {app.cfg.path.parent}")
    print("=" * 62)
    print("  Браузер откроется автоматически. Это окно можно свернуть.")
    print("  Закрыть программу: закройте это окно или нажмите Ctrl+C.")
    print("=" * 62)

    def open_later() -> None:
        time.sleep(0.7)
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            print(f"Не удалось открыть браузер — откройте вручную: {url}")

    threading.Thread(target=open_later, daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка…")
    finally:
        try:
            app.engine.stop()
        except Exception:  # noqa: BLE001
            pass
        httpd.server_close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _pause_if_console()
        raise SystemExit(1)
