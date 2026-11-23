"""SQLite-хранилище очереди и журнала. Соединение на поток."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT NOT NULL,
    sha256          TEXT,
    title           TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    group_ref       TEXT,
    wallpost        INTEGER NOT NULL DEFAULT 0,
    is_private      INTEGER NOT NULL DEFAULT 0,
    no_comments     INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    stage           TEXT,
    progress        REAL NOT NULL DEFAULT 0,
    bytes_total     INTEGER NOT NULL DEFAULT 0,
    bytes_sent      INTEGER NOT NULL DEFAULT 0,
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    error           TEXT,
    result_url      TEXT,
    duration        REAL,
    width           INTEGER,
    height          INTEGER,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    started_at      REAL,
    finished_at     REAL,
    priority        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_jobs_sha ON jobs(sha256);

CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    job_id  INTEGER,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
"""

STATUSES = {"pending", "uploading", "done", "failed", "canceled"}


class DB:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DATA_DIR / "vkup.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
            # если файл БД подменили/удалили — схема создастся заново
            try:
                c.execute("SELECT 1 FROM jobs LIMIT 1")
            except sqlite3.OperationalError:
                c.executescript(SCHEMA)
        return c

    # --- журнал ---
    def log(self, level: str, message: str, job_id: int | None = None) -> None:
        c = self.conn()
        c.execute("INSERT INTO logs(ts, level, job_id, message) VALUES (?,?,?,?)",
                  (time.time(), level, job_id, message))
        c.execute("DELETE FROM logs WHERE id <= (SELECT MAX(id) - 5000 FROM logs)")

    def logs(self, limit: int = 200, after_id: int = 0) -> list[dict[str, Any]]:
        rows = self.conn().execute(
            "SELECT * FROM logs WHERE id > ? ORDER BY id DESC LIMIT ?",
            (after_id, min(max(int(limit), 1), 1000)),
        ).fetchall()
        return [dict(r) for r in rows]

    def clear_logs(self) -> None:
        self.conn().execute("DELETE FROM logs")

    # --- задания ---
    def add_job(self, **kw: Any) -> tuple[int, str]:
        sha = kw.get("sha256")
        c = self.conn()
        if sha and kw.get("skip_duplicates", True):
            row = c.execute(
                "SELECT id FROM jobs WHERE sha256=? AND status IN "
                "('pending','uploading','done') ORDER BY id LIMIT 1", (sha,),
            ).fetchone()
            if row:
                return int(row["id"]), "duplicate"

        fields = [
            "path", "sha256", "title", "description", "group_ref", "wallpost",
            "is_private", "no_comments", "bytes_total", "size_bytes", "duration",
            "width", "height", "max_attempts", "priority",
        ]
        values = {f: kw[f] for f in fields if kw.get(f) is not None}
        values["status"] = "pending"
        values["created_at"] = time.time()
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cur = c.execute(f"INSERT INTO jobs({cols}) VALUES({marks})", tuple(values.values()))
        job_id = int(cur.lastrowid)
        self.log("info", f"в очередь: {Path(kw['path']).name}", job_id)
        return job_id, "added"

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        r = self.conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def list_jobs(self, limit: int = 300, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM jobs"
        args: list[Any] = []
        if statuses:
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args = list(statuses)
        # активные сверху, затем очередь по порядку, затем завершённые (новые выше)
        sql += (" ORDER BY CASE status WHEN 'uploading' THEN 0 WHEN 'pending' THEN 1"
                " ELSE 2 END, CASE WHEN status='pending' THEN id END ASC,"
                " CASE WHEN status<>'pending' THEN id END DESC LIMIT ?")
        args.append(min(max(int(limit), 1), 2000))
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    def stats(self) -> dict[str, int]:
        rows = self.conn().execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        out = {s: 0 for s in STATUSES}
        for r in rows:
            out[r["status"]] = int(r["n"])
        out["total"] = sum(out.values())
        return out

    def claim_next(self) -> dict[str, Any] | None:
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute(
                "SELECT * FROM jobs WHERE status='pending' AND next_attempt_at<=? "
                "ORDER BY priority DESC, id ASC LIMIT 1", (time.time(),),
            ).fetchone()
            if not row:
                c.execute("COMMIT")
                return None
            c.execute(
                "UPDATE jobs SET status='uploading', started_at=COALESCE(started_at,?), "
                "attempts=attempts+1, progress=0, error=NULL WHERE id=?",
                (time.time(), row["id"]),
            )
            c.execute("COMMIT")
            job = dict(row)
            job["attempts"] = int(job["attempts"]) + 1
            job["status"] = "uploading"
            return job
        except Exception:
            c.execute("ROLLBACK")
            raise

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn().execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def set_progress(self, job_id: int, sent: int, total: int) -> None:
        pct = (sent / total * 100.0) if total else 0.0
        self.conn().execute(
            "UPDATE jobs SET bytes_sent=?, bytes_total=?, progress=? WHERE id=?",
            (int(sent), int(total), round(pct, 2), job_id),
        )

    def set_progress_pct(self, job_id: int, pct: float, stage: str | None = None) -> None:
        job = self.get_job(job_id)
        total = int(job["bytes_total"]) if job else 0
        pct = max(0.0, min(100.0, float(pct)))
        self.conn().execute(
            "UPDATE jobs SET progress=?, bytes_sent=?, stage=COALESCE(?, stage) WHERE id=?",
            (round(pct, 2), int(total * pct / 100), stage, job_id),
        )

    def finish(self, job_id: int, *, status: str, error: str | None = None,
               result_url: str | None = None, stage: str | None = None) -> None:
        self.conn().execute(
            "UPDATE jobs SET status=?, error=?, result_url=COALESCE(?, result_url), "
            "stage=COALESCE(?, stage), finished_at=?, "
            "progress=CASE WHEN ?='done' THEN 100 ELSE progress END WHERE id=?",
            (status, error, result_url, stage, time.time(), status, job_id),
        )

    def reschedule(self, job_id: int, delay: float, error: str,
                   rollback_attempt: bool = False) -> None:
        self.conn().execute(
            "UPDATE jobs SET status='pending', next_attempt_at=?, error=?, progress=0, "
            "bytes_sent=0, stage=NULL, "
            "attempts=CASE WHEN ? THEN MAX(attempts-1, 0) ELSE attempts END WHERE id=?",
            (time.time() + delay, error, 1 if rollback_attempt else 0, job_id),
        )

    def retry_now(self, job_id: int) -> bool:
        cur = self.conn().execute(
            "UPDATE jobs SET status='pending', next_attempt_at=0, error=NULL, progress=0, "
            "stage=NULL, attempts=0 WHERE id=? AND status IN ('failed','canceled','pending')",
            (job_id,),
        )
        return cur.rowcount > 0

    def cancel(self, job_id: int) -> bool:
        cur = self.conn().execute(
            "UPDATE jobs SET status='canceled', finished_at=? WHERE id=? "
            "AND status IN ('pending','failed')", (time.time(), job_id),
        )
        return cur.rowcount > 0

    def delete(self, job_id: int) -> bool:
        cur = self.conn().execute(
            "DELETE FROM jobs WHERE id=? AND status NOT IN ('uploading')", (job_id,),
        )
        return cur.rowcount > 0

    def clear_finished(self) -> int:
        cur = self.conn().execute("DELETE FROM jobs WHERE status IN ('done','canceled')")
        return cur.rowcount

    def recover_orphans(self) -> int:
        cur = self.conn().execute(
            "UPDATE jobs SET status='pending', progress=0, bytes_sent=0, stage=NULL "
            "WHERE status='uploading'")
        return cur.rowcount

    def export_json(self) -> str:
        return json.dumps(self.list_jobs(2000), ensure_ascii=False, indent=2)
