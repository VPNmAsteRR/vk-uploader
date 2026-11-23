#!/usr/bin/env python3
"""Генератор проекта Browser Automation Studio для загрузки видео в VK.

Результат — bas/vk_video_upload.xml. Открывается в BAS через
«Файл → Открыть проект», запускается кнопкой «Запуск».

Почему генератор, а не xml руками: у каждого действия BAS есть
base64-метаданные (/*Dat:...*/), по которым редактор рисует блоки.
Их проще собирать кодом, чем расставлять вручную.

Запуск:  python3 bas/build_bas.py
Проверка синтаксиса: python3 bas/check_bas.py
"""

from __future__ import annotations

import base64
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "vk_video_upload.xml"

_rng = random.Random(20240607)


def uid() -> int:
    return _rng.randint(100_000_000, 999_999_999)


def b64(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def js(value: str) -> str:
    """JS-литерал строки (в кавычках, с экранированием)."""
    return json.dumps(value, ensure_ascii=False)


def V(name: str) -> str:
    """Имя переменной в тексте скрипта (BAS хранит их с префиксом VAR_)."""
    return "VAR_" + name


# ---------------------------------------------------------------------------
# Селекторы. XPath выбран потому, что одним выражением покрывает и локальную
# демо-страницу (#video_title и т.п.), и боевую вёрстку VK.
# Если VK перерисует форму — правится только этот блок.
# ---------------------------------------------------------------------------
SEL = {
    "file_input": "//input[@type='file']",
    "title": ("(//*[@id='video_title']"
              " | //input[@name='title']"
              " | //textarea[@name='title'])[1]"),
    "description": ("(//*[@id='video_description']"
                    " | //textarea[@name='description'])[1]"),
    "submit": ("(//*[@id='publish']"
               " | //button[contains(., 'Опубликовать')]"
               " | //button[contains(., 'Сохранить')])[1]"),
    "login": ("(//input[@name='login']"
              " | //*[@id='login_button']"
              " | //*[@id='index_login'])[1]"),
    "wallpost": ("(//*[@id='wallpost']"
                 " | //label[contains(., 'на стен')]//input[@type='checkbox'])[1]"),
}

# JS-фрагменты, которые выполняются прямо на странице
JS_STATE = (
    "(function(){"
    "var t=(document.body&&document.body.innerText)||'';"
    "var e=document.querySelector('#upload_success');"
    "if(e&&e.innerText.trim().length){var s=getComputedStyle(e);"
    "if(s.display!='none'&&s.visibility!='hidden')return 'ok';}"
    "if(t.indexOf('Видео опубликовано')>=0)return 'ok';"
    "if(t.indexOf('успешно загружено')>=0)return 'ok';"
    "if(t.indexOf('Видео добавлено')>=0)return 'ok';"
    "var r=document.querySelector('#upload_error');"
    "if(r&&r.innerText.trim().length){var s2=getComputedStyle(r);"
    "if(s2.display!='none')return 'err';}"
    "if(t.indexOf('Не удалось загрузить')>=0)return 'err';"
    "if(t.indexOf('Ошибка загрузки')>=0)return 'err';"
    "return 'wait';"
    "})()"
)

JS_FILE_ATTACHED = (
    "(function(){var i=document.querySelector('input[type=file]');"
    "return (i&&i.files&&i.files.length>0)?'1':'0';})()"
)

# путь -> имя без каталога и без расширения
JS_BASENAME = r"(function(p){return String(p).replace(/^.*[\\/]/,'').replace(/\.[^.]+$/,'');})"

# ---------------------------------------------------------------------------
# Метаданные действий
# ---------------------------------------------------------------------------
MOVE_FIELDS = [
    {"id": "Check2", "type": "check", "data": False, "is_def": True},
    {"id": "Select", "type": "select", "data": "left", "is_def": True},
    {"id": "Check", "type": "check", "data": False, "is_def": True},
    {"id": "Speed", "type": "constr", "data": "100", "class": "expression", "is_def": True},
    {"id": "Gravity", "type": "constr", "data": "6", "class": "expression", "is_def": True},
    {"id": "Deviation", "type": "constr", "data": "2.5", "class": "expression", "is_def": True},
] + [
    {"id": k, "type": "constr", "data": "", "class": "string", "is_def": True}
    for k in ("time", "accuracy", "intensity", "sleep", "longElementMove",
              "shortElementMove", "scroll", "randomMove", "microMove",
              "goingBeyond", "moveAlongText", "longMax", "shortMax", "miss",
              "delay", "targetScroll", "targetDelay", "filters", "hoverMode",
              "reverseScroll", "additionalEmulation")
] + [
    {"id": k, "type": "constr", "data": "", "class": "int", "is_def": True}
    for k in ("maxRepeatSkip", "overlapByPoints")
]


def c(name: str, value: str, cls: str = "string") -> dict:
    """Поле-константа."""
    return {"id": name, "type": "constr", "data": value, "class": cls, "is_def": False}


def var(name: str, value: str) -> dict:
    return {"id": name, "type": "var", "data": value, "is_def": False}


def chk(name: str, value: bool) -> dict:
    return {"id": name, "type": "check", "data": value, "is_def": True}


def act(action: str, fields: list | None = None, selector: str | None = None) -> dict:
    d = {
        "s": action, "v": 1, "f": [],
        "uw": "0", "ut": "0", "uto": "0", "um": "0", "ue": "0", "usp": "0",
        "d": fields or [],
    }
    if selector is not None:
        d["p"] = {
            "is_image": False, "css": "", "version": "1.0",
            "css1": "", "css2": "", "css3": "",
            "current": "xpath", "match": "",
            "xpath": " >XPATH> " + selector,
            "at": "", "we": False, "fa": False,
        }
    return d


# ---------------------------------------------------------------------------
# Сборщик скрипта
# ---------------------------------------------------------------------------
class Script:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.lv = 1

    def raw(self, text: str) -> None:
        for line in text.split("\n"):
            self.lines.append(("   " * self.lv + line) if line.strip() else "")

    def action(self, meta: dict, code: str) -> None:
        self.raw(f'section_start("", {uid()})!')
        self.raw(f"/*Dat:{b64(meta)}*/")
        self.raw(code)
        self.raw("section_end()!")

    def section(self, meta: dict, head_code: str, body, tail_code: str = "") -> None:
        """section_start + метаданные + открывающий код + тело + закрывающий."""
        self.raw(f'section_start("", {uid()})!')
        self.raw(f"/*Dat:{b64(meta)}*/")
        self.raw(head_code)
        self.lv += 1
        body()
        self.lv -= 1
        if tail_code:
            self.raw(tail_code)
        self.raw("section_end()!")

    # --- простые действия ------------------------------------------------
    def set_var(self, name: str, expr: str, cls: str = "string") -> None:
        self.action(
            act("setvariable", [var("SetVariableName", name), c("SetVariableValue", expr, cls)]),
            f"{V(name)} = {expr}",
        )

    def inc_var(self, name: str, by: int = 1) -> None:
        self.action(
            act("incvariable", [var("SetVariableName", name),
                                c("IncVariableValue", str(by), "int")]),
            f"{V(name)} = parseInt({V(name)}) + parseInt({by})",
        )

    def log(self, text: str) -> None:
        self.action(act("log", [c("LogText", text)]), f"log({js(text)})")

    def log_expr(self, expr: str) -> None:
        self.action(act("log", [c("LogText", expr, "expression")]), f"log({expr})")

    def page_js(self, code: str, save: str) -> None:
        self.action(
            act("custom", [c("Code", code, "expression")]),
            "/*Browser*/\n"
            f"page().script({js(code)})!\n"
            f"{V(save)} = _result()",
        )

    def load(self, url_expr: str) -> None:
        self.action(
            act("load", [c("LoadUrl", url_expr), c("Referrer", "")]),
            f"/*Browser*/\nload({url_expr})!",
        )

    def open_file_dialog(self, path_expr: str) -> None:
        self.action(
            act("openfile", [c("Value", path_expr)]),
            f"/*Browser*/\nopen_file_dialog({path_expr})!",
        )

    def wait_visible(self, selector: str) -> None:
        self.action(
            act("waitelement", [chk("Check", True)], selector),
            f"/*Browser*/\n_SELECTOR = {js(' >XPATH> ' + selector)};\n"
            "wait_element_visible(_SELECTOR)!",
        )

    def wait_exists(self, selector: str) -> None:
        """Ждёт появления в DOM, не требуя видимости (скрытые input[type=file])."""
        self.action(
            act("waitelement", [chk("Check", False)], selector),
            f"/*Browser*/\n_SELECTOR = {js(' >XPATH> ' + selector)};\n"
            "wait_element(_SELECTOR)!",
        )

    def click(self, selector: str, js_click: bool = False, require_visible: bool = True) -> None:
        guard = ("wait_element_visible(_SELECTOR)!"
                 if require_visible else
                 "wait_element(_SELECTOR)!")
        head = f"/*Browser*/\n_SELECTOR = {js(' >XPATH> ' + selector)};\n{guard}"
        if js_click:
            body = '\nget_element_selector(_SELECTOR, false).script("self.click()")!'
        else:
            body = (
                "\n_call(_random_point, {})!"
                "\n_if(_result().length > 0, function(){"
                "\nmove({})!"
                "\nget_element_selector(_SELECTOR, false).clarify(X,Y)!"
                "\n_call(_clarify, {})!"
                "\nmouse(X,Y)!"
                "\n})!"
            )
        self.action(act("moveandclickelement", MOVE_FIELDS, selector), head + body)

    def type(self, selector: str, value_expr: str, interval: int = 50) -> None:
        self.action(
            act("typeelement",
                [c("TypeData", value_expr), c("TypeInterval", str(interval), "int")] + MOVE_FIELDS,
                selector),
            f"/*Browser*/\n_SELECTOR = {js(' >XPATH> ' + selector)};\n"
            "wait_element_visible(_SELECTOR)!\n"
            "_call(_random_point, {})!\n"
            "_if(_result().length > 0, function(){\n"
            "move({})!\n"
            "get_element_selector(_SELECTOR, false).clarify(X,Y)!\n"
            "_call(_clarify, {})!\n"
            "mouse(X,Y)!\n"
            f"_type({value_expr},{interval})!\n"
            "})!",
        )

    def clear_field(self, selector: str) -> None:
        self.type(selector, '"<CONTROL>a<DELETE>"')

    def exist(self, selector: str, save: str) -> None:
        self.action(
            act("exist", [var("Save", save), chk("Check", False)], selector),
            f"/*Browser*/\n_SELECTOR = {js(' >XPATH> ' + selector)};\n"
            "get_element_selector(_SELECTOR, false).nowait().exist()!\n"
            f"{V(save)} = _result() == 1",
        )

    def sleep(self, ms: int) -> None:
        self.action(
            act("sleep", [c("sleepfrom", str(ms), "int"),
                          c("sleepto", str(ms), "int"),
                          c("sleepfromto", str(ms), "int"),
                          chk("sleeprandom", False)]),
            f"sleep({ms})!",
        )

    def append_file(self, path_expr: str, value_expr: str) -> None:
        self.action(
            act("filesystem_writefile",
                [c("File", path_expr), c("Value", value_expr),
                 chk("Check3", True), chk("Check", True), chk("Check2", False)]),
            "native(\"filesystem\", \"writefile\", JSON.stringify({path: "
            f"{path_expr},value: {value_expr},base64:false,append:true}}))",
        )

    def fail(self, msg_expr: str) -> None:
        self.action(
            act("fail", [c("FailMessage", msg_expr), chk("Check", False)]),
            f"fail({msg_expr},false)",
        )

    def success(self, msg_expr: str) -> None:
        self.action(act("success", [c("SuccessMessage", msg_expr)]),
                    f"success({msg_expr})")

    # --- управляющие конструкции -----------------------------------------
    def if_(self, cond: str, body) -> None:
        meta = act("if", [c("IfExpression", cond, "expression"), chk("IfElse", False)])
        self.section(meta,
                     f"_set_if_expression({js(base64.b64encode(('[[%s]]' % cond).encode()).decode())});\n"
                     f"_if({cond} ,function(){{\n"
                     "section_insert()",
                     body,
                     "})!")

    def while_(self, cond: str, body) -> None:
        meta = act("while", [c("WhileExpression", cond, "expression")])
        self.section(meta,
                     "_do(function(){\n"
                     '_set_action_info({ name: "While" });\n'
                     "VAR_CYCLE_INDEX = _iterator() - 1\n"
                     f"BREAK_CONDITION = {cond};\n"
                     "if(!BREAK_CONDITION)_break();\n"
                     "section_insert()",
                     body,
                     "})!")

    def foreach(self, list_expr: str, item: str, body) -> None:
        meta = act("foreach", [c("ForArray", list_expr, "expression")])
        self.section(meta,
                     f'_do_with_params({{"foreach_data":({list_expr})}},function(){{\n'
                     '_set_action_info({ name: "Foreach" });\n'
                     "VAR_CYCLE_INDEX = _iterator() - 1\n"
                     'if(VAR_CYCLE_INDEX > _cycle_param("foreach_data").length - 1)_break();\n'
                     'VAR_FOREACH_DATA = _cycle_param("foreach_data")[VAR_CYCLE_INDEX]\n'
                     "section_insert()\n"
                     f"{V(item)} = VAR_FOREACH_DATA",
                     body,
                     "})!")

    def ignore_errors(self, body) -> None:
        self.section(act("ignoreerrors"),
                     "_call(function()\n"
                     "{\n"
                     '_set_action_info({ name: "Ignore Errors" });\n'
                     "_on_fail(function(){\n"
                     "VAR_LAST_ERROR = _result()\n"
                     "VAR_ERROR_ID = ScriptWorker.GetCurrentAction()\n"
                     "VAR_WAS_ERROR = false\n"
                     "_break(1,true)\n"
                     "})\n"
                     'CYCLES.Current().RemoveLabel("function")\n'
                     "section_insert()",
                     body,
                     "},null)!")

    def text(self) -> str:
        return "\n".join(self.lines)


# ---------------------------------------------------------------------------
# Сам сценарий
# ---------------------------------------------------------------------------
def build_script() -> str:
    s = Script()
    s.raw("section(1 /*1*/,1000000 /*1000000*/,1000000 /*1000000*/,0,function(){")
    s.raw('   section_start("\\u007b\\u0022n\\u0022:\\u0022Initialize\\u0022\\u007d", 0)!')
    s.raw("")
    s.raw("   section_end()!")
    s.lv = 1

    # ---------------- настройки ----------------
    s.log("=== VK Video Uploader (BAS) ===")
    s.log("Настройки находятся в первых строках скрипта.")
    s.set_var("DEMO", "false", "expression")
    s.set_var("VK_HOME", js("https://vk.com/feed"))
    s.set_var("UPLOAD_URL", js("https://vk.com/video?act=upload"))
    s.set_var("FILES_FILE", js("C:\\vk\\videos.txt"))
    s.set_var("MAX_ATTEMPTS", "3", "int")
    s.set_var("UPLOAD_TIMEOUT_MS", "1200000", "int")
    s.set_var("WALLPOST", "false", "expression")
    s.set_var("REPORT_FILE", f"{V('FILES_FILE')} + '.report.txt'", "expression")

    # в демо-режиме идём на локальную страницу-заглушку и не требуем входа
    s.if_(V("DEMO"), lambda: (
        s.log("ДЕМО-РЕЖИМ: локальная страница, реальный VK не затрагивается."),
        s.set_var("VK_HOME", js("http://127.0.0.1:8765/mockvk/upload")),
        s.set_var("UPLOAD_URL", js("http://127.0.0.1:8765/mockvk/upload")),
    ))

    # ---------------- список файлов ----------------
    s.log("Читаю список файлов...")
    s.ignore_errors(lambda: s.action(
        act("filesystem_file_to_list", [c("Value", V("FILES_FILE")), var("Save", "FILES")]),
        "{\n"
        'var d = native("filesystem", "readfile", JSON.stringify({value: '
        f'{V("FILES_FILE")},base64:false,from:0,to:0}}))\n'
        f"{V('FILES')} = (d.length == 0) ? [] : d.split(/\\r?\\n/)\n"
        "}",
    ))
    s.if_(f"typeof({V('FILES')}) === 'undefined' || {V('FILES')}.length == 0",
          lambda: s.fail(f"'Список файлов пуст или не найден: ' + {V('FILES_FILE')}"))
    s.log_expr(f"'Файлов в очереди: ' + {V('FILES')}.length")

    s.set_var("OK_COUNT", "0", "int")
    s.set_var("FAIL_COUNT", "0", "int")
    s.set_var("LAST_ERROR", js(""), "string")

    # ---------------- вход в VK ----------------
    s.log("Открываю VK и проверяю вход...")
    s.load(V("VK_HOME"))
    s.sleep(3000)
    s.exist(SEL["login"], "NEED_LOGIN")
    s.if_(f"{V('NEED_LOGIN')} && !{V('DEMO')}", lambda: (
        s.log("Вход не выполнен. Войдите в браузере: QR-код или логин/пароль."),
        s.log("Жду до 10 минут..."),
        s.set_var("LOGIN_WAIT", "0", "int"),
        s.while_(f"parseInt({V('LOGIN_WAIT')}) < 120", lambda: (
            s.sleep(5000),
            s.exist(SEL["login"], "STILL_LOGIN"),
            s.if_(f"!{V('STILL_LOGIN')}", lambda: s.set_var("LOGIN_WAIT", "999000", "int")),
            s.inc_var("LOGIN_WAIT"),
        )),
        s.if_(V("STILL_LOGIN"), lambda: s.fail('"Не удалось войти в VK за отведённое время"')),
        s.log("Вход выполнен."),
    ))

    # ---------------- обработка одного файла ----------------
    def process_file() -> None:
        s.log_expr(f"'--- [' + (VAR_CYCLE_INDEX + 1) + '/' + {V('FILES')}.length + '] ' + {V('CURRENT_FILE')}")
        s.set_var("TITLE", f"{JS_BASENAME}({V('CURRENT_FILE')})", "expression")
        s.set_var("DONE", "false", "expression")
        s.set_var("ATTEMPT", "0", "int")

        def attempt() -> None:
            s.inc_var("ATTEMPT")
            s.log_expr(f"'Попытка ' + {V('ATTEMPT')} + ' из ' + {V('MAX_ATTEMPTS')}")
            s.set_var("DONE", "false", "expression")
            s.set_var("STATE", js("wait"))
            s.set_var("LAST_ERROR", js(""), "string")

            def upload() -> None:
                s.load(V("UPLOAD_URL"))
                s.sleep(2500)
                s.exist(SEL["login"], "NEED_LOGIN")
                s.if_(f"{V('NEED_LOGIN')} && !{V('DEMO')}",
                      lambda: s.fail('"Сессия VK слетела — нужен повторный вход"'))

                s.wait_exists(SEL["file_input"])
                s.open_file_dialog(V("CURRENT_FILE"))
                s.click(SEL["file_input"], require_visible=False)
                s.sleep(2000)

                s.page_js(JS_FILE_ATTACHED, "FILE_ATTACHED")
                s.if_(f"{V('FILE_ATTACHED')} != '1'", lambda: (
                    s.log("Поле файла не приняло путь — пробую программный клик"),
                    s.open_file_dialog(V("CURRENT_FILE")),
                    s.click(SEL["file_input"], js_click=True, require_visible=False),
                    s.sleep(2000),
                ))

                s.wait_visible(SEL["title"])
                s.clear_field(SEL["title"])
                s.type(SEL["title"], V("TITLE"))

                s.exist(SEL["description"], "HAS_DESC")
                s.if_(V("HAS_DESC"), lambda: (
                    s.clear_field(SEL["description"]),
                    s.type(SEL["description"], f"'Загружено автоматически: ' + {V('TITLE')}"),
                ))

                s.if_(V("WALLPOST"), lambda: (
                    s.exist(SEL["wallpost"], "HAS_WALL"),
                    s.if_(V("HAS_WALL"), lambda: s.click(SEL["wallpost"])),
                ))

                s.sleep(1500)
                s.click(SEL["submit"])

                s.set_var("WAIT", "0", "int")
                s.set_var("WAIT_MAX", f"parseInt({V('UPLOAD_TIMEOUT_MS')}) / 3000", "expression")
                s.while_(f"{V('STATE')} == 'wait' && parseInt({V('WAIT')}) < parseInt({V('WAIT_MAX')})",
                         lambda: (
                             s.sleep(3000),
                             s.page_js(JS_STATE, "STATE"),
                             s.inc_var("WAIT"),
                         ))

                s.if_(f"{V('STATE')} == 'ok'", lambda: (
                    s.set_var("DONE", "true", "expression"),
                    s.log("Загружено."),
                ))
                s.if_(f"{V('STATE')} == 'err'", lambda: (
                    s.set_var("LAST_ERROR", js("VK сообщил об ошибке загрузки"), "string"),
                    s.log("VK сообщил об ошибке загрузки."),
                ))
                s.if_(f"{V('STATE')} == 'wait'", lambda: (
                    s.set_var("LAST_ERROR", js("истекло время ожидания загрузки"), "string"),
                    s.log("Истекло время ожидания загрузки."),
                ))

            s.ignore_errors(upload)
            s.if_(f"!{V('DONE')}", lambda: s.sleep(5000))

        s.while_(f"parseInt({V('ATTEMPT')}) < parseInt({V('MAX_ATTEMPTS')}) && !{V('DONE')}",
                 attempt)

        def on_ok() -> None:
            s.inc_var("OK_COUNT")
            s.append_file(V("REPORT_FILE"), f"'OK   ' + {V('CURRENT_FILE')} + '\\r\\n'")
            s.log_expr(f"'OK: ' + {V('CURRENT_FILE')}")

        def on_fail() -> None:
            s.inc_var("FAIL_COUNT")
            s.append_file(V("REPORT_FILE"),
                          f"'FAIL ' + {V('CURRENT_FILE')} + ' | ' + {V('LAST_ERROR')} + '\\r\\n'")
            s.log_expr(f"'ОШИБКА: ' + {V('CURRENT_FILE')} + ' — ' + {V('LAST_ERROR')}")

        s.if_(V("DONE"), on_ok)
        s.if_(f"!{V('DONE')}", on_fail)

    def each_item() -> None:
        # пустые строки в списке пропускаем
        s.if_(f"String({V('CURRENT_FILE')}).replace(/^\\s+|\\s+$/g,'').length > 0",
              process_file)
        s.if_(f"String({V('CURRENT_FILE')}).replace(/^\\s+|\\s+$/g,'').length == 0",
              lambda: s.log("Пустая строка в списке — пропускаю"))

    s.log("Начинаю загрузку...")
    s.foreach(V("FILES"), "CURRENT_FILE", each_item)

    s.log_expr(f"'Готово. Успешно: ' + {V('OK_COUNT')} + ', с ошибкой: ' + {V('FAIL_COUNT')}")
    s.log_expr(f"'Отчёт: ' + {V('REPORT_FILE')}")
    s.success(f"'Загрузка завершена: OK=' + {V('OK_COUNT')} + ', FAIL=' + {V('FAIL_COUNT')}")

    s.raw("})!")
    return s.text()


HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<BrowserAutomationStudioProject>
    <Script><![CDATA[@@SCRIPT@@]]></Script>
    <ModuleInfo><![CDATA[{
}
]]></ModuleInfo>
    <Modules/>
    <EmbeddedData><![CDATA[[]]]></EmbeddedData>
    <DatabaseId></DatabaseId>
    <Schema></Schema>
    <ConnectionIsRemote>false</ConnectionIsRemote>
    <ConnectionServer></ConnectionServer>
    <ConnectionPort></ConnectionPort>
    <ConnectionLogin></ConnectionLogin>
    <ConnectionPassword></ConnectionPassword>
    <HideDatabase>false</HideDatabase>
    <DatabaseAdvanced>true</DatabaseAdvanced>
    <DatabaseAdvancedDisabled>false</DatabaseAdvancedDisabled>
    <ScriptName>VK Video Uploader</ScriptName>
    <ScriptVersion>1.0.0</ScriptVersion>
    <EngineVersion>29.3.1</EngineVersion>
    <ProtectionStrength>4</ProtectionStrength>
    <UnusedModules>URL;InMail;ThreadSync;PhoneVerification;ClickCaptcha</UnusedModules>
    <SettingsWorker>EnableWidevine=true
EnableSafeBrowsing=true
EnableVariations=true
EnableComponents=true
AutostartDebug=false
SaveBrowserLog=false
ProfilesCaching=true
MaxBrowserStartSimultaneously=3
MinFreeMemoryToStartBrowser=1500
MinUnusedCpu=35
IsMaximized=true
Restart=true
UseHumanLikeMouseMoves=true
DebugToolbox=false
DebugScenario=false
Languages=&quot;&quot;
Modules=&quot;&quot;
Helper=&quot;disable&quot;
Canvas=&quot;enable&quot;
Audio=&quot;enable&quot;
QUIC=&quot;enable&quot;
MaxFPS=90
Webrtc=&quot;enable&quot;
Webgl=&quot;enable&quot;
Detector=true
</SettingsWorker>
    <ModulesMetaJson>{
    &quot;Archive&quot;: false,
    &quot;FTP&quot;: false,
    &quot;Excel&quot;: false,
    &quot;SQL&quot;: false,
    &quot;ReCaptcha&quot;: false,
    &quot;FunCaptcha&quot;: false,
    &quot;HCaptcha&quot;: false,
    &quot;SmsReceive&quot;: false,
    &quot;Checksum&quot;: false,
    &quot;MailDeprecated&quot;: false
}
</ModulesMetaJson>
    <ScriptIcon></ScriptIcon>
    <IsCustomIcon>false</IsCustomIcon>
    <HideBrowsers>false</HideBrowsers>
    <IntegrateScheduler>false</IntegrateScheduler>
    <SingleInstance>false</SingleInstance>
    <CopySilent>false</CopySilent>
    <IsEnginesInAppData>false</IsEnginesInAppData>
    <CompileType>NoProtection</CompileType>
    <AvailableLanguages>en,ru</AvailableLanguages>
    <OutputTitle1 en="First Results" ru="Первые результаты"/>
    <OutputTitle2 en="Second Results" ru="Вторые результаты"/>
    <OutputTitle3 en="Third Results" ru="Третьи результаты"/>
    <OutputTitle4 en="Fourth Results" ru="Четвёртые результаты"/>
    <OutputTitle5 en="Fifth Results" ru="Пятые результаты"/>
    <OutputTitle6 en="Sixth Results" ru="Шестые результаты"/>
    <OutputTitle7 en="Seventh Results" ru="Седьмые результаты"/>
    <OutputTitle8 en="Eighth Results" ru="Восьмые результаты"/>
    <OutputTitle9 en="Ninth Results" ru="Девятые результаты"/>
    <OutputVisible1>1</OutputVisible1>
    <OutputVisible2>0</OutputVisible2>
    <OutputVisible3>0</OutputVisible3>
    <OutputVisible4>0</OutputVisible4>
    <OutputVisible5>0</OutputVisible5>
    <OutputVisible6>0</OutputVisible6>
    <OutputVisible7>0</OutputVisible7>
    <OutputVisible8>0</OutputVisible8>
    <OutputVisible9>0</OutputVisible9>
    <ModelList/>
</BrowserAutomationStudioProject>
"""


def main() -> None:
    script = build_script()
    if "]]>" in script:
        raise SystemExit("в скрипте встретилось ]]> — CDATA сломается")
    OUT.write_text(HEAD.replace("@@SCRIPT@@", script), encoding="utf-8")
    print(f"готово: {OUT}")
    print(f"строк скрипта: {script.count(chr(10)) + 1}, символов: {len(script)}")


if __name__ == "__main__":
    main()
