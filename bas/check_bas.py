#!/usr/bin/env python3
"""Проверка BAS-скрипта без BAS: порт препроцессора + проверка синтаксиса JS.

Препроцессор повторяет Engine/preprocessor.cpp из открытых исходников BAS:
  1. удаляет комментарии /* */ и // ;
  2. section_start("..", N)!  ->  ;_sa(N);   (N==0 -> ;)
  3. section_end()!           ->  ;
  4. section_insert()         ->  пусто
  5. wait_code()!             ->  пусто
  6. разворачивает "хвостовой" вызов X)! в X, function(){ ... })  —
     так BAS превращает «действие с колбэком» в асинхронный вызов.

После этого код — обычный JS, его можно прогнать через `node --check`.

Запуск: python3 bas/check_bas.py [путь.xml]
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 1. извлечение <Script><![CDATA[...]]></Script>
# ---------------------------------------------------------------------------
def extract_script(xml_text: str) -> str:
    m = re.search(r"<Script><!\[CDATA\[(.*?)\]\]></Script>", xml_text, re.S)
    if not m:
        raise SystemExit("в файле нет <Script><![CDATA[...]]></Script>")
    return m.group(1)


# ---------------------------------------------------------------------------
# 2. порт Preprocessor::Preprocess
# ---------------------------------------------------------------------------
def strip_comments(src: str) -> str:
    """Удаляет /* */ и //, не трогая содержимое строк."""
    out: list[str] = []
    i = 0
    n = len(src)
    quote = ""
    while i < n:
        ch = src[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if src.startswith("//", i):
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


RE_SECTION_START = re.compile(r'section_start\(\s*"([^"]*)"\s*,\s*(-?\d*)\)!')
RE_SECTION_END = re.compile(r"section_end\(\)!")
RE_SECTION_INSERT = re.compile(r"section_insert\(\)")
RE_WAIT_CODE = re.compile(r"wait_code\(\)!")


def expand_callbacks(src: str) -> str:
    """Разворачивает X)!  ->  X, function(){ <остаток блока> }).

    Повторяет блок из Preprocessor::Preprocess: берётся последний по тексту
    ")!", и всё до конца текущего блока (по балансу фигурных скобок) либо до
    конца скрипта становится телом колбэка. Так линейная запись BAS
    превращается во вложенные вызовы.
    """
    res = src
    guard = 0
    while True:
        guard += 1
        if guard > 500_000:
            raise SystemExit("развёртка колбэков зациклилась")

        idx = res.rfind(")!")
        if idx < 0:
            break

        # один аргумент? (перед ")" стоит "(" — тогда запятую не добавляем)
        j = idx - 1
        while j >= 0 and res[j].isspace():
            j -= 1
        one_arg = j >= 0 and res[j] == "("
        prefix = "function(){" if one_arg else ", function(){"

        # конец блока — первая "}" ниже нуля по балансу
        n = len(res)
        k = idx + 2
        depth = 0
        closed = -1
        while k < n:
            ch = res[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    closed = k
                    break
            k += 1

        if closed < 0:
            res = res[:idx] + prefix + res[idx + 2:] + "})"
        else:
            body = res[idx + 2:closed]
            res = res[:idx] + prefix + body + "})}" + res[closed + 1:]

    return res


def preprocess(src: str) -> str:
    res = strip_comments(src)
    res = RE_SECTION_START.sub(lambda m: ";" if not m.group(1) else f";_sa({m.group(2)});", res)
    res = RE_SECTION_END.sub(";", res)
    res = RE_SECTION_INSERT.sub("", res)
    res = RE_WAIT_CODE.sub("", res)
    res = expand_callbacks(res)
    return res


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else HERE / "vk_video_upload.xml"
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        return 2

    script = extract_script(path.read_text("utf-8"))
    print(f"скрипт: {len(script)} символов, {script.count(chr(10)) + 1} строк")

    # --- проверки структуры ---
    starts = len(re.findall(r"section_start\(", script))
    ends = len(re.findall(r"section_end\(\)!", script))
    print(f"section_start: {starts}, section_end: {ends}", end="  ")
    if starts != ends:
        print("НЕ СОВПАДАЮТ")
        return 1
    print("ok")

    # скобки в тексте скрипта должны быть сбалансированы
    bal = script.count("(") - script.count(")")
    if bal:
        print(f"дисбаланс круглых скобок: {bal:+d}")
        return 1

    processed = preprocess(script)

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(processed)
        tmp = f.name

    r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
    if r.returncode != 0:
        print("СИНТАКСИС НЕ ПРОШЁЛ:")
        print(r.stderr[:4000])
        print(f"развёрнутый файл: {tmp}")
        return 1

    print("синтаксис JS: ok")
    Path(tmp).unlink(missing_ok=True)
    print("проверка пройдена")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
