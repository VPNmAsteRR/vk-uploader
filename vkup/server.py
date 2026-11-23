"""Локальный HTTP-сервер: веб-интерфейс, JSON API управления, демо-страница."""

from __future__ import annotations

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import CONFIG, WEB_DIR, Config
from .db import DB
from .engine import Engine
from .selectors import DEFAULT_SELECTORS, load_selectors, save_selectors

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg"}
MAX_BODY = 8 * 1024 * 1024


class App:
    def __init__(self, cfg: Config | None = None, db: DB | None = None) -> None:
        self.cfg = cfg or CONFIG
        self.db = db or DB()
        self.engine = Engine(self.db, self.cfg)
        recovered = self.db.recover_orphans()
        if recovered:
            self.db.log("warn", f"возвращено в очередь после перезапуска: {recovered}")

    def state(self) -> dict[str, Any]:
        return {
            "config": self.cfg.public(),
            "stats": self.db.stats(),
            "jobs": self.db.list_jobs(300),
            "logs": self.db.logs(150),
            "engine": {
                "running": self.engine.running,
                "active_job": self.engine.active_job,
                "login_needed": self.engine.login_needed,
            },
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "vkup/2.0"
    app: App

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    # --- ответы ---
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _err(self, message: str, code: int = 400) -> None:
        self._json({"ok": False, "error": message}, code)

    def _body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError("слишком большой запрос")
        raw = self.rfile.read(n)
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("ожидался JSON-объект")
        return data

    # --- GET ---
    def do_GET(self) -> None:  # noqa: N802
        try:
            self._do_get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001 — сервер не должен падать
            try:
                self._err(f"{type(e).__name__}: {e}", 500)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _do_get(self) -> None:
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path

        if p in ("/", "/index.html"):
            return self._serve_file(WEB_DIR / "index.html")
        if p == "/mockvk/upload":
            return self._serve_file(WEB_DIR / "mock_upload.html")
        if p.startswith("/static/"):
            rel = p[len("/static/"):]
            if ".." in rel or rel.startswith("/"):
                return self._err("недопустимый путь", 400)
            return self._serve_file(WEB_DIR / rel)

        if p == "/api/state":
            return self._json(self.app.state())
        if p == "/api/logs":
            return self._json({"logs": self.app.db.logs(int(q.get("limit") or 200),
                                                        int(q.get("after_id") or 0))})
        if p == "/api/export":
            body = self.app.db.export_json().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="vkup-jobs.json"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if p == "/api/fs/list":
            return self._json(_fs_list(q.get("dir") or str(Path.home())))
        if p == "/api/probe":
            return self._json(_probe(q.get("path") or ""))
        if p == "/api/selectors":
            return self._json({"ok": True, "selectors": load_selectors(),
                               "defaults": DEFAULT_SELECTORS})
        if p == "/api/screenshot":
            shot = self.app.engine.backend.screenshot("manual")
            return self._json({"ok": bool(shot), "path": shot} if shot
                              else {"ok": False, "error": "браузер не запущен"})
        return self._err("не найдено", 404)

    # --- POST ---
    def do_POST(self) -> None:  # noqa: N802
        p = urlparse(self.path).path
        try:
            data = self._body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._err(f"некорректный запрос: {e}")

        try:
            if p == "/api/config":
                return self._handle_config(data)
            if p == "/api/enqueue":
                return self._handle_enqueue(data)
            if p == "/api/queue/start":
                self.app.engine.start()
                return self._json({"ok": True, "state": self.app.state()})
            if p == "/api/queue/stop":
                self.app.engine.stop()
                return self._json({"ok": True, "state": self.app.state()})
            if p == "/api/queue/clear_finished":
                n = self.app.db.clear_finished()
                return self._json({"ok": True, "removed": n, "state": self.app.state()})
            if p == "/api/logs/clear":
                self.app.db.clear_logs()
                return self._json({"ok": True})
            if p == "/api/browser/open":
                self.app.engine.backend.open_visible()
                return self._json({"ok": True, "state": self.app.state()})
            if p == "/api/browser/check_login":
                r = self.app.engine.backend.check_login()
                self.app.db.log("success" if r.get("ok") else "warn",
                                f"проверка входа: {r.get('message')}")
                return self._json({"ok": True, "login": r, "state": self.app.state()})
            if p == "/api/browser/quit":
                self.app.engine.backend.close()
                return self._json({"ok": True})
            if p == "/api/browser/calibrate":
                return self._json({"ok": True, "report": self.app.engine.backend.calibrate()})
            if p == "/api/browser/dump":
                return self._json({"ok": True, "dump": self.app.engine.backend.dump_page()})
            if p == "/api/selectors":
                sel = data.get("selectors")
                if not isinstance(sel, dict):
                    raise ValueError("ожидался объект selectors")
                save_selectors(sel)
                self.app.engine.backend.selectors = load_selectors()
                self.app.db.log("info", "селекторы обновлены")
                return self._json({"ok": True, "selectors": load_selectors()})
            if p.startswith("/api/jobs/"):
                return self._handle_job(p, data)
            return self._err("не найдено", 404)
        except ValueError as e:
            return self._err(str(e))
        except Exception as e:  # noqa: BLE001
            return self._err(f"{type(e).__name__}: {e}", 500)

    # --- обработчики ---
    def _handle_config(self, data: dict[str, Any]) -> None:
        backend_changed = "backend" in data and data["backend"] != self.app.cfg.get("backend")
        if data.get("profile_dir"):
            data["profile_dir"] = str(Path(data["profile_dir"]).expanduser())
        if data:
            self.app.cfg.update(data)
        if backend_changed:
            self.app.engine.backend.selectors = load_selectors()
            self.app.db.log("info", f"режим: {self.app.cfg.get('backend')}")
        self.app.db.log("info", "настройки сохранены")
        return self._json({"ok": True, "state": self.app.state()})

    def _handle_enqueue(self, data: dict[str, Any]) -> None:
        paths = data.get("paths") or ([data["path"]] if data.get("path") else [])
        if not isinstance(paths, list) or not paths:
            raise ValueError("не указан ни один файл")
        results = []
        for raw in paths:
            payload = dict(data)
            payload.pop("paths", None)
            payload["path"] = raw
            if len(paths) > 1 and not data.get("keep_title"):
                payload["title"] = Path(str(raw)).stem[:128]
            try:
                jid, state = self.app.engine.enqueue(payload)
                results.append({"path": str(raw), "id": jid, "state": state, "ok": True})
            except ValueError as e:
                results.append({"path": str(raw), "ok": False, "error": str(e)})
        added = sum(1 for r in results if r.get("ok") and r.get("state") == "added")
        dups = sum(1 for r in results if r.get("state") == "duplicate")
        self.app.db.log("info", f"добавлено: {added}, дубликатов пропущено: {dups}")
        return self._json({"ok": True, "results": results, "state": self.app.state()})

    def _handle_job(self, path: str, data: dict[str, Any]) -> None:
        parts = path.strip("/").split("/")
        if len(parts) != 4:
            return self._err("некорректный путь", 404)
        try:
            jid = int(parts[2])
        except ValueError:
            return self._err("некорректный id", 400)
        action = parts[3]

        if action == "retry":
            ok = self.app.db.retry_now(jid)
            if ok:
                self.app.db.log("info", "задание возвращено в очередь", jid)
                self.app.engine.kick()
            return self._json({"ok": ok, "state": self.app.state()})
        if action == "cancel":
            self.app.engine.request_cancel(jid)
            ok = self.app.db.cancel(jid)
            return self._json({"ok": ok, "state": self.app.state()})
        if action == "delete":
            ok = self.app.db.delete(jid)
            return self._json({"ok": ok, "state": self.app.state()})
        if action == "update":
            if not self.app.db.get_job(jid):
                return self._err("задание не найдено", 404)
            allowed = {"title", "description", "wallpost", "is_private", "no_comments", "priority"}
            fields = {k: v for k, v in data.items() if k in allowed}
            for k in ("wallpost", "is_private", "no_comments"):
                if k in fields:
                    fields[k] = int(bool(fields[k]))
            self.app.db.update_job(jid, **fields)
            return self._json({"ok": True, "state": self.app.state()})
        return self._err("неизвестное действие", 404)

    # --- статика ---
    def _serve_file(self, path: Path) -> None:
        try:
            if not path.resolve().is_relative_to(WEB_DIR.resolve()):
                return self._err("недопустимый путь", 403)
            body = path.read_bytes()
        except (OSError, ValueError):
            return self._err("файл не найден", 404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, body, ctype)


# --- вспомогательные ---
def _fs_list(dir_raw: str) -> dict[str, Any]:
    base = Path(dir_raw).expanduser()
    if not base.is_absolute():
        base = Path.cwd() / base
    try:
        base = base.resolve()
        entries = []
        with os.scandir(base) as it:
            for e in it:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    size = 0 if is_dir else e.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
                if e.name.startswith("."):
                    continue
                if not is_dir and Path(e.name).suffix.lower() not in VIDEO_EXT:
                    continue
                entries.append({"name": e.name, "path": str(Path(e.path)),
                                "dir": is_dir, "size": size})
        entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        parent = str(base.parent) if base.parent != base else None
        return {"ok": True, "path": str(base), "parent": parent, "entries": entries}
    except OSError as e:
        return {"ok": False, "error": str(e), "path": str(base)}


def _probe(path_raw: str) -> dict[str, Any]:
    if not path_raw:
        return {"ok": False, "error": "не указан путь"}
    p = Path(path_raw).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"файл не найден: {p}"}
    size = p.stat().st_size
    if p.suffix.lower() not in VIDEO_EXT:
        return {"ok": False, "error": f"не похоже на видео: {p.suffix}"}
    info = _media_info(p)
    info.update({"ok": True, "path": str(p.resolve()), "name": p.name,
                 "size": size, "ext": p.suffix.lower()})
    return info


