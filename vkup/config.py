"""Конфигурация: data/config.json + переменные окружения."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

def _resource_dir() -> Path:
    """Каталог с ресурсами (web/): рядом с исходниками или внутри exe-бандла."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """Рабочие данные (конфиг, БД, профиль браузера) — рядом с exe, не в бандле."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "data"
    return _resource_dir() / "data"


BASE_DIR = _resource_dir()
DATA_DIR = Path(os.environ.get("VKUP_DATA_DIR", _data_dir()))
WEB_DIR = BASE_DIR / "web"


DEFAULTS: dict[str, Any] = {
    # --- что именно делаем ---
    # "browser" — реальный VK через браузер, "demo" — локальная тестовая страница
    "backend": "demo",

    # --- браузер ---
    "browser_binary": "",              # пусто — найдём автоматически
    "chromedriver_path": "chromedriver",
    "headless": False,
    "profile_dir": str(DATA_DIR / "chrome-profile"),
    "page_load_timeout": 60,
    "element_timeout": 30,
    "login_timeout": 300,          # сколько ждать ручного входа в VK
    "keep_browser_open": True,
    "user_agent": "",              # пусто — браузерный по умолчанию

    # --- адреса ---
    "upload_url": "https://vk.com/video?act=upload",
    "vk_home": "https://vk.com/feed",
    "mock_url": "http://127.0.0.1:8765/mockvk/upload",

    # --- поведение загрузки ---
    "wallpost": False,
    "is_private": False,
    "no_comments": False,
    "delete_after_upload": False,
    "skip_duplicates": True,
    "verify_after_upload": True,   # проверять, что видео появилось в списке

    # --- очередь ---
    "workers": 1,
    "max_attempts": 3,
    "retry_base_delay": 10.0,
    "retry_max_delay": 300.0,

}


def _mask_text(value: str) -> str:
    return value if len(value) <= 40 else value[:20] + "…"


class Config:
    """Потокобезопасный доступ к настройкам с сохранением на диск."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else DATA_DIR / "config.json"
        self._lock = threading.RLock()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text("utf-8"))
                    if isinstance(raw, dict):
                        for k, v in raw.items():
                            if k in DEFAULTS:
                                self._data[k] = v
                except (OSError, ValueError):
                    pass  # битый конфиг не должен ронять приложение
            if os.environ.get("VKUP_BACKEND"):
                self._data["backend"] = os.environ["VKUP_BACKEND"]
            return self.snapshot()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, *, save: bool = True) -> None:
        if key not in DEFAULTS:
            raise KeyError(key)
        with self._lock:
            self._data[key] = coerce(key, value)
            if save:
                self.save()

    def update(self, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            for k, v in values.items():
                if k in DEFAULTS:
                    self._data[k] = coerce(k, v)
            self.save()
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def public(self) -> dict[str, Any]:
        data = self.snapshot()
        data["profile_dir"] = _mask_text(str(data.get("profile_dir") or ""))
        return data


def coerce(key: str, value: Any) -> Any:
    ref = DEFAULTS[key]
    if isinstance(ref, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "да"}
        return bool(value)
    if isinstance(ref, int) and not isinstance(ref, bool):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return ref
    if isinstance(ref, float):
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return ref
    if key == "backend":
        return value if value in {"browser", "demo"} else "demo"
    return str(value) if value is not None else ""


CONFIG = Config()
