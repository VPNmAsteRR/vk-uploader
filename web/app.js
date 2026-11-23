'use strict';

const $ = (id) => document.getElementById(id);
const state = { data: null, lastLogId: 0, busy: false, browseFiles: new Set() };

// ---------- API ----------
async function api(path, body) {
  const opts = body === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const txt = await r.text();
  let data;
  try { data = txt ? JSON.parse(txt) : {}; } catch { throw new Error('Сервер вернул не JSON: ' + txt.slice(0, 200)); }
  if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

function humanSize(n) {
  if (!n) return '—';
  const u = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? Math.round(v) : v.toFixed(1)) + ' ' + u[i];
}
function humanTime(sec) {
  if (!sec) return '';
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
function fmtTs(ts) {
  return new Date(ts * 1000).toLocaleTimeString('ru-RU', { hour12: false });
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function flash(el, text, kind) {
  if (!el) return;
  el.textContent = text;
  el.className = 'hint' + (kind ? ' ' + kind : '');
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.textContent = ''; el.className = 'hint'; }, 7000);
}

// ---------- рендер ----------
const STATUS_LABEL = {
  pending: 'в очереди', uploading: 'загрузка',
  done: 'готово', failed: 'ошибка', canceled: 'отменено',
};

function renderState(s) {
  state.data = s;
  const cfg = s.config;

  const mb = $('modeBadge');
  if (cfg.backend === 'demo') { mb.textContent = 'ДЕМО'; mb.className = 'badge badge-demo'; }
  else { mb.textContent = 'VK'; mb.className = 'badge badge-warn'; }

  const lb = $('loginBadge');
  if (cfg.backend === 'demo') { lb.textContent = 'без входа'; lb.className = 'badge badge-muted'; }
  else if (s.engine.login_needed) { lb.textContent = 'нужен вход'; lb.className = 'badge badge-warn'; }
  else { lb.textContent = 'браузерный профиль'; lb.className = 'badge badge-ok'; }

  const es = $('engineState');
  if (s.engine.running) { es.textContent = 'очередь работает'; es.className = 'pill pill-run'; }
  else if (s.engine.login_needed) { es.textContent = 'нужен вход в VK'; es.className = 'pill pill-warn'; }
  else { es.textContent = 'очередь остановлена'; es.className = 'pill pill-muted'; }

  $('loginBanner').classList.toggle('hidden', !(cfg.backend === 'browser' && s.engine.login_needed));
  $('btnStart').disabled = s.engine.running;
  $('btnStop').disabled = !s.engine.running;

  const st = s.stats;
  $('stats').innerHTML = [
    ['всего', st.total], ['в очереди', st.pending], ['в работе', st.uploading],
    ['готово', st.done], ['ошибок', st.failed], ['отменено', st.canceled],
  ].map(([k, v]) => `<span class="stat">${k}: <b>${v}</b></span>`).join('');

  renderJobs(s.jobs);
  renderLogs(s.logs);
}

function renderJobs(jobs) {
  const body = $('jobsBody');
  if (!jobs.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">Пока пусто. Добавь видео выше.</td></tr>';
    return;
  }
  body.innerHTML = jobs.map((j) => {
    const name = j.path.split('/').pop();
    const st = j.status;
    const pct = st === 'done' ? 100 : Number(j.progress || 0);
    const barCls = st === 'done' ? 'done' : (st === 'failed' ? 'err' : '');
    let sub;
    if (st === 'done') {
      sub = j.result_url
        ? `<a href="${esc(j.result_url)}" target="_blank" rel="noopener">открыть</a>`
        : 'опубликовано';
    } else if (st === 'pending') {
      sub = 'ожидает запуска';
    } else {
      sub = `${humanSize(j.bytes_sent || 0)} / ${humanSize(j.bytes_total || j.size_bytes || 0)}`;
    }
    const meta = [j.duration ? humanTime(j.duration) : '', j.width ? `${j.width}×${j.height}` : '']
      .filter(Boolean).join(' · ');
    const stage = j.stage && st === 'uploading' ? ` · <span class="stage">${esc(j.stage)}</span>` : '';

    const acts = [];
    if (['failed', 'canceled'].includes(st)) acts.push(`<button class="act" data-act="retry" data-id="${j.id}">Повторить</button>`);
    if (['pending', 'failed'].includes(st)) acts.push(`<button class="act" data-act="cancel" data-id="${j.id}">Отменить</button>`);
    if (st === 'uploading') acts.push(`<button class="act danger" data-act="cancel" data-id="${j.id}">Прервать</button>`);
    if (st !== 'uploading') acts.push(`<button class="act danger" data-act="delete" data-id="${j.id}">Удалить</button>`);

    return `<tr>
      <td class="c-id">${j.id}</td>
      <td>
        <div class="fname">
          <b title="${esc(j.title || name)}">${esc(j.title || name)}</b>
          <small>${esc(name)}${meta ? ' · ' + meta : ''}</small>
          ${j.error ? `<div class="job-err">${esc(j.error)}</div>` : ''}
        </div>
      </td>
      <td class="c-status"><span class="st st-${st}"><i class="dot"></i>${STATUS_LABEL[st] || st}</span></td>
      <td class="c-progress">
        <div class="bar ${barCls}"><i style="width:${Math.min(pct, 100)}%"></i></div>
        <div class="pct">${pct.toFixed(0)}% · ${sub}${stage}</div>
      </td>
      <td class="c-size">${humanSize(j.size_bytes)}</td>
      <td class="c-act"><div class="actions">${acts.join('')}</div></td>
    </tr>`;
  }).join('');
}

