#!/usr/bin/env python3
"""Автотесты vkup: очередь + реальный браузер на локальной демо-странице.

Запуск:
    ../venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vkup.config import Config, DEFAULTS  # noqa: E402
from vkup.db import DB  # noqa: E402
from vkup.engine import Engine  # noqa: E402
from vkup.server import App, build_server  # noqa: E402

BROWSER = "/usr/bin/chromium"
DRIVER = "/usr/bin/chromedriver"


def make_cfg(tmp: Path, **over) -> Config:
    cfg = Config(tmp / "config.json")
    cfg._data.update(DEFAULTS)
    cfg._data.update({
        "backend": "demo",
        "browser_binary": BROWSER,
        "chromedriver_path": DRIVER,
        "headless": True,
        "profile_dir": str(tmp / "profile"),
        "page_load_timeout": 30,
        "retry_base_delay": 0.2,
        "retry_max_delay": 1.0,
        "verify_after_upload": False,
        **over,
    })
    return cfg


def make_video(tmp: Path, name: str = "clip.mp4", size: int = 40_000) -> Path:
    p = tmp / name
    p.write_bytes(os.urandom(size))
    return p


class EngineTest(unittest.TestCase):
    """Очередь на демо-странице: реально запускает Chromium."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="vkup-engine-"))
        cls.cfg = make_cfg(cls.tmp)
        cls.app = App(cfg=cls.cfg, db=DB(cls.tmp / "db.sqlite"))
        cls.httpd = build_server(cls.app, "127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.engine.stop()
        cls.app.engine.backend.close()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def wait_status(self, job_id: int, statuses: tuple[str, ...], timeout: float = 90.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            job = self.app.db.get_job(job_id)
            if job and job["status"] in statuses:
                return job
            time.sleep(0.2)
        self.fail(f"задание {job_id} не достигло {statuses}, сейчас "
                  f"{self.app.db.get_job(job_id)['status']}")

    def test_01_full_cycle(self) -> None:
        f = make_video(self.tmp, "cycle.mp4", 300_000)
        job_id, state = self.app.engine.enqueue({"path": str(f), "title": "Тестовый ролик",
                                                "description": "описание"})
        self.assertEqual(state, "added")
        self.app.engine.start()
        job = self.wait_status(job_id, ("done", "failed"))
        self.assertEqual(job["status"], "done", job["error"])
        self.assertEqual(job["progress"], 100)
        self.assertTrue(job["result_url"].startswith("http"))
        self.assertEqual(job["size_bytes"], f.stat().st_size)

    def test_02_duplicate(self) -> None:
        f = make_video(self.tmp, "dup.mp4")
        id1, s1 = self.app.engine.enqueue({"path": str(f)})
        id2, s2 = self.app.engine.enqueue({"path": str(f)})
        self.assertEqual(s1, "added")
        self.assertEqual(s2, "duplicate")
        self.assertEqual(id1, id2)

    def test_03_failure_and_retry(self) -> None:
        # имя с "fail" заставляет демо-страницу выдать ошибку
        f = make_video(self.tmp, "fail-clip.mp4")
        job_id, _ = self.app.engine.enqueue({"path": str(f)})
        job = self.wait_status(job_id, ("failed", "done"), timeout=120)
        self.assertEqual(job["status"], "failed")
        self.assertIn("ошибк", (job["error"] or "").lower())
        self.assertGreaterEqual(job["attempts"], 3)
        self.assertTrue(self.app.db.retry_now(job_id))
        self.assertEqual(self.app.db.get_job(job_id)["status"], "pending")

    def test_04_cancel_pending(self) -> None:
        f = make_video(self.tmp, "cancel.mp4")
        job_id, _ = self.app.engine.enqueue({"path": str(f)})
        self.assertTrue(self.app.db.cancel(job_id))
        self.assertEqual(self.app.db.get_job(job_id)["status"], "canceled")

    def test_05_missing_and_empty(self) -> None:
        with self.assertRaises(ValueError):
            self.app.engine.enqueue({"path": str(self.tmp / "nope.mp4")})
        p = self.tmp / "empty.mp4"
        p.write_bytes(b"")
        with self.assertRaises(ValueError):
            self.app.engine.enqueue({"path": str(p)})

    def test_06_multiple(self) -> None:
        files = [make_video(self.tmp, f"multi{i}.mp4", 20_000) for i in range(3)]
        ids = [self.app.engine.enqueue({"path": str(f)}) for f in files]
        self.assertTrue(all(s == "added" for _, s in ids))
        for jid, _ in ids:
            job = self.wait_status(jid, ("done", "failed"))
            self.assertEqual(job["status"], "done", job["error"])


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="vkup-api-"))
        cls.app = App(cfg=make_cfg(cls.tmp), db=DB(cls.tmp / "db.sqlite"))
        cls.httpd = build_server(cls.app, "127.0.0.1", 0)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.app.engine.stop()
        cls.app.engine.backend.close()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=15) as r:
            return json.loads(r.read())

    def post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def test_index(self) -> None:
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            html = r.read().decode()
        self.assertIn("vkup", html)

    def test_mock_page(self) -> None:
        with urllib.request.urlopen(self.base + "/mockvk/upload", timeout=10) as r:
            html = r.read().decode()
        self.assertIn("video_file_input", html)

    def test_state(self) -> None:
        s = self.get("/api/state")
        for k in ("config", "stats", "jobs", "logs", "engine"):
            self.assertIn(k, s)
        self.assertEqual(s["config"]["backend"], "demo")

    def test_config_and_selectors(self) -> None:
        r = self.post("/api/config", {"max_attempts": 5, "wallpost": True})
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"]["config"]["max_attempts"], 5)
        s = self.get("/api/selectors")
        self.assertIn("file_input", s["selectors"])
        r = self.post("/api/selectors", {"selectors": {"file_input": ["#my_input"]}})
        self.assertTrue(r["ok"])
        self.assertEqual(r["selectors"]["file_input"][0], "#my_input")

    def test_fs_and_probe(self) -> None:
        f = make_video(self.tmp, "probe.mp4", 5000)
        p = self.get("/api/probe?path=" + str(f))
        self.assertTrue(p["ok"])
        self.assertEqual(p["size"], 5000)
        lst = self.get("/api/fs/list?dir=" + str(self.tmp))
        self.assertTrue(lst["ok"])
        self.assertTrue(any(e["name"] == "probe.mp4" for e in lst["entries"]))
        self.assertFalse(any(e["name"].startswith(".") for e in lst["entries"]))

    def test_enqueue_and_run(self) -> None:
        f = make_video(self.tmp, "api.mp4", 60_000)
        r = self.post("/api/enqueue", {"path": str(f), "title": "api"})
        self.assertTrue(r["ok"])
        jid = r["results"][0]["id"]
        self.post("/api/queue/start", {})
        end = time.time() + 90
        while time.time() < end:
            job = self.app.db.get_job(jid)
            if job and job["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        self.assertEqual(self.app.db.get_job(jid)["status"], "done")

    def test_bad_enqueue(self) -> None:
        r = self.post("/api/enqueue", {"path": "/nonexistent/x.mp4"})
        self.assertFalse(r["results"][0]["ok"])
        self.assertIn("не найден", r["results"][0]["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
