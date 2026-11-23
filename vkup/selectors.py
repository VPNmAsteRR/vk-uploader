"""Селекторы страницы загрузки. Можно переопределить в data/selectors.json.

Порядок: сначала пробуем CSS, потом XPath (строки, начинающиеся с "//").
Значения подобраны «с запасом»: несколько вариантов на случай, если VK
перерисует интерфейс. Демо-страница использует те же id, поэтому один и тот
же код работает и на реальном VK, и на локальной заглушке.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import DATA_DIR

DEFAULT_SELECTORS: dict[str, list[str]] = {
    # поле выбора файла (скрытое — это нормально, send_keys работает)
    "file_input": [
        "input[type=file]",
        "#video_file_input",
    ],
    # кнопка, открывающая диалог загрузки (если поле не на странице сразу)
    "open_upload": [
        "//*[self::button or self::a][contains(., 'Добавить видео')]",
        "//*[self::button or self::a][contains(., 'Загрузить видео')]",
        "//*[self::button or self::a][contains(., 'Загрузить')]",
        "#open_upload",
    ],
    # название
    "title_input": [
        "input[name='title']",
        "#video_title",
        "input[placeholder*='Название']",
        "input[placeholder*='название']",
    ],
    # описание
    "description_input": [
        "textarea[name='description']",
        "#video_description",
        "textarea[placeholder*='Описание']",
        "textarea[placeholder*='описание']",
    ],
    # кнопка публикации/сохранения
    "submit": [
        "#publish",
        "//button[contains(., 'Опубликовать')]",
        "//button[contains(., 'Сохранить')]",
        "//button[contains(., 'Загрузить')]",
        "//button[contains(., 'Добавить')]",
    ],
    # чекбокс «запись на стене» (если есть)
    "wallpost_toggle": [
        "#wallpost",
        "//label[contains(., 'на стен')]//input[@type='checkbox']",
    ],
    # чекбокс «без комментариев»
    "no_comments_toggle": [
        "#no_comments",
        "//label[contains(., 'комментар')]//input[@type='checkbox']",
    ],
    # признак успешного завершения
    "success": [
        "#upload_success",
        "//*[contains(., 'Видео опубликовано')]",
        "//*[contains(., 'Видео добавлено')]",
        "//*[contains(., 'успешно загружено')]",
    ],
    # признак ошибки
    "error": [
        "#upload_error",
        "//*[contains(., 'Не удалось загрузить')]",
        "//*[contains(., 'Ошибка загрузки')]",
    ],
    # блок с процентом загрузки
    "progress_text": [
        "#upload_progress",
        "//*[contains(text(), '%')]",
    ],
}


def load_selectors(path: Path | None = None) -> dict[str, list[str]]:
    """Возвращает дефолты, поверх которых наложен пользовательский файл."""
    out = {k: list(v) for k, v in DEFAULT_SELECTORS.items()}
    p = Path(path) if path else DATA_DIR / "selectors.json"
    if p.exists():
        try:
            raw = json.loads(p.read_text("utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k in out and isinstance(v, list) and v:
                        out[k] = [str(x) for x in v] + out[k]
        except (OSError, ValueError):
            pass
    return out


def save_selectors(data: dict[str, Any], path: Path | None = None) -> None:
    p = Path(path) if path else DATA_DIR / "selectors.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
