(function () {
  'use strict';

  const LS_BASE = {
    sessions: 'pulsenet.sessions.v1',
    msgs: 'pulsenet.msgs.v1.',
    current: 'pulsenet.current.v1',
    settings: 'pulsenet.settings.v1',
  };
  // Ключи localStorage получают префикс uid после проверки сессии: чаты разных
  // аккаунтов на одном браузере не смешиваются.
  let LS = { ...LS_BASE };
  const DEFAULTS = {
    compact: false,
    fontSize: 'm',
    autoScroll: true,
    enterSends: true,
    showReasoning: false,
    webSearch: false,
    model: '',
    ttsVoice: '',        // пусто = голос сервера по умолчанию
    updatedAt: 0,        // для синхронизации с сервером: побеждает более свежая копия
  };
  // Команды: серверные уходят в чат, local — выполняются в интерфейсе.
  const COMMANDS = [
    { name: 'search', arg: 'запрос', hint: 'поиск в интернете и ответ по источникам' },
    { name: 'fetch', arg: 'адрес', hint: 'прочитать страницу и ответить по ней' },
    { name: 'memory', arg: 'запрос', hint: 'найти в памяти факты и фрагменты' },
    { name: 'remember', arg: 'текст', hint: 'сохранить в память' },
    { name: 'chatmem', hint: 'чат → память: документ + факты, без дублей', local: true },
    { name: 'graph', hint: 'карта нейронов памяти', local: true },
    { name: 'new', hint: 'новый чат', local: true },
  ];
  const SUGGESTIONS = [
    { title: 'Что ты помнишь?', prompt: 'Что ты знаешь обо мне и моих предпочтениях?' },
    { title: 'Поиск в интернете', prompt: '/search ', fill: true, hint: 'введите запрос после команды' },
    { title: 'Сохранить в память', prompt: '/remember ', fill: true, hint: 'напишите, что запомнить' },
    { title: 'Чат в память', prompt: '/chatmem', local: true, hint: 'извлечь факты из всего диалога' },
    { title: 'Карта нейронов', prompt: '/graph', local: true, hint: 'как растёт память' },
  ];
  const PALETTE = ['#a6bfe0', '#4cc38a', '#e0a458', '#e5544f', '#c58af9', '#7fd2e8', '#f28db2', '#b5c77a'];
  const ICON_SEND = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4Z"/></svg>';
  const ICON_STOP = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
  const ICON_COPY = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const ICON_REFRESH = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 6.7"/><path d="M20 5v6h-6"/></svg>';
  const ICON_SPEAK = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>';
  const ICON_MIC = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>';

  const $ = (id) => document.getElementById(id);
  const els = {
    app: $('app'), sidebar: $('sidebar'), scrim: $('scrim'), statusDot: $('statusDot'),
    sessionList: $('sessionList'), searchInput: $('searchInput'), newChatBtn: $('newChatBtn'),
    chatTitle: $('chatTitle'), chatSub: $('chatSub'), modelChip: $('modelChip'), modelName: $('modelName'),
    renameBtn: $('renameBtn'), exportBtn: $('exportBtn'), clearBtn: $('clearBtn'), deleteBtn: $('deleteBtn'),
    drawerBtn: $('drawerBtn'), menuBtn: $('menuBtn'), messages: $('messages'), inner: $('inner'),
    scrollBottomBtn: $('scrollBottomBtn'), input: $('input'), sendBtn: $('sendBtn'), counter: $('counter'),
    hint: $('hint'), reasonBtn: $('reasonBtn'), webTool: $('webTool'), composer: $('composer'),
    voiceBtn: $('voiceBtn'), voiceBtnLabel: $('voiceBtnLabel'),
    sbStat: $('sbStat'), settingsBtn: $('settingsBtn'),
    modelSelect: $('modelSelect'), metrics: $('metrics'), metricSpeed: $('metricSpeed'),
    metricTokens: $('metricTokens'), metricTime: $('metricTime'),
    authUser: $('authUser'), userEmail: $('userEmail'), logoutBtn: $('logoutBtn'),
    cmdPop: $('cmdPop'),
    drawer: $('drawer'), drawerScrim: $('drawerScrim'), drawerClose: $('drawerClose'), drawerSub: $('drawerSub'),
    factsReload: $('factsReload'), factsMeta: $('factsMeta'), factsList: $('factsList'),
    searchForm: $('searchForm'), searchQuery: $('searchQuery'), searchResults: $('searchResults'),
    ingestText: $('ingestText'), ingestBtn: $('ingestBtn'), ingestOut: $('ingestOut'),
    graphReload: $('graphReload'), graphLive: $('graphLive'), graphStats: $('graphStats'),
    graphRelayout: $('graphRelayout'),
    graphStage: $('graphStage'), graphCanvas: $('graphCanvas'), graphEmpty: $('graphEmpty'),
    graphZoomIn: $('graphZoomIn'), graphZoomOut: $('graphZoomOut'), graphZoomReset: $('graphZoomReset'),
    graphFullscreen: $('graphFullscreen'),
    graphLegend: $('graphLegend'), graphGrowth: $('graphGrowth'), graphDetail: $('graphDetail'),
    statusReload: $('statusReload'), consolidateBtn: $('consolidateBtn'), statusGrid: $('statusGrid'),
    errorsBox: $('errorsBox'), sleepOut: $('sleepOut'),
    settingsDialog: $('settingsDialog'), setCompact: $('setCompact'), setFontSize: $('setFontSize'),
    setAutoScroll: $('setAutoScroll'), setEnterSends: $('setEnterSends'), setReasoning: $('setReasoning'),
    modelsReload: $('modelsReload'),
    exportAll: $('exportAll'), wipeAll: $('wipeAll'),
    mcpTokenValue: $('mcpTokenValue'), mcpTokenMeta: $('mcpTokenMeta'),
    mcpTokenHint: $('mcpTokenHint'), mcpTokenRotate: $('mcpTokenRotate'), mcpTokenRevoke: $('mcpTokenRevoke'),
    syncStatus: $('syncStatus'), syncNow: $('syncNow'),
    confirmDialog: $('confirmDialog'), confirmTitle: $('confirmTitle'), confirmText: $('confirmText'),
    confirmYes: $('confirmYes'), toast: $('toast'),
    imageDialog: $('imageDialog'), imageDialogImg: $('imageDialogImg'),
    imageDialogTitle: $('imageDialogTitle'), imageClose: $('imageClose'),
    imagePrev: $('imagePrev'), imageNext: $('imageNext'), imageStage: $('imageStage'),
  };

  let sessions = [];
  let messages = {};
  let currentId = null;
  let settings = { ...DEFAULTS };
  let busy = false;
  let abortCtl = null;
  let stopRequested = false;
  let statusBusy = false;
  let factsBusy = false;
  let titleBeforeEdit = '';
  let toastTimer = null;
  let paintTimer = null;
  let paintQueued = false;
  // Блоки рассуждений, обновление которых приостановлено, пока пользователь
  // выделяет в них текст (иначе DOM перерисовывается и выделение сбрасывается).
  const frozenReasonings = new Set();
  let lightboxItems = [];
  let lightboxIndex = 0;
  let drawerTab = null;
  let cmdItems = [];
  let cmdIndex = 0;
  // localStorage — локальный кеш. Источник истины — сервер (/api/chats),
  // поэтому один и тот же чат открывается на телефоне, компьютере и в PWA.
  const SYNC = { ready: false, busy: false, applying: false, watching: false,
                 rev: 0, pushTimer: null, events: null, streamTimer: null, watchTimer: null };
  // Метрики ответа: сервер не отдаёт usage, поэтому токены оцениваются по длине текста.
  const METRICS = { active: false, t0: 0, elapsed: 0, chars: 0, timer: null };

  function chatKey(s) { return (s && (s.chatId || s.id)) || 'default'; }

  // ---------- метрики генерации ----------

  function estimatedTokens() {
    return METRICS.chars ? Math.max(1, Math.round(METRICS.chars / 3.6)) : 0;
  }
  function renderMetrics() {
    if (!els.metrics) return;
    const tokens = estimatedTokens();
    const secs = METRICS.active
      ? Math.max(0, (performance.now() - METRICS.t0) / 1000)
      : METRICS.elapsed;
    const speed = secs > 0.05 ? tokens / secs : 0;
    els.metricSpeed.textContent = tokens ? speed.toFixed(1) : '—';
    els.metricTokens.textContent = tokens ? String(tokens) : '—';
    els.metricTime.textContent = secs ? secs.toFixed(1) : '—';
    els.metrics.classList.toggle('live', METRICS.active);
  }
  function startMetrics(seedChars) {
    METRICS.active = true;
    METRICS.t0 = performance.now();
    METRICS.elapsed = 0;
    METRICS.chars = Math.max(0, Number(seedChars) || 0);
    clearInterval(METRICS.timer);
    METRICS.timer = setInterval(renderMetrics, 200);
    renderMetrics();
  }
  function addMetricsText(text) {
    if (METRICS.active) METRICS.chars += String(text || '').length;
  }
  function stopMetrics() {
    if (!METRICS.active) return;
    METRICS.elapsed = (performance.now() - METRICS.t0) / 1000;
    METRICS.active = false;
    clearInterval(METRICS.timer);
    METRICS.timer = null;
    renderMetrics();
  }
  function captureMetrics() {
    const tokens = estimatedTokens();
    const secs = METRICS.elapsed || (METRICS.active
      ? Math.max(0, (performance.now() - METRICS.t0) / 1000) : 0);
    return { tokens, elapsed: Number(secs.toFixed(1)),
             tps: secs > 0.05 ? Number((tokens / secs).toFixed(1)) : 0 };
  }

  // ---------- дедупликация сообщений ----------
  // Сервер пишет время в секундах, браузер — в миллисекундах. Приводим к одному
  // виду и схлопываем копии с тем же id, а также старые копии от сломанного реаттача.

  function normalizeTime(value) {
    const t = Number(value) || 0;
    return t > 0 && t < 1e12 ? t * 1000 : t;
  }
  function mergeImages(a, b) {
    const out = [];
    const seen = new Set();
    for (const img of [...(Array.isArray(a) ? a : []), ...(Array.isArray(b) ? b : [])]) {
      if (!img || typeof img.url !== 'string' || !img.url || seen.has(img.url)) continue;
      seen.add(img.url);
      out.push(img);
    }
    return out;
  }
  function dedupeMessages(list) {
    if (!Array.isArray(list)) return list;
    const seen = new Map(); // ключ → индекс в out, чтобы обновить готовое сообщение картинкой
    const out = [];
    for (const m of list) {
      if (!m || typeof m !== 'object') continue;
      // Пустой ответ-артефакт от старого параллельного стрима: только метка очереди.
      if (m.role === 'assistant' && !m.pending && !m.text && !m.reasoning &&
          !(m.tools || []).length && !(m.sources || []).length && !(m.images || []).length &&
          (m.indexPending || (!m.error && !m.note))) continue;
      m.time = normalizeTime(m.time);
      const key = m.id ? 'id:' + m.id
        : 'ct:' + (m.role || '') + '|' + m.time + '|' + String(m.text || '');
      const known = seen.get(key);
      if (known !== undefined) {
        // Тот же id, но сообщение могло позже заполниться текстом/картинкой.
        const prev = out[known];
        const imgs = mergeImages(prev.images, m.images);
        const longer = String(m.text || '').length > String(prev.text || '').length ||
          (!prev.text && m.text);
        if (imgs.length) out[known] = { ...(longer ? m : prev), images: imgs };
        else if (longer) out[known] = m;
        continue;
      }
      // Следствие старого реаттача: один и тот же ответ под разными id, созданными
      // watchRemote() по несколько раз на одно state-changed. Ищем такую копию по
      // всему списку (не только по соседям) — копии могут быть разнесены
      // другими сообщениями.
      if (m.role === 'assistant') {
        const twin = out.find((p) => p.role === 'assistant' &&
          String(p.text || '') === String(m.text || '') &&
          Math.abs(p.time - m.time) < 600000);
        if (twin) {
          const idx = out.indexOf(twin);
          const imgs = mergeImages(twin.images, m.images);
          const longer = String(m.text || '').length > String(twin.text || '').length ||
            (!twin.text && m.text);
          if (imgs.length) {
            out[idx] = { ...(longer ? m : twin), images: imgs };
          } else if (longer) {
            out[idx] = m;
          }
          continue;
        }
      }
      seen.set(key, out.length);
      out.push(m);
    }
    return out;
  }

  // ---------- storage ----------

  function readRaw(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  }
  function writeRaw(key, value) {
    try { localStorage.setItem(key, value); return true; } catch { return false; }
  }
  function readJSON(key, fallback) {
    const raw = readRaw(key);
    if (!raw) return fallback;
    try { return JSON.parse(raw); } catch { return fallback; }
  }
  function writeJSON(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; }
    catch { toast('Локальное хранилище заполнено — старые сообщения могут не сохраниться'); return false; }
  }
  function newId() {
    return 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }
  function newSession(title) {
    const now = Date.now();
    return { id: newId(), chatId: '', title: title || 'Новый чат', created: now, updated: now };
  }
  function saveSessions() {
    writeJSON(LS.sessions, sessions);
    if (currentId) writeRaw(LS.current, currentId);
    schedulePush();
  }
  function saveMessages(id) {
    if (!id || !Array.isArray(messages[id])) return;
    writeJSON(LS.msgs + id, messages[id].slice(-300));
    schedulePush();
  }
  function loadState() {
    const stored = readJSON(LS.sessions, []);
    sessions = Array.isArray(stored) ? stored.filter((s) => s && s.id) : [];
    for (const s of sessions) {
      if (!s.chatId) s.chatId = s.id;
      if (!s.title) s.title = 'Новый чат';
      if (!s.created) s.created = s.updated || Date.now();
    }
    if (!sessions.length) sessions = [newSession()];
    currentId = readRaw(LS.current);
    if (!sessions.some((s) => s.id === currentId)) currentId = sessions[0].id;
    for (const s of sessions) {
      const list = readJSON(LS.msgs + s.id, []);
      const loaded = Array.isArray(list) ? list.map((m, i) => {
        if (!m || typeof m !== 'object') return null;
        if (!m.id) m.id = 'm' + s.id + '-' + i;   // ключ для серверной синхронизации
        if (m.pending) {
          m.pending = false;
          if (!m.note) m.note = m.text ? 'Ответ прерван' : 'Ответ не получен';
        }
        return m;
      }).filter(Boolean) : [];
      // Чистим последствия прежнего дублирования до того, как сообщения уйдут на сервер.
      messages[s.id] = dedupeMessages(loaded);
    }
    saveSessions();
  }

  function currentSession() {
    return sessions.find((s) => s.id === currentId) || null;
  }
  function ensureSession() {
    let s = currentSession();
    if (!s) {
      s = newSession();
      sessions.unshift(s);
      messages[s.id] = [];
      currentId = s.id;
      saveSessions();
    }
    if (!messages[s.id]) messages[s.id] = [];
    return s;
  }

  // ---------- helpers ----------

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function fmtTime(ts) {
    try { return new Date(ts || Date.now()).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }); }
    catch { return ''; }
  }
  function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }
  function isHttpUrl(value) {
    return typeof value === 'string' && /^https?:\/\//i.test(value);
  }
  function parseIso(value) {
    if (!value) return 0;
    // Сервер отдаёт микросекунды; Date понимает миллисекунды.
    const t = Date.parse(String(value).replace(/(\.\d{3})\d+/, '$1'));
    return Number.isNaN(t) ? 0 : t;
  }
  function renderMd(text) {
    if (window.marked && window.DOMPurify) {
      try { return DOMPurify.sanitize(marked.parse(text || '', { breaks: true, gfm: true })); }
      catch { /* fallback ниже */ }
    }
    return escapeHtml(text || '').replace(/\n/g, '<br>');
  }
  function copyText(text) {
    const value = String(text || '');
    if (!value) return;
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(value).then(() => toast('Скопировано')).catch(() => fallbackCopy(value));
    } else {
      fallbackCopy(value);
    }
  }
  function fallbackCopy(value) {
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('Скопировано'); }
    catch { toast('Не удалось скопировать'); }
    ta.remove();
  }
  function toast(text) {
    els.toast.textContent = text;
    els.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => els.toast.classList.remove('show'), 2600);
  }
  function download(name, text, type) {
    const blob = new Blob([text], { type: type || 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }
  function safeName(name) {
    return String(name || 'chat').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 80) || 'chat';
  }
  function ask(title, text, okLabel, danger = true) {
    return new Promise((resolve) => {
      els.confirmTitle.textContent = title;
      els.confirmText.textContent = text;
      els.confirmYes.textContent = okLabel || (danger ? 'Удалить' : 'Продолжить');
      els.confirmYes.classList.toggle('btn-danger', danger);
      els.confirmYes.classList.toggle('btn-primary', !danger);
      const done = () => {
        els.confirmDialog.removeEventListener('close', done);
        resolve(els.confirmDialog.returnValue === 'confirm');
      };
      els.confirmDialog.addEventListener('close', done);
      if (els.confirmDialog.open) els.confirmDialog.close();
      els.confirmDialog.showModal();
    });
  }
  function unauthorized(r) {
    if (r.status !== 401 || location.pathname === '/login') return;
    location.href = '/login';
  }
  async function api(method, path, body) {
    const r = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      unauthorized(r);
      throw new Error(data.error || ('HTTP ' + r.status));
    }
    return data;
  }

  // Сессия проверяется на сервере: без неё страница чата недоступна.
  async function checkAuth() {
    try {
      const r = await fetch('/auth/status', { credentials: 'same-origin', cache: 'no-store' });
      const j = await r.json();
      if (j.required && !j.authenticated) { location.href = '/login'; return false; }
      const prefix = j.uid ? (j.uid + ':') : '';
      LS = Object.fromEntries(Object.entries(LS_BASE).map(([k, v]) => [k, prefix + v]));
      const label = j.email || j.uid;
      if (label && els.userEmail && els.authUser) {
        els.userEmail.textContent = label;
        els.authUser.hidden = false;
      }
      return true;
    } catch (_) { return false; }
  }
  if (els.logoutBtn) {
    els.logoutBtn.addEventListener('click', async () => {
      const ok = await ask('Выйти из аккаунта?',
        'Чаты и память останутся на сервере и будут доступны после повторного входа.',
        'Выйти', false);
      if (!ok) return;
      try { await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' }); } catch (_) { /* ignore */ }
      location.href = '/login';
    });
  }

  // ---------- синхронизация чатов между устройствами ----------
  // Сервер хранит чаты пользователя; локальный localStorage работает как кеш.
  // На другой странице EventSource сообщает об изменениях, а активную генерацию
  // можно подсмотреть через /api/chat/stream с тем же chat_id.

  function setSyncStatus(text, bad) {
    if (!els.syncStatus) return;
    els.syncStatus.textContent = text;
    els.syncStatus.classList.toggle('bad', !!bad);
  }

  function schedulePush() {
    if (!SYNC.ready || SYNC.applying) return;
    clearTimeout(SYNC.pushTimer);
    SYNC.pushTimer = setTimeout(() => { syncPush(); }, 700);
  }

  async function syncPush() {
    if (!SYNC.ready || SYNC.applying || SYNC.busy) return;
    clearTimeout(SYNC.pushTimer);
    try {
      const payload = {
        sessions: sessions.map((s) => ({ ...s, chatId: chatKey(s) })),
        messages: Object.fromEntries(sessions.map((s) => [chatKey(s),
          dedupeMessages((messages[s.id] || []).map((m) => m.pending ? { ...m, draft: true } : { ...m }))])),
      };
      const data = await api('PUT', '/api/chats', payload);
      if (typeof data.rev === 'number') SYNC.rev = data.rev;
      setSyncStatus('сохранено ' + fmtTime(Date.now()));
    } catch (err) {
      setSyncStatus('ошибка сохранения: ' + (err.message || err), true);
    }
  }

  function mergeRemote(data) {
    let changed = false;
    for (const rs of data.sessions || []) {
      if (!rs || !rs.id) continue;
      const i = sessions.findIndex((s) => s.id === rs.id);
      if (i < 0) {
        sessions.push({ ...rs, chatId: chatKey(rs) });
        changed = true;
      } else if ((rs.updated || 0) > (sessions[i].updated || 0)) {
        sessions[i] = { ...sessions[i], title: rs.title, created: rs.created || sessions[i].created,
                        updated: rs.updated, chatId: chatKey(rs) };
        changed = true;
      }
    }
    for (const [cid, list] of Object.entries(data.messages || {})) {
      const sess = sessions.find((s) => chatKey(s) === cid);
      if (!sess) continue;
      let cur = messages[sess.id] || (messages[sess.id] = []);
      const deduped = dedupeMessages(cur);
      if (deduped.length !== cur.length) { changed = true; cur = messages[sess.id] = deduped; }
      for (const rm of list || []) {
        if (!rm || !rm.id) continue;
        rm.time = normalizeTime(rm.time);
        const i = cur.findIndex((m) => m.id === rm.id);
        if (rm.draft) {
          // Черновик другой вкладки: обновляем только уже идущий ответ, не создаём новый.
          if (i >= 0 && cur[i].pending) {
            cur[i].text = rm.text || '';
            cur[i].reasoning = rm.reasoning || '';
            schedulePaint(cur[i], i);
          }
          continue;
        }
        if (i < 0) {
          cur.push({ ...rm });
          changed = true;
        } else if (!rm.pending && (cur[i].pending || (rm.time || 0) >= normalizeTime(cur[i].time || 0))) {
          cur[i] = { ...rm };
          changed = true;
        }
      }
      // Сервер может вернуть старые копии, сохранённые до исправления реаттача.
      const clean = dedupeMessages(messages[sess.id] || []);
      if (clean.length !== (messages[sess.id] || []).length) {
        messages[sess.id] = clean;
        changed = true;
      }
    }
    if (!changed) return;
    saveSessions();
    for (const s of sessions) saveMessages(s.id);
    if (!sessions.some((s) => s.id === currentId)) currentId = sessions[0] && sessions[0].id;
    renderSessions();
    updateTitle();
    // Синхронизация не должна дёргать прокрутку: следуем за ответом, только если уже внизу.
    renderMessages({ keep: true });
  }

  async function syncPull() {
    if (!SYNC.ready || SYNC.busy) return;
    SYNC.busy = true;
    try {
      const data = await api('GET', '/api/chats' + (SYNC.rev ? '?rev=' + SYNC.rev : ''));
      if (data.unchanged) {
        setSyncStatus('актуально ' + fmtTime(Date.now()));
        return;
      }
      SYNC.rev = Number(data.rev) || 0;
      const emptyLocal = !sessions.some((s) => (messages[s.id] || []).length);
      SYNC.applying = true;
      try { mergeRemote(data); } finally { SYNC.applying = false; }
      // Новое устройство: открываем самый свежий чат с сервера, а не пустую локальную вкладку.
      if (emptyLocal && Array.isArray(data.sessions) && data.sessions.length) {
        const latest = data.sessions.slice().sort((a, b) => (b.updated || 0) - (a.updated || 0))[0];
        const found = sessions.find((s) => s.id === latest.id);
        if (found) {
          currentId = found.id;
          writeRaw(LS.current, currentId);
          renderSessions();
          updateTitle();
          renderMessages({ force: true });
        }
      }
      setSyncStatus('загружено ' + fmtTime(Date.now()));
    } catch (err) {
      setSyncStatus('сервер чатов недоступен', true);
    } finally {
      SYNC.busy = false;
    }
  }

  // Пачка state-changed от сервера не должна порождать параллельные стримы:
  // один таймер на все уведомления, а флаг watching выставляется до первого await.
  function requestWatch() {
    if (SYNC.watchTimer) return;
    SYNC.watchTimer = setTimeout(() => { SYNC.watchTimer = null; watchRemote(); }, 350);
  }

  async function watchRemote() {
    if (busy || SYNC.watching) return;
    SYNC.watching = true;   // синхронно, пока нет await — закрываем гонку replicated-стримов
    const s = currentSession();
    if (!s) { SYNC.watching = false; return; }
    const cid = chatKey(s);
    try {
      const r = await fetch('/api/chat/active?chat_id=' + encodeURIComponent(cid), { credentials: 'same-origin' });
      const j = await r.json();
      if (!j.active || busy) return;
      const list = messages[s.id] || (messages[s.id] = []);
      dedupeMessages(list);
      // Главное исправление дублей: ищем ответ по id активной задачи сервера.
      // Раньше брался «последний в списке» и создавался новый bot с новым id —
      // каждый вызов watchRemote() добавлял ещё одну копию одного и того же ответа.
      let bot = list.find((m) => m && m.id === j.assistant_msg_id);
      let idx = bot ? list.indexOf(bot) : -1;
      if (!bot) {
        bot = list.find((m) => m && m.role === 'assistant' && m.pending);
        if (bot) idx = list.indexOf(bot);
      }
      if (!bot) {
        bot = { id: j.assistant_msg_id || newId(), role: 'assistant', text: '', reasoning: '',
                time: Date.now(), pending: true, tools: [], sources: [], images: [] };
        const userIdx = j.user_msg_id ? list.findIndex((m) => m && m.id === j.user_msg_id) : -1;
        if (userIdx >= 0) { list.splice(userIdx + 1, 0, bot); idx = userIdx + 1; }
        else { list.push(bot); idx = list.length - 1; }
      }
      busy = true;
      abortCtl = new AbortController();
      startMetrics(bot.text.length);
      updateComposer();
      renderMessages({ force: true });
      let finished = false;
      await streamChat(null, (ev) => {
        if (typeof ev.token === 'string') { bot.text += ev.token; addMetricsText(ev.token); schedulePaint(bot, idx); }
        if (typeof ev.reasoning === 'string') { bot.reasoning += ev.reasoning; schedulePaint(bot, idx); }
        if (ev.tool) { upsertTool(bot, ev.tool); schedulePaint(bot, idx); }
        if (Array.isArray(ev.todo)) { bot.todos = ev.todo; schedulePaint(bot, idx); }
        if (ev.trace) { bot.trace = { ...(bot.trace || {}), ...ev.trace }; schedulePaint(bot, idx); }
        if (Array.isArray(ev.sources)) { addSources(bot, ev.sources); schedulePaint(bot, idx); }
        if (ev.image) { addImage(bot, ev.image); schedulePaint(bot, idx); }
        if (ev.error) bot.error = String(ev.error);
        if (ev.done) {
          if (!bot.text && ev.done.answer) bot.text = ev.done.answer;
          bot.pending = false;
          bot.gen = captureMetrics();
          bot.indexPending = Number(ev.done.index_pending) || 0;
        feedTts(bot, true);
          finished = true;
        }
      }, abortCtl.signal, '/api/chat/stream?chat_id=' + encodeURIComponent(cid), 'GET');
      if (finished) toast('Подключился к ответу, начатому на другом устройстве');
    } catch (_) {
      // Ответ уже закончился — финальный текст придёт через state-changed.
    } finally {
      stopMetrics();
      busy = false;
      abortCtl = null;
      SYNC.watching = false;
      updateComposer();
      saveMessages(s.id);
      syncPull();
    }
  }

  function startStateEvents() {
    if (SYNC.events || typeof EventSource === 'undefined') return;
    try {
      SYNC.events = new EventSource('/api/chats/events', { withCredentials: true });
      SYNC.events.addEventListener('state-changed', () => { syncPull().then(requestWatch); });
    } catch (_) { /* браузер без EventSource — останется периодическая проверка */ }
  }

  // ---------- токен MCP ----------

  async function loadMcpToken() {
    if (!els.mcpTokenValue) return;
    try {
      const j = await api('GET', '/api/mcp/token');
      const shown = j.token || (j.active ? '•••••••• ' + (j.fingerprint || '') : 'нет активного токена');
      els.mcpTokenValue.textContent = shown;
      els.mcpTokenValue.title = j.token || '';
      els.mcpTokenValue.classList.toggle('empty', !j.token);
      els.mcpTokenMeta.textContent = j.owner ? 'владелец (.env)' : (j.active ? 'активен' : 'не выдан');
      if (els.mcpTokenHint) els.mcpTokenHint.textContent = j.hint ||
        'Токен внешнего ИИ под вашим аккаунтом. Новый токен отключает прежние подключения.';
      if (els.mcpTokenRotate) els.mcpTokenRotate.disabled = !j.can_rotate;
      if (els.mcpTokenRevoke) els.mcpTokenRevoke.disabled = !j.can_rotate || !j.active;
    } catch (err) {
      els.mcpTokenMeta.textContent = 'недоступно: ' + (err.message || err);
    }
  }

  async function rotateMcpToken() {
    if (!(await ask('Выпустить новый ключ MCP?',
      'Старый токен перестанет работать: все подключённые внешние ИИ нужно обновить.', 'Выпустить'))) return;
    try {
      const j = await api('POST', '/api/mcp/token', {});
      els.mcpTokenValue.textContent = j.token || '';
      els.mcpTokenValue.title = j.token || '';
      els.mcpTokenValue.classList.toggle('empty', !j.token);
      toast('Новый токен выдан. Скопируйте его сейчас.');
      loadMcpToken();
    } catch (err) {
      toast('Ошибка: ' + (err.message || err));
    }
  }

  async function revokeMcpToken() {
    if (!(await ask('Отозвать ключ MCP?', 'Все внешние подключения этого аккаунта будут разорваны.', 'Отозвать'))) return;
    try {
      await api('DELETE', '/api/mcp/token');
      toast('Токен отозван');
      loadMcpToken();
    } catch (err) {
      toast('Ошибка: ' + (err.message || err));
    }
  }

  // ---------- settings ----------

  function loadSettings() {
    settings = { ...DEFAULTS, ...(readJSON(LS.settings, {}) || {}) };
  }
  function saveSettings(localOnly) {
    settings.updatedAt = Date.now();
    writeJSON(LS.settings, settings);
    if (!localOnly) scheduleSettingsPush();
  }

  // Настройки живут и в браузере, и на сервере (settings.sqlite по uid). Сервер — источник
  // истины после перезапуска/смены устройства; localStorage даёт мгновенную реакцию и офлайн.
  const SET_SYNC = { timer: null, busy: false, applying: false };
  function scheduleSettingsPush() {
    clearTimeout(SET_SYNC.timer);
    SET_SYNC.timer = setTimeout(pushSettings, 800);
  }
  async function pushSettings() {
    if (SET_SYNC.applying || SET_SYNC.busy) return;
    clearTimeout(SET_SYNC.timer);
    SET_SYNC.busy = true;
    try {
      const data = await api('PUT', '/api/settings', settings);
      if (data && Number(data.updatedAt) > Number(settings.updatedAt || 0)) {
        settings.updatedAt = Number(data.updatedAt);
        writeJSON(LS.settings, settings);
      }
    } catch (e) { /* офлайн: локальная копия уже сохранена */ }
    finally { SET_SYNC.busy = false; }
  }
  async function pullSettings() {
    if (SET_SYNC.applying) return;
    try {
      const data = await api('GET', '/api/settings');
      const remote = data && data.settings;
      if (!remote || !(Number(remote.updatedAt) > Number(settings.updatedAt || 0))) return;
      SET_SYNC.applying = true;
      settings = { ...DEFAULTS, ...remote };
      writeJSON(LS.settings, settings);
      applySettings();
      applyVoiceButton();
    } catch (e) { /* сервер недоступен — остаёмся на локальной копии */ }
    finally { SET_SYNC.applying = false; }
  }
  function applySettings() {
    document.body.dataset.fs = settings.fontSize || 'm';
    if (typeof setUIScale === 'function') setUIScale(settings.fontSize || 'm');
    document.body.classList.toggle('compact', !!settings.compact);
    els.reasonBtn.setAttribute('aria-pressed', settings.showReasoning ? 'true' : 'false');
    els.webTool.setAttribute('aria-pressed', settings.webSearch ? 'true' : 'false');
    if (els.modelSelect) els.modelSelect.value = settings.model || '';
    els.setCompact.checked = !!settings.compact;
    els.setFontSize.value = settings.fontSize || 'm';
    els.setAutoScroll.checked = !!settings.autoScroll;
    els.setEnterSends.checked = !!settings.enterSends;
    els.setReasoning.checked = !!settings.showReasoning;
    applyVoiceButton();
    els.hint.textContent = (settings.enterSends
      ? 'Enter — отправить · Shift+Enter — новая строка'
      : 'Ctrl+Enter — отправить · Enter — новая строка') + ' · / — команды · Ctrl K — новый чат';
  }

  // ---------- sessions ----------

  function groupLabel(ts) {
    const now = new Date();
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (ts >= start) return 'Сегодня';
    if (ts >= start - 86400000) return 'Вчера';
    if (ts >= start - 7 * 86400000) return 'На этой неделе';
    return 'Ранее';
  }

  function renderSessions() {
    const q = (els.searchInput.value || '').trim().toLowerCase();
    const items = sessions
      .filter((s) => !q || String(s.title || '').toLowerCase().includes(q))
      .sort((a, b) => (b.updated || b.created) - (a.updated || a.created));
    els.sessionList.innerHTML = '';
    if (!items.length) {
      els.sessionList.appendChild(el('div', 'sb-empty', q ? 'Ничего не найдено' : 'Чатов пока нет'));
      return;
    }
    let lastGroup = '';
    for (const s of items) {
      const g = groupLabel(s.updated || s.created || Date.now());
      if (g !== lastGroup) {
        lastGroup = g;
        els.sessionList.appendChild(el('div', 'sb-group', g));
      }
      const b = el('button', 'sess' + (s.id === currentId ? ' active' : ''));
      b.type = 'button';
      b.appendChild(el('span', 't', s.title || 'Новый чат'));
      if (busy && s.id === currentId) {
        const dot = el('span', 'dot');
        dot.title = 'Идёт ответ';
        b.appendChild(dot);
      }
      b.addEventListener('click', () => selectSession(s.id));
      attachMenu(b, (x, y) => showChatMenu(x, y, s.id));
      b.title = 'Нажмите, чтобы открыть · правый клик или долгое нажатие — действия';
      els.sessionList.appendChild(b);
    }
  }

  function selectSession(id) {
    if (busy) { toast('Сначала остановите текущий ответ'); return; }
    if (!sessions.some((s) => s.id === id)) return;
    currentId = id;
    saveSessions();
    renderSessions();
    updateTitle();
    renderMessages({ force: true });
    closeNav();
    els.input.focus();
  }

  function createChat() {
    if (busy) { toast('Сначала остановите текущий ответ'); return; }
    const cur = currentSession();
    if (cur && (messages[cur.id] || []).length === 0) {
      closeNav();
      els.input.focus();
      return;
    }
    const s = newSession();
    sessions.unshift(s);
    messages[s.id] = [];
    currentId = s.id;
    saveSessions();
    saveMessages(s.id);
    renderSessions();
    updateTitle();
    renderMessages({ force: true });
    closeNav();
    els.input.focus();
  }

  function updateTitle() {
    const s = currentSession();
    const title = s ? (s.title || 'Новый чат') : 'Новый чат';
    if (els.chatTitle.contentEditable !== 'true') els.chatTitle.textContent = title;
    document.title = title + ' · ТРИПЛЕКСИЯ';
    updateActions();
  }
  function updateActions() {
    const s = currentSession();
    const n = s ? (messages[s.id] || []).length : 0;
    els.renameBtn.disabled = !s;
    els.deleteBtn.disabled = !s;
    els.exportBtn.disabled = !s || n === 0;
    els.clearBtn.disabled = !s || n === 0;
  }

  function startRename() {
    const s = currentSession();
    if (!s) return;
    titleBeforeEdit = s.title || 'Новый чат';
    els.chatTitle.contentEditable = 'true';
    els.chatTitle.focus();
    const range = document.createRange();
    range.selectNodeContents(els.chatTitle);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
  function finishRename(save) {
    if (els.chatTitle.contentEditable !== 'true') return;
    els.chatTitle.contentEditable = 'false';
    const s = currentSession();
    if (!s) return;
    let title = save ? String(els.chatTitle.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60) : titleBeforeEdit;
    if (!title) title = titleBeforeEdit || 'Новый чат';
    s.title = title;
    s.updated = Date.now();
    saveSessions();
    renderSessions();
    updateTitle();
  }

  // ---------- messages ----------

  function isNearBottom() {
    return els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight < 80;
  }
  function scrollEnd() {
    els.messages.scrollTop = els.messages.scrollHeight;
    updateScrollButton();
  }
  function updateScrollButton() {
    els.scrollBottomBtn.hidden = isNearBottom();
  }

  function welcomeNode() {
    const w = el('div', 'welcome');
    w.appendChild(el('div', 'big', '✦'));
    w.appendChild(el('h2', '', 'Чем помочь?'));
    w.appendChild(el('p', '', 'Спросите что угодно. Память подскажет факты, интернет — свежие источники. ' +
      'Команды — по символу / в поле ввода.'));
    const grid = el('div', 'suggest-grid');
    for (const s of SUGGESTIONS) {
      const b = el('button', 'suggest');
      b.type = 'button';
      b.appendChild(el('b', '', s.title));
      b.appendChild(el('span', '', s.hint || s.prompt));
      b.addEventListener('click', () => {
        if (s.local) runLocal(s.prompt.replace('/', ''));
        else if (s.fill) fillInput(s.prompt);
        else send(s.prompt);
      });
      grid.appendChild(b);
    }
    w.appendChild(grid);
    return w;
  }

  function decorate(root) {
    if (window.hljs) {
      root.querySelectorAll('pre code').forEach((code) => {
        if (code.dataset.hl) return;
        try { hljs.highlightElement(code); code.dataset.hl = '1'; } catch { /* ignore */ }
      });
    }
    root.querySelectorAll('pre').forEach((pre) => {
      if (pre.parentElement && pre.parentElement.classList.contains('code-wrap')) return;
      const wrap = el('div', 'code-wrap');
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      const btn = el('button', 'copy-code-btn', 'Копировать');
      btn.type = 'button';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        copyText((pre.querySelector('code') || pre).textContent || '');
      });
      wrap.appendChild(btn);
    });
  }

  function safeImageUrl(value) {
    if (typeof value !== 'string') return '';
    const url = value.trim();
    if (/^https?:\/\//i.test(url) || url.startsWith('/images/') || /^data:image\//i.test(url)) return url;
    return '';
  }

  function renderLightbox() {
    const item = lightboxItems[lightboxIndex] || {};
    const url = safeImageUrl(item.url);
    if (els.imageDialogImg) {
      els.imageDialogImg.loading = 'eager';
      els.imageDialogImg.src = url;
      els.imageDialogImg.alt = item.prompt || 'Сгенерированное изображение';
    }
    if (els.imageDialogTitle) {
      els.imageDialogTitle.textContent = item.prompt
        ? String(item.prompt).slice(0, 140)
        : 'Изображение' + (lightboxItems.length > 1 ? ' ' + (lightboxIndex + 1) + ' из ' + lightboxItems.length : '');
      els.imageDialogTitle.title = item.prompt || '';
    }
    const many = lightboxItems.length > 1;
    if (els.imagePrev) els.imagePrev.hidden = !many;
    if (els.imageNext) els.imageNext.hidden = !many;
  }

  function openImageLightbox(items, index) {
    lightboxItems = (Array.isArray(items) ? items : [])
      .map((it) => (typeof it === 'string' ? { url: it } : it))
      .filter((it) => it && safeImageUrl(it.url));
    if (!lightboxItems.length || !els.imageDialog) return;
    lightboxIndex = Math.max(0, Math.min(Number(index) || 0, lightboxItems.length - 1));
    renderLightbox();
    if (!els.imageDialog.open) els.imageDialog.showModal();
  }

  function stepImage(delta) {
    if (lightboxItems.length < 2) return;
    lightboxIndex = (lightboxIndex + delta + lightboxItems.length) % lightboxItems.length;
    renderLightbox();
  }

  function paintBubble(bubble, m, live) {
    const prevReasoning = bubble.querySelector('details.reasoning');
    const wasOpen = !!(prevReasoning || {}).open;
    const keepFrozen = !!(prevReasoning && frozenReasonings.has(prevReasoning));
    const queuedReasoning = keepFrozen
      ? (prevReasoning.querySelector('.rbody')?.dataset.queued ?? m.reasoning)
      : m.reasoning;
    if (prevReasoning) frozenReasonings.delete(prevReasoning);
    bubble.innerHTML = '';
    delete bubble.dataset.liveSig;
    const isBot = m.role !== 'user';
    if (isBot && m.reasoning && settings.showReasoning) {
      const d = el('details', 'reasoning');
      d.open = wasOpen || (!!m.pending && !m.text);
      if (keepFrozen) {
        frozenReasonings.add(d);
        d.classList.add('frozen');
      }
      const sum = el('summary', '', 'Рассуждения');
      const copy = el('button', 'reasoning-copy', 'Копировать');
      copy.type = 'button';
      copy.title = 'Скопировать рассуждения';
      copy.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        copyText(d.querySelector('.rbody')?.textContent || '');
      });
      sum.appendChild(copy);
      d.appendChild(sum);
      const rbody = el('div', 'rbody', queuedReasoning);
      if (keepFrozen) rbody.dataset.queued = queuedReasoning;
      d.appendChild(rbody);
      bubble.appendChild(d);
    }
    if (isBot) {
      const trace = renderTrace(m);
      if (trace) bubble.appendChild(trace);
    }
    if (m.pending && !m.text) {
      const t = el('div', 'typing');
      t.append(el('span'), el('span'), el('span'));
      bubble.appendChild(t);
    } else if (m.text) {
      const md = el('div', 'markdown-body' + (live && m.pending ? ' streaming' : ''));
      md.innerHTML = renderMd(m.text);
      md.dataset.raw = m.text;
      bubble.appendChild(md);
      if (!live) decorate(md);
    }
    if (isBot && Array.isArray(m.images) && m.images.length) {
      const imgs = m.images.filter((img) => img && safeImageUrl(img.url));
      const gallery = el('div', 'image-attachments');
      imgs.forEach((img, i) => {
        const url = safeImageUrl(img.url);
        const fig = el('figure', 'image-attachment');
        const a = el('a', 'image-link');
        a.href = url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.title = 'Открыть просмотр';
        // Обычный клик — в модальный просмотр; Ctrl/средняя кнопка — как раньше,
        // в новой вкладке.
        a.addEventListener('click', (e) => {
          if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          openImageLightbox(imgs, i);
        });
        const pic = el('img', 'attachment');
        pic.src = url;
        pic.alt = (img.prompt || 'Сгенерированное изображение').slice(0, 160);
        pic.loading = 'lazy';
        a.appendChild(pic);
        fig.appendChild(a);
        if (img.seed !== undefined && img.seed !== null) {
          fig.appendChild(el('figcaption', 'image-meta',
            (img.backend ? img.backend + ' · ' : '') + 'seed ' + img.seed));
        }
        gallery.appendChild(fig);
      });
      if (gallery.childElementCount) bubble.appendChild(gallery);
    }
    if (isBot && Array.isArray(m.sources) && m.sources.length) {
      const box = el('div', 'sources');
      box.appendChild(el('div', 'sources-title', 'Источники'));
      for (const s of m.sources) {
        if (!s || !isHttpUrl(s.url)) continue;
        const a = el('a', 'src', (s.n ? '[' + s.n + '] ' : '') + (s.title || s.url));
        a.href = s.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.title = s.url;
        box.appendChild(a);
      }
      if (box.childElementCount > 1) bubble.appendChild(box);
    }
    if (m.error) bubble.appendChild(el('div', 'err-text', 'Ошибка: ' + m.error));
    if (m.note) bubble.appendChild(el('div', 'meta-line', m.note));
    if (m.indexPending) bubble.appendChild(el('div', 'meta-line', 'в очереди индексации: ' + m.indexPending));
  }

  function liveSignature(m) {
    const tools = (m.tools || []).map((t) => (t.status || '') + ':' + (t.label || t.name || '')).join('|');
    return [settings.showReasoning ? 1 : 0, m.pending ? 1 : 0, tools,
      (m.images || []).length, (m.sources || []).length,
      m.error || '', m.note || '', m.indexPending || 0, m.gen ? 1 : 0,
      JSON.stringify(m.todos || []), JSON.stringify(m.trace || null)].join('~');
  }

  // Во время стриминга перерисовываем только текст: так выделение и прокрутка
  // рассуждений не сбрасываются на каждой порции токенов.
  function updateLiveBubble(bubble, m) {
    const sig = liveSignature(m);
    if (bubble.dataset.liveSig !== sig) {
      paintBubble(bubble, m, true);
      bubble.dataset.liveSig = sig;
      return;
    }
    const d = bubble.querySelector('details.reasoning');
    if (d && settings.showReasoning) {
      const body = d.querySelector('.rbody');
      const next = m.reasoning || '';
      if (body && body.textContent !== next) {
        const sel = window.getSelection();
        if (sel && sel.toString() && d.contains(sel.anchorNode)) {
          frozenReasonings.add(d);
          d.classList.add('frozen');
        }
        body.dataset.queued = next;
        if (!frozenReasonings.has(d)) {
          body.textContent = next;
          delete body.dataset.queued;
        }
      }
    }
    const md = bubble.querySelector('.markdown-body');
    if (m.text) {
      if (!md) { paintBubble(bubble, m, true); bubble.dataset.liveSig = sig; return; }
      if (md.dataset.raw !== m.text) {
        md.innerHTML = renderMd(m.text);
        md.dataset.raw = m.text;
      }
      md.classList.toggle('streaming', !!m.pending);
    } else if (md) {
      paintBubble(bubble, m, true);
      bubble.dataset.liveSig = sig;
    }
  }

  function flushReasoning(d) {
    const body = d.querySelector('.rbody');
    if (body && body.dataset.queued != null) {
      body.textContent = body.dataset.queued;
      delete body.dataset.queued;
    }
    d.classList.remove('frozen');
    frozenReasonings.delete(d);
  }

  document.addEventListener('selectionchange', () => {
    if (!frozenReasonings.size) return;
    const sel = window.getSelection();
    for (const d of [...frozenReasonings]) {
      if (!d.isConnected) { frozenReasonings.delete(d); continue; }
      if (sel && sel.toString() && d.contains(sel.anchorNode)) continue;
      flushReasoning(d);
    }
  });

  function buildMessage(m, idx) {
    const isUser = m.role === 'user';
    const root = el('div', 'msg ' + (isUser ? 'user' : 'bot') +
      (m.error ? ' error' : '') + (m.pending ? ' pending' : ''));
    root.dataset.idx = String(idx);
    if (!isUser) root.appendChild(el('div', 'avatar-bot', '✦'));
    const body = el('div', 'msg-body');
    const bubble = el('div', 'bubble');
    if (isUser) bubble.textContent = m.text || '';
    else paintBubble(bubble, m, false);
    body.appendChild(bubble);

    const foot = el('div', 'msg-foot');
    foot.appendChild(el('span', 'time', fmtTime(m.time)));
    const stats = statsNode(m);
    if (stats) foot.appendChild(stats);
    if (!isUser && m.text && m.pending) {
      foot.appendChild(actButton('Озвучивать по ходу генерации', ICON_SPEAK, (b) => toggleSpeak(m, b)));
    }
    if (m.text && !m.pending) {
      foot.appendChild(actButton('Копировать', ICON_COPY, () => copyText(m.text)));
      if (!isUser) {
        foot.appendChild(actButton('Другой ответ', ICON_REFRESH, () => regenerate(idx)));
        foot.appendChild(actButton('Озвучить ответ', ICON_SPEAK, (b) => toggleSpeak(m, b)));
      }
    }
    body.appendChild(foot);
    root.appendChild(body);
    attachMessageMenu(root, idx);
    return root;
  }

  function renderMessages(opts) {
    opts = opts || {};
    const wasAtBottom = isNearBottom();
    // Перерисовка пересобирает ленту; без сохранения позиции браузер сбрасывает прокрутку в начало.
    const savedTop = els.messages.scrollTop;
    els.inner.innerHTML = '';
    const s = currentSession();
    const list = s ? (messages[s.id] || []) : [];
    if (!list.length) {
      els.inner.appendChild(welcomeNode());
    } else {
      list.forEach((m, i) => els.inner.appendChild(buildMessage(m, i)));
    }
    updateActions();
    if (opts.force || (wasAtBottom && settings.autoScroll)) scrollEnd();
    else els.messages.scrollTop = savedTop;
    updateScrollButton();
  }

  function updateCounter() {
    const n = (els.input.value || '').length;
    els.counter.textContent = n ? n + ' симв.' : '';
  }
  function autoSize() {
    els.input.style.height = 'auto';
    els.input.style.height = Math.min(els.input.scrollHeight, 200) + 'px';
  }
  function fillInput(text) {
    els.input.value = text;
    autoSize();
    updateCounter();
    updateCmdPop();
    els.input.focus();
  }
  function updateComposer() {
    els.sendBtn.classList.toggle('stop', busy);
    els.sendBtn.title = busy ? 'Остановить' : 'Отправить';
    els.sendBtn.setAttribute('aria-label', busy ? 'Остановить' : 'Отправить');
    els.sendBtn.innerHTML = busy ? ICON_STOP : ICON_SEND;
  }

  function schedulePaint(bot, idx) {
    if (paintQueued) return;
    paintQueued = true;
    clearTimeout(paintTimer);
    paintTimer = setTimeout(() => {
      paintQueued = false;
      const bubble = els.inner.querySelector('.msg[data-idx="' + idx + '"] .bubble');
      if (!bubble) return;
      const atBottom = isNearBottom();
      const savedTop = els.messages.scrollTop;
      updateLiveBubble(bubble, bot);
      feedTts(bot, false);
      if (atBottom && settings.autoScroll) scrollEnd();
      else els.messages.scrollTop = savedTop;
    }, 60);
  }

  // ---------- инструменты и источники в ответе ----------

  // ---------- трасса работы агента: шаги, карточки инструментов, план ----------

  const openTraceCards = new Set();
  const TRACE_BADGE = { web: 'веб', mem: 'память', img: 'картинка', skill: 'навык', note: 'заметка', plan: 'план', tool: 'инструмент' };
  const TRACE_GROUP = {
    web: ['web_search', 'web_fetch'],
    mem: ['memory_search', 'memory_remember', 'triplex_search', 'triplex_ingest'],
    img: ['image_generate'],
    skill: ['skill_list', 'skill_read', 'skill_write', 'skill_patch'],
    note: ['note_write', 'note_append', 'note_read', 'note_list'],
    plan: ['todo_write'],
  };
  const STOP_LABEL = {
    model_done: 'ответ готов', round_limit: 'лимит шагов', call_limit: 'лимит вызовов',
    failures: 'ошибки инструментов', no_progress: 'нет прогресса', cancelled: 'остановлено',
  };
  function traceKind(name) {
    for (const [kind, names] of Object.entries(TRACE_GROUP)) if (names.includes(name)) return kind;
    return 'tool';
  }
  function fmtMs(ms) {
    return ms < 1000 ? ms + ' мс' : (ms / 1000).toFixed(1) + ' с';
  }
  function renderPlan(todos) {
    const done = todos.filter((x) => x.status === 'done').length;
    const box = el('div', 'trace-plan');
    box.append(el('div', 'plan-title', 'План · ' + done + ' из ' + todos.length));
    const ul = el('ul', 'plan-list');
    for (const it of todos) {
      const st = it.status || 'pending';
      const li = el('li', 'plan-item ' + st);
      li.append(el('span', 'plan-mark', st === 'done' ? '●' : (st === 'in_progress' ? '◐' : '○')));
      li.append(el('span', 'plan-text', it.text || ''));
      ul.append(li);
    }
    box.append(ul);
    return box;
  }
  function renderCard(t) {
    const st = t.status || 'running';
    const kind = traceKind(t.name);
    const card = el('details', 'trace-card ' + st);
    if (t.sub) card.classList.add('nested');
    const id = t.id || (t.name + ':' + (t.label || ''));
    card.open = st === 'error' || openTraceCards.has(id);
    card.addEventListener('toggle', () => {
      if (card.open) openTraceCards.add(id); else openTraceCards.delete(id);
    });
    const sum = el('summary', 'card-sum');
    sum.append(el('span', 'card-kind', TRACE_BADGE[kind]));
    sum.append(el('span', 'card-label', t.label || t.name || 'инструмент'));
    sum.append(el('span', 'card-status', st === 'done' ? '✓' : (st === 'error' ? '✕' : '')));
    if (t.ms) sum.append(el('span', 'card-ms', fmtMs(t.ms)));
    card.append(sum);
    const body = el('div', 'card-body');
    if (t.detail) body.append(el('div', 'card-detail', t.detail));
    if (t.preview) body.append(el('pre', 'card-preview', t.preview));
    if (!t.detail && !t.preview) {
      body.append(el('div', 'card-detail muted', st === 'running' ? 'выполняется…' : 'подробностей нет'));
    }
    card.append(body);
    return card;
  }
  function renderTrace(m) {
    const tools = Array.isArray(m.tools) ? m.tools : [];
    const todos = Array.isArray(m.todos) ? m.todos : [];
    if (!tools.length && !todos.length) return null;
    const trace = m.trace || {};
    const wrap = el('div', 'trace' + (m.pending ? ' live' : ''));
    const totalMs = tools.reduce((a, t) => a + (t.ms || 0), 0);
    const steps = Math.max(trace.round || 0, ...tools.map((t) => t.step || 0));
    const head = el('div', 'trace-head');
    head.append(el('span', 'trace-title', 'Ход работы'));
    const meta = [];
    if (steps) meta.push('шагов ' + steps);
    meta.push('инструментов ' + tools.length);
    if (totalMs) meta.push(fmtMs(totalMs));
    head.append(el('span', 'trace-meta', meta.join(' · ')));
    if (trace.stop) head.append(el('span', 'trace-stop stop-' + trace.stop, STOP_LABEL[trace.stop] || trace.stop));
    else if (m.pending) head.append(el('span', 'trace-stop running', 'выполняется'));
    wrap.append(head);
    if (todos.length) wrap.append(renderPlan(todos));
    const groups = new Map();
    for (const t of tools) {
      const key = t.step || 0;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(t);
    }
    if (groups.size) {
      const list = el('ol', 'trace-steps');
      for (const [key, items] of groups) {
        const li = el('li', 'trace-step');
        li.append(el('span', 'step-dot'));
        const body = el('div', 'step-body');
        body.append(el('div', 'step-label', key ? 'Шаг ' + key : 'Инструменты'));
        const cards = el('div', 'trace-cards');
        for (const t of items) cards.append(renderCard(t));
        body.append(cards);
        li.append(body);
        list.append(li);
      }
      wrap.append(list);
    }
    return wrap;
  }

  function upsertTool(bot, t) {
    if (!t || !t.id) return;
    if (!Array.isArray(bot.tools)) bot.tools = [];
    const cur = bot.tools.find((x) => x.id === t.id);
    if (cur) Object.assign(cur, t);
    else bot.tools.push({ ...t });
  }
  function addSources(bot, list) {
    if (!Array.isArray(bot.sources)) bot.sources = [];
    for (const s of list) {
      if (!s || !isHttpUrl(s.url)) continue;
      if (!bot.sources.some((x) => x.url === s.url)) bot.sources.push({ ...s });
    }
  }
  function addImage(bot, img) {
    if (!img || typeof img.url !== 'string' || !img.url) return;
    if (!Array.isArray(bot.images)) bot.images = [];
    if (bot.images.some((x) => x.url === img.url)) return;
    bot.images.push({ ...img });
  }

  async function streamChat(body, onEvent, signal, path, method) {
    const url = path || '/api/chat';
    const verb = method || 'POST';
    const res = await fetch(url, {
      method: verb,
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: verb === 'POST' ? JSON.stringify(body) : undefined,
      signal,
    });
    if (!res.ok) {
      unauthorized(res);
      let msg = 'HTTP ' + res.status;
      try { const data = await res.json(); if (data && data.error) msg = data.error; } catch { /* ignore */ }
      throw new Error(msg);
    }
    if (!res.body) throw new Error('Сервер не отдал поток');
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let cut;
        while ((cut = buf.indexOf('\n\n')) >= 0) {
          const block = buf.slice(0, cut);
          buf = buf.slice(cut + 2);
          for (const line of block.split('\n')) {
            const t = line.trim();
            if (!t.startsWith('data:')) continue;
            const data = t.slice(5).trim();
            if (!data || data === '[DONE]') continue;
            let ev;
            try { ev = JSON.parse(data); } catch { continue; }
            onEvent(ev);
          }
        }
      }
    } finally {
      if (reader.releaseLock) reader.releaseLock();
    }
  }

  function runLocal(name) {
    if (name === 'graph') openDrawer('graph');
    else if (name === 'new') createChat();
    else if (name === 'chatmem') saveChatMemory(currentId, { command: true });
  }

  async function send(override, reuse) {
    if (busy) return;
    stopSpeech();
    if (voiceRec) stopVoice();
    const text = String(override !== undefined ? override : els.input.value).trim();
    if (!text && !reuse) return;
    const local = /^\/(graph|new|chatmem|впамять)\s*$/i.exec(text);
    if (local) {
      els.input.value = '';
      autoSize();
      updateCounter();
      hideCmdPop();
      const name = local[1].toLowerCase() === 'впамять' ? 'chatmem' : local[1].toLowerCase();
      runLocal(name);
      return;
    }
    const s = ensureSession();
    const list = messages[s.id] || (messages[s.id] = []);
    if (!list.length && (!s.title || s.title === 'Новый чат')) {
      s.title = text.replace(/\s+/g, ' ').slice(0, 48);
    }
    const user = reuse ? reuse.user : { id: newId(), role: 'user', text, time: Date.now() };
    const bot = reuse ? reuse.bot : { id: newId(), role: 'assistant', text: '', reasoning: '', time: Date.now(), pending: true, tools: [], sources: [], images: [] };
    if (reuse) {
      // Перегенерация переиспользует id: сервер перезапишет прежний ответ,
      // а не сохранит его второй копией.
      bot.text = ''; bot.reasoning = ''; bot.tools = []; bot.sources = [];
      bot.pending = true; bot.error = null; bot.note = ''; bot.indexPending = 0;
      bot.time = Date.now();
      delete bot.gen;
    } else list.push(user, bot);
    if (list.length > 300) list.splice(0, list.length - 300);
    s.updated = Date.now();
    els.input.value = '';
    autoSize();
    updateCounter();
    hideCmdPop();
    saveSessions();
    saveMessages(s.id);
    busy = true;
    stopRequested = false;
    abortCtl = new AbortController();
    startMetrics();
    // Пока ответ пишется, периодически отправляем черновик — другое устройство
    // увидит текст в потоке даже до финального сообщения от сервера.
    clearInterval(SYNC.streamTimer);
    SYNC.streamTimer = setInterval(() => { if (busy) syncPush(); }, 2000);
    renderSessions();
    updateTitle();
    renderMessages({ force: true });
    updateComposer();

    const botIdx = reuse ? reuse.idx : list.length - 1;
    let finished = false;

    try {
      const body = { message: text, chat_id: chatKey(s), web: !!settings.webSearch,
        user_msg_id: user.id, assistant_msg_id: bot.id };
      if (settings.model) body.model = settings.model;
      await streamChat(body, (ev) => {
        if (typeof ev.token === 'string') { bot.text += ev.token; addMetricsText(ev.token); schedulePaint(bot, botIdx); }
        if (typeof ev.reasoning === 'string') { bot.reasoning += ev.reasoning; schedulePaint(bot, botIdx); }
        if (ev.tool) { upsertTool(bot, ev.tool); schedulePaint(bot, botIdx); }
        if (Array.isArray(ev.todo)) { bot.todos = ev.todo; schedulePaint(bot, botIdx); }
        if (ev.trace) { bot.trace = { ...(bot.trace || {}), ...ev.trace }; schedulePaint(bot, botIdx); }
        if (Array.isArray(ev.sources)) { addSources(bot, ev.sources); schedulePaint(bot, botIdx); }
        if (ev.image) { addImage(bot, ev.image); schedulePaint(bot, botIdx); }
        if (ev.error) { bot.error = String(ev.error); bot.pending = false; }
        if (ev.done) {
          if (!bot.text && ev.done.answer) bot.text = ev.done.answer;
          bot.pending = false;
          bot.gen = captureMetrics();
          bot.indexPending = Number(ev.done.index_pending) || 0;
        feedTts(bot, true);
          finished = true;
        }
      }, abortCtl.signal);
    } catch (err) {
      if (err && err.name === 'AbortError') {
        if (!bot.error) bot.note = stopRequested ? 'Остановлено' : 'Ответ прерван';
      } else {
        bot.error = err && err.message ? err.message : String(err);
      }
    } finally {
      stopMetrics();
      if (!bot.gen) bot.gen = captureMetrics();
      clearTimeout(paintTimer);
      paintQueued = false;
      if (bot.pending) bot.pending = false;
      for (const t of bot.tools || []) {
        if (t.status === 'running') t.status = finished ? 'done' : 'error';
      }
      if (!finished && !bot.error && !bot.note) bot.note = 'Поток завершён';
      if (!bot.text && !bot.error && !bot.note) bot.note = 'Пустой ответ';
      if (bot.error && !bot.text) bot.pending = false;
      s.updated = Date.now();
      busy = false;
      abortCtl = null;
      clearInterval(SYNC.streamTimer);
      SYNC.streamTimer = null;
      saveSessions();
      saveMessages(s.id);
      syncPush();
      updateComposer();
      renderSessions();
      renderMessages();
      refreshStatus();
      els.input.focus();
    }
  }

  function stopGeneration() {
    if (!busy) return;
    stopRequested = true;
    if (abortCtl) abortCtl.abort();
    fetch('/api/chat/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin' }).catch(() => {});
    toast('Останавливаю ответ…');
  }

  // ---------- действия с сообщениями: метрики, озвучка, меню ----------

  function statsNode(m) {
    if (!m || m.role === 'user' || !m.text) return null;
    const node = el('span', 'msg-stats');
    const g = m.gen || {};
    const parts = [];
    if (g.elapsed) parts.push(Number(g.elapsed).toFixed(1) + ' с');
    if (g.tokens) parts.push(String(g.tokens) + ' ток');
    if (g.tps) parts.push(Number(g.tps).toFixed(1) + ' tok/s');
    if (!parts.length) parts.push('~' + Math.max(1, Math.round(String(m.text).length / 3.6)) + ' ток');
    node.textContent = parts.join(' · ');
    node.title = 'Скорость и токены оценены по длине текста (~3.6 символа на токен)';
    return node;
  }

  function speechPlainText(md) {
    return String(md || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`([^`]*)`/g, '$1')
      .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/^#{1,6}\s*/gm, '')
      .replace(/[*_~>|]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  let speakingBtn = null;
  let ttsServer = false;                 // сервер озвучки (Silero) включён И готов
  let ttsError = '';                     // причина, по которой сервер озвучки недоступен
  let ttsToken = 0;                      // номер текущей озвучки: остановка отменяет предыдущую
  let ttsAudio = null;                   // проигрываемый фрагмент
  let ttsQueue = [];                     // фразы, которые ждут озвучки
  let ttsLive = null;                    // { id, pos } — ответ, озвучиваемый по мере генерации
  let ttsDone = true;                    // ответ полностью получен, новых фраз не будет
  let ttsFeeding = 0;                    // сколько фрагментов ещё режутся на фразы на сервере
  let ttsChain = Promise.resolve();      // фрагменты режутся по порядку
  let ttsRunToken = -1;                  // для какой очереди уже запущен проигрыватель
  let ttsVoices = [];                    // список голосов с примерами от сервера
  let ttsDefaultVoice = '';              // голос по умолчанию из .env, если пользователь не выбирал
  let previewAudio = null;               // сейчас играет пример голоса
  let previewToken = 0;
  async function loadTtsStatus(retry) {
    try {
      const st = await api('GET', '/api/tts/status' + (retry ? '?retry=1' : ''));
      // ready=false до первого синтеза (модель грузится лениво), поэтому смотрим available
      const available = st && (st.available !== undefined ? st.available : (st.enabled && st.ready));
      ttsServer = !!available;
      ttsError = ttsServer ? '' : ((st && st.error) ||
        (st && st.enabled && !st.model_exists && !st.autodownload ? 'модель не найдена' : ''));
      if (st && Array.isArray(st.voices) && st.voices.length) ttsVoices = st.voices;
      if (st && st.speaker) ttsDefaultVoice = st.speaker;
      applyVoiceButton();
    } catch (e) {
      ttsServer = false;
      ttsError = '';
    }
    return ttsServer;
  }
  function voiceById(id) {
    return ttsVoices.find((v) => v.id === id) ||
      { id: id || '', label: id || 'Голос', description: '', preview: '' };
  }
  function applyVoiceButton() {
    if (!els.voiceBtn) return;
    const v = voiceById(currentVoice());
    if (els.voiceBtnLabel) els.voiceBtnLabel.textContent = v.label;
    els.voiceBtn.setAttribute('aria-label', 'Голос озвучки: ' + v.label + '. Выбрать или прослушать');
    els.voiceBtn.classList.toggle('on', !!settings.ttsVoice);
  }
  function currentVoice() {
    return settings.ttsVoice || ttsDefaultVoice || (ttsVoices[0] && ttsVoices[0].id) || '';
  }
  function setVoice(id) {
    if (!id || settings.ttsVoice === id) return;
    stopSpeech();                        // сменить голос можно только на новую озвучку
    settings.ttsVoice = id;
    saveSettings();
    applyVoiceButton();
    toast('Голос: ' + voiceById(id).label);
  }
  const ttsSleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function stopSpeech() {
    ttsToken++;
    ttsQueue = [];
    ttsLive = null;
    ttsDone = true;
    if (ttsAudio) {
      ttsAudio.pause();
      if (ttsAudio._stop) ttsAudio._stop();
      ttsAudio = null;
    }
    if (previewAudio) { previewToken++; previewAudio.pause(); previewAudio = null; }
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    if (speakingBtn) { speakingBtn.classList.remove('on'); speakingBtn = null; }
  }

  // Нажатие «Озвучить»: во время генерации озвучиваем уже готовые фразы и дальше — по мере появления.
  async function toggleSpeak(msg, btn) {
    const wasThis = speakingBtn && speakingBtn === btn;
    stopSpeech();
    if (wasThis) return;
    const text = (msg && msg.text) || '';
    if (!text.trim()) return;
    if (await loadTtsStatus(true)) { startTts(msg, btn); return; }
    if (ttsError) toast('Silero не готов: ' + ttsError + '. Использую голос браузера');
    legacySpeak(text, btn);
  }

  function startTts(msg, btn) {
    const token = ttsToken;
    ttsQueue = [];
    ttsLive = { id: msg.id, pos: 0 };
    ttsDone = false;
    speakingBtn = btn;
    if (btn) btn.classList.add('on');
    feedTts(msg, !msg.pending);
    runTts(token, btn, currentVoice());
  }

  // Режем новый кусок ответа на фразы и ставим в очередь. Только законченные предложения:
  // хвост без точки ждёт продолжения, а при final — озвучивается целиком.
  function feedTts(msg, final) {
    if (!ttsLive || !msg || ttsLive.id !== msg.id) return;
    const text = msg.text || '';
    let cut = text.length;
    if (!final) {
      const re = /[.!?…]+(\s|$)/g;
      const rest = text.slice(ttsLive.pos);
      let end = -1;
      let m;
      while ((m = re.exec(rest))) end = m.index + m[0].length;
      if (end < 0) return;
      cut = ttsLive.pos + end;
    }
    const chunk = text.slice(ttsLive.pos, cut);
    ttsLive.pos = cut;
    if (!chunk.trim()) { if (final) ttsDone = true; return; }
    const token = ttsToken;
    ttsFeeding++;
    ttsChain = ttsChain.then(async () => {
      try {
        const r = await api('POST', '/api/tts/segments', { text: chunk });
        if (token === ttsToken && Array.isArray(r.segments)) ttsQueue.push(...r.segments);
      } catch (e) {
        // фрагмент не разрезался: пропускаем, ответ всё равно озвучится дальше
      } finally {
        ttsFeeding--;
        if (final) ttsDone = true;
      }
    });
  }

  function fetchTtsSegment(seg, voice) {
    return fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ text: seg, speaker: voice || currentVoice() }),
    }).then(async (r) => {
      if (!r.ok) {
        let msg = '';
        try { msg = (await r.json()).message || ''; } catch (e) { /* не JSON */ }
        throw new Error(msg || ('tts ' + r.status));
      }
      return r.blob();
    }).then((blob) => URL.createObjectURL(blob));
  }

  // Короткий пример голоса из списка. Не трогает очередь основного озвучивания.
  async function previewVoice(id) {
    const token = ++previewToken;
    if (previewAudio) { previewAudio.pause(); previewAudio = null; }
    document.querySelectorAll('.voice-play.playing').forEach((b) => b.classList.remove('playing'));
    if (!(await loadTtsStatus(true))) {
      toast('Silero недоступен' + (ttsError ? ': ' + ttsError : ''));
      return;
    }
    const v = voiceById(id);
    const text = v.preview || 'Привет! Это пример озвучки.';
    try {
      const url = await fetchTtsSegment(text, id);
      if (token !== previewToken) { URL.revokeObjectURL(url); return; }
      const a = new Audio(url);
      previewAudio = a;
      const done = () => {
        if (previewAudio === a) previewAudio = null;
        URL.revokeObjectURL(url);
        document.querySelectorAll('.voice-play.playing').forEach((b) => b.classList.remove('playing'));
      };
      a.onended = done;
      a.onerror = done;
      a.play().catch(done);
    } catch (e) {
      toast('Не удалось прослушать голос: ' + ((e && e.message) || e));
    }
  }

  // Проигрыватель: берёт фразы из очереди, следующую качает, пока играет текущая.
  async function runTts(token, btn, voice) {
    if (ttsRunToken === token) return;
    ttsRunToken = token;
    let prefetched = null;
    const finish = () => {
      if (token !== ttsToken) return;
      if (btn) btn.classList.remove('on');
      if (speakingBtn === btn) speakingBtn = null;
      ttsLive = null;
      ttsAudio = null;
      ttsRunToken = -1;
    };
    for (;;) {
      if (token !== ttsToken) return;
      if (!ttsQueue.length) {
        if (ttsDone && ttsFeeding === 0) break;
        await ttsSleep(120);
        continue;
      }
      const seg = ttsQueue.shift();
      const ready = prefetched && prefetched.seg === seg ? prefetched.p : fetchTtsSegment(seg, voice);
      prefetched = null;
      let url;
      try {
        url = await ready;
      } catch (e) {
        // сервер озвучки не ответил: остаток ответа — голосом браузера
        ttsServer = false;
        const why = (e && e.message) ? ': ' + String(e.message).slice(0, 120) : '';
        toast('Озвучка сервера недоступна' + why + '. Использую голос браузера');
        const rest = [seg, ...ttsQueue].join(' ');
        ttsQueue = [];
        finish();
        legacySpeak(rest, btn);
        return;
      }
      if (ttsQueue.length) {
        const nxt = ttsQueue[0];
        prefetched = { seg: nxt, p: fetchTtsSegment(nxt, voice) };
        prefetched.p.catch(() => {});
      }
      await new Promise((resolve) => {
        const a = new Audio(url);
        a._stop = resolve;
        ttsAudio = a;
        a.onended = resolve;
        a.onerror = resolve;
        a.play().catch(resolve);
      });
      URL.revokeObjectURL(url);
    }
    finish();
  }

  function legacySpeak(text, btn) {
    if (!('speechSynthesis' in window)) return;
    const plain = speechPlainText(text);
    if (!plain) return;
    const u = new SpeechSynthesisUtterance(plain);
    u.lang = 'ru-RU';
    const ru = window.speechSynthesis.getVoices().find((v) => /^ru/i.test(v.lang || ''));
    if (ru) u.voice = ru;
    const done = () => {
      if (speakingBtn === btn) { if (btn) btn.classList.remove('on'); speakingBtn = null; }
    };
    u.onend = done;
    u.onerror = done;
    speakingBtn = btn;
    if (btn) btn.classList.add('on');
    window.speechSynthesis.speak(u);
  }

  function actButton(title, icon, onClick) {
    const b = el('button', 'act');
    b.type = 'button';
    b.title = title;
    b.setAttribute('aria-label', title);
    b.innerHTML = icon;
    b.addEventListener('click', (e) => { e.stopPropagation(); onClick(b); });
    return b;
  }

  function regenerate(idx) {
    if (busy) return;
    const s = currentSession();
    if (!s) return;
    const list = messages[s.id] || [];
    const bot = list[idx];
    if (!bot || bot.role === 'user') return;
    let userIdx = idx - 1;
    while (userIdx >= 0 && list[userIdx].role !== 'user') userIdx--;
    if (userIdx < 0) { toast('Нет вопроса для перегенерации'); return; }
    if (list.length - 1 > idx) list.splice(idx + 1);
    stopSpeech();
    toast('Генерирую другой ответ…');
    send(list[userIdx].text, { user: list[userIdx], bot, idx });
  }

  let menuEl = null;
  function closeMenu() {
    if (menuEl) {
      menuEl.remove();
      menuEl = null;
      document.dispatchEvent(new Event('pulsenet:menu-close'));
    }
  }
  function placeMenu(m, x, y) {
    document.body.appendChild(m);
    const r = m.getBoundingClientRect();
    // visualViewport учитывает клавиатуру iOS; window.innerHeight — нет.
    const vv = window.visualViewport;
    const vLeft = vv ? vv.offsetLeft : 0;
    const vTop = vv ? vv.offsetTop : 0;
    const vw = vv ? vv.width : window.innerWidth;
    const vh = vv ? vv.height : window.innerHeight;
    m.style.left = Math.max(vLeft + 8, Math.min(x, vLeft + vw - r.width - 8)) + 'px';
    m.style.top = Math.max(vTop + 8, Math.min(y, vTop + vh - r.height - 8)) + 'px';
    menuEl = m;
  }
  function showMenu(x, y, items) {
    closeMenu();
    const m = el('div', 'ui-menu');
    m.setAttribute('role', 'menu');
    for (const it of items) {
      if (it.sep) { m.appendChild(el('div', 'ui-menu-sep')); continue; }
      const b = el('button', 'ui-menu-item' + (it.danger ? ' danger' : ''), it.label);
      b.type = 'button';
      b.setAttribute('role', 'menuitem');
      b.disabled = !!it.disabled;
      b.addEventListener('click', () => { closeMenu(); if (it.onClick) it.onClick(); });
      m.appendChild(b);
    }
    placeMenu(m, x, y);
  }
  function showVoiceMenu(anchor) {
    closeMenu();
    if (!ttsVoices.length) ttsVoices = [
      { id: 'aidar', label: 'Айдар', description: 'низкий мужской' },
      { id: 'baya', label: 'Бая', description: 'мягкий женский' },
      { id: 'kseniya', label: 'Ксения', description: 'ровный женский' },
      { id: 'xenia', label: 'Ксения 2', description: 'живой женский' },
      { id: 'eugene', label: 'Евгений', description: 'мужской' },
    ];
    const m = el('div', 'ui-menu voice-menu');
    m.setAttribute('role', 'menu');
    m.appendChild(el('div', 'voice-menu-title', 'Голос озвучки · ▶ — пример'));
    const activeVoice = currentVoice();
    for (const v of ttsVoices) {
      const row = el('div', 'voice-item' + (activeVoice === v.id ? ' selected' : ''));
      row.setAttribute('role', 'menuitem');
      const meta = el('div', 'voice-meta');
      meta.appendChild(el('div', 'voice-name', v.label || v.id));
      if (v.description) meta.appendChild(el('div', 'voice-desc', v.description));
      const play = el('button', 'voice-play', '▶');
      play.type = 'button';
      play.title = 'Прослушать: ' + (v.label || v.id);
      play.setAttribute('aria-label', play.title);
      play.addEventListener('click', (e) => { e.stopPropagation(); previewVoice(v.id); });
      row.appendChild(meta);
      row.appendChild(play);
      row.addEventListener('click', () => { setVoice(v.id); closeMenu(); });
      m.appendChild(row);
    }
    const r = anchor.getBoundingClientRect();
    placeMenu(m, Math.max(8, r.left), r.bottom + 6);
    els.voiceBtn.setAttribute('aria-expanded', 'true');
    const off = () => {
      els.voiceBtn.setAttribute('aria-expanded', 'false');
      document.removeEventListener('pulsenet:menu-close', off);
    };
    document.addEventListener('pulsenet:menu-close', off);
  }
  function showMessageMenu(x, y, idx) {
    const s = currentSession();
    const m = s && (messages[s.id] || [])[idx];
    if (!m) return;
    const isBot = m.role !== 'user';
    const g = m.gen || {};
    const stat = [g.tps ? Number(g.tps).toFixed(1) + ' tok/s' : '',
                  g.tokens ? g.tokens + ' ток' : '',
                  g.elapsed ? Number(g.elapsed).toFixed(1) + ' с' : ''].filter(Boolean).join(' · ') ||
                 '≈' + Math.max(1, Math.round(String(m.text || '').length / 3.6)) + ' ток';
    showMenu(x, y, [
      { label: 'Копировать', disabled: !m.text, onClick: () => copyText(m.text || '') },
      { label: 'Другой ответ', disabled: !isBot || busy, onClick: () => regenerate(idx) },
      { label: speakingBtn && isBot ? 'Остановить озвучивание' : 'Озвучить',
        disabled: (!speakingBtn || !isBot) && (!isBot || !m.text),
        onClick: () => { if (speakingBtn) stopSpeech(); else toggleSpeak(m, null); } },
      { sep: true },
      { label: 'Метрики: ' + stat, disabled: true },
    ]);
  }
  function showBlankMenu(x, y) {
    showMenu(x, y, [
      { label: 'Новый чат', onClick: () => createChat() },
      { label: 'Чат в память', disabled: !currentId || !(messages[currentId] || []).length,
        onClick: () => saveChatMemory(currentId, { command: true }) },
      { sep: true },
      { label: 'Память: факты', onClick: () => openDrawer('facts') },
      { label: 'Настройки', onClick: () => { if (els.settingsDialog.showModal) els.settingsDialog.showModal(); } },
    ]);
  }
  function showChatMenu(x, y, id) {
    const s = sessions.find((v) => v.id === id);
    if (!s) return;
    const current = id === currentId;
    const n = (messages[id] || []).length;
    showMenu(x, y, [
      { label: current ? 'Текущий чат' : 'Открыть чат', disabled: current,
        onClick: () => selectSession(id) },
      { label: 'Выгрузить в .md', disabled: !n, onClick: () => exportChat(id) },
      { label: 'В память (факты + дедупликация)', disabled: !n,
        onClick: () => saveChatMemory(id) },
      { sep: true },
      { label: 'Переименовать', disabled: !current, onClick: () => startRename() },
      { label: 'Очистить историю', disabled: !n || busy, onClick: () => clearChat(id) },
      { label: 'Удалить чат', danger: true, disabled: busy, onClick: () => deleteChat(id) },
    ]);
  }

  function showFactMenu(x, y, fid, text) {
    showMenu(x, y, [
      { label: 'Копировать факт', disabled: !text, onClick: () => copyText(String(text || '')) },
      { label: 'Найти связанное', disabled: !text,
        onClick: () => { openDrawer('search'); els.searchQuery.value = String(text || ''); runSearch(); } },
      { sep: true },
      { label: 'Верно', onClick: () => memoryAction('/api/memory/confirm', { fact_id: fid, yes: true }, 'Факт подтверждён') },
      { label: 'Неверно', onClick: () => memoryAction('/api/memory/confirm', { fact_id: fid, yes: false }, 'Уверенность снижена') },
      { label: 'Пересобрать', onClick: () => distillFact(fid) },
      { label: 'Отозвать', danger: true, onClick: async () => {
        if (!(await ask('Отозвать факт?', 'Факт перестанет участвовать в ответах.', 'Отозвать'))) return;
        memoryAction('/api/memory/retract', { fact_id: fid }, 'Факт отозван');
      } },
    ]);
  }

  function nodeFactIds(n) {
    const out = [];
    for (const e of graph.edges) {
      if (e.a !== n && e.b !== n) continue;
      const fid = e.id || e.fact_id;
      if (fid && !out.includes(fid)) out.push(fid);
    }
    return out;
  }

  function selectedFactIds(nodes) {
    const ids = [];
    for (const n of nodes || []) {
      for (const fid of nodeFactIds(n)) if (!ids.includes(fid)) ids.push(fid);
    }
    return ids;
  }

  async function autoRecombineSelected(nodes) {
    const sel = (nodes && nodes.length ? nodes : selectedNodes()).filter(Boolean);
    if (sel.length < 2) { toast('Выберите минимум два нейрона (Ctrl+клик)'); return; }
    const ids = selectedFactIds(sel);
    if (ids.length < 2) { toast('У выбранных нейронов недостаточно фактов для слияния'); return; }
    const label = sel.map((n) => shorten(n.label, 24)).join(', ');
    if (!(await ask('Авто-рекомбинация нейронов?',
      'LLM объединит факты выбранных нейронов (' + sel.length + '): ' + label, 'Объединить'))) return;
    try {
      toast('LLM объединяет нейроны…');
      const out = await api('POST', '/api/memory/recombine', {
        fact_ids: ids, model: settings.model || '', apply: true,
      });
      const p = out.preview || {};
      toast('Нейроны объединены: ' + [p.s, p.r, p.o].filter(Boolean).join(' '));
      clearSelection();
      await refreshMemoryViews();
    } catch (err) {
      toast('Ошибка авто-рекомбинации: ' + (err.message || err));
    }
  }

  function showNodeMenu(x, y, n) {
    if (n && !graph.selectedIds.has(n.id)) setSelectedNodes([n], n);
    const ids = nodeFactIds(n);
    const sel = selectedNodes();
    const multi = sel.length > 1;
    const selIds = selectedFactIds(sel);
    showMenu(x, y, [
      { label: multi ? 'Выбрано: ' + sel.length + ' нейронов' : 'Копировать название', disabled: multi,
        onClick: () => copyText(n.label) },
      { label: n.fixed ? 'Открепить позицию' : 'Закрепить позицию', disabled: multi, onClick: () => {
          n.fixed = !n.fixed; toast(n.fixed ? 'Нейрон закреплён' : 'Позиция свободна'); draw();
        } },
      { sep: true },
      { label: 'Авто-рекомбинация выбранных (LLM)', disabled: sel.length < 2 || selIds.length < 2,
        onClick: () => autoRecombineSelected(sel) },
      { label: 'Снять выделение', disabled: !multi, onClick: clearSelection },
      { sep: true },
      { label: 'Править вручную', disabled: multi || !ids.length, onClick: () => openEditDialog(n, ids) },
      { label: 'Дистилляция LLM', disabled: multi || !ids.length, onClick: () => openDistillDialog(n, ids) },
      { label: 'Поиск дублей LLM', disabled: multi || !ids.length, onClick: () => openDedupeDialog(ids) },
      { label: 'Рекомбинация с нейроном…', disabled: multi || !ids.length, onClick: () => openMergeDialog(n, ids) },
      { sep: true },
      { label: 'Раскладка заново', onClick: relayoutGraph },
      { label: 'Удалить нейрон из памяти', danger: true, disabled: multi || !ids.length, onClick: async () => {
          const ok = await ask('Удалить нейрон?',
            'Все его факты будут отозваны. История изменений останется в журнале.', 'Удалить');
          if (!ok) return;
          await memoryAction('/api/memory/forget', { node_id: n.id }, 'Нейрон удалён из памяти');
          if (graph.selected === n) clearSelection();
        } },
    ]);
  }

  // Универсальное «меню по правой кнопке / долгому нажатию»: работает на сообщениях,
  // чатах, фактах — везде, где есть осмысленные действия над объектом.
  function isMenuExempt(target, root) {
    if (!target || target === root || !target.closest) return false;
    // Кнопка чата в сайдбаре сама является root: клик по её внутреннему <span>
    // не должен считаться кликом по вложенной интерактивной кнопке.
    const hit = target.closest('button, a, input, textarea, select, [contenteditable="true"], [role="menuitem"]');
    return !!hit && hit !== root;
  }
  function attachMenu(root, open, skipSelector) {
    if (!root) return;
    const skipped = (target) => !!skipSelector && !!target.closest && !!target.closest(skipSelector);
    root.addEventListener('contextmenu', (e) => {
      // На iOS long-press в поле ввода порождает contextmenu — отдаём его
      // системе (выделение, вставка, «Искать»), иначе меню перекрывает клавиатуру.
      if (isMenuExempt(e.target, root) || skipped(e.target)) return;
      e.preventDefault();
      open(e.clientX, e.clientY);
    });
    let timer = null, swallow = false;
    const clear = () => { clearTimeout(timer); timer = null; root.classList.remove('menu-press'); };
    root.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1 || isMenuExempt(e.target, root) || skipped(e.target)) { clear(); return; }
      const t = e.touches[0], x = t.clientX, y = t.clientY;
      clear();
      root.classList.add('menu-press');
      timer = setTimeout(() => {
        timer = null;
        root.classList.remove('menu-press');
        swallow = true;
        setTimeout(() => { swallow = false; }, 600);
        open(x, y);
        if (navigator.vibrate) navigator.vibrate(12);
      }, 500);
    }, { passive: true });
    root.addEventListener('touchmove', clear, { passive: true });
    root.addEventListener('touchend', clear);
    root.addEventListener('touchcancel', clear);
    root.addEventListener('click', (e) => {
      if (!swallow) return;
      swallow = false;
      e.preventDefault();
      e.stopPropagation();
    }, true);
  }
  function attachMessageMenu(root, idx) {
    attachMenu(root, (x, y) => showMessageMenu(x, y, idx));
  }
  document.addEventListener('pointerdown', (e) => {
    if (menuEl && !menuEl.contains(e.target)) closeMenu();
  }, true);
  // Фокус поля = пользователь печатает: любое меню должно исчезнуть, иначе на iOS
  // оно остаётся «под» клавиатурой и мешает вводу.
  document.addEventListener('focusin', (e) => {
    if (e.target && e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"]')) closeMenu();
  }, true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
  window.addEventListener('scroll', closeMenu, true);
  window.addEventListener('resize', closeMenu);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', closeMenu);
    window.visualViewport.addEventListener('scroll', closeMenu);
  }

  // ---------- команды ----------

  function commandMatches(value) {
    if (!value.startsWith('/') || /\s/.test(value)) return [];
    const q = value.slice(1).toLowerCase();
    return COMMANDS.filter((c) => c.name.startsWith(q));
  }
  function hideCmdPop() {
    els.cmdPop.hidden = true;
    cmdItems = [];
    cmdIndex = 0;
  }
  function updateCmdPop() {
    if (busy) { hideCmdPop(); return; }
    cmdItems = commandMatches(els.input.value);
    if (!cmdItems.length) { hideCmdPop(); return; }
    cmdIndex = Math.min(cmdIndex, cmdItems.length - 1);
    els.cmdPop.innerHTML = '';
    cmdItems.forEach((c, i) => {
      const b = el('button', 'cmd-item' + (i === cmdIndex ? ' on' : ''));
      b.type = 'button';
      b.setAttribute('role', 'option');
      b.setAttribute('aria-selected', i === cmdIndex ? 'true' : 'false');
      b.appendChild(el('b', '', '/' + c.name + (c.arg ? ' ' + c.arg : '')));
      b.appendChild(el('span', '', c.hint));
      b.addEventListener('mousedown', (e) => { e.preventDefault(); pickCommand(c); });
      els.cmdPop.appendChild(b);
    });
    els.cmdPop.hidden = false;
  }
  function pickCommand(c) {
    hideCmdPop();
    if (c.local) {
      els.input.value = '';
      autoSize();
      updateCounter();
      runLocal(c.name);
      return;
    }
    fillInput('/' + c.name + ' ');
  }
  function moveCmd(step) {
    if (!cmdItems.length) return;
    cmdIndex = (cmdIndex + step + cmdItems.length) % cmdItems.length;
    updateCmdPop();
  }

  // ---------- модели ----------

  async function loadModels() {
    const select = els.modelSelect;
    if (!select) return;
    const setHint = (text) => { select.title = text; };
    select.innerHTML = '<option value="">загрузка моделей…</option>';
    setHint('загрузка списка…');
    try {
      const data = await api('GET', '/api/models');
      const models = Array.isArray(data.models) ? data.models : [];
      select.innerHTML = '';
      const def = document.createElement('option');
      def.value = '';
      def.textContent = 'по умолчанию' + (data.default ? ' (' + data.default + ')' : '');
      select.appendChild(def);
      if (settings.model && !models.includes(settings.model)) models.unshift(settings.model);
      for (const id of models) {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = id;
        select.appendChild(opt);
      }
      if (!models.length && !settings.model) {
        const none = document.createElement('option');
        none.value = '';
        none.textContent = 'моделей нет';
        select.appendChild(none);
      }
      select.value = settings.model || '';
      setHint(models.length
        ? 'Модель применяется к следующему ответу. Эмбеддинги в список не входят.'
        : 'Моделей нет: загрузите модель в LM Studio или проверьте LLM_BACKEND.');
    } catch (err) {
      select.innerHTML = '<option value="">список недоступен</option>';
      setHint('Список недоступен: ' + (err.message || err));
    }
  }

  // ---------- status ----------

  function renderStatus(s) {
    const m = s.memory || {};
    els.drawerSub.textContent = 'фактов: ' + (m.facts_active ?? 0) + ' · документов: ' + (m.docs ?? 0);
    els.statusGrid.innerHTML = '';
    const items = [
      ['факты', m.facts_active],
      ['документы', m.docs],
      ['фрагменты', m.chunks],
      ['сущности', m.entities],
      ['связи', m.relations],
      ['очередь', s.index_pending],
    ];
    for (const [label, value] of items) {
      const card = el('div', 'stat');
      card.appendChild(el('b', '', value ?? '—'));
      card.appendChild(el('span', '', label));
      els.statusGrid.appendChild(card);
    }
    const wide = el('div', 'stat wide');
    wide.appendChild(el('b', '', (s.model || s.backend || '—')));
    wide.appendChild(el('span', '', 'модель · ' + (s.backend || 'backend')));
    els.statusGrid.appendChild(wide);
    const mem2 = el('div', 'stat wide');
    const online = s.memory_online !== false;
    mem2.appendChild(el('b', '', (s.memory_backend === 'mcp' ? 'общая память (MCP)' : 'локальная память') +
      (online ? '' : ' — недоступна')));
    mem2.appendChild(el('span', '', online ? 'все агенты пишут в одно хранилище' : 'чат работает без памяти, сообщения ждут восстановления'));
    els.statusGrid.appendChild(mem2);
    if (s.fact_model) {
      const fm = el('div', 'stat wide');
      fm.appendChild(el('b', '', s.fact_model));
      fm.appendChild(el('span', '', 'модель записи фактов (та же, что в чате)'));
      els.statusGrid.appendChild(fm);
    }
    if (s.embedder) {
      const e = el('div', 'stat wide');
      e.appendChild(el('b', '', s.embedder.name || m.encoder || '—'));
      const where = s.embedder.location === 'mcp' ? ' · в MCP' : '';
      e.appendChild(el('span', '', 'эмбеддер' + where + (s.embedder.model ? ' · ' + s.embedder.model : '')));
      els.statusGrid.appendChild(e);
    }
    els.errorsBox.innerHTML = '';
    const errors = Array.isArray(s.errors) ? s.errors : [];
    if (!errors.length) {
      els.errorsBox.appendChild(el('div', 'empty', 'нет'));
    } else {
      for (const err of errors.slice(0, 10)) {
        const card = el('div', 'card');
        const line = typeof err === 'string'
          ? err
          : [err.time, err.where].filter(Boolean).join(' · ') + (err.message ? ': ' + err.message : '');
        card.appendChild(el('div', 'fact-text', line || 'неизвестная ошибка'));
        els.errorsBox.appendChild(card);
      }
    }
  }

  async function refreshStatus() {
    if (statusBusy) return;
    statusBusy = true;
    try {
      const s = await api('GET', '/api/status');
      pullSettings();  // подхватить настройки, изменённые на другом устройстве
      const m = s.memory || {};
      els.statusDot.className = 'status ok';
      els.modelChip.classList.remove('bad');
      els.modelName.textContent = s.model || s.backend || 'модель';
      els.modelChip.title = 'Бэкенд: ' + (s.backend || '—') + (s.model ? ' · модель: ' + s.model : '');
      els.chatSub.textContent = (s.backend || 'backend') + (s.model ? ' · ' + s.model : '') +
        ' · фактов: ' + (m.facts_active ?? 0);
      els.sbStat.textContent = 'фактов: ' + (m.facts_active ?? 0) + ' · документов: ' + (m.docs ?? 0) +
        ' · очередь: ' + (s.index_pending ?? 0);
      renderStatus(s);
    } catch {
      els.statusDot.className = 'status bad';
      els.modelChip.classList.add('bad');
      els.chatSub.textContent = 'сервер недоступен';
    } finally {
      statusBusy = false;
    }
  }

  // ---------- карта нейронов ----------
  // Узлы — сущности памяти, связи — действующие факты. Раскладка — силовая (Фрюхтерман–Рейнгольд),
  // позиции сохраняются между обновлениями, новые узлы (созданные в этой сессии) подсвечиваются.

  const graph = {
    nodes: [], edges: [], byId: {}, W: 0, H: 0, dpr: 1, temp: 0, frames: 0, raf: 0,
    hover: null, selected: null, selectedIds: new Set(), busy: false, typeColors: {}, relColors: {}, pulses: [],
    anim: [], nodeDrag: null, pointers: new Map(), pinch: null,
    view: { scale: 1, ox: 0, oy: 0 }, drag: null, press: null, fullscreen: false,
    sessionStart: Date.now(),
  };
  const LAYOUT_FRAMES = 420;
  const LONG_PRESS_MS = 450;

  function typeColor(type) {
    const key = type || 'без типа';
    if (!graph.typeColors[key]) {
      graph.typeColors[key] = PALETTE[Object.keys(graph.typeColors).length % PALETTE.length];
    }
    return graph.typeColors[key];
  }
  // Цвет связи по отношению — как в MCPIISys: по цвету видно, какие именно
  // синапсы связывают выбранный нейрон с соседями.
  function relColor(label) {
    const key = String(label || 'связь');
    if (!graph.relColors[key]) {
      graph.relColors[key] = PALETTE[(Object.keys(graph.relColors).length + 3) % PALETTE.length];
    }
    return graph.relColors[key];
  }
  function shorten(text, n) {
    const s = String(text || '');
    return s.length > n ? s.slice(0, n - 1) + '…' : s;
  }
  function graphVisible() {
    return drawerTab === 'graph' && els.drawer.classList.contains('open');
  }

  function resizeGraph() {
    const c = els.graphCanvas;
    const box = els.graphStage;
    if (!box) return;
    const w = box.clientWidth || 300;
    const h = box.clientHeight || 340;
    graph.dpr = window.devicePixelRatio || 1;
    graph.W = w;
    graph.H = h;
    c.width = Math.round(w * graph.dpr);
    c.height = Math.round(h * graph.dpr);
    c.style.width = w + 'px';
    c.style.height = h + 'px';
    kick();
  }

  function spawnPulse(edge, delay) {
    if (!edge) return;
    const start = performance.now() + (delay || 0);
    graph.pulses.push({ edge, t0: start, until: start + 900 });
    if (graph.pulses.length > 24) graph.pulses.shift();
  }

  function edgeKey(e) {
    return e.source + '->' + e.target + ':' + (e.label || '');
  }

  function nodeRadius(degree) {
    return 6 + Math.min(12, Math.sqrt(Math.max(1, degree || 1)) * 2.8);
  }

  function buildGraph(data) {
    const prev = graph.byId;
    const prevEdges = new Set(graph.edges.map(edgeKey));
    const W = graph.W || 600;
    const H = graph.H || 420;
    const rawNodes = data.nodes || [];
    const rawEdges = data.edges || [];

    // Предварительно считаем степень по входящим ребрам, чтобы новые узлы
    // появлялись рядом с соседями, а не случайной "пылью" по углам.
    const deg = {};
    for (const e of rawEdges) {
      deg[e.source] = (deg[e.source] || 0) + 1;
      deg[e.target] = (deg[e.target] || 0) + 1;
    }

    const nodes = rawNodes.map((n, i) => {
      const old = prev[n.id];
      const created = parseIso(n.created);
      const degree = n.degree || deg[n.id] || 0;
      let x = old ? old.x : NaN;
      let y = old ? old.y : NaN;
      if (!old) {
        // Ищем уже размещённого соседа для естественного появления.
        const nb = rawEdges.find((e) => (e.source === n.id && prev[e.target]) || (e.target === n.id && prev[e.source]));
        if (nb) {
          const ref = prev[nb.source === n.id ? nb.target : nb.source];
          const ang = (i / Math.max(1, rawNodes.length)) * Math.PI * 2;
          x = ref.x + Math.cos(ang) * 56;
          y = ref.y + Math.sin(ang) * 56;
        } else {
          const ang = (i / Math.max(1, rawNodes.length)) * Math.PI * 2;
          const rad = Math.min(W, H) * 0.22 + (i % 5) * 18;
          x = W / 2 + Math.cos(ang) * rad;
          y = H / 2 + Math.sin(ang) * rad;
        }
      }
      return {
        id: n.id, label: n.label || n.id, type: n.type || null, degree,
        created, isNew: created >= graph.sessionStart, linked: !!n.linked,
        r: nodeRadius(degree),
        x, y,
        vx: old && Number.isFinite(old.vx) ? old.vx * 0.35 : 0,
        vy: old && Number.isFinite(old.vy) ? old.vy * 0.35 : 0,
        dx: 0, dy: 0, color: typeColor(n.type), fixed: old ? !!old.fixed : false,
      };
    });

    const byId = {};
    for (const n of nodes) byId[n.id] = n;
    const edges = [];
    for (const e of rawEdges) {
      const a = byId[e.source];
      const b = byId[e.target];
      if (a && b) edges.push({ ...e, a, b, created: parseIso(e.recorded_at), color: relColor(e.label) });
    }

    const changed = nodes.length !== Object.keys(prev).length || nodes.some((n) => !prev[n.id]) ||
      edges.length !== graph.edges.length;
    const hasNew = nodes.some((n) => !prev[n.id]);
    graph.nodes = nodes;
    graph.edges = edges;
    graph.byId = byId;
    if (changed) {
      graph.temp = hasNew ? Math.max(graph.temp, 1.2) : Math.max(graph.temp, 0.45);
      graph.frames = Math.min(graph.frames, LAYOUT_FRAMES - 120);
    }

    if (changed && graphVisible()) {
      let n = 0;
      for (const e of edges) {
        if (!prevEdges.has(edgeKey(e))) spawnPulse(e, n++ * 90);
      }
    }
    renderGraphMeta(data, nodes, edges);
    const kept = [...graph.selectedIds].filter((id) => byId[id]);
    graph.selectedIds = new Set(kept);
    const sel = graph.selected && byId[graph.selected.id];
    syncSelection(sel || null);
    selectNode(graph.selected, { silent: true });
    kick();
  }

  function renderGraphMeta(data, nodes, edges) {
    const totals = data.totals || {};
    const fresh = nodes.filter((n) => n.isNew).length;
    els.graphStats.textContent = 'нейронов: ' + nodes.length + ' · связей: ' + edges.length +
      ' · всего фактов: ' + (totals.facts ?? 0) + ' · новых за сессию: ' + fresh +
      (data.truncated ? ' · показаны свежие связи' : '');
    els.graphEmpty.hidden = nodes.length > 0;

    els.graphLegend.innerHTML = '';
    const present = [...new Set(nodes.map((n) => n.type || 'без типа'))];
    for (const key of present) {
      const item = el('span', 'legend-item');
      const dot = el('i', 'legend-dot');
      dot.style.background = typeColor(key);
      item.appendChild(dot);
      item.appendChild(document.createTextNode(key));
      els.graphLegend.appendChild(item);
    }

    els.graphGrowth.innerHTML = '';
    const growth = (data.growth || []).slice(-30);
    if (!growth.length) {
      els.graphGrowth.appendChild(el('div', 'empty', 'пока пусто'));
      return;
    }
    const max = Math.max(1, ...growth.map((g) => g.facts));
    for (const g of growth) {
      const bar = el('div', 'bar');
      bar.style.height = Math.max(6, Math.round((g.facts / max) * 100)) + '%';
      bar.title = g.day + ': ' + g.facts + ' фактов';
      els.graphGrowth.appendChild(bar);
    }
  }

  function kick() {
    if (!graph.raf) graph.raf = requestAnimationFrame(frame);
  }

  function animateTo(node, x, y, dur) {
    if (!node) return;
    graph.anim = graph.anim.filter((a) => a.node !== node);
    graph.anim.push({ node, fx: node.x, fy: node.y, tx: x, ty: y,
                      t0: performance.now(), dur: dur || 620 });
    kick();
  }

  function stepAnims(now) {
    if (!graph.anim.length) return;
    graph.anim = graph.anim.filter((a) => {
      const p = Math.max(0, Math.min(1, (now - a.t0) / a.dur));
      const e = 1 - Math.pow(1 - p, 3);
      a.node.x = a.fx + (a.tx - a.fx) * e;
      a.node.y = a.fy + (a.ty - a.fy) * e;
      if (p >= 1) { a.node.vx = 0; a.node.vy = 0; }
      return p < 1;
    });
  }

  function relayoutGraph() {
    const W = graph.W || 600;
    const H = graph.H || 420;
    const nodes = graph.nodes;
    if (!nodes.length) return;
    const cx = W / 2;
    const cy = H / 2;
    const radius = Math.max(60, Math.min(W, H) * 0.32);
    nodes.forEach((n, i) => {
      n.fixed = false;
      n.vx = 0;
      n.vy = 0;
      const ang = (i / nodes.length) * Math.PI * 2 - Math.PI / 2;
      const jitter = (i % 3 - 1) * 10;
      animateTo(n, cx + Math.cos(ang) * (radius + jitter), cy + Math.sin(ang) * (radius + jitter), 700);
    });
    graph.temp = 1.6;
    graph.frames = 0;
    toast('Раскладка нейронов обновлена');
  }

  function frame() {
    graph.raf = 0;
    const now = performance.now();
    stepAnims(now);
    const hasMotion = layoutStep();
    draw();
    const active = hasMotion || graph.anim.length || graph.pulses.length ||
      graph.nodeDrag || graph.drag || graph.pinch ||
      ((graph.selected || graph.hover) && graphVisible());
    if (active) kick();
  }

  function layoutStep() {
    const { nodes, edges, W, H } = graph;
    const n = nodes.length;
    if (!n) return false;
    if (n === 1) {
      const only = nodes[0];
      if (!only.fixed && !graph.anim.some((a) => a.node === only)) {
        only.x += (W / 2 - only.x) * 0.08;
        only.y += (H / 2 - only.y) * 0.08;
        only.vx *= 0.7; only.vy *= 0.7;
      }
      return Math.abs(W / 2 - only.x) + Math.abs(H / 2 - only.y) > 0.5;
    }

    const area = Math.max(1, W * H);
    const k = Math.sqrt(area / n) * 0.95;
    const repel = k * k * 1.25;
    const centerX = W / 2;
    const centerY = H / 2;
    const temp = Math.max(0, graph.temp || 0);
    let motion = 0;

    for (const a of nodes) { a.fx = 0; a.fy = 0; }

    // Отталкивание: O(n^2), но граф памяти обычно небольшой.
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = (i + 1) * 0.01; dy = (j + 1) * 0.01; d2 = dx * dx + dy * dy; }
        const d = Math.sqrt(d2);
        const minDist = a.r + b.r + 22;
        let f = repel / d2;
        if (d < minDist) f += (minDist - d) / d * 2.2;
        const nx = (dx / d) * f;
        const ny = (dy / d) * f;
        a.fx += nx; a.fy += ny;
        b.fx -= nx; b.fy -= ny;
      }
    }

    // Пружины по фактам: более уверенные/сильные связи тянут ближе.
    for (const e of edges) {
      const a = e.a;
      const b = e.b;
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const conf = Math.max(0.25, Math.min(1, e.confidence || 0.6));
      const target = Math.max(a.r + b.r + 26, k * (1.35 - conf * 0.45));
      const strength = (0.015 + conf * 0.02) * (1 + temp * 0.25);
      const f = (d - target) * strength;
      const nx = (dx / d) * f;
      const ny = (dy / d) * f;
      a.fx += nx; a.fy += ny;
      b.fx -= nx; b.fy -= ny;
    }

    for (const a of nodes) {
      if (a.fixed || graph.nodeDrag && graph.nodeDrag.node === a) {
        a.vx = 0; a.vy = 0;
        continue;
      }
      // Мягкое притяжение к центру, чтобы граф не расползался бесконечно, но без жёстких границ.
      a.fx += (centerX - a.x) * (0.0022 + temp * 0.0008);
      a.fy += (centerY - a.y) * (0.0022 + temp * 0.0008);

      a.vx = (a.vx + a.fx) * 0.86;
      a.vy = (a.vy + a.fy) * 0.86;
      const speed = Math.sqrt(a.vx * a.vx + a.vy * a.vy);
      const maxSpeed = 2.2 + temp * 3.2;
      if (speed > maxSpeed) {
        a.vx = (a.vx / speed) * maxSpeed;
        a.vy = (a.vy / speed) * maxSpeed;
      }
      if (speed < 0.015 && temp < 0.08) { a.vx = 0; a.vy = 0; }
      a.x += a.vx;
      a.y += a.vy;
      motion += Math.abs(a.vx) + Math.abs(a.vy);
    }

    if (graph.frames < LAYOUT_FRAMES) graph.frames++;
    graph.temp = temp > 0.02 ? temp * 0.985 : 0;
    return motion > 0.08 || temp > 0.03;
  }

  function isNeighbor(n, hi) {
    return graph.edges.some((e) => (e.a === hi && e.b === n) || (e.b === hi && e.a === n));
  }

  function withAlpha(hex, alpha) {
    let h = String(hex || '#a6bfe0').replace('#', '');
    if (h.length === 3) h = h.split('').map((x) => x + x).join('');
    const v = parseInt(h, 16) || 0xa6bfe0;
    return 'rgba(' + ((v >> 16) & 255) + ',' + ((v >> 8) & 255) + ',' + (v & 255) + ',' + alpha + ')';
  }

  function drawEdgeLabel(ctx, e, inv) {
    const text = shorten(e.label || 'связь', 24);
    const mx = (e.a.x + e.b.x) / 2;
    const my = (e.a.y + e.b.y) / 2;
    ctx.font = Math.round(10.5 * inv) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
    const padX = 5 * inv;
    const h = 16 * inv;
    const w = ctx.measureText(text).width + padX * 2;
    const r = 5 * inv;
    const x = mx - w / 2;
    const y = my - h / 2;
    ctx.fillStyle = 'rgba(10, 13, 19, 0.82)';
    ctx.strokeStyle = withAlpha(e.color || '#a6bfe0', 0.45);
    ctx.lineWidth = 1 * inv;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, w, h, r);
    else {
      ctx.moveTo(x + r, y);
      ctx.arcTo(x + w, y, x + w, y + h, r);
      ctx.arcTo(x + w, y + h, x, y + h, r);
      ctx.arcTo(x, y + h, x, y, r);
      ctx.arcTo(x, y, x + w, y, r);
    }
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = withAlpha('#eceef1', 0.92);
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x + padX, my + 0.5 * inv);
  }

  function edgeCurve(e) {
    const dx = e.b.x - e.a.x;
    const dy = e.b.y - e.a.y;
    const d = Math.sqrt(dx * dx + dy * dy) || 1;
    const bend = Math.min(28, d * 0.08);
    const nx = -dy / d;
    const ny = dx / d;
    // Deterministic bend direction so edges don't jitter between frames.
    const sign = ((e.source || '').length + (e.target || '').length + (e.label || '').length) % 2 ? 1 : -1;
    return { cx: (e.a.x + e.b.x) / 2 + nx * bend * sign, cy: (e.a.y + e.b.y) / 2 + ny * bend * sign };
  }

  function drawNeuronShape(ctx, n, inv, opts) {
    const { dim, lifted, related, phase } = opts;
    const r = n.r;
    // Outer glow / membrane.
    ctx.globalAlpha = dim ? 0.16 : 1;
    const glow = ctx.createRadialGradient(n.x, n.y, r * 0.2, n.x, n.y, r * 4.6);
    glow.addColorStop(0, withAlpha(n.color, lifted ? 0.28 : 0.16));
    glow.addColorStop(1, withAlpha(n.color, 0));
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r * 4.6, 0, Math.PI * 2);
    ctx.fill();

    // Dendrite-like processes.
    const seed = (n.id || n.label || '').split('').reduce((a, ch) => a + ch.charCodeAt(0), 0);
    const count = 5 + (seed % 4);
    ctx.save();
    ctx.lineCap = 'round';
    for (let i = 0; i < count; i++) {
      const ang = (i / count) * Math.PI * 2 + (seed % 17) * 0.13;
      const len = r * (1.8 + ((seed >> i) % 5) * 0.35) + (lifted ? 3 : 0);
      const wob = Math.sin(phase * Math.PI * 2 + i + seed) * r * 0.18;
      const x1 = n.x + Math.cos(ang) * r * 0.85;
      const y1 = n.y + Math.sin(ang) * r * 0.85;
      const x2 = n.x + Math.cos(ang + 0.18) * (r + len * 0.55) + Math.cos(ang + Math.PI / 2) * wob;
      const y2 = n.y + Math.sin(ang + 0.18) * (r + len * 0.55) + Math.sin(ang + Math.PI / 2) * wob;
      const x3 = n.x + Math.cos(ang) * (r + len);
      const y3 = n.y + Math.sin(ang) * (r + len);
      ctx.strokeStyle = withAlpha(n.color, dim ? 0.08 : (related ? 0.42 : 0.22));
      ctx.lineWidth = (1.3 - i * 0.05) * inv;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.quadraticCurveTo(x2, y2, x3, y3);
      ctx.stroke();
      // synaptic tip
      ctx.fillStyle = withAlpha(n.color, dim ? 0.08 : 0.28);
      ctx.beginPath();
      ctx.arc(x3, y3, 1.2 * inv, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    // Soma body.
    const body = ctx.createRadialGradient(n.x - r * 0.35, n.y - r * 0.4, r * 0.15, n.x, n.y, r * 1.3);
    body.addColorStop(0, withAlpha('#ffffff', 0.92));
    body.addColorStop(0.18, withAlpha(n.color, 1));
    body.addColorStop(0.65, withAlpha(n.color, 0.95));
    body.addColorStop(1, withAlpha(n.color, 0.38));
    ctx.fillStyle = body;
    ctx.beginPath();
    ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
    ctx.fill();

    // Membrane ring.
    ctx.strokeStyle = withAlpha('#ffffff', lifted ? 0.75 : 0.26);
    ctx.lineWidth = (lifted ? 1.2 : 0.8) * inv;
    ctx.stroke();

    // Nucleus.
    ctx.fillStyle = withAlpha('#fff', dim ? 0.08 : 0.16);
    ctx.beginPath();
    ctx.arc(n.x - r * 0.12, n.y - r * 0.12, r * 0.38, 0, Math.PI * 2);
    ctx.fill();

    if (related) {
      for (let ring = 0; ring < 2; ring++) {
        const p = (phase + ring * 0.5) % 1;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4 + p * 14, 0, Math.PI * 2);
        ctx.strokeStyle = withAlpha(n.color, (1 - p) * 0.35);
        ctx.lineWidth = 1.3 * inv;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  function draw() {
    const c = els.graphCanvas;
    const ctx = c.getContext ? c.getContext('2d') : null;
    if (!ctx) return;
    const { dpr, view } = graph;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.setTransform(dpr * view.scale, 0, 0, dpr * view.scale, dpr * view.ox, dpr * view.oy);
    const hi = graph.hover || graph.selected;
    const selIds = graph.selectedIds || new Set();
    const now = performance.now();
    const phase = (now % 1600) / 1600;
    const inv = 1 / view.scale;

    // Тонкая сетка помогает читать масштаб и движение.
    const grid = 80;
    const topLeftX = -view.ox / view.scale;
    const topLeftY = -view.oy / view.scale;
    const visW = c.width / (dpr * view.scale);
    const visH = c.height / (dpr * view.scale);
    const startX = Math.floor(topLeftX / grid) * grid;
    const startY = Math.floor(topLeftY / grid) * grid;
    ctx.strokeStyle = 'rgba(255,255,255,0.025)';
    ctx.lineWidth = 1 * inv;
    ctx.beginPath();
    for (let x = startX; x < topLeftX + visW + grid; x += grid) { ctx.moveTo(x, topLeftY); ctx.lineTo(x, topLeftY + visH); }
    for (let y = startY; y < topLeftY + visH + grid; y += grid) { ctx.moveTo(topLeftX, y); ctx.lineTo(topLeftX + visW, y); }
    ctx.stroke();

    const focusedEdges = [];
    for (const e of graph.edges) {
      const focus = hi && (e.a === hi || e.b === hi);
      const pulse = graph.pulses.find((p) => p.edge === e);
      const conf = Math.max(0, Math.min(1, e.confidence || 0.55));
      if (focus) focusedEdges.push(e);
      const c1 = edgeCurve(e);
      ctx.strokeStyle = focus ? withAlpha(e.color || '#eceef1', 0.9)
        : withAlpha(e.color || '#9aa3b2', hi ? 0.04 : 0.1 + 0.2 * conf);
      ctx.lineWidth = (focus ? 2.1 : (0.7 + conf * 0.7)) * inv;
      if (focus || pulse) {
        ctx.setLineDash([7 * inv, 8 * inv]);
        ctx.lineDashOffset = -(now / 40) * inv;
      } else {
        ctx.setLineDash([]);
      }
      ctx.beginPath();
      ctx.moveTo(e.a.x, e.a.y);
      ctx.quadraticCurveTo(c1.cx, c1.cy, e.b.x, e.b.y);
      ctx.stroke();
      e._curve = c1;
    }
    ctx.setLineDash([]);

    if (hi) {
      for (const e of focusedEdges.slice(0, 18)) drawEdgeLabel(ctx, e, inv);
    }

    graph.pulses = graph.pulses.filter((p) => p.until > now);
    for (const p of graph.pulses) {
      const t = Math.max(0, Math.min(1, (now - p.t0) / (p.until - p.t0)));
      const c1 = p.edge._curve || edgeCurve(p.edge);
      const mt = 1 - t;
      const x = mt * mt * p.edge.a.x + 2 * mt * t * c1.cx + t * t * p.edge.b.x;
      const y = mt * mt * p.edge.a.y + 2 * mt * t * c1.cy + t * t * p.edge.b.y;
      const color = p.edge.color || '#eceef1';
      ctx.beginPath();
      ctx.arc(x, y, 3.2 * inv, 0, Math.PI * 2);
      ctx.fillStyle = withAlpha(color, 1 - t * 0.4);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, 8 * inv, 0, Math.PI * 2);
      ctx.fillStyle = withAlpha(color, (1 - t) * 0.22);
      ctx.fill();
    }

    for (const n of graph.nodes) {
      const related = hi && (n === hi || isNeighbor(n, hi) || selIds.has(n.id));
      const dim = hi && !related;
      const lifted = n === graph.hover || selIds.has(n.id) || (graph.nodeDrag && graph.nodeDrag.node === n);
      drawNeuronShape(ctx, n, inv, { dim, lifted, related, phase });

      if (n.isNew) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 3.5, 0, Math.PI * 2);
        ctx.strokeStyle = '#4cc38a';
        ctx.lineWidth = 1.5 * inv;
        ctx.stroke();
      }
      if (n.fixed) {
        ctx.beginPath();
        ctx.arc(n.x + n.r * 0.75, n.y - n.r * 0.75, 2.4 * inv, 0, Math.PI * 2);
        ctx.fillStyle = '#c3d7f7';
        ctx.fill();
      }
      if (selIds.has(n.id)) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 6.5, 0, Math.PI * 2);
        ctx.strokeStyle = n === graph.selected ? '#eceef1' : 'rgba(236,238,241,0.65)';
        ctx.lineWidth = (n === graph.selected ? 1.8 : 1.2) * inv;
        ctx.stroke();
      }
      if (graph.press && graph.press.node === n && !graph.press.longFired) {
        const p = Math.min(1, (now - graph.press.t0) / LONG_PRESS_MS);
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 10, -Math.PI / 2, -Math.PI / 2 + p * Math.PI * 2);
        ctx.strokeStyle = '#eceef1';
        ctx.lineWidth = 2 * inv;
        ctx.stroke();
      }
    }

    // Подписи: показываем выбранный/hover + самые связанные, но избегаем наложений.
    const labels = [];
    const candidates = [...graph.nodes].sort((a, b) => b.degree - a.degree).slice(0, 18);
    for (const n of selectedNodes()) if (!candidates.includes(n)) candidates.unshift(n);
    if (hi && !candidates.includes(hi)) candidates.unshift(hi);
    ctx.font = Math.round(12 * inv) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.textBaseline = 'middle';
    for (const n of candidates) {
      const text = shorten(n.label, 28);
      const x = n.x + n.r + 7 * inv;
      const y = n.y;
      const w = ctx.measureText(text).width;
      const h = 14 * inv;
      const box = { x, y: y - h / 2, w, h };
      const hit = labels.some((b) => box.x < b.x + b.w + 6 * inv && box.x + box.w + 6 * inv > b.x && box.y < b.y + b.h + 4 * inv && box.y + box.h + 4 * inv > b.y);
      if (hit && n !== hi && !selIds.has(n.id)) continue;
      labels.push(box);
      const dim = hi && !(n === hi || isNeighbor(n, hi) || selIds.has(n.id));
      ctx.fillStyle = dim ? 'rgba(236,238,241,0.32)' : 'rgba(236,238,241,0.95)';
      ctx.fillText(text, x, y);
    }
  }

  function nodeAt(x, y) {
    let best = null;
    let bestD = Infinity;
    const slack = 6 / graph.view.scale;
    for (const n of graph.nodes) {
      const d = Math.hypot(n.x - x, n.y - y);
      if (d <= n.r + slack && d < bestD) { best = n; bestD = d; }
    }
    return best;
  }
  function canvasPoint(ev) {
    const r = els.graphCanvas.getBoundingClientRect();
    const v = graph.view;
    return { x: (ev.clientX - r.left - v.ox) / v.scale,
             y: (ev.clientY - r.top - v.oy) / v.scale };
  }
  function screenPoint(ev) {
    const r = els.graphCanvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }
  function nodeScreenPoint(n) {
    const r = els.graphCanvas.getBoundingClientRect();
    const v = graph.view;
    return { x: r.left + v.ox + n.x * v.scale, y: r.top + v.oy + n.y * v.scale };
  }
  function zoomAt(sx, sy, factor) {
    const v = graph.view;
    const scale = Math.max(0.35, Math.min(4, v.scale * factor));
    if (scale === v.scale) return;
    const wx = (sx - v.ox) / v.scale;
    const wy = (sy - v.oy) / v.scale;
    v.scale = scale;
    v.ox = sx - wx * scale;
    v.oy = sy - wy * scale;
    kick();
  }
  function resetView() {
    graph.view = { scale: 1, ox: 0, oy: 0 };
    kick();
  }

  function selectedNodes() {
    return graph.nodes.filter((n) => graph.selectedIds.has(n.id));
  }

  function syncSelection(primary) {
    const ids = graph.selectedIds;
    if (primary && ids.has(primary.id)) graph.selected = primary;
    else if (graph.selected && ids.has(graph.selected.id)) { /* keep */ }
    else graph.selected = selectedNodes()[0] || null;
  }

  function toggleNodeSelection(n) {
    if (!n) return;
    if (graph.selectedIds.has(n.id)) graph.selectedIds.delete(n.id);
    else graph.selectedIds.add(n.id);
    syncSelection(n);
    selectNode(graph.selected, { silent: true });
  }

  function setSelectedNodes(nodes, primary) {
    graph.selectedIds = new Set(nodes.filter(Boolean).map((n) => n.id));
    syncSelection(primary || nodes[0] || null);
    selectNode(graph.selected, { silent: true });
  }

  function clearSelection() {
    graph.selectedIds.clear();
    graph.selected = null;
    selectNode(null, { silent: true });
  }

  function renderSelectionDetail(nodes) {
    const box = els.graphDetail;
    box.innerHTML = '';
    if (!nodes.length) {
      box.appendChild(el('div', 'empty', 'Нажмите на узел на карте. Ctrl+клик — выбрать несколько.'));
      return;
    }
    if (nodes.length === 1) {
      const n = nodes[0];
      const head = el('div', 'card');
      head.appendChild(el('div', 'fact-text', n.label));
      const meta = [n.type, 'связей: ' + n.degree];
      if (n.created) meta.push('с ' + new Date(n.created).toLocaleDateString('ru-RU'));
      if (n.isNew) meta.push('новый в этой сессии');
      if (!n.linked) meta.push('без сущности');
      head.appendChild(el('div', 'fact-meta', meta.filter(Boolean).join(' · ')));
      box.appendChild(head);
      const related = graph.edges.filter((e) => e.a === n || e.b === n).slice(0, 30);
      for (const e of related) {
        const card = el('div', 'card');
        card.appendChild(el('div', 'fact-text', e.a.label + ' — ' + e.label + ' → ' + e.b.label));
        const when = e.created ? ' · ' + new Date(e.created).toLocaleString('ru-RU') : '';
        card.appendChild(el('div', 'fact-meta', 'уверенность ' + e.confidence + when));
        const factId = e.id || e.fact_id;
        factActions(factId, card);
        attachMenu(card, (x, y) => showFactMenu(x, y, factId, e.a.label + ' — ' + e.label + ' → ' + e.b.label));
        box.appendChild(card);
      }
      return;
    }

    const head = el('div', 'card');
    head.appendChild(el('div', 'fact-text', 'Выбрано нейронов: ' + nodes.length));
    head.appendChild(el('div', 'fact-meta', nodes.map((n) => shorten(n.label, 28)).join(' · ')));
    const row = el('div', 'card-row');
    const mergeBtn = el('button', 'btn-primary', 'Авто-рекомбинация LLM');
    mergeBtn.type = 'button';
    mergeBtn.addEventListener('click', () => autoRecombineSelected(nodes));
    const clearBtn = el('button', 'btn-ghost', 'Снять выделение');
    clearBtn.type = 'button';
    clearBtn.addEventListener('click', clearSelection);
    row.append(mergeBtn, clearBtn);
    head.appendChild(row);
    box.appendChild(head);

    for (const n of nodes.slice(0, 12)) {
      const card = el('div', 'card');
      card.appendChild(el('div', 'fact-text', n.label));
      card.appendChild(el('div', 'fact-meta', [n.type, 'связей: ' + n.degree].filter(Boolean).join(' · ')));
      box.appendChild(card);
    }
  }

  function selectNode(n, opts) {
    const silent = opts && opts.silent;
    if (!silent) {
      if (n) graph.selectedIds = new Set([n.id]);
      else graph.selectedIds.clear();
    }
    graph.selected = n || null;
    draw();
    renderSelectionDetail(selectedNodes());
  }

  function factActions(fid, card) {
    if (!fid) return;
    const row = el('div', 'card-row');
    const yes = el('button', 'btn-ghost', 'Верно');
    yes.type = 'button';
    yes.addEventListener('click', () => memoryAction('/api/memory/confirm', { fact_id: fid, yes: true }, 'Факт подтверждён'));
    const no = el('button', 'btn-ghost', 'Неверно');
    no.type = 'button';
    no.addEventListener('click', () => memoryAction('/api/memory/confirm', { fact_id: fid, yes: false }, 'Уверенность снижена'));
    const distill = el('button', 'btn-ghost', 'Пересобрать');
    distill.type = 'button';
    distill.title = 'Темпоральная дистилляция: LLM обновит факт по свежим данным';
    distill.addEventListener('click', () => distillFact(fid));
    const rm = el('button', 'btn-danger', 'Отозвать');
    rm.type = 'button';
    rm.addEventListener('click', async () => {
      if (!(await ask('Отозвать факт?', 'Факт перестанет участвовать в ответах.', 'Отозвать'))) return;
      memoryAction('/api/memory/retract', { fact_id: fid }, 'Факт отозван');
    });
    row.append(yes, no, distill, rm);
    card.appendChild(row);
  }

  async function loadGraph() {
    if (graph.busy) return;
    graph.busy = true;
    resizeGraph();
    try {
      const data = await api('GET', '/api/memory/graph?limit=200');
      buildGraph(data);
    } catch (err) {
      els.graphStats.textContent = 'Ошибка: ' + (err.message || err);
    } finally {
      graph.busy = false;
    }
  }

  // ---------- memory drawer ----------

  function selectTab(name) {
    drawerTab = name;
    document.querySelectorAll('.tab').forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle('on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === 'pane-' + name));
    if (name === 'facts') loadFacts();
    if (name === 'status') refreshStatus();
    if (name === 'graph') requestAnimationFrame(loadGraph);
    if (name === 'search') setTimeout(() => els.searchQuery.focus(), 30);
  }
  function openDrawer(tab) {
    closeNav();
    els.drawer.classList.add('open');
    els.drawerScrim.classList.add('open');
    els.drawer.setAttribute('aria-hidden', 'false');
    els.drawerBtn.setAttribute('aria-expanded', 'true');
    selectTab(tab || 'facts');
  }
  function closeDrawer() {
    setFullscreen(false);
    els.drawer.classList.remove('open');
    els.drawerScrim.classList.remove('open');
    els.drawer.setAttribute('aria-hidden', 'true');
    els.drawerBtn.setAttribute('aria-expanded', 'false');
    drawerTab = null;
  }

  async function loadFacts() {
    if (factsBusy) return;
    factsBusy = true;
    els.factsMeta.textContent = 'загрузка…';
    els.factsList.innerHTML = '';
    try {
      const data = await api('GET', '/api/memory/facts?limit=100');
      const facts = Array.isArray(data.facts) ? data.facts : [];
      els.factsMeta.textContent = 'фактов: ' + facts.length;
      if (!facts.length) {
        els.factsList.appendChild(el('div', 'empty', 'Пока пусто. Факты появятся после сообщений или добавления текста.'));
        return;
      }
      for (const f of facts) {
        const card = el('div', 'card');
        card.appendChild(el('div', 'fact-text', f.text || ''));
        const src = f.source && f.source.doc_id ? ' · ' + f.source.doc_id : '';
        card.appendChild(el('div', 'fact-meta', 'уверенность ' + f.confidence + ' · обращений ' + f.access_count + src));
        factActions(f.fact_id, card);
        attachMenu(card, (x, y) => showFactMenu(x, y, f.fact_id, f.text));
        els.factsList.appendChild(card);
      }
    } catch (err) {
      els.factsMeta.textContent = '';
      els.factsList.appendChild(el('div', 'empty', 'Ошибка: ' + (err.message || err)));
    } finally {
      factsBusy = false;
    }
  }

  async function memoryAction(path, body, okText) {
    try {
      await api('POST', path, body);
      toast(okText);
      await loadFacts();
      if (graphVisible()) await loadGraph();
      refreshStatus();
    } catch (err) {
      toast('Ошибка: ' + (err.message || err));
    }
  }

  async function distillFact(fid) {
    try {
      toast('Пересобираем факт по актуальным данным…');
      const out = await api('POST', '/api/memory/distill', { fact_id: fid, model: settings.model || '' });
      const label = out.verdict === 'update' ? 'Факт пересобран'
        : out.verdict === 'retract' ? 'Факт отозван моделью' : 'Факт подтверждён моделью';
      toast(label + (out.reason ? ': ' + shorten(out.reason, 80) : ''));
      await loadFacts();
      if (graphVisible()) await loadGraph();
      refreshStatus();
    } catch (err) {
      toast('Ошибка дистилляции: ' + (err.message || err));
    }
  }

  // ---------- управление нейронами: правка, дедупликация, слияние ----------

  async function refreshMemoryViews() {
    await loadFacts();
    if (graphVisible()) await loadGraph();
    refreshStatus();
  }

  function openNeuronDialog(title, subtitle) {
    const d = el('dialog', 'dialog dialog-sm neuron-dialog');
    const body = el('div', 'dialog-body');
    const head = el('header');
    head.appendChild(el('h2', '', title));
    const x = el('button', 'icon-btn', '×');
    x.type = 'button'; x.title = 'Закрыть';
    x.addEventListener('click', () => d.close());
    head.appendChild(x);
    body.appendChild(head);
    const content = el('div', 'neuron-form');
    if (subtitle) content.appendChild(el('p', 'lead', subtitle));
    body.appendChild(content);
    const foot = el('footer');
    body.appendChild(foot);
    d.appendChild(body);
    d.addEventListener('close', () => d.remove());
    document.body.appendChild(d);
    d.showModal();
    return { d, content, foot };
  }

  function formField(label, value, opts) {
    opts = opts || {};
    const wrap = el('label', 'form-field');
    wrap.appendChild(el('span', '', label));
    let input;
    if (opts.options) {
      input = el('select');
      for (const o of opts.options) {
        const op = el('option', '', o.label);
        op.value = o.value;
        input.appendChild(op);
      }
      if (value) input.value = value;
    } else {
      input = el('input');
      input.type = opts.type || 'text';
      input.value = value === undefined || value === null ? '' : String(value);
      if (opts.step) input.step = opts.step;
      if (opts.min !== undefined) input.min = opts.min;
      if (opts.max !== undefined) input.max = opts.max;
      if (opts.placeholder) input.placeholder = opts.placeholder;
    }
    wrap.appendChild(input);
    return { wrap, input };
  }

  async function openEditDialog(n, ids) {
    const { d, content, foot } = openNeuronDialog('Ручная правка нейрона', shorten(n.label, 70));
    let facts = [];
    try {
      const out = await api('GET', '/api/memory/facts?limit=500');
      facts = (out.facts || []).filter((f) => ids.includes(f.fact_id));
    } catch (err) {
      toast('Не удалось загрузить факты: ' + (err.message || err));
    }
    if (!d.isConnected) return;
    if (!facts.length) {
      content.appendChild(el('div', 'empty', 'Активных фактов у нейрона нет — нечего править.'));
      return;
    }
    const first = facts[0];
    const pick = formField('Факт', first.fact_id,
      { options: facts.map((f) => ({ value: f.fact_id, label: shorten(f.text || f.fact_id, 72) })) });
    const s = formField('Субъект', first.s);
    const r = formField('Отношение', first.r);
    const o = formField('Объект', first.o);
    const conf = formField('Уверенность', first.confidence,
      { type: 'number', step: '0.05', min: 0, max: 1 });
    const reason = formField('Причина правки', '', { placeholder: 'необязательно' });
    content.append(pick.wrap, s.wrap, r.wrap, o.wrap, conf.wrap, reason.wrap);
    pick.input.addEventListener('change', () => {
      const f = facts.find((x) => x.fact_id === pick.input.value);
      if (!f) return;
      s.input.value = f.s || '';
      r.input.value = f.r || '';
      o.input.value = f.o || '';
      conf.input.value = f.confidence;
    });
    const cancel = el('button', 'btn-ghost', 'Отмена');
    cancel.type = 'button'; cancel.addEventListener('click', () => d.close());
    const save = el('button', 'btn-primary', 'Сохранить');
    save.type = 'button';
    save.addEventListener('click', async () => {
      save.disabled = true;
      try {
        await api('POST', '/api/memory/edit', {
          fact_id: pick.input.value, s: s.input.value, r: r.input.value, o: o.input.value,
          confidence: conf.input.value, reason: reason.input.value, model: settings.model || '',
        });
        toast('Факт нейрона обновлён');
        d.close();
        await refreshMemoryViews();
      } catch (err) {
        toast('Ошибка правки: ' + (err.message || err));
        save.disabled = false;
      }
    });
    foot.append(cancel, save);
  }

  function openDistillDialog(n, ids) {
    const { d, content, foot } = openNeuronDialog('Дистилляция нейрона',
      'LLM проверит факты по актуальным данным и пересоберёт устаревшие.');
    const rows = graph.edges.filter((e) => e.a === n || e.b === n);
    for (const e of rows) {
      const b = el('button', 'btn-ghost neuron-fact-btn',
        e.a.label + ' — ' + e.label + ' → ' + e.b.label);
      b.type = 'button';
      b.addEventListener('click', async () => {
        b.disabled = true;
        await distillFact(e.id || e.fact_id);
        d.close();
      });
      content.appendChild(b);
    }
    const all = el('button', 'btn-primary', 'Пересобрать все (' + rows.length + ')');
    all.type = 'button';
    all.addEventListener('click', async () => {
      all.disabled = true;
      for (const e of rows) await distillFact(e.id || e.fact_id);
      d.close();
      await refreshMemoryViews();
    });
    const cancel = el('button', 'btn-ghost', 'Отмена');
    cancel.type = 'button'; cancel.addEventListener('click', () => d.close());
    foot.append(cancel, all);
  }

  async function openDedupeDialog(ids) {
    const { d, content, foot } = openNeuronDialog('Дедупликация LLM',
      ids.length ? 'Сканируем ' + ids.length + ' фактов нейрона…' : 'Сканируем всю память…');
    content.appendChild(el('div', 'muted', 'Модель ищет смысловые повторы, а не только совпадение слов.'));
    try {
      const out = await api('POST', '/api/memory/dedupe', {
        fact_ids: ids, model: settings.model || '', limit: 200,
      });
      if (!d.isConnected) return;
      content.innerHTML = '';
      const groups = out.groups || [];
      if (!groups.length) {
        content.appendChild(el('div', 'empty', 'Смысловых дублей не найдено.'));
        return;
      }
      const checks = [];
      for (const g of groups) {
        const card = el('div', 'card');
        const label = el('label', 'check');
        const cb = el('input');
        cb.type = 'checkbox'; cb.checked = true;
        label.append(cb, document.createTextNode(' ' + shorten(g.canonical || g.reason || 'Объединить группу', 80)));
        card.appendChild(label);
        card.appendChild(el('div', 'fact-meta', (g.ids || []).join(' · ')));
        if (g.reason) card.appendChild(el('div', 'fact-meta', g.reason));
        content.appendChild(card);
        checks.push({ cb, g });
      }
      const apply = el('button', 'btn-primary', 'Объединить выбранные');
      apply.type = 'button';
      apply.addEventListener('click', async () => {
        apply.disabled = true;
        let done = 0;
        for (const { cb, g } of checks) {
          if (!cb.checked) continue;
          try {
            await api('POST', '/api/memory/recombine', {
              fact_ids: g.ids, model: settings.model || '', apply: true,
            });
            done++;
          } catch (err) {
            toast('Ошибка слияния: ' + (err.message || err));
          }
        }
        d.close();
        toast('Объединено групп: ' + done);
        await refreshMemoryViews();
      });
      foot.appendChild(apply);
    } catch (err) {
      d.close();
      toast('Ошибка поиска дублей: ' + (err.message || err));
    }
  }

  async function openMergeDialog(n, ids) {
    const others = graph.nodes.filter((x) => x !== n);
    if (!others.length) { toast('Нет другого нейрона для слияния'); return; }
    const { d, content, foot } = openNeuronDialog('Рекомбинация нейронов',
      'Источник: ' + shorten(n.label, 70));
    const pick = formField('Объединить с', others[0].id,
      { options: others.map((o) => ({ value: o.id, label: shorten(o.label, 72) })) });
    content.appendChild(pick.wrap);
    const preview = el('div', 'merge-preview fact-meta',
      'Нажмите «Предъединить», чтобы LLM предложил общую триаду.');
    content.appendChild(preview);
    let mergedIds = [];
    const recalc = () => {
      const t = graph.byId[pick.input.value];
      mergedIds = [...new Set(ids.concat(t ? nodeFactIds(t) : []))];
      preview.textContent = 'Фактов в объединении: ' + mergedIds.length + '. Нажмите «Предъединить».';
    };
    recalc();
    pick.input.addEventListener('change', recalc);
    const cancel = el('button', 'btn-ghost', 'Отмена');
    cancel.type = 'button'; cancel.addEventListener('click', () => d.close());
    const pre = el('button', 'btn-ghost', 'Предъединить LLM');
    pre.type = 'button';
    pre.addEventListener('click', async () => {
      pre.disabled = true;
      preview.textContent = 'LLM объединяет факты…';
      try {
        const out = await api('POST', '/api/memory/recombine', {
          fact_ids: mergedIds, model: settings.model || '', apply: false,
        });
        const p = out.preview || {};
        preview.textContent = [p.s, '—', p.r, '→', p.o].join(' ') +
          ' · уверенность ' + p.confidence + (p.reason ? '\n' + p.reason : '');
        apply.disabled = false;
      } catch (err) {
        preview.textContent = 'Ошибка: ' + (err.message || err);
      } finally {
        pre.disabled = false;
      }
    });
    const apply = el('button', 'btn-primary', 'Применить');
    apply.type = 'button'; apply.disabled = true;
    apply.addEventListener('click', async () => {
      apply.disabled = true;
      try {
        await api('POST', '/api/memory/recombine', {
          fact_ids: mergedIds, model: settings.model || '', apply: true,
        });
        toast('Нейроны объединены');
        d.close();
        await refreshMemoryViews();
      } catch (err) {
        toast('Ошибка слияния: ' + (err.message || err));
        apply.disabled = false;
      }
    });
    foot.append(cancel, pre, apply);
  }

  function renderSearch(r) {
    els.searchResults.innerHTML = '';
    const status = r.status === 'rag_fallback' ? 'только фрагменты (RAG)' : 'факты и фрагменты';
    els.searchResults.appendChild(el('div', 'muted', 'статус: ' + status));
    const facts = Array.isArray(r.facts) ? r.facts : [];
    const chunks = Array.isArray(r.chunks) ? r.chunks : [];
    if (!facts.length && !chunks.length) {
      els.searchResults.appendChild(el('div', 'empty', 'Ничего не найдено'));
      return;
    }
    for (const f of facts) {
      const card = el('div', 'card');
      card.appendChild(el('span', 'score', 'score ' + f.score));
      card.appendChild(el('div', 'fact-text', f.text || ''));
      card.appendChild(el('div', 'fact-meta', 'уверенность ' + f.confidence));
      els.searchResults.appendChild(card);
    }
    for (const c of chunks) {
      const card = el('div', 'card');
      card.appendChild(el('div', 'chunk', c.text || ''));
      els.searchResults.appendChild(card);
    }
  }

  async function runSearch(e) {
    e.preventDefault();
    const query = (els.searchQuery.value || '').trim();
    if (!query) return;
    els.searchResults.innerHTML = '';
    els.searchResults.appendChild(el('div', 'empty', 'Ищу…'));
    try {
      const r = await api('POST', '/api/memory/search', { query, k: 8 });
      renderSearch(r);
    } catch (err) {
      els.searchResults.innerHTML = '';
      els.searchResults.appendChild(el('div', 'empty', 'Ошибка: ' + (err.message || err)));
    }
  }

  async function runIngest() {
    const text = (els.ingestText.value || '').trim();
    if (!text) return;
    els.ingestBtn.disabled = true;
    els.ingestOut.textContent = 'сохраняю…';
    try {
      const body = { text, role: 'user' };
      if (settings.model) body.model = settings.model;   // факты извлекаются моделью чата
      const r = await api('POST', '/api/memory/ingest', body);
      const ok = (r.facts || []).filter((f) => f.status === 'ok').length;
      const labels = {
        ok: 'готово',
        no_extractor: 'документ сохранён, извлечение выключено',
        extraction_failed: 'документ сохранён, экстракция не удалась',
      };
      els.ingestOut.textContent = (labels[r.status] || r.status) + ' · фактов: ' + ok;
      els.ingestText.value = '';
      toast('Текст сохранён в память');
      refreshStatus();
      loadFacts();
    } catch (err) {
      els.ingestOut.textContent = 'Ошибка: ' + (err.message || err);
    } finally {
      els.ingestBtn.disabled = false;
    }
  }

  async function consolidate() {
    els.consolidateBtn.disabled = true;
    els.sleepOut.hidden = false;
    els.sleepOut.textContent = 'Считаю…';
    try {
      const r = await api('POST', '/api/memory/consolidate');
      els.sleepOut.textContent = JSON.stringify(r, null, 2);
    } catch (err) {
      els.sleepOut.textContent = 'Ошибка: ' + (err.message || err);
    } finally {
      els.consolidateBtn.disabled = false;
    }
  }

  // ---------- nav ----------

  function openNav() {
    closeDrawer();
    els.app.classList.add('nav-open');
    els.scrim.classList.add('open');
    els.menuBtn.setAttribute('aria-expanded', 'true');
  }
  function closeNav() {
    els.app.classList.remove('nav-open');
    els.scrim.classList.remove('open');
    els.menuBtn.setAttribute('aria-expanded', 'false');
  }

  // ---------- export / delete ----------

  function chatDocument(id) {
    const s = sessions.find((v) => v.id === id);
    const list = id ? (messages[id] || []) : [];
    if (!s || !list.length) return '';
    const lines = ['# ' + (s.title || 'Чат'), ''];
    for (const m of list) {
      lines.push(m.role === 'user' ? '## Вы' : '## Ассистент');
      if (m.reasoning && settings.showReasoning) lines.push('', '> ' + m.reasoning.replace(/\n/g, '\n> '));
      lines.push('', m.text || (m.error ? 'Ошибка: ' + m.error : (m.note || '')));
      if (m.role !== 'user' && Array.isArray(m.sources) && m.sources.length) {
        lines.push('', 'Источники:');
        for (const src of m.sources) {
          if (isHttpUrl(src.url)) lines.push('- ' + (src.n ? '[' + src.n + '] ' : '') + (src.title || '') + ' — ' + src.url);
        }
      }
      lines.push('');
    }
    return lines.join('\n');
  }

  function exportChat(id) {
    const s = sessions.find((v) => v.id === id);
    const doc = chatDocument(id);
    if (!s || !doc) return;
    download(safeName(s.title) + '.md', doc, 'text/markdown;charset=utf-8');
  }
  function exportCurrent() { if (currentId) exportChat(currentId); }

  // Один чат = один документ в памяти. Повтор той же history не создаёт дубли:
  // сервер выводит recorded_at из времени последнего сообщения, а doc_id хеширует текст.
  async function saveChatMemory(id, opts) {
    const s = sessions.find((v) => v.id === id);
    if (!s) { toast('Чат не найден'); return; }
    const list = messages[id] || [];
    if (!list.length) { toast('В чате нет сообщений'); return; }
    try {
      const body = { chat_id: chatKey(s), messages: list };
      if (settings.model) body.model = settings.model;
      const out = await api('POST', '/api/chat/save-memory', body);
      const facts = Array.isArray(out.facts) ? out.facts : [];
      const ok = facts.filter((f) => f && f.status === 'ok').length;
      const conflicts = facts.filter((f) => f && f.status === 'conflict').length;
      toast('В память: ' + (out.messages || list.length) + ' сообщений · ' + ok + ' фактов' +
            (conflicts ? ' · ' + conflicts + ' конфликт(ов)' : ''));
      if (!factsBusy) loadFacts();
      if (opts && opts.command && drawerTab === 'facts') loadFacts();
    } catch (err) {
      toast('Ошибка: ' + (err.message || err));
    }
  }

  async function clearChat(id) {
    if (busy) { toast('Сначала остановите ответ'); return; }
    const s = sessions.find((v) => v.id === id);
    if (!s) return;
    if (!(await ask('Очистить историю?', 'Сообщения исчезнут из интерфейса, а контекст модели начнётся заново.', 'Очистить'))) return;
    const oldChatId = chatKey(s);
    messages[id] = [];
    s.chatId = newId();
    s.updated = Date.now();
    try { await api('DELETE', '/api/chats?chat_id=' + encodeURIComponent(oldChatId)); } catch (_) { /* ignore */ }
    saveMessages(id);
    saveSessions();
    renderSessions();
    updateTitle();
    if (id === currentId) renderMessages({ force: true });
    toast('История очищена');
  }
  function clearCurrent() { if (currentId) return clearChat(currentId); }

  async function deleteChat(id) {
    if (busy) { toast('Сначала остановите ответ'); return; }
    const s = sessions.find((v) => v.id === id);
    if (!s) return;
    const wasCurrent = id === currentId;
    if (!(await ask('Удалить чат?', '«' + (s.title || 'Новый чат') + '» будет удалён вместе с сообщениями на всех устройствах.', 'Удалить'))) return;
    try { await api('DELETE', '/api/chats?chat_id=' + encodeURIComponent(chatKey(s))); } catch (_) { /* ignore */ }
    try { localStorage.removeItem(LS.msgs + id); } catch { /* ignore */ }
    delete messages[id];
    sessions = sessions.filter((x) => x.id !== id);
    if (!sessions.length) {
      const fresh = newSession();
      sessions = [fresh];
      messages[fresh.id] = [];
      currentId = fresh.id;
    } else if (wasCurrent) {
      currentId = sessions[0].id;
    }
    saveSessions();
    renderSessions();
    updateTitle();
    if (wasCurrent || !messages[currentId]) renderMessages({ force: true });
    toast('Чат удалён');
  }
  function deleteCurrent() { if (currentId) return deleteChat(currentId); }

  function exportAll() {
    const data = { exported: new Date().toISOString(), sessions, messages };
    download('triplex-chats-' + new Date().toISOString().slice(0, 10) + '.json', JSON.stringify(data, null, 2), 'application/json;charset=utf-8');
  }

  async function wipeAll() {
    if (busy) { toast('Сначала остановите ответ'); return; }
    if (!(await ask('Удалить все чаты?', 'Все чаты и сообщения вашего аккаунта будут удалены на всех устройствах. Настройки останутся.', 'Удалить всё'))) return;
    try { await api('DELETE', '/api/chats'); } catch (_) { /* ignore */ }
    for (const s of sessions) {
      try { localStorage.removeItem(LS.msgs + s.id); } catch { /* ignore */ }
    }
    try { localStorage.removeItem(LS.sessions); localStorage.removeItem(LS.current); } catch { /* ignore */ }
    const fresh = newSession();
    sessions = [fresh];
    messages = { [fresh.id]: [] };
    currentId = fresh.id;
    saveSessions();
    renderSessions();
    updateTitle();
    renderMessages({ force: true });
    els.settingsDialog.close();
    toast('Все чаты удалены');
  }

  // ---------- events ----------

  // Контекстное меню на «пустой» области чата. Композер намеренно НЕ тронут:
  // на iOS его поле должно получать системное меню выделения/вставки без помех.
  attachMenu(els.messages, (x, y) => showBlankMenu(x, y), '.msg');

  els.newChatBtn.addEventListener('click', createChat);
  els.menuBtn.addEventListener('click', openNav);
  els.scrim.addEventListener('click', closeNav);
  els.searchInput.addEventListener('input', renderSessions);
  els.searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { els.searchInput.value = ''; renderSessions(); els.searchInput.blur(); }
  });

  els.renameBtn.addEventListener('click', startRename);
  els.chatTitle.addEventListener('click', () => { if (currentSession()) startRename(); });
  attachMenu(els.chatTitle, (x, y) => { if (currentId) showChatMenu(x, y, currentId); });
  els.chatTitle.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finishRename(true); els.chatTitle.blur(); }
    if (e.key === 'Escape') { e.preventDefault(); finishRename(false); els.chatTitle.blur(); }
  });
  els.chatTitle.addEventListener('blur', () => finishRename(true));

  els.exportBtn.addEventListener('click', exportCurrent);
  els.clearBtn.addEventListener('click', clearCurrent);
  els.deleteBtn.addEventListener('click', deleteCurrent);
  els.modelChip.addEventListener('click', () => openDrawer('status'));
  els.drawerBtn.addEventListener('click', () => openDrawer('facts'));
  els.drawerClose.addEventListener('click', closeDrawer);
  els.drawerScrim.addEventListener('click', closeDrawer);

  document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => selectTab(t.dataset.tab)));
  els.factsReload.addEventListener('click', loadFacts);
  els.searchForm.addEventListener('submit', runSearch);
  els.ingestBtn.addEventListener('click', runIngest);
  els.ingestText.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); runIngest(); }
  });
  els.statusReload.addEventListener('click', refreshStatus);
  els.consolidateBtn.addEventListener('click', consolidate);

  els.graphReload.addEventListener('click', loadGraph);
  if (els.graphRelayout) els.graphRelayout.addEventListener('click', relayoutGraph);
  els.graphZoomIn.addEventListener('click', () => zoomAt(graph.W / 2, graph.H / 2, 1.2));
  els.graphZoomOut.addEventListener('click', () => zoomAt(graph.W / 2, graph.H / 2, 1 / 1.2));
  els.graphZoomReset.addEventListener('click', resetView);
  function setFullscreen(on) {
    if (on === graph.fullscreen) return;
    graph.fullscreen = on;
    if (on) {
      graph.stageHome = { parent: els.graphStage.parentNode, next: els.graphStage.nextSibling };
      document.body.appendChild(els.graphStage);   // вне drawer: backdrop-filter ломает position:fixed
    } else if (graph.stageHome) {
      const { parent, next } = graph.stageHome;
      if (next && next.parentNode === parent) parent.insertBefore(els.graphStage, next);
      else parent.appendChild(els.graphStage);
      graph.stageHome = null;
    }
    els.graphStage.classList.toggle('fullscreen', on);
    els.graphFullscreen.textContent = on ? '↙' : '⛶';
    els.graphFullscreen.title = on ? 'Выйти из полноэкранного режима' : 'На весь экран';
    resizeGraph();
  }
  els.graphFullscreen.addEventListener('click', () => setFullscreen(!graph.fullscreen));
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && graph.fullscreen) {
      ev.preventDefault();
      ev.stopImmediatePropagation();   // не закрываем панель вместе с полноэкранным режимом
      setFullscreen(false);
    }
  });

  function pointerScreen(ev) {
    const r = els.graphCanvas.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }
  function clearPress() {
    if (graph.press && graph.press.timer) clearTimeout(graph.press.timer);
    graph.press = null;
  }
  function beginPinch() {
    const pts = [...graph.pointers.values()];
    if (pts.length < 2) return;
    const [a, b] = pts;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    graph.pinch = {
      dist: Math.hypot(dx, dy) || 1,
      mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
      view: { ...graph.view },
    };
    clearPress();
    graph.nodeDrag = null;
    graph.drag = null;
  }

  els.graphCanvas.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 && ev.pointerType !== 'touch') return;
    if (ev.button !== 0) return;
    ev.preventDefault();
    els.graphCanvas.setPointerCapture(ev.pointerId);
    const s = pointerScreen(ev);
    graph.pointers.set(ev.pointerId, s);
    if (graph.pointers.size >= 2) {
      beginPinch();
      return;
    }
    const p = canvasPoint(ev);
    const node = nodeAt(p.x, p.y);
    if (node) {
      clearPress();
      graph.press = { pointerId: ev.pointerId, node, sx: s.x, sy: s.y, t0: performance.now(), moved: false, longFired: false, multi: ev.ctrlKey || ev.metaKey };
      graph.press.timer = setTimeout(() => {
        if (!graph.press || graph.press.moved || graph.press.pointerId !== ev.pointerId) return;
        graph.press.longFired = true;
        if (!graph.selectedIds.has(graph.press.node.id)) setSelectedNodes([graph.press.node], graph.press.node);
        const at = nodeScreenPoint(graph.press.node);
        showNodeMenu(at.x, at.y, graph.press.node);
      }, LONG_PRESS_MS);
      els.graphCanvas.style.cursor = 'grabbing';
    } else {
      clearPress();
      if (!ev.shiftKey) clearSelection();
      graph.drag = { pointerId: ev.pointerId, sx: s.x, sy: s.y, ox: graph.view.ox, oy: graph.view.oy };
      els.graphCanvas.style.cursor = 'grabbing';
    }
  });

  els.graphCanvas.addEventListener('pointermove', (ev) => {
    if (!graph.pointers.has(ev.pointerId)) {
      const p = canvasPoint(ev);
      const n = nodeAt(p.x, p.y);
      if (n !== graph.hover) { graph.hover = n; draw(); }
      els.graphCanvas.style.cursor = n ? 'grab' : 'default';
      return;
    }
    const s = pointerScreen(ev);
    graph.pointers.set(ev.pointerId, s);

    if (graph.pinch && graph.pointers.size >= 2) {
      const pts = [...graph.pointers.values()];
      const [a, b] = pts;
      const dist = Math.hypot(b.x - a.x, b.y - a.y) || 1;
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const base = graph.pinch.view;
      const factor = dist / graph.pinch.dist;
      const scale = Math.max(0.35, Math.min(4, base.scale * factor));
      const wx = (graph.pinch.mid.x - base.ox) / base.scale;
      const wy = (graph.pinch.mid.y - base.oy) / base.scale;
      graph.view.scale = scale;
      graph.view.ox = mid.x - wx * scale;
      graph.view.oy = mid.y - wy * scale;
      kick();
      return;
    }

    if (graph.drag && graph.drag.pointerId === ev.pointerId) {
      graph.view.ox = graph.drag.ox + (s.x - graph.drag.sx);
      graph.view.oy = graph.drag.oy + (s.y - graph.drag.sy);
      kick();
      return;
    }

    const p = canvasPoint(ev);
    if (graph.nodeDrag && graph.nodeDrag.pointerId === ev.pointerId) {
      const d = graph.nodeDrag;
      d.node.x = p.x - d.offX;
      d.node.y = p.y - d.offY;
      d.node.vx = 0; d.node.vy = 0;
      d.node.fixed = true;
      kick();
      return;
    }

    const n = nodeAt(p.x, p.y);
    if (graph.press && graph.press.pointerId === ev.pointerId && !graph.press.moved &&
        Math.hypot(s.x - graph.press.sx, s.y - graph.press.sy) > 6) {
      graph.press.moved = true;
      clearTimeout(graph.press.timer);
      if (graph.press.multi) {
        if (!graph.selectedIds.has(graph.press.node.id)) toggleNodeSelection(graph.press.node);
      } else if (!graph.selectedIds.has(graph.press.node.id)) {
        setSelectedNodes([graph.press.node], graph.press.node);
      }
      graph.nodeDrag = { pointerId: ev.pointerId, node: graph.press.node, offX: p.x - graph.press.node.x, offY: p.y - graph.press.node.y };
      els.graphCanvas.style.cursor = 'grabbing';
    }
    if (n !== graph.hover) {
      graph.hover = n;
      if (!graph.nodeDrag && !graph.drag) els.graphCanvas.style.cursor = n ? 'grab' : 'default';
      draw();
    }
  });

  function endPointer(ev) {
    const id = ev && ev.pointerId;
    if (id != null) graph.pointers.delete(id);
    if (graph.pinch) {
      if (graph.pointers.size < 2) {
        graph.pinch = null;
        const rem = [...graph.pointers.entries()][0];
        if (rem) {
          const [pid, s] = rem;
          graph.drag = { pointerId: pid, sx: s.x, sy: s.y, ox: graph.view.ox, oy: graph.view.oy };
        }
      }
    }
    if (graph.press && (id == null || graph.press.pointerId === id)) {
      const press = graph.press;
      clearPress();
      if (!press.moved && !press.longFired) {
        if (press.multi) toggleNodeSelection(press.node);
        else selectNode(graph.selected === press.node && graph.selectedIds.size === 1 ? null : press.node);
      }
    }
    if (graph.nodeDrag && (id == null || graph.nodeDrag.pointerId === id)) {
      graph.nodeDrag.node.vx = 0;
      graph.nodeDrag.node.vy = 0;
      graph.nodeDrag = null;
      graph.temp = Math.max(graph.temp, 0.12);
    }
    if (graph.drag && (id == null || graph.drag.pointerId === id)) {
      graph.drag = null;
    }
    if (!graph.pointers.size) {
      clearPress();
      graph.nodeDrag = null;
      graph.drag = null;
      graph.pinch = null;
    }
    els.graphCanvas.style.cursor = graph.hover ? 'grab' : 'default';
    draw();
    kick();
  }

  els.graphCanvas.addEventListener('pointerup', endPointer);
  els.graphCanvas.addEventListener('pointercancel', endPointer);
  els.graphCanvas.addEventListener('pointerleave', (ev) => {
    if (graph.pointers.has(ev.pointerId)) return;
    if (!graph.nodeDrag && !graph.drag && !graph.pinch) { graph.hover = null; draw(); }
  });
  els.graphCanvas.addEventListener('contextmenu', (ev) => {
    ev.preventDefault();
    const p = canvasPoint(ev);
    const n = nodeAt(p.x, p.y);
    if (n) {
      if (!graph.selectedIds.has(n.id)) setSelectedNodes([n], n);
      showNodeMenu(ev.clientX, ev.clientY, n);
    } else {
      showBlankMenu(ev.clientX, ev.clientY);
    }
  });
  els.graphCanvas.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const s = screenPoint(ev);
    const factor = Math.pow(1.0015, -ev.deltaY);
    zoomAt(s.x, s.y, Math.max(0.8, Math.min(1.25, factor)));
  }, { passive: false });
  window.addEventListener('resize', () => { if (graphVisible()) resizeGraph(); });
  // Факты появляются фоном (очередь индексации) — карта обновляется сама, пока открыта.
  setInterval(() => {
    if (graphVisible() && els.graphLive.checked && !busy) loadGraph();
  }, 4000);

  // ---------- голосовой ввод: долгое нажатие на кнопку отправки ----------
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  let voiceRec = null, voiceTimer = null, voiceStarted = false, suppressSend = false;

  function voiceCleanup() {
    clearTimeout(voiceTimer);
    voiceTimer = null;
    if (els.composer) els.composer.classList.remove('recording');
    els.sendBtn.classList.remove('recording');
    els.input.placeholder = 'Напишите сообщение…';
    updateComposer();
  }
  function stopVoice() {
    if (voiceRec) {
      const r = voiceRec;
      voiceRec = null;
      r.onresult = null;
      r.onerror = null;
      try { r.stop(); } catch (_) { /* уже остановлено */ }
    }
    voiceCleanup();
    els.input.focus();
  }
  function startVoice() {
    if (!SpeechRec || busy || voiceRec) return;
    const base = els.input.value ? els.input.value.replace(/\s*$/, ' ') : '';
    let finalText = '';
    voiceRec = new SpeechRec();
    voiceRec.lang = 'ru-RU';
    voiceRec.continuous = true;
    voiceRec.interimResults = true;
    voiceRec.onresult = (ev) => {
      let interim = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      els.input.value = base + finalText + interim;
      autoSize();
      updateCounter();
    };
    voiceRec.onerror = (ev) => {
      if (ev.error === 'aborted' || ev.error === 'no-speech') return;
      const old = els.input.placeholder;
      els.input.placeholder = 'Микрофон недоступен (' + ev.error + ')';
      setTimeout(() => { if (!voiceRec) els.input.placeholder = old; }, 2500);
    };
    voiceRec.onend = () => { voiceRec = null; voiceCleanup(); };
    els.sendBtn.classList.add('recording');
    if (els.composer) els.composer.classList.add('recording');
    els.input.placeholder = 'Говорите…';
    els.sendBtn.innerHTML = ICON_MIC;
    try { voiceRec.start(); } catch (_) { voiceRec = null; voiceCleanup(); }
  }

  els.sendBtn.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (!SpeechRec || busy || voiceRec) return;
    voiceStarted = false;
    clearTimeout(voiceTimer);
    voiceTimer = setTimeout(() => {
      voiceTimer = null;
      voiceStarted = true;
      suppressSend = true;
      startVoice();
      if (navigator.vibrate) navigator.vibrate(15);
    }, 450);
  });
  for (const type of ['pointerup', 'pointercancel', 'pointerleave']) {
    els.sendBtn.addEventListener(type, () => {
      clearTimeout(voiceTimer);
      voiceTimer = null;
      if (voiceStarted) { voiceStarted = false; stopVoice(); }
    });
  }
  els.sendBtn.addEventListener('click', (e) => {
    if (suppressSend) { suppressSend = false; e.preventDefault(); e.stopPropagation(); return; }
    if (busy) stopGeneration(); else send();
  });
  els.input.addEventListener('input', () => {
    autoSize();
    updateCounter();
    cmdIndex = 0;
    updateCmdPop();
  });
  els.input.addEventListener('blur', () => setTimeout(hideCmdPop, 120));
  els.input.addEventListener('keydown', (e) => {
    if (!els.cmdPop.hidden && cmdItems.length) {
      if (e.key === 'ArrowDown') { e.preventDefault(); moveCmd(1); return; }
      if (e.key === 'ArrowUp') { e.preventDefault(); moveCmd(-1); return; }
      if (e.key === 'Escape') { e.preventDefault(); hideCmdPop(); return; }
      if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey)) {
        e.preventDefault();
        pickCommand(cmdItems[cmdIndex]);
        return;
      }
    }
    if (e.key !== 'Enter') return;
    const force = e.ctrlKey || e.metaKey;
    if (force || (settings.enterSends && !e.shiftKey)) {
      e.preventDefault();
      if (!busy) send();
    }
  });
  els.reasonBtn.addEventListener('click', () => {
    settings.showReasoning = !settings.showReasoning;
    saveSettings();
    applySettings();
    renderMessages();
  });
  els.webTool.addEventListener('click', () => {
    settings.webSearch = !settings.webSearch;
    saveSettings();
    applySettings();
    toast(settings.webSearch ? 'Интернет включён: модель может искать и читать страницы' : 'Интернет выключен');
  });
  if (els.voiceBtn) {
    els.voiceBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (menuEl && menuEl.classList.contains('voice-menu')) closeMenu();
      else showVoiceMenu(els.voiceBtn);
    });
  }
  els.scrollBottomBtn.addEventListener('click', scrollEnd);
  els.messages.addEventListener('scroll', updateScrollButton, { passive: true });

  els.settingsBtn.addEventListener('click', () => {
    applySettings();
    els.settingsDialog.showModal();
  });
  els.modelSelect.addEventListener('change', () => {
    settings.model = els.modelSelect.value;
    saveSettings();
    toast(settings.model ? 'Модель: ' + settings.model : 'Модель по умолчанию');
  });
  els.modelsReload.addEventListener('click', loadModels);
  els.setCompact.addEventListener('change', () => { settings.compact = els.setCompact.checked; saveSettings(); applySettings(); });
  els.setFontSize.addEventListener('change', () => { settings.fontSize = els.setFontSize.value; saveSettings(); applySettings(); });
  els.setAutoScroll.addEventListener('change', () => { settings.autoScroll = els.setAutoScroll.checked; saveSettings(); });
  els.setEnterSends.addEventListener('change', () => { settings.enterSends = els.setEnterSends.checked; saveSettings(); applySettings(); });
  els.setReasoning.addEventListener('change', () => {
    settings.showReasoning = els.setReasoning.checked;
    saveSettings();
    applySettings();
    renderMessages();
  });
  els.exportAll.addEventListener('click', exportAll);
  els.wipeAll.addEventListener('click', wipeAll);

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && String(e.key).toLowerCase() === 'k') {
      e.preventDefault();
      createChat();
      return;
    }
    const tag = (e.target && e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable);
    // «/» в поле чата — это начало команды, а не переход к поиску по чатам.
    if (e.key === '/' && !typing) {
      e.preventDefault();
      if (window.innerWidth <= 860) {
        openNav();
        requestAnimationFrame(() => els.searchInput.focus());
      } else {
        els.searchInput.focus();
      }
      return;
    }
    if (e.key === 'Escape') {
      if (els.imageDialog && els.imageDialog.open) return; // диалог закроется сам
      if (els.app.classList.contains('nav-open')) closeNav();
      else if (els.drawer.classList.contains('open')) closeDrawer();
    }
  });

  // Кнопки синхронизации и MCP-токена в настройках.
  if (els.syncNow) els.syncNow.addEventListener('click', () => { syncPull().then(requestWatch); });
  if (els.mcpTokenRotate) els.mcpTokenRotate.addEventListener('click', rotateMcpToken);
  if (els.mcpTokenRevoke) els.mcpTokenRevoke.addEventListener('click', revokeMcpToken);
  if (els.settingsBtn) els.settingsBtn.addEventListener('click', loadMcpToken);

  // ---------- просмотр изображений ----------
  if (els.imageDialog) {
    if (els.imageClose) els.imageClose.addEventListener('click', () => els.imageDialog.close());
    if (els.imagePrev) els.imagePrev.addEventListener('click', () => stepImage(-1));
    if (els.imageNext) els.imageNext.addEventListener('click', () => stepImage(1));
    if (els.imageStage) {
      els.imageStage.addEventListener('click', (e) => {
        if (e.target === els.imageStage) els.imageDialog.close();
      });
    }
    // Клик по фону самого dialog (за пределами белой области) тоже закрывает.
    els.imageDialog.addEventListener('click', (e) => {
      if (e.target === els.imageDialog) els.imageDialog.close();
    });
    els.imageDialog.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') { e.preventDefault(); stepImage(-1); }
      if (e.key === 'ArrowRight') { e.preventDefault(); stepImage(1); }
    });
    let touchX = 0, touchY = 0;
    els.imageDialog.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1) return;
      touchX = e.touches[0].clientX;
      touchY = e.touches[0].clientY;
    }, { passive: true });
    els.imageDialog.addEventListener('touchend', (e) => {
      if (!e.changedTouches.length) return;
      const dx = e.changedTouches[0].clientX - touchX;
      const dy = e.changedTouches[0].clientY - touchY;
      if (dy > 90 && Math.abs(dy) > Math.abs(dx) * 1.4) els.imageDialog.close();
      else if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.4) stepImage(dx < 0 ? 1 : -1);
    }, { passive: true });
  }

  // ---------- start ----------

  (async function start() {
  // Старый интерфейс регистрировал /sw.js: снимаем регистрацию и чистим кэш.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations()
      .then((regs) => Promise.all(regs.map((reg) => reg.unregister())))
      .catch(() => {});
  }
  if (window.caches) {
    caches.keys()
      .then((keys) => Promise.all(keys.map((key) => caches.delete(key))))
      .catch(() => {});
  }

  // PWA standalone: отделяем от обычной вкладки браузера (стиль и жесты).
  if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches || navigator.standalone) {
    document.body.classList.add('standalone');
  }
  await checkAuth();   // дождёмся uid: от него зависят ключи localStorage и email в сайдбаре
  loadSettings();
  loadState();
  SYNC.ready = true;   // только теперь локальные изменения уходят на сервер
  applySettings();
  pullSettings();      // серверная копия настроек (если свежее локальной)
  updateComposer();
  updateCounter();
  renderMetrics();
  loadModels();
  renderSessions();
  updateTitle();
  renderMessages({ force: true });
  loadTtsStatus();
  refreshStatus();
  setInterval(refreshStatus, 10000);
  // Подтягиваем чаты с сервера и подписываемся на изменения других устройств.
  await syncPull();
  startStateEvents();
  await watchRemote();
  setInterval(() => { syncPull().then(requestWatch); }, 20000);
  els.input.focus();
  })();
})();
