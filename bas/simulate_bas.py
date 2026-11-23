#!/usr/bin/env python3
"""Прогон BAS-скрипта без BAS: мини-рантайм на Node + поддельная страница.

Что проверяется по-настоящему:
  * управляющие конструкции (_if / _do / foreach / ignore errors) и то,
    во что препроцессор BAS разворачивает `)!`-колбэки;
  * порядок шагов загрузки, повторы, таймаут, подсчёт успехов и ошибок;
  * JS-сниппеты определения статуса (они выполняются как настоящий JS
    против поддельного DOM).

Чего не проверяет: реальную вёрстку VK и работу CDP — для этого нужен BAS
и живой аккаунт.

Запуск: python3 bas/simulate_bas.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_bas  # noqa: E402


RUNTIME = r"""
// ==================== мини-рантайм BAS ====================
let _RESULT = "";
let ONFAIL = null;
const BREAK = {__break: true};
const NEXT = {__next: true};
const DONE = {__success: true};

const LOGS = [];
const REPORT = [];
let FILES;
const VAR_LAST_ERROR_SENTINEL = null;

class FailError extends Error {
  constructor(msg) { super(String(msg)); this.__fail = true; }
}

function log(t) { LOGS.push(String(t)); }
function _result() { return _RESULT; }
function _set_result(v) { _RESULT = v; }
function _set_action_info(o) {}
function _set_if_expression(s) {}
function _sa(id) {}
function _stop_subscript_execution() {}

function make_frame(params, it) {
  return {
    params: params || {},
    it: it || 0,
    labels: ["function"],
    RemoveLabel(l) {
      const i = this.labels.indexOf(l);
      if (i >= 0) this.labels.splice(i, 1);
    },
    SetLabel(l) { if (this.labels.indexOf(l) < 0) this.labels.push(l); },
    HasLabel(l) { return this.labels.indexOf(l) >= 0; }
  };
}

const CYCLES = {Data: []};
CYCLES.Current = function () {
  return this.Data.length ? this.Data[this.Data.length - 1] : null;
};
CYCLES.Push = function (f) { this.Data.push(f); };
CYCLES.Pop = function () { return this.Data.pop(); };

function _iterator() {
  const c = CYCLES.Current();
  return c ? c.it : 0;
}
function _cycle_param(k) {
  const c = CYCLES.Current();
  return c ? c.params[k] : undefined;
}
function _next() { throw NEXT; }
function _break(n, flag) { throw BREAK; }

function _do(body, cont) { return _do_with_params({}, body, cont); }

function _do_with_params(params, body, cont) {
  const frame = make_frame(params, 0);
  CYCLES.Push(frame);
  try {
    for (;;) {
      frame.it++;
      if (frame.it > 100000) throw new Error("защита от бесконечного цикла");
      try {
        body();
      } catch (e) {
        if (e === BREAK) break;
        if (e === NEXT) continue;
        throw e;
      }
    }
  } finally {
    CYCLES.Pop();
  }
  if (cont) cont();
}

function _call(f, a, cont) {
  const prevOnFail = ONFAIL;
  const frame = make_frame({}, 0);
  frame.arguments = a;
  CYCLES.Push(frame);
  let err = null;
  try {
    f();
  } catch (e) {
    err = e;
  }
  if (!err) {
    CYCLES.Pop();
    ONFAIL = prevOnFail;
    if (cont) cont();
    return;
  }

  if (err === BREAK || err === NEXT || err === DONE) {
    CYCLES.Pop();
    ONFAIL = prevOnFail;
    throw err;
  }

  const handler = ONFAIL;
  if (handler) {
    let hErr = null;
    // как в BAS: _result() внутри обработчика = текст ошибки
    _RESULT = (err && err.message) ? err.message : String(err);
    if (typeof process !== "undefined" && process.env && process.env.BAS_TRACE) console.error("FAIL:", _RESULT, "\n", (err && err.stack) || err);
    try {
      handler(err);
    } catch (e2) {
      hErr = e2;
    }
    CYCLES.Pop();
    ONFAIL = prevOnFail;
    if (hErr) {
      if (hErr === BREAK || hErr === NEXT) { if (cont) cont(); return; }
      throw hErr;
    }
    if (cont) cont();
    return;
  }

  CYCLES.Pop();
  ONFAIL = prevOnFail;
  throw err;
}

