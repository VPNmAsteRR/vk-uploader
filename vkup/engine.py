"""Очередь: воркеры, повторы, отмена, прогресс. Работает через браузер."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from .browser import BrowserBackend, BrowserError
from .config import Config
from .db import DB


class Engine:
    def __init__(self, db: DB, cfg: Config) -> None:
        self.db = db
        self.cfg = cfg
        self.backend = BrowserBackend(cfg, db.log)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock = threading.RLock()
        self._running = False
        self._active_job: int | None = None
        self._cancel_requested: set[int] = set()
        self._login_needed = False

    # --- состояние ---
    @property
    def running(self) -> bool:
        return self._running and any(t.is_alive() for t in self._threads)

    @property
    def active_job(self) -> int | None:
        return self._active_job

    @property
    def login_needed(self) -> bool:
        return self._login_needed

    # --- управление ---
    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._login_needed = False
            self._running = True
            n = max(1, min(int(self.cfg.get("workers")), 3))
            if n > 1:
                self.db.log("warn", "браузерный режим надёжнее с 1 воркером — "
                                    "использую 1")
                n = 1
            self._threads = [
                threading.Thread(target=self._worker, name=f"vkup-w{i}", daemon=True)
                for i in range(n)
            ]
            for t in self._threads:
                t.start()
            self.db.log("info", f"очередь запущена ({self.cfg.get('backend')}), воркеров: {n}")

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        self._wake.set()
        if wait:
            for t in self._threads:
                t.join(timeout=10)
        self._running = False
        self.db.log("info", "очередь остановлена")
        if not self.cfg.get("keep_browser_open"):
            self.backend.close()

    def kick(self) -> None:
        self._wake.set()

    def request_cancel(self, job_id: int) -> None:
        with self._lock:
            self._cancel_requested.add(job_id)

    def _is_canceled(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._cancel_requested

    def _clear_cancel(self, job_id: int) -> None:
        with self._lock:
            self._cancel_requested.discard(job_id)

    # --- воркер ---
    def _worker(self) -> None:
        while not self._stop.is_set():
            job = self.db.claim_next()
            if not job:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            self._process(job)

    def _process(self, job: dict[str, Any]) -> None:
        jid = int(job["id"])
        path = Path(job["path"])
        self._active_job = jid
        self._login_needed = False
        try:
            if self._is_canceled(jid):
                self.db.finish(jid, status="canceled", error="отменено пользователем")
                return
            if not path.exists():
                self.db.finish(jid, status="failed", error=f"файл не найден: {path}")
                self.db.log("error", f"файл не найден: {path}", jid)
                return
            size = path.stat().st_size
            if size == 0:
                self.db.finish(jid, status="failed", error="файл пустой")
                return

            self.db.update_job(jid, size_bytes=size, bytes_total=size, stage="подготовка")
            self.db.log("info", f"загрузка «{job['title'] or path.name}» "
                                f"({_human(size)}), попытка {job['attempts']}/{job['max_attempts']}",
                        jid)

            def on_progress(pct: float, stage: str) -> None:
                self.db.set_progress_pct(jid, pct, stage)

            res = self.backend.upload(job, on_progress, lambda: self._is_canceled(jid))
            self.db.finish(jid, status="done", result_url=str(res.get("url") or ""))
            self.db.log("success", "видео опубликовано", jid)
            if self.cfg.get("delete_after_upload"):
                try:
                    path.unlink()
                    self.db.log("info", "исходный файл удалён", jid)
                except OSError as e:
                    self.db.log("warn", f"не удалось удалить файл: {e}", jid)
        except BrowserError as e:
            if e.need_login:
                self._login_needed = True
                self._stop.set()
                self.db.log("error", f"нужен вход в VK: {e}", jid)
            self._handle_failure(job, str(e), retryable=e.retryable)
        except Exception as e:  # noqa: BLE001
            self._handle_failure(job, f"{type(e).__name__}: {e}", retryable=True)
        finally:
            self._active_job = None
            self._clear_cancel(jid)

    def _handle_failure(self, job: dict[str, Any], error: str, *, retryable: bool) -> None:
        jid = int(job["id"])
        attempts = int(job["attempts"])
        max_attempts = int(job["max_attempts"] or 3)

        if self._is_canceled(jid):
            self.db.finish(jid, status="canceled", error="отменено пользователем")
            self.db.log("warn", "отменено пользователем", jid)
            return
        if self._stop.is_set() and self._login_needed:
            self.db.finish(jid, status="failed", error=error)
            self.db.log("error", "остановлено: требуется вход в VK", jid)
            return
        if self._stop.is_set():
            self.db.reschedule(jid, 0, error, rollback_attempt=True)
            self.db.log("warn", "очередь остановлена — задание вернётся в очередь", jid)
            return

        if retryable and attempts < max_attempts:
            base = float(self.cfg.get("retry_base_delay"))
            cap = float(self.cfg.get("retry_max_delay"))
            delay = min(base * (2 ** (attempts - 1)), cap)
            self.db.reschedule(jid, delay, error)
            self.db.log("warn", f"ошибка (попытка {attempts}/{max_attempts}): {error}. "
                                f"Повтор через {delay:.0f} с", jid)
        else:
            self.db.finish(jid, status="failed", error=error)
            shot = self.backend.screenshot(f"fail-{jid}")
            if shot:
                self.db.log("info", f"скриншот ошибки: {shot}", jid)
            self.db.log("error", f"провалено: {error}", jid)

    # --- постановка в очередь ---
    def enqueue(self, payload: dict[str, Any]) -> tuple[int, str]:
        path = Path(str(payload["path"])).expanduser()
        if not path.exists() or not path.is_file():
            raise ValueError(f"файл не найден: {path}")
        if path.stat().st_size == 0:
            raise ValueError("файл пустой")

        sha = _sha256(path) if self.cfg.get("skip_duplicates") else None
        job_id, state = self.db.add_job(
            path=str(path.resolve()),
            sha256=sha,
            title=str(payload.get("title") or path.stem)[:128],
            description=str(payload.get("description") or "")[:5000],
            group_ref=str(payload.get("group_ref") or "") or None,
            wallpost=int(bool(payload.get("wallpost", self.cfg.get("wallpost")))),
            is_private=int(bool(payload.get("is_private", self.cfg.get("is_private")))),
            no_comments=int(bool(payload.get("no_comments", self.cfg.get("no_comments")))),
            max_attempts=int(self.cfg.get("max_attempts")),
            priority=int(payload.get("priority") or 0),
            skip_duplicates=bool(self.cfg.get("skip_duplicates")),
        )
        if state == "added":
            self.kick()
        return job_id, state


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _human(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024 or unit == "ТБ":
            return f"{int(n)} Б" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"