def _media_info(path: Path) -> dict[str, Any]:
    import subprocess

    out: dict[str, Any] = {"duration": None, "width": None, "height": None}
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if p.returncode == 0:
            data = json.loads(p.stdout or "{}")
            dur = (data.get("format") or {}).get("duration")
            if dur:
                out["duration"] = round(float(dur), 2)
            for st in data.get("streams") or []:
                if st.get("width") and st.get("height"):
                    out["width"] = int(st["width"])
                    out["height"] = int(st["height"])
                    break
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return out


def build_server(app: App, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    # демо-страница живёт на том же сервере — адрес знаем только сейчас
    app.cfg.update({"mock_url": f"http://{host}:{httpd.server_port}/mockvk/upload"})
    return httpd


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="vkup — загрузка видео в VK через браузер")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--backend", choices=["demo", "browser"], default=None,
                    help="demo — локальная страница (по умолчанию), browser — реальный VK")
    ap.add_argument("--headless", action="store_true", help="без окна браузера")
    ap.add_argument("--autostart", action="store_true", help="сразу запустить очередь")
    args = ap.parse_args(argv)

    app = App()
    if args.backend:
        app.cfg.update({"backend": args.backend})
    if args.headless:
        app.cfg.update({"headless": True})
    if args.autostart:
        app.engine.start()

    httpd = build_server(app, args.host, args.port)
    print(f"vkup запущен:  http://{args.host}:{args.port}/")
    print(f"режим: {app.cfg.get('backend')}   данные: {app.cfg.path.parent}")
    print("Ctrl+C — остановка")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостановка…")
    finally:
        app.engine.stop()
        httpd.server_close()
    return 0