function _if(c, f, n) {
  // как в BAS: _if(c,f,n) — выполнить f при истине, затем продолжить n.
  // Ошибки не глотаем: они всплывают до ближайшего _call/ignore errors.
  if (c) f();
  if (n) n();
}

function _on_fail(f) { ONFAIL = f; }
function script() {}

const ScriptWorker = {
  GetCurrentAction() { return 0; },
  SetFailFunction() {}, SetSuccessFunction() {},
  SetScript() {}, RunSubScript() {}, AbortSubscript() {}
};

// section(...) в BAS запускает «стадию» многопоточного движка;
// в симуляторе просто выполняем тело.
function section(threads, max_success, max_fail, max_time, body, callback) {
  body();
  if (callback) callback();
}

function _get_function_body(f) { return f; }
function rand(a, b) { return a; }

// ==================== поддельная страница ====================
const page_state = {
  url: "", file: null, title: "", desc: "", wallpost: false,
  submitted: false, ticks: 0, should_fail: false, ready: false
};

function reset_page(url) {
  page_state.url = url;
  page_state.file = null;
  page_state.title = "";
  page_state.desc = "";
  page_state.wallpost = false;
  page_state.submitted = false;
  page_state.ticks = 0;
  page_state.ready = true;
}

function is_mock(url) { return url.indexOf("mockvk") >= 0; }

function el(text, display) {
  return {innerText: text, style: {display: display || "block"}};
}

globalThis.document = {
  body: {
    get innerText() {
      if (!page_state.submitted) return "";
      if (page_state.ticks < 2) return "Загрузка " + (page_state.ticks * 33) + "%";
      return page_state.should_fail
        ? "Не удалось загрузить видео: сервер отклонил файл (демо)"
        : "Видео опубликовано: «" + page_state.title + "»";
    }
  },
  querySelector: function (sel) {
    if (sel === "#upload_success") {
      return page_state.submitted && page_state.ticks >= 2 && !page_state.should_fail
        ? el("Видео опубликовано: «" + page_state.title + "»") : null;
    }
    if (sel === "#upload_error") {
      return page_state.submitted && page_state.ticks >= 2 && page_state.should_fail
        ? el("Не удалось загрузить видео") : null;
    }
    if (sel === "input[type=file]") {
      return {files: page_state.file ? [{name: page_state.file}] : []};
    }
    return null;
  }
};
globalThis.getComputedStyle = function (e) { return {display: "block", visibility: "visible"}; };

// ==================== браузерные примитивы ====================
let _SELECTOR = "";
let pending_file = null;
let X = 10;
let Y = 10;

function selKind(sel) {
  sel = String(sel);
  if (sel.indexOf("type='file'") >= 0) return "file";
  if (sel.indexOf("video_title") >= 0 || sel.indexOf("'title'") >= 0) return "title";
  if (sel.indexOf("video_description") >= 0 || sel.indexOf("'description'") >= 0) return "desc";
  if (sel.indexOf("publish") >= 0 || sel.indexOf("Опубликовать") >= 0) return "submit";
  if (sel.indexOf("login") >= 0) return "login";
  if (sel.indexOf("wallpost") >= 0 || sel.indexOf("на стен") >= 0) return "wallpost";
  return "other";
}

function present(sel) {
  const k = selKind(sel);
  if (k === "login") return false;                  // считаем, что сессия есть
  if (k === "wallpost") return false;               // чекбокса нет
  if (k === "other") return false;
  return page_state.ready;
}

// Каждое "действие()!" BAS превращает в "действие(callback)".
// Ищем функцию среди аргументов и вызываем её после выполнения.
function withCb(args) {
  for (let i = args.length - 1; i >= 0; i--) {
    if (typeof args[i] === "function") return args[i];
  }
  return null;
}
function done(args, fn) {
  const cb = withCb(args);
  if (cb) return cb();
  return fn;
}

