#!/usr/bin/env python3
"""Проверка селекторов и JS-сниппетов BAS-скрипта в настоящем браузере.

Симулятор (simulate_bas.py) проверяет логику, но не вёрстку. Здесь мы берём
ровно те XPath-селекторы и те JS-сниппеты, что попали в BAS-проект, и гоняем
их в реальном Chromium против локальной демо-страницы.

Если этот тест зелёный — значит связка «селектор → элемент → статус» рабочая,
и в BAS останется проверить только саму вёрстку VK.

Запуск (сервер поднимается сам):
    ../venv/bin/python bas/verify_selectors.py
"""

from __future__ import annotations

import base64
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import build_bas  # noqa: E402  (SEL, JS_STATE, JS_FILE_ATTACHED)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, timeout: float = 25.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise SystemExit(f"сервер на порту {port} не поднялся")


def xpath_of(sel: str) -> str:
    return " >XPATH> " + sel


def main() -> int:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By

    port = free_port()
    srv = subprocess.Popen(
        [sys.executable, str(ROOT / "vkup.py"), "--backend", "demo",
         "--port", str(port)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    failures = 0
    driver = None
    try:
        wait_port(port)
        url = f"http://127.0.0.1:{port}/mockvk/upload"

        opts = Options()
        opts.binary_location = "/usr/bin/chromium"
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1280,900")
        driver = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=opts)
        driver.get(url)

        # ---------- 1. селекторы находят элементы ----------
        print("=== селекторы ===")
        expected = {
            "file_input": True,
            "title": True,
            "description": True,
            "submit": True,
            "login": False,      # на демо-странице формы входа нет
            "wallpost": True,
        }
        for key, sel in build_bas.SEL.items():
            try:
                els = driver.find_elements(By.XPATH, sel)
                found = len(els) > 0
            except Exception as exc:            # noqa: BLE001
                found = False
                print(f"  {key:12s} ОШИБКА XPATH: {exc}")
            mark = "OK" if found == expected[key] else "ПРОВАЛ"
            if found != expected[key]:
                failures += 1
            print(f"  {key:12s} найден={str(found):5s} ожидалось={str(expected[key]):5s} {mark}")

        # ---------- 2. JS-сниппеты ----------
        print()
        print("=== JS-сниппеты ===")

        def run_js(code: str):
            return driver.execute_script("return " + code)

        # файл ещё не выбран
        attached = run_js(build_bas.JS_FILE_ATTACHED)
        print(f"  файл прикреплён до выбора: {attached!r} (ожидалось '0')")
        if attached != "0":
            failures += 1

        state = run_js(build_bas.JS_STATE)
        print(f"  статус до загрузки:        {state!r} (ожидалось 'wait')")
        if state != "wait":
            failures += 1

        # ---------- 3. полный проход формы ----------
        print()
        print("=== полный проход формы ===")
        video = ROOT / "demo" / "demo1.mp4"
        if not video.exists():
            raise SystemExit(f"нет демо-файла: {video}")

        inp = driver.find_element(By.XPATH, build_bas.SEL["file_input"])
        # BAS заполняет поле через перехват системного диалога; в Selenium
        # эквивалент — send_keys прямо в input[type=file].
        inp.send_keys(str(video))
        time.sleep(0.6)

        attached = run_js(build_bas.JS_FILE_ATTACHED)
        print(f"  файл прикреплён:           {attached!r} (ожидалось '1')")
        if attached != "1":
            failures += 1

        # заполняем название (как BAS: очистка + ввод)
        title = driver.find_element(By.XPATH, build_bas.SEL["title"])
        title.clear()
        title.send_keys("Тестовое видео BAS")
        desc = driver.find_element(By.XPATH, build_bas.SEL["description"])
        desc.clear()
        desc.send_keys("Загружено автоматически: Тестовое видео BAS")

        driver.find_element(By.XPATH, build_bas.SEL["submit"]).click()

        state = "wait"
        for _ in range(40):
            time.sleep(0.25)
            state = run_js(build_bas.JS_STATE)
            if state != "wait":
                break
        print(f"  итоговый статус:           {state!r} (ожидалось 'ok')")
        if state != "ok":
            failures += 1

        # ---------- 4. ошибка распознаётся ----------
        print()
        print("=== распознавание ошибки ===")
        driver.get(url)
        inp = driver.find_element(By.XPATH, build_bas.SEL["file_input"])
        inp.send_keys(str(ROOT / "demo" / "fail-example.mp4"))
        time.sleep(0.5)
        t = driver.find_element(By.XPATH, build_bas.SEL["title"])
        t.clear()
        t.send_keys("fail")
        driver.find_element(By.XPATH, build_bas.SEL["submit"]).click()

        state = "wait"
        for _ in range(40):
            time.sleep(0.25)
            state = run_js(build_bas.JS_STATE)
            if state != "wait":
                break
        print(f"  статус для плохого файла:  {state!r} (ожидалось 'err')")
        if state != "err":
            failures += 1

    finally:
        if driver is not None:
            driver.quit()
        srv.terminate()
        try:
            srv.wait(timeout=8)
        except subprocess.TimeoutExpired:
            srv.kill()

    print()
    if failures:
        print(f"ПРОВАЛОВ: {failures}")
        return 1
    print("все проверки пройдены")
    return 0


if __name__ == "__main__":
    sys.exit(main())
