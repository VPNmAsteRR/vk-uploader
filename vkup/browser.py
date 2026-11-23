"""Браузерный бэкенд: Selenium + Chromium, без API и без токенов.

Логика одна и та же для реального VK и для локальной демо-страницы:
найти поле файла → отправить путь → заполнить название/описание →
нажать «Опубликовать» → дождаться успеха, попутно читая процент.

Профиль браузера постоянный, поэтому вход в VK делается один раз руками,
а дальше очередь работает уже с залогиненной сессией.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .config import DATA_DIR, Config
from .selectors import load_selectors

ProgressCb = Callable[[float, str], None]
LogCb = Callable[[str, str], None]


class BrowserError(RuntimeError):
    """Ошибка автоматизации. retryable — имеет ли смысл пробовать ещё раз."""

    def __init__(self, message: str, *, retryable: bool = True,
                 need_login: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.need_login = need_login


class DriverManager:
    """Один общий драйвер, потокобезопасно."""

    def __init__(self, cfg: Config, log: LogCb) -> None:
        self.cfg = cfg
        self.log = log
        self._driver: webdriver.Chrome | None = None
        self._lock = threading.RLock()
        self._login_checked = False

    # --- запуск ---
    def get(self) -> webdriver.Chrome:
        with self._lock:
            if self._driver is not None:
                try:
                    _ = self._driver.current_url  # живой?
                    return self._driver
                except WebDriverException:
                    self.log("warn", "браузер не отвечает, перезапускаю")
                    self._quit_locked()
            self._driver = self._start()
            self._login_checked = False
            return self._driver

    def _start(self) -> webdriver.Chrome:
        cfg = self.cfg
        opts = Options()
        binary = _resolve_binary(str(cfg.get("browser_binary") or ""))
        if binary:
            opts.binary_location = binary
        if cfg.get("headless"):
            opts.add_argument("--headless=new")
        profile = str(cfg.get("profile_dir") or "").strip()
        if profile:
            Path(profile).mkdir(parents=True, exist_ok=True)
            opts.add_argument(f"--user-data-dir={profile}")
        ua = str(cfg.get("user_agent") or "").strip()
        if ua:
            opts.add_argument(f"--user-agent={ua}")

        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        opts.add_argument("--window-size=1280,900")
        opts.add_argument("--lang=ru-RU")

        service = Service(_resolve_driver(str(cfg.get("chromedriver_path") or "chromedriver")))
        try:
            driver = webdriver.Chrome(service=service, options=opts)
        except WebDriverException as e:
            raise BrowserError(
                f"не удалось запустить браузер: {str(e).splitlines()[0]}\n"
                f"проверь пути: chromium={binary or 'по умолчанию'}, "
                f"chromedriver={cfg.get('chromedriver_path')}",
                retryable=False,
            ) from e

        driver.set_page_load_timeout(int(cfg.get("page_load_timeout")))
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"},
            )
        except WebDriverException:
            pass
        self.log("info", "браузер запущен" + (" (headless)" if cfg.get("headless") else ""))
        return driver

    def _quit_locked(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:  # noqa: BLE001
                pass
            self._driver = None

    def existing(self) -> webdriver.Chrome | None:
        """Уже запущенный живой драйвер или None (без запуска нового)."""
        with self._lock:
            if self._driver is None:
                return None
            try:
                _ = self._driver.current_url
                return self._driver
            except WebDriverException:
                self._quit_locked()
                return None

    def quit(self) -> None:
        with self._lock:
            self._quit_locked()

    # --- готовность / вход ---
    def ensure_ready(self) -> webdriver.Chrome:
        driver = self.get()
        if self.cfg.get("backend") == "demo":
            return driver
        if self._login_checked and self.is_logged_in(driver):
            return driver
        return self.wait_login(driver)

    def is_logged_in(self, driver: webdriver.Chrome) -> bool:
        try:
            url = driver.current_url or ""
        except WebDriverException:
            return False
        if any(x in url for x in ("/login", "id.vk.ru/auth", "oauth.vk")):
            return False
        try:
            body = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
        except WebDriverException:
            return False
        markers = ("выйти", "моя страница", "добавить историю", "что у вас нового")
        login_markers = ("войти", "зарегистрироваться", "вход вконтакте")
        if any(m in body for m in markers):
            return True
        if any(m in body for m in login_markers):
            return False
        return False

    def wait_login(self, driver: webdriver.Chrome) -> webdriver.Chrome:
        self.log("info", f"проверяю вход в VK — открою {self.cfg.get('vk_home')}")
        try:
            driver.get(str(self.cfg.get("vk_home")))
        except TimeoutException:
            self.log("warn", "страница VK грузилась долго, продолжаю")
        if self.is_logged_in(driver):
            self._login_checked = True
            self.log("success", "вход в VK подтверждён")
            return driver

        if self.cfg.get("headless"):
            raise BrowserError(
                "нужно войти в VK, а браузер запущен без окна (headless). "
                "Выключи headless, нажми «Открыть браузер» и войди один раз — "
                "сессия сохранится в профиле.",
                retryable=False, need_login=True,
            )

        timeout = int(self.cfg.get("login_timeout"))
        self.log("warn", f"войди в VK в открытом окне браузера. Жду до {timeout} с…")
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(2)
            if self.is_logged_in(driver):
                self._login_checked = True
                self.log("success", "вход выполнен, продолжаю")
                return driver
        raise BrowserError("вход в VK не выполнен — загрузка остановлена",
                           retryable=False, need_login=True)


class BrowserBackend:
    def __init__(self, cfg: Config, log: LogCb,
                 selectors_path: Path | None = None) -> None:
        self.cfg = cfg
        self.log = log
        self.drivers = DriverManager(cfg, log)
        self.selectors_path = selectors_path
        self.selectors = load_selectors(selectors_path)

    # --- вспомогательное ---
    def _find(self, driver: webdriver.Chrome, kind: str, timeout: float,
              *, visible: bool = False):
        """Ищет первый из подходящих селекторов; возвращает (element, selector)."""
        selectors = self.selectors.get(kind) or []
        end = time.time() + timeout
        last_err: Exception | None = None
        while time.time() < end:
            for sel in selectors:
                try:
                    by, value = _parse(sel)
                    elems = driver.find_elements(by, value)
                    if not elems:
                        continue
                    el = elems[0]
                    if visible and not el.is_displayed():
                        continue
                    return el, sel
                except (WebDriverException, StaleElementReferenceException) as e:
                    last_err = e
                    continue
            time.sleep(0.4)
        raise BrowserError(
            f"не найден элемент «{kind}» (селекторы: {', '.join(selectors[:3])}…)"
            + (f" — {type(last_err).__name__}" if last_err else ""),
            retryable=True,
        )

    def _click(self, driver: webdriver.Chrome, el) -> None:
        try:
            el.click()
        except ElementClickInterceptedException:
            driver.execute_script("arguments[0].click();", el)

    def _set_checkbox(self, driver: webdriver.Chrome, kind: str, want: bool) -> None:
        try:
            el, _ = self._find(driver, kind, 1.5)
        except BrowserError:
            return  # такого поля на странице нет — не ошибка
        try:
            if el.is_selected() != want:
                self._click(driver, el)
        except WebDriverException:
            pass

    # --- публичный вход ---
    def open_visible(self) -> None:
        """Открыть браузер, чтобы человек мог войти руками / посмотреть страницу."""
        if self.cfg.get("headless"):
            self.cfg.update({"headless": False})
            self.drivers.quit()
            self.log("info", "headless выключен, чтобы можно было войти")
        driver = self.drivers.get()
        if self.cfg.get("backend") == "demo":
            driver.get(str(self.cfg.get("mock_url")))
            self.log("info", "браузер открыт на демо-странице загрузки")
            return
        driver.get(str(self.cfg.get("vk_home")))
        self.log("info", "браузер открыт — войди в VK, затем нажми «Проверить вход»")

    def check_login(self) -> dict[str, Any]:
        if self.cfg.get("backend") == "demo":
            return {"ok": True, "demo": True,
                    "message": "демо-режим: вход не требуется"}
        driver = self.drivers.get()
        driver.get(str(self.cfg.get("vk_home")))
        ok = self.drivers.is_logged_in(driver)
        if not ok:
            # иногда VK открывает ленту только после перехода на главную
            try:
                driver.get("https://vk.com/")
                ok = self.drivers.is_logged_in(driver)
            except WebDriverException:
                pass
        if ok:
            self.drivers._login_checked = True
        return {"ok": ok, "url": driver.current_url,
                "message": "вход подтверждён" if ok else "вход не выполнен"}

    # --- диагностика ---
    def calibrate(self) -> dict[str, Any]:
        """Открывает страницу загрузки и проверяет, какие селекторы на ней живут."""
        driver = self.drivers.get()
        url = str(self.cfg.get("mock_url") if self.cfg.get("backend") == "demo"
                  else self.cfg.get("upload_url"))
        try:
            driver.get(url)
        except TimeoutException:
            pass
        report: dict[str, Any] = {"url": driver.current_url, "title": driver.title,
                                  "matches": {}}
        for kind, selectors in self.selectors.items():
            rows = []
            for sel in selectors:
                try:
                    by, value = _parse(sel)
                    els = driver.find_elements(by, value)
                    if els:
                        rows.append({
                            "selector": sel,
                            "count": len(els),
                            "visible": any(_safe_displayed(e) for e in els),
                            "sample": _describe(els[0]),
                        })
                except WebDriverException:
                    continue
            report["matches"][kind] = rows
        return report

    def dump_page(self) -> dict[str, Any]:
        """Собирает интерактивные элементы страницы — чтобы подобрать селекторы."""
        driver = self.drivers.get()
        items: list[dict[str, Any]] = []
        for el in driver.find_elements(By.CSS_SELECTOR,
                                       "input, textarea, button, select, [role=button], [contenteditable=true]"):
            try:
                if not el.is_displayed():
                    continue
                items.append(_describe(el))
            except WebDriverException:
                continue
            if len(items) >= 120:
                break
        return {"url": driver.current_url, "title": driver.title, "items": items}

    def screenshot(self, name: str) -> str | None:
        """Снимок текущего окна. Не поднимает браузер, если он не запущен."""
        try:
            driver = self.drivers.existing()
            if driver is None:
                return None
            out = DATA_DIR / "screenshots"
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"{name}.png"
            driver.save_screenshot(str(path))
            return str(path)
        except Exception:  # noqa: BLE001
            return None

    # --- основная работа ---
    def upload(self, job: dict[str, Any], on_progress: ProgressCb,
               should_cancel: Callable[[], bool]) -> dict[str, Any]:
        driver = self.drivers.ensure_ready()
        path = Path(job["path"])
        total = path.stat().st_size
        url = str(self.cfg.get("mock_url") if self.cfg.get("backend") == "demo"
                  else self.cfg.get("upload_url"))

        on_progress(1.0, "открываю страницу загрузки")
        try:
            driver.get(url)
        except TimeoutException:
            self.log("warn", "страница загрузки грузилась долго, пробую продолжить")

        if should_cancel():
            raise BrowserError("отменено пользователем", retryable=False)

        # 1. файл
        on_progress(3.0, "выбираю файл")
        file_input = self._find_file_input(driver)
        driver.execute_script(
            "arguments[0].style.display='block';"
            "arguments[0].style.visibility='visible';"
            "arguments[0].style.opacity='1';", file_input)
        file_input.send_keys(str(path))
        self.log("info", f"файл передан браузеру: {path.name} ({_human(total)})")

        if should_cancel():
            raise BrowserError("отменено пользователем", retryable=False)

        # VK часто открывает редактор в новой вкладке
        self._follow_new_window(driver)

        # 2. метаданные
        on_progress(6.0, "заполняю название и описание")
        title = (job.get("title") or path.stem)[:128]
        try:
            el, _ = self._find(driver, "title_input", 10, visible=True)
            el.clear()
            el.send_keys(title)
        except BrowserError:
            self.log("warn", "поле названия не найдено — беру имя файла по умолчанию")
        desc = (job.get("description") or "")[:5000]
        if desc:
            try:
                el, _ = self._find(driver, "description_input", 5, visible=True)
                el.clear()
                el.send_keys(desc)
            except BrowserError:
                self.log("warn", "поле описания не найдено — пропускаю")

        self._set_checkbox(driver, "wallpost_toggle", bool(job.get("wallpost")))
        self._set_checkbox(driver, "no_comments_toggle", bool(job.get("no_comments")))

        # 3. публикация
        on_progress(8.0, "нажимаю «Опубликовать»")
        submit, _ = self._find(driver, "submit", 15, visible=True)
        self._click(driver, submit)

        # 4. ожидание
        upload_timeout = float(self.cfg.get("page_load_timeout")) * 30
        end = time.time() + max(upload_timeout, 120)
        last_pct = 8.0
        started = time.time()
        while time.time() < end:
            if should_cancel():
                raise BrowserError("отменено пользователем", retryable=False)
            if self._has(driver, "success"):
                on_progress(100.0, "готово")
                return {"url": driver.current_url, "title": title}
            if self._has(driver, "error"):
                raise BrowserError(
                    f"VK сообщил об ошибке загрузки ({self._error_text(driver)})",
                    retryable=True,
                )
            pct = self._read_pct(driver)
            if pct is not None and pct > last_pct:
                last_pct = min(pct, 99.0)
                on_progress(last_pct, f"загрузка {last_pct:.0f}%")
            else:
                # если сайт не показывает процент — двигаем оценку по времени
                elapsed = time.time() - started
                guess = min(95.0, 8.0 + elapsed * (90.0 / max(upload_timeout, 60)))
                if guess > last_pct + 1:
                    last_pct = guess
                    on_progress(last_pct, "загрузка идёт…")
            time.sleep(0.8)

        shot = self.screenshot(f"timeout-{job.get('id')}")
        raise BrowserError(
            "не дождался завершения загрузки за отведённое время"
            + (f"; скриншот: {shot}" if shot else ""),
            retryable=True,
        )

    def _find_file_input(self, driver: webdriver.Chrome):
        """Поле файла может быть в iframe или появляться после кнопки."""
        try:
            return self._find(driver, "file_input", 6)[0]
        except BrowserError:
            pass
        # вариант: сначала нажать «Добавить видео», потом искать поле
        try:
            btn, _ = self._find(driver, "open_upload", 5, visible=True)
            self._click(driver, btn)
        except BrowserError:
            pass
        try:
            return self._find(driver, "file_input", 10)[0]
        except BrowserError:
            pass
        # вариант: поле внутри iframe
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                driver.switch_to.frame(frame)
                el, _ = self._find(driver, "file_input", 3)
                return el
            except (BrowserError, WebDriverException):
                continue
            finally:
                driver.switch_to.default_content()
        raise BrowserError(
            "не нашёл поле выбора файла. Открой «Диагностика → Показать элементы "
            "страницы», найди нужный элемент и добавь его селектор на вкладке "
            "«Селекторы».", retryable=True,
        )

    def _follow_new_window(self, driver: webdriver.Chrome) -> None:
        """Если загрузка открылась в новой вкладке, переключаемся туда."""
        time.sleep(1.5)
        try:
            handles = driver.window_handles
            if len(handles) > 1 and driver.current_window_handle != handles[-1]:
                driver.switch_to.window(handles[-1])
                self.log("info", "переключился на вкладку редактора видео")
        except WebDriverException:
            pass

    def _has(self, driver: webdriver.Chrome, kind: str) -> bool:
        """Есть ли на странице видимый элемент-признак."""
        return any(self._matches(driver, sel, kind) for sel in self.selectors.get(kind) or [])

    _SKIP_TAGS = {"html", "body", "head", "script", "style", "title", "meta", "link"}

    def _matches(self, driver: webdriver.Chrome, sel: str, kind: str) -> bool:
        """Проверяет один селектор, отсекая служебные и слишком общие элементы."""
        try:
            by, value = _parse(sel)
            for el in driver.find_elements(by, value):
                try:
                    tag = (el.tag_name or "").lower()
                    if tag in self._SKIP_TAGS or not el.is_displayed():
                        continue
                    text = (el.text or "").strip()
                    if not text or len(text) > 200:
                        continue
                    # только «листовые» узлы: иначе цепляем контейнеры с чужим текстом
                    if el.find_elements(By.XPATH, ".//*"):
                        continue
                    return True
                except (WebDriverException, StaleElementReferenceException):
                    continue
        except WebDriverException:
            pass
        return False

    def _error_text(self, driver: webdriver.Chrome) -> str:
        for sel in self.selectors.get("error") or []:
            try:
                by, value = _parse(sel)
                for el in driver.find_elements(by, value):
                    if el.is_displayed() and el.text.strip():
                        return el.text.strip()[:200]
            except WebDriverException:
                continue
        return "без текста"

    def _read_pct(self, driver: webdriver.Chrome) -> float | None:
        for sel in self.selectors.get("progress_text") or []:
            try:
                by, value = _parse(sel)
                for el in driver.find_elements(by, value):
                    if not el.is_displayed():
                        continue
                    m = re.search(r"(\d{1,3})\s*%", el.text or "")
                    if m:
                        return float(m.group(1))
            except WebDriverException:
                continue
        # прогресс-бар с aria-valuenow
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, "[role=progressbar]"):
                v = el.get_attribute("aria-valuenow")
                if v:
                    return float(v)
        except WebDriverException:
            pass
        return None

    def close(self) -> None:
        self.drivers.quit()


def _parse(selector: str) -> tuple[str, str]:
    if selector.startswith("//") or selector.startswith("(//"):
        return By.XPATH, selector
    return By.CSS_SELECTOR, selector


def _safe_displayed(el) -> bool:
    try:
        return bool(el.is_displayed())
    except WebDriverException:
        return False


def _describe(el) -> dict[str, Any]:
    """Краткое описание элемента: тег, id, классы, тип, подпись, селектор."""
    out: dict[str, Any] = {}
    try:
        tag = (el.tag_name or "").lower()
        el_id = el.get_attribute("id") or ""
        cls = (el.get_attribute("class") or "").strip()
        typ = el.get_attribute("type") or ""
        name = el.get_attribute("name") or ""
        ph = el.get_attribute("placeholder") or ""
        aria = el.get_attribute("aria-label") or ""
        text = (el.text or "").strip()[:80]
        shown = _safe_displayed(el)
        out.update({"tag": tag, "id": el_id, "type": typ, "name": name,
                    "placeholder": ph, "aria": aria, "text": text,
                    "classes": cls[:120], "visible": shown})
        if el_id:
            out["selector"] = f"#{el_id}"
        elif name:
            out["selector"] = f"{tag}[name='{name}']"
        elif ph:
            out["selector"] = f"{tag}[placeholder*='{ph[:25]}']"
        elif cls:
            first = cls.split()[0]
            out["selector"] = f"{tag}.{first}"
        else:
            out["selector"] = tag
    except WebDriverException:
        pass
    return out


def _resolve_binary(name: str) -> str:
    """Путь к браузеру: явный, из PATH или типовой."""
    import shutil

    if name:
        p = Path(name).expanduser()
        if p.is_file():
            return str(p)
        found = shutil.which(name)
        if found:
            return found
    for cand in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "msedge", "microsoft-edge", "chrome"):
        found = shutil.which(cand)
        if found:
            return found
    for cand in ("/usr/bin/chromium", "/usr/bin/google-chrome",
                 "/usr/bin/google-chrome-stable"):
        if Path(cand).is_file():
            return cand
    # Windows: типовые места установки Chrome / Edge
    if os.name == "nt":
        roots = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        rels = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
        ]
        for root in roots:
            if not root:
                continue
            for rel in rels:
                p = Path(root) / rel
                if p.is_file():
                    return str(p)
    return ""


def _resolve_driver(name: str) -> str:
    """chromedriver по имени или абсолютному пути; ищем в PATH и типовых местах."""
    import shutil

    if not name:
        name = "chromedriver"
    p = Path(name).expanduser()
    if p.is_file():
        return str(p)
    found = shutil.which(name)
    if found:
        return found
    for cand in ("/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver",
                 "/snap/bin/chromedriver", "/opt/homebrew/bin/chromedriver"):
        if Path(cand).is_file():
            return cand
    # пусть Selenium попробует сам (Selenium Manager)
    return name


def _human(n: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"