function load(...args) {
  reset_page(String(args[0]));
  return done(args);
}

function sleep(ms, ...rest) {
  // в BAS sleep(ms)!/sleep(ms, callback)
  const cb = typeof rest[rest.length - 1] === "function" ? rest.pop() : null;
  if (page_state.submitted) page_state.ticks++;
  if (cb) cb();
}

function wait_element(sel, ...args) {
  if (!present(sel)) throw new FailError("нет элемента " + sel);
  return done(args);
}

function wait_element_visible(sel, ...args) {
  if (!present(sel)) throw new FailError("не виден " + sel);
  return done(args);
}

function get_element_selector(sel, all) {
  _SELECTOR = sel;
  const obj = {
    nowait: function () { return obj; },
    style: function () { _RESULT = "block"; return obj; },
    exist: function (...a) { _RESULT = present(sel) ? "1" : "0"; return done(a, obj); },
    script: function (code, ...a) { _RESULT = "true"; return done(a, obj); },
    script2: function (code, ...a) {
      _RESULT = JSON.stringify({variables: "{}", is_success: true});
      return done(a, obj);
    },
    clarify: function (x, y, ...a) { return done(a, obj); },
    random_point: function (...a) { _RESULT = "10,10"; return done(a, obj); },
    move: function (...a) { return done(a, obj); },
    set: function (...a) { return done(a, obj); },
    length: function (...a) { _RESULT = "1"; return done(a, obj); },
    attr: function (...a) { _RESULT = ""; return done(a, obj); },
    text: function (...a) { _RESULT = ""; return done(a, obj); },
    render_base64: function (...a) { _RESULT = ""; return done(a, obj); }
  };
  return obj;
}

function _random_point(...a) { _RESULT = "10,10"; return done(a); }
function _clarify(...a) { return done(a); }
function move(...a) { return done(a); }

function mouse(x, y, ...a) {
  const k = selKind(_SELECTOR);
  if (k === "file") {
    if (!pending_file) throw new FailError("диалог выбора файла не был заполнен");
    page_state.file = pending_file;
  } else if (k === "submit") {
    page_state.submitted = true;
    page_state.ticks = 0;
    page_state.should_fail = /fail/i.test(String(page_state.file));
  } else if (k === "wallpost") {
    page_state.wallpost = !page_state.wallpost;
  }
  return done(a);
}

function _type(value, interval, ...a) {
  const k = selKind(_SELECTOR);
  if (k === "title") page_state.title = String(value);
  else if (k === "desc") page_state.desc = String(value);
  return done(a);
}

function open_file_dialog(path, ...a) {
  pending_file = String(path);
  return done(a);
}

function page() {
  return {
    script: function (code, ...a) {
      _RESULT = String(eval(code));
      return done(a, {});
    }
  };
}

function native(dll, fn, data) {
  const p = JSON.parse(data);
  if (fn === "readfile") return FILES_TEXT;
  if (fn === "writefile") { REPORT.push(p.value); return ""; }
  return "";
}

function fail(msg, flag) { throw new FailError(msg); }
function success(msg) {
  globalThis.__SUCCESS_MSG = String(msg);
  throw DONE;
}


// ==================== запуск ====================
globalThis.__FILES = __FILES__;
FILES = globalThis.__FILES;
// как настоящий BAS: readfile возвращает строку, а не массив
FILES_TEXT = FILES.join("\n");

try {
  __SCRIPT__
  globalThis.__RESULT_KIND = "end";
} catch (e) {
  if (e === DONE) globalThis.__RESULT_KIND = "success";
  else if (e && e.__fail) { globalThis.__RESULT_KIND = "fail"; globalThis.__FAIL_MSG = e.message; }
  else if (e && e.message) { globalThis.__RESULT_KIND = "error"; globalThis.__FAIL_MSG = e.message; }
  else { globalThis.__RESULT_KIND = "error"; globalThis.__FAIL_MSG = String(e); }
}