function renderLogs(logs) {
  const box = $('logBox');
  const fresh = logs.filter((l) => l.id > state.lastLogId).reverse();
  if (!fresh.length) return;
  for (const l of fresh) {
    const d = document.createElement('div');
    d.innerHTML = `<span class="ts">${fmtTs(l.ts)}</span>
      <span class="lv ${esc(l.level)}">${esc(l.level)}</span>
      ${l.job_id ? `<span class="jid">#${l.job_id}</span>` : ''}
      <span class="msg">${esc(l.message)}</span>`;
    box.prepend(d);
    state.lastLogId = Math.max(state.lastLogId, l.id);
  }
  while (box.childElementCount > 400) box.lastChild.remove();
}

// ---------- опрос ----------
async function poll() {
  try {
    renderState(await api('/api/state'));
  } catch (e) {
    console.warn('poll', e);
  } finally {
    setTimeout(poll, state.data && state.data.engine.running ? 700 : 2000);
  }
}

// ---------- действия ----------
async function act(path, body) {
  if (state.busy) return;
  state.busy = true;
  try {
    const r = await api(path, body);
    if (r.state) renderState(r.state);
    return r;
  } finally { state.busy = false; }
}

$('btnStart').onclick = () => act('/api/queue/start', {}).catch((e) => flash($('composerMsg'), e.message, 'err'));
$('btnStop').onclick = () => act('/api/queue/stop', {}).catch((e) => flash($('composerMsg'), e.message, 'err'));
$('btnClearDone').onclick = () => act('/api/queue/clear_finished', {}).catch((e) => alert(e.message));
$('btnClearLogs').onclick = async () => {
  await act('/api/logs/clear', {});
  $('logBox').innerHTML = '';
  state.lastLogId = 0;
};
$('btnScreenshot').onclick = async () => {
  try {
    const r = await api('/api/screenshot', {});
    if (r.ok) flash($('composerMsg'), 'Скриншот: ' + r.path, 'ok');
  } catch (e) { alert(e.message); }
};

$('jobsBody').addEventListener('click', async (ev) => {
  const b = ev.target.closest('button[data-act]');
  if (!b) return;
  const id = b.dataset.id, a = b.dataset.act;
  if (a === 'delete' && !confirm('Удалить задание #' + id + ' из списка?')) return;
  b.disabled = true;
  try { await act(`/api/jobs/${id}/${a}`, {}); }
  catch (e) { alert(e.message); }
  finally { b.disabled = false; }
});

// ---------- браузер / вход ----------
async function openBrowser() {
  try {
    await api('/api/browser/open', {});
    flash($('composerMsg'), 'Браузер открыт — войди в VK, затем нажми «Проверить вход»', 'ok');
  } catch (e) { flash($('composerMsg'), e.message, 'err'); }
}
async function checkLogin() {
  try {
    const r = await api('/api/browser/check_login', {});
    if (r.state) renderState(r.state);
    const ok = r.login && r.login.ok;
    flash($('composerMsg'), (r.login && r.login.message) || (ok ? 'вход есть' : 'входа нет'), ok ? 'ok' : 'err');
  } catch (e) { flash($('composerMsg'), e.message, 'err'); }
}
$('btnOpenBrowser').onclick = openBrowser;
$('btnBannerOpen').onclick = openBrowser;
$('btnBannerCheck').onclick = checkLogin;

// ---------- добавление ----------
async function probe() {
  const path = $('pathInput').value.trim();
  const out = $('probeOut');
  if (!path) { flash(out, 'Укажи путь к файлу', 'err'); return null; }
  try {
    const info = await api('/api/probe?path=' + encodeURIComponent(path));
    if (!info.ok) throw new Error(info.error || 'не удалось прочитать файл');
    out.className = 'hint ok';
    out.textContent = `${info.name} · ${humanSize(info.size)}` +
      (info.duration ? ` · ${humanTime(info.duration)}` : '') +
      (info.width ? ` · ${info.width}×${info.height}` : '');
    if (!$('titleInput').value.trim()) $('titleInput').value = info.name.replace(/\.[^.]+$/, '');
    return info;
  } catch (e) {
    out.className = 'hint err';
    out.textContent = e.message;
    return null;
  }
}
$('btnProbe').onclick = probe;

async function addJob() {
  const path = $('pathInput').value.trim();
  if (!path) { flash($('composerMsg'), 'Укажи файл', 'err'); return; }
  const payload = {
    path,
    title: $('titleInput').value.trim() || undefined,
    description: $('descInput').value.trim(),
    wallpost: $('wallpostInput').checked,
    is_private: $('privateInput').checked,
    no_comments: $('noCommentsInput').checked,
    keep_title: $('keepTitleInput').checked,
  };
  try {
    const r = await api('/api/enqueue', payload);
    if (r.state) renderState(r.state);
    const res = (r.results || [])[0] || {};
    if (res.state === 'duplicate') flash($('composerMsg'), 'Уже есть в очереди (# ' + res.id + ')', '');
    else if (res.state === 'added') {
      flash($('composerMsg'), 'Добавлено в очередь: #' + res.id, 'ok');
      $('pathInput').value = ''; $('titleInput').value = ''; $('descInput').value = ''; $('probeOut').textContent = '';
    } else flash($('composerMsg'), res.error || 'не добавлено', 'err');
  } catch (e) { flash($('composerMsg'), e.message, 'err'); }
}
$('btnAdd').onclick = addJob;
$('btnAddPick').onclick = () => { state.browseFiles.clear(); openBrowse($('pathInput').value.trim() || '~'); };

// ---------- модалки ----------
function openModal(id) { $(id).classList.remove('hidden'); }
function closeModal(id) { $(id).classList.add('hidden'); }
document.querySelectorAll('[data-close]').forEach((b) => b.onclick = () => closeModal(b.dataset.close));
document.querySelectorAll('.modal').forEach((m) => m.addEventListener('click', (e) => { if (e.target === m) m.classList.add('hidden'); }));

// ---------- выбор файлов ----------
let browseCwd = '';
async function openBrowse(dir) { openModal('browseModal'); await loadDir(dir); }
async function loadDir(dir) {
  const msg = $('browseMsg');
  try {
    const d = await api('/api/fs/list?dir=' + encodeURIComponent(dir));
    if (!d.ok) throw new Error(d.error);
    browseCwd = d.path;
    $('browsePath').value = d.path;
    state.browseFiles.clear();
    const rows = [];
    if (d.parent) rows.push(`<div class="fitem dir" data-path="${esc(d.parent)}"><span class="ico">↩</span><span class="fname-txt">..</span></div>`);
    for (const e of d.entries) {
      if (e.dir) rows.push(`<div class="fitem dir" data-path="${esc(e.path)}"><span class="ico">📁</span><span class="fname-txt">${esc(e.name)}</span></div>`);
      else rows.push(`<div class="fitem" data-path="${esc(e.path)}"><input type="checkbox" data-file="${esc(e.path)}"><span class="ico">🎬</span><span class="fname-txt">${esc(e.name)}</span><span class="fsize">${humanSize(e.size)}</span></div>`);
    }
    $('browseList').innerHTML = rows.join('') || '<div class="fitem">Пусто (видео не найдено)</div>';
  } catch (e) { flash(msg, e.message, 'err'); }
}
$('browseList').addEventListener('click', (ev) => {
  const cb = ev.target.closest('input[type=checkbox]');
  if (cb) {
    if (cb.checked) state.browseFiles.add(cb.dataset.file); else state.browseFiles.delete(cb.dataset.file);
    return;
  }
  const d = ev.target.closest('.fitem.dir');
  if (d) loadDir(d.dataset.path);
});
$('browseList').addEventListener('dblclick', (ev) => {
  const d = ev.target.closest('.fitem.dir');
  if (d) loadDir(d.dataset.path);
});
$('btnUp').onclick = async () => {
  try {
    const d = await api('/api/fs/list?dir=' + encodeURIComponent(browseCwd));
    if (d.parent) loadDir(d.parent);
  } catch (e) { flash($('browseMsg'), e.message, 'err'); }
};
$('btnGo').onclick = () => loadDir($('browsePath').value.trim());
$('browsePath').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadDir($('browsePath').value.trim()); });

$('btnBrowsePick').onclick = async () => {
  const files = [...state.browseFiles];
  if (!files.length) { flash($('browseMsg'), 'Ничего не отмечено', 'err'); return; }
  closeModal('browseModal');
  if (files.length === 1) {
    $('pathInput').value = files[0];
    await probe();
    return;
  }
  try {
    const r = await api('/api/enqueue', {
      paths: files,
      keep_title: false,
      description: $('descInput').value.trim(),
      wallpost: $('wallpostInput').checked,
      is_private: $('privateInput').checked,
      no_comments: $('noCommentsInput').checked,
    });
    if (r.state) renderState(r.state);
    const added = (r.results || []).filter((x) => x.state === 'added').length;
    const dup = (r.results || []).filter((x) => x.state === 'duplicate').length;
    flash($('composerMsg'), `Добавлено: ${added}, дубликатов: ${dup}`, 'ok');
  } catch (e) { flash($('composerMsg'), e.message, 'err'); }
};
$('btnBrowse').onclick = () => { state.browseFiles.clear(); openBrowse($('pathInput').value.trim() || '~'); };

// ---------- настройки ----------
function fillSettings(cfg) {
  $('cfgBackend').value = cfg.backend;
  $('cfgBinary').value = cfg.browser_binary;
  $('cfgDriver').value = cfg.chromedriver_path;
  $('cfgProfile').value = cfg.profile_dir;
  $('cfgUploadUrl').value = cfg.upload_url;
  $('cfgVkHome').value = cfg.vk_home;
  $('cfgTimeout').value = cfg.page_load_timeout;
  $('cfgLoginTimeout').value = cfg.login_timeout;
  $('cfgAttempts').value = cfg.max_attempts;
  $('cfgDelay').value = cfg.retry_base_delay;
  $('cfgUserAgent').value = cfg.user_agent;
  $('cfgHeadless').checked = !!cfg.headless;
  $('cfgWallpost').checked = !!cfg.wallpost;
  $('cfgNoComments').checked = !!cfg.no_comments;
  $('cfgSkipDup').checked = !!cfg.skip_duplicates;
  $('cfgDeleteAfter').checked = !!cfg.delete_after_upload;
  $('cfgKeepOpen').checked = !!cfg.keep_browser_open;
}
$('btnSettings').onclick = () => {
  if (state.data) fillSettings(state.data.config);
  openModal('settingsModal');
};
$('btnSaveSettings').onclick = async () => {
  const payload = {
    backend: $('cfgBackend').value,
    browser_binary: $('cfgBinary').value.trim(),
    chromedriver_path: $('cfgDriver').value.trim(),
    profile_dir: $('cfgProfile').value.trim(),
    upload_url: $('cfgUploadUrl').value.trim(),
    vk_home: $('cfgVkHome').value.trim(),
    page_load_timeout: Number($('cfgTimeout').value),
    login_timeout: Number($('cfgLoginTimeout').value),
    max_attempts: Number($('cfgAttempts').value),
    retry_base_delay: Number($('cfgDelay').value),
    user_agent: $('cfgUserAgent').value.trim(),
    headless: $('cfgHeadless').checked,
    wallpost: $('cfgWallpost').checked,
    no_comments: $('cfgNoComments').checked,
    skip_duplicates: $('cfgSkipDup').checked,
    delete_after_upload: $('cfgDeleteAfter').checked,
    keep_browser_open: $('cfgKeepOpen').checked,
  };
  try {
    const r = await api('/api/config', payload);
    if (r.state) renderState(r.state);
    closeModal('settingsModal');
    flash($('composerMsg'), 'Настройки сохранены', 'ok');
  } catch (e) { flash($('settingsMsg'), e.message, 'err'); }
};

// ---------- селекторы ----------
const SEL_LABELS = {
  file_input: 'Поле выбора файла',
  open_upload: 'Кнопка «Добавить видео»',
  title_input: 'Поле названия',
  description_input: 'Поле описания',
  submit: 'Кнопка «Опубликовать»',
  wallpost_toggle: 'Чекбокс «на стене»',
  no_comments_toggle: 'Чекбокс «без комментариев»',
  success: 'Признак успеха',
  error: 'Признак ошибки',
  progress_text: 'Процент загрузки',
};
async function openSelectors() {
  try {
    const s = await api('/api/selectors');
    const grid = $('selGrid');
    grid.innerHTML = Object.entries(s.selectors).map(([k, list]) => `
      <label>${esc(SEL_LABELS[k] || k)}
        <textarea data-sel="${esc(k)}">${esc(list.join('\n'))}</textarea>
      </label>`).join('');
    openModal('selectorsModal');
  } catch (e) { alert(e.message); }
}
$('btnSaveSelectors').onclick = async () => {
  const selectors = {};
  document.querySelectorAll('#selGrid textarea').forEach((t) => {
    const lines = t.value.split('\n').map((x) => x.trim()).filter(Boolean);
    if (lines.length) selectors[t.dataset.sel] = lines;
  });
  try {
    await api('/api/selectors', { selectors });
    closeModal('selectorsModal');
    flash($('composerMsg'), 'Селекторы сохранены', 'ok');
  } catch (e) { flash($('selMsg'), e.message, 'err'); }
};

// кнопка «Селекторы» добавляется рядом с «Настройки»
(() => {
  const b = document.createElement('button');
  b.className = 'btn btn-ghost';
  b.textContent = 'Селекторы';
  b.onclick = openSelectors;
  $('btnSettings').before(b);

  const d = document.createElement('button');
  d.className = 'btn btn-ghost';
  d.textContent = 'Диагностика';
  d.onclick = async () => {
    openModal('diagModal');
    $('diagOut').innerHTML = '';
    $('diagMsg').textContent = '';
  };
  $('btnSettings').before(d);
})();

// ---------- диагностика ----------
$('btnCalibrate').onclick = async () => {
  const out = $('diagOut');
  out.innerHTML = 'проверяю…';
  try {
    const r = await api('/api/browser/calibrate', {});
    const rep = r.report || {};
    const rows = Object.entries(rep.matches || {}).map(([kind, list]) => {
      const body = list.length
        ? list.map((x) => `<li><code>${esc(x.selector)}</code> — найдено ${x.count}
            ${x.visible ? '· видимый' : '· скрытый'}${x.sample && x.sample.text ? ' · «' + esc(x.sample.text) + '»' : ''}</li>`).join('')
        : '<li class="bad">ничего не найдено</li>';
      return `<div class="diag-block"><b>${esc(SEL_LABELS[kind] || kind)}</b><ul>${body}</ul></div>`;
    }).join('');
    out.innerHTML = `<p class="hint-inline">Страница: ${esc(rep.url)} — ${esc(rep.title || '')}</p>${rows}`;
  } catch (e) { out.innerHTML = `<p class="hint err">${esc(e.message)}</p>`; }
};

$('btnDump').onclick = async () => {
  const out = $('diagOut');
  out.innerHTML = 'собираю…';
  try {
    const r = await api('/api/browser/dump', {});
    const items = (r.dump && r.dump.items) || [];
    out.innerHTML = `<p class="hint-inline">Элементов: ${items.length}</p>` +
      items.map((e) => `<div class="diag-el">
        <code>${esc(e.selector || e.tag)}</code>
        <span class="tag">${esc(e.tag)}</span>
        ${e.type ? `<span class="tag">type=${esc(e.type)}</span>` : ''}
        ${e.placeholder ? `<span>placeholder: ${esc(e.placeholder)}</span>` : ''}
        ${e.aria ? `<span>aria: ${esc(e.aria)}</span>` : ''}
        ${e.text ? `<span>«${esc(e.text)}»</span>` : ''}
      </div>`).join('');
  } catch (e) { out.innerHTML = `<p class="hint err">${esc(e.message)}</p>`; }
};


// ---------- запуск ----------
(async function init() {
  try { renderState(await api('/api/state')); }
  catch (e) {
    document.body.insertAdjacentHTML('afterbegin',
      `<div class="hint err" style="padding:14px">Не удалось связаться с сервером: ${esc(e.message)}</div>`);
  }
  poll();
})();