console.log(JSON.stringify({
  kind: globalThis.__RESULT_KIND,
  fail: globalThis.__FAIL_MSG || "",
  success: globalThis.__SUCCESS_MSG || "",
  logs: LOGS,
  report: REPORT,
  files: FILES
}, null, 1));
"""


def run_case(name: str, script_js: str, files: list[str]) -> dict:
    js = (RUNTIME
          .replace("__FILES__", json.dumps(files, ensure_ascii=False))
          .replace("__SCRIPT__", script_js))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        path = f.name
    r = subprocess.run(["node", path], capture_output=True, text=True, timeout=120)
    Path(path).unlink(missing_ok=True)
    if r.returncode != 0:
        raise SystemExit(f"[{name}] node упал:\n{r.stderr[:3000]}")
    return json.loads(r.stdout)


def main() -> int:
    xml = HERE / "vk_video_upload.xml"
    script = check_bas.extract_script(xml.read_text("utf-8"))
    prepared = check_bas.preprocess(script)

    failed = 0

    # --- сценарий 1: два нормальных файла ---
    res = run_case("два файла", prepared, ["C:\\\\vk\\\\demo1.mp4", "C:\\\\vk\\\\demo2.mp4"])
    print("=== сценарий: два нормальных файла ===")
    print("  итог:", res["kind"], "|", res["success"] or res["fail"])
    for line in res["logs"]:
        print("   ", line)
    print("  отчёт:")
    for line in res["report"]:
        print("    ", line.replace("\r", "").replace("\n", ""))
    ok = (res["kind"] == "success"
          and "OK=" in res["success"] and "OK=2" in res["success"]
          and len(res["report"]) == 2)
    print("  РЕЗУЛЬТАТ:", "OK" if ok else "ПРОВАЛ")
    if not ok:
        failed += 1

    # --- сценарий 2: файл с "fail" в имени -> повторы -> FAIL ---
    res2 = run_case("провал файла", prepared, ["C:\\\\vk\\\\fail-example.mp4"])
    print()
    print("=== сценарий: файл, который VK отклоняет ===")
    print("  итог:", res2["kind"], "|", res2["success"] or res2["fail"])
    for line in res2["logs"]:
        print("   ", line)
    attempts = sum(1 for x in res2["logs"] if x.startswith("Попытка"))
    ok2 = (res2["kind"] == "success" and "FAIL=1" in res2["success"] and attempts == 3)
    print("  попыток:", attempts)
    print("  РЕЗУЛЬТАТ:", "OK" if ok2 else "ПРОВАЛ")
    if not ok2:
        failed += 1

    # --- сценарий 3: смешанный список ---
    res_m = run_case("смешанный", prepared,
                     ["C:\\\\vk\\\\demo1.mp4", "C:\\\\vk\\\\fail-example.mp4",
                      "C:\\\\vk\\\\demo2.mp4"])
    print()
    print("=== сценарий: смешанный список (2 ок, 1 отклонён) ===")
    print("  итог:", res_m["kind"], "|", res_m["success"] or res_m["fail"])
    ok_m = (res_m["kind"] == "success"
            and "OK=2" in res_m["success"] and "FAIL=1" in res_m["success"]
            and len(res_m["report"]) == 3)
    for line in res_m["report"]:
        print("    ", line.replace("\r", "").replace("\n", ""))
    print("  РЕЗУЛЬТАТ:", "OK" if ok_m else "ПРОВАЛ")
    if not ok_m:
        failed += 1

    # --- сценарий 4: пустой список ---
    res3 = run_case("пустой список", prepared, [])
    print()
    print("=== сценарий: пустой список файлов ===")
    print("  итог:", res3["kind"], "|", (res3["success"] or res3["fail"])[:90])
    ok3 = res3["kind"] == "fail"
    print("  РЕЗУЛЬТАТ:", "OK" if ok3 else "ПРОВАЛ")
    if not ok3:
        failed += 1

    print()
    print("ПРОВАЛОВ:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
