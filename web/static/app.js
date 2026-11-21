(function () {
  'use strict';

  const LS = {
    sessions: 'pulsenet.sessions.v1',
    msgs: 'pulsenet.msgs.v1.',
    current: 'pulsenet.current.v1',
    settings: 'pulsenet.settings.v1',
  };
  const DEFAULTS = {
    compact: false,
    fontSize: 'm',
    autoScroll: true,
    enterSends: true,
    showReasoning: false,
    webSearch: false,
    model: '',
  };
  // Команды: серверные уходят в чат, local — выполняются в интерфейсе.
  const COMMANDS = [
    { name: 'search', arg: 'запрос', hint: 'поиск в интернете и ответ по источникам' },
    { name: 'fetch', arg: 'адрес', hint: 'прочитать страницу и ответить по ней' },
    { name: 'memory', arg: 'запрос', hint: 'найти в памяти факты и фрагменты' },
    { name: 'remember', arg: 'текст', hint: 'сохранить в память' },
    { name: 'graph', hint: 'карта нейронов памяти', local: true },
    { name: 'new', hint: 'новый чат', local: true },
  ];
  const SUGGESTIONS = [
    { title: 'Что ты помнишь?', prompt: 'Что ты знаешь обо мне и моих предпочтениях?' },
    { title: 'Поиск в интернете', prompt: '/search ', fill: true, hint: 'введите запрос после команды' },
    { title: 'Сохранить в память', prompt: '/remember ', fill: true, hint: 'напишите, что запомнить' },
    { title: 'Карта нейронов', prompt: '/graph', local: true, hint: 'как растёт память' },
  ];
  const PALETTE = ['#8ea2ff', '#4cc38a', '#e0a458', '#e5544f', '#c58af9', '#5bc0de', '#f28db2', '#b5c77a'];
  const ICON_SEND = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2 11 13"/><path d="m22 2-7 20-4-9-9-4Z"/></svg>';
  const ICON_STOP = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
  const ICON_COPY = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

  const $ = (id) => document.getElementById(id);
  const els = {
    app: $('app'), sidebar: $('sidebar'), scrim: $('scrim'), statusDot: $('statusDot'),
    sessionList: $('sessionList'), searchInput: $('searchInput'), newChatBtn: $('newChatBtn'),
    chatTitle: $('chatTitle'), chatSub: $('chatSub'), modelChip: $('modelChip'), modelName: $('modelName'),
    renameBtn: $('renameBtn'), exportBtn: $('exportBtn'), clearBtn: $('clearBtn'), deleteBtn: $('deleteBtn'),
    drawerBtn: $('drawerBtn'), menuBtn: $('menuBtn'), messages: $('messages'), inner: $('inner'),
    scrollBottomBtn: $('scrollBottomBtn'), input: $('input'), sendBtn: $('sendBtn'), counter: $('counter'),
    hint: $('hint'), reasonBtn: $('reasonBtn'), webTool: $('webTool'), memoryTool: $('memoryTool'),
    memoryBtn: $('memoryBtn'), memCount: $('memCount'), sbStat: $('sbStat'), settingsBtn: $('settingsBtn'),
    cmdPop: $('cmdPop'),
    drawer: $('drawer'), drawerScrim: $('drawerScrim'), drawerClose: $('drawerClose'), drawerSub: $('drawerSub'),
    factsReload: $('factsReload'), factsMeta: $('factsMeta'), factsList: $('factsList'),
    searchForm: $('searchForm'), searchQuery: $('searchQuery'), searchResults: $('searchResults'),
    ingestText: $('ingestText'), ingestBtn: $('ingestBtn'), ingestOut: $('ingestOut'),
    graphReload: $('graphReload'), graphLive: $('graphLive'), graphStats: $('graphStats'),
    graphStage: $('graphStage'), graphCanvas: $('graphCanvas'), graphEmpty: $('graphEmpty'),
    graphZoomIn: $('graphZoomIn'), graphZoomOut: $('graphZoomOut'), graphZoomReset: $('graphZoomReset'),
    graphFullscreen: $('graphFullscreen'),
    graphLegend: $('graphLegend'), graphGrowth: $('graphGrowth'), graphDetail: $('graphDetail'),
    statusReload: $('statusReload'), consolidateBtn: $('consolidateBtn'), statusGrid: $('statusGrid'),
    errorsBox: $('errorsBox'), sleepOut: $('sleepOut'),
    settingsDialog: $('settingsDialog'), setCompact: $('setCompact'), setFontSize: $('setFontSize'),
    setAutoScroll: $('setAutoScroll'), setEnterSends: $('setEnterSends'), setReasoning: $('setReasoning'),
    setModel: $('setModel'), modelsReload: $('modelsReload'), modelHint: $('modelHint'),
    exportAll: $('exportAll'), wipeAll: $('wipeAll'),
    confirmDialog: $('confirmDialog'), confirmTitle: $('confirmTitle'), confirmText: $('confirmText'),
    confirmYes: $('confirmYes'), toast: $('toast'),
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
  let drawerTab = null;
  let cmdItems = [];
  let cmdIndex = 0;

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
  }
  function saveMessages(id) {
    if (!id || !Array.isArray(messages[id])) return;
    writeJSON(LS.msgs + id, messages[id].slice(-300));
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
      messages[s.id] = Array.isArray(list) ? list.map((m) => {
        if (!m || typeof m !== 'object') return null;
        if (m.pending) {
          m.pending = false;
          if (!m.note) m.note = m.text ? 'Ответ прерван' : 'Ответ не получен';
        }
        return m;
      }).filter(Boolean) : [];
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
  function ask(title, text, okLabel) {
    return new Promise((resolve) => {
      els.confirmTitle.textContent = title;
      els.confirmText.textContent = text;
      els.confirmYes.textContent = okLabel || 'Удалить';
      const done = () => {
        els.confirmDialog.removeEventListener('close', done);
        resolve(els.confirmDialog.returnValue === 'confirm');
      };
      els.confirmDialog.addEventListener('close', done);
      if (els.confirmDialog.open) els.confirmDialog.close();
      els.confirmDialog.showModal();
    });
  }
  async function api(method, path, body) {
    const r = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    return data;
  }

  // ---------- settings ----------

  function loadSettings() {
    settings = { ...DEFAULTS, ...(readJSON(LS.settings, {}) || {}) };
  }
  function saveSettings() {
    writeJSON(LS.settings, settings);
  }
  function applySettings() {
    document.body.dataset.fs = settings.fontSize || 'm';
    document.body.classList.toggle('compact', !!settings.compact);
    els.reasonBtn.setAttribute('aria-pressed', settings.showReasoning ? 'true' : 'false');
    els.webTool.setAttribute('aria-pressed', settings.webSearch ? 'true' : 'false');
    els.setCompact.checked = !!settings.compact;
    els.setFontSize.value = settings.fontSize || 'm';
    els.setAutoScroll.checked = !!settings.autoScroll;
    els.setEnterSends.checked = !!settings.enterSends;
    els.setReasoning.checked = !!settings.showReasoning;
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

  function paintBubble(bubble, m, live) {
    const wasOpen = !!(bubble.querySelector('details.reasoning') || {}).open;
    bubble.innerHTML = '';
    const isBot = m.role !== 'user';
    if (isBot && m.reasoning && settings.showReasoning) {
      const d = el('details', 'reasoning');
      d.open = wasOpen || (!!m.pending && !m.text);
      d.appendChild(el('summary', '', 'Рассуждения'));
      d.appendChild(el('div', 'rbody', m.reasoning));
      bubble.appendChild(d);
    }
    if (isBot && Array.isArray(m.tools) && m.tools.length) {
      const row = el('div', 'tool-row');
      for (const t of m.tools) {
        const chip = el('span', 'tool-chip ' + (t.status || 'running'));
        const mark = t.status === 'done' ? '✓ ' : (t.status === 'error' ? '✕ ' : '');
        chip.textContent = mark + (t.label || t.name || 'инструмент');
        if (t.detail) chip.title = t.detail;
        row.appendChild(chip);
      }
      bubble.appendChild(row);
    }
    if (m.pending && !m.text) {
      const t = el('div', 'typing');
      t.append(el('span'), el('span'), el('span'));
      bubble.appendChild(t);
    } else if (m.text) {
      const md = el('div', 'markdown-body');
      md.innerHTML = renderMd(m.text);
      bubble.appendChild(md);
      if (!live) decorate(md);
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

  function buildMessage(m, idx) {
    const isUser = m.role === 'user';
    const root = el('div', 'msg ' + (isUser ? 'user' : 'bot') + (m.error ? ' error' : ''));
    root.dataset.idx = String(idx);
    if (!isUser) root.appendChild(el('div', 'avatar-bot', '✦'));
    const body = el('div', 'msg-body');
    const bubble = el('div', 'bubble');
    if (isUser) bubble.textContent = m.text || '';
    else paintBubble(bubble, m, false);
    body.appendChild(bubble);

    const foot = el('div', 'msg-foot');
    foot.appendChild(el('span', 'time', fmtTime(m.time)));
    if (m.text && !m.pending) {
      const copy = el('button', 'act');
      copy.type = 'button';
      copy.title = 'Копировать';
      copy.setAttribute('aria-label', 'Копировать');
      copy.innerHTML = ICON_COPY;
      copy.addEventListener('click', () => copyText(m.text));
      foot.appendChild(copy);
    }
    body.appendChild(foot);
    root.appendChild(body);
    return root;
  }

  function renderMessages(opts) {
    opts = opts || {};
    const wasAtBottom = isNearBottom();
    els.inner.innerHTML = '';
    const s = currentSession();
    const list = s ? (messages[s.id] || []) : [];
    if (!list.length) {
      els.inner.appendChild(welcomeNode());
    } else {
      list.forEach((m, i) => els.inner.appendChild(buildMessage(m, i)));
    }
    updateActions();
    updateScrollButton();
    if (opts.force || (wasAtBottom && settings.autoScroll)) scrollEnd();
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
      paintBubble(bubble, bot, true);
      if (atBottom && settings.autoScroll) scrollEnd();
    }, 60);
  }

  // ---------- инструменты и источники в ответе ----------

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

  async function streamChat(body, onEvent, signal) {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) {
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
  }

  async function send(override) {
    if (busy) return;
    const text = String(override !== undefined ? override : els.input.value).trim();
    if (!text) return;
    const local = /^\/(graph|new)\s*$/i.exec(text);
    if (local) {
      els.input.value = '';
      autoSize();
      updateCounter();
      hideCmdPop();
      runLocal(local[1].toLowerCase());
      return;
    }
    const s = ensureSession();
    const list = messages[s.id] || (messages[s.id] = []);
    if (!list.length && (!s.title || s.title === 'Новый чат')) {
      s.title = text.replace(/\s+/g, ' ').slice(0, 48);
    }
    const user = { role: 'user', text, time: Date.now() };
    const bot = { role: 'assistant', text: '', reasoning: '', time: Date.now(), pending: true, tools: [], sources: [] };
    list.push(user, bot);
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
    renderSessions();
    updateTitle();
    renderMessages({ force: true });
    updateComposer();

    const botIdx = list.length - 1;
    let finished = false;

    try {
      const body = { message: text, chat_id: s.chatId || s.id, web: !!settings.webSearch };
      if (settings.model) body.model = settings.model;
      await streamChat(body, (ev) => {
        if (typeof ev.token === 'string') { bot.text += ev.token; schedulePaint(bot, botIdx); }
        if (typeof ev.reasoning === 'string') { bot.reasoning += ev.reasoning; schedulePaint(bot, botIdx); }
        if (ev.tool) { upsertTool(bot, ev.tool); schedulePaint(bot, botIdx); }
        if (Array.isArray(ev.sources)) { addSources(bot, ev.sources); schedulePaint(bot, botIdx); }
        if (ev.error) { bot.error = String(ev.error); bot.pending = false; }
        if (ev.done) {
          if (!bot.text && ev.done.answer) bot.text = ev.done.answer;
          bot.pending = false;
          bot.indexPending = Number(ev.done.index_pending) || 0;
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
      saveSessions();
      saveMessages(s.id);
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
    fetch('/api/chat/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' } }).catch(() => {});
    toast('Останавливаю ответ…');
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
    const select = els.setModel;
    els.modelHint.textContent = 'загрузка списка…';
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
      select.value = settings.model || '';
      els.modelHint.textContent = models.length
        ? 'Модель сохраняется и применяется к следующему ответу. Эмбеддинги в список не входят и не перезагружаются.'
        : 'Моделей нет: загрузите модель в LM Studio или проверьте LLM_BACKEND.';
    } catch (err) {
      els.modelHint.textContent = 'Список недоступен: ' + (err.message || err);
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
      const m = s.memory || {};
      els.statusDot.className = 'status ok';
      els.modelChip.classList.remove('bad');
      els.modelName.textContent = s.model || s.backend || 'модель';
      els.modelChip.title = 'Бэкенд: ' + (s.backend || '—') + (s.model ? ' · модель: ' + s.model : '');
      els.chatSub.textContent = (s.backend || 'backend') + (s.model ? ' · ' + s.model : '') +
        ' · фактов: ' + (m.facts_active ?? 0);
      els.memCount.textContent = m.facts_active ?? 0;
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
    hover: null, selected: null, busy: false, typeColors: {},
    view: { scale: 1, ox: 0, oy: 0 }, drag: null, press: null, fullscreen: false,
    sessionStart: Date.now(),
  };
  const LAYOUT_FRAMES = 300;
  const LONG_PRESS_MS = 450;

  function typeColor(type) {
    const key = type || 'без типа';
    if (!graph.typeColors[key]) {
      graph.typeColors[key] = PALETTE[Object.keys(graph.typeColors).length % PALETTE.length];
    }
    return graph.typeColors[key];
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

  function buildGraph(data) {
    const prev = graph.byId;
    const W = graph.W || 300;
    const H = graph.H || 340;
    const nodes = (data.nodes || []).map((n) => {
      const old = prev[n.id];
      const created = parseIso(n.created);
      return {
        id: n.id, label: n.label || n.id, type: n.type || null, degree: n.degree || 0,
        created, isNew: created >= graph.sessionStart, linked: !!n.linked,
        r: 4 + Math.min(10, Math.sqrt(n.degree || 1) * 2.6),
        x: old ? old.x : W / 2 + (Math.random() - 0.5) * W * 0.6,
        y: old ? old.y : H / 2 + (Math.random() - 0.5) * H * 0.6,
        dx: 0, dy: 0, color: typeColor(n.type),
      };
    });
    const byId = {};
    for (const n of nodes) byId[n.id] = n;
    const edges = [];
    for (const e of data.edges || []) {
      const a = byId[e.source];
      const b = byId[e.target];
      if (a && b) edges.push({ ...e, a, b, created: parseIso(e.recorded_at) });
    }
    const changed = nodes.length !== Object.keys(prev).length || nodes.some((n) => !prev[n.id]);
    graph.nodes = nodes;
    graph.edges = edges;
    graph.byId = byId;
    if (changed) {
      graph.temp = W / 8;
      graph.frames = 0;
    }
    renderGraphMeta(data, nodes, edges);
    const sel = graph.selected && byId[graph.selected.id];
    selectNode(sel || null);
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
  function frame() {
    graph.raf = 0;
    const laying = graph.frames < LAYOUT_FRAMES && graph.nodes.length > 0;
    if (laying) {
      layoutStep();
      graph.frames++;
    }
    draw();
    if (laying || ((graph.selected || graph.hover) && graphVisible())) kick();
  }

  function layoutStep() {
    const { nodes, edges, W, H } = graph;
    const n = nodes.length;
    const k = Math.sqrt((W * H) / n) * 0.9;
    for (const a of nodes) { a.dx = 0; a.dy = 0; }
    for (let i = 0; i < n; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j++) {
        const b = nodes[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = (k * k) / d;
        a.dx += (dx / d) * f;
        a.dy += (dy / d) * f;
        b.dx -= (dx / d) * f;
        b.dy -= (dy / d) * f;
      }
    }
    for (const e of edges) {
      const dx = e.a.x - e.b.x;
      const dy = e.a.y - e.b.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d * d) / k;
      e.a.dx -= (dx / d) * f;
      e.a.dy -= (dy / d) * f;
      e.b.dx += (dx / d) * f;
      e.b.dy += (dy / d) * f;
    }
    const cx = W / 2;
    const cy = H / 2;
    const t = graph.temp;
    for (const a of nodes) {
      a.dx += (cx - a.x) * 0.01;
      a.dy += (cy - a.y) * 0.01;
      const len = Math.sqrt(a.dx * a.dx + a.dy * a.dy) || 0.01;
      const lim = Math.min(len, t);
      a.x += (a.dx / len) * lim;
      a.y += (a.dy / len) * lim;
      a.x = Math.max(a.r + 4, Math.min(W - a.r - 4, a.x));
      a.y = Math.max(a.r + 4, Math.min(H - a.r - 4, a.y));
    }
    graph.temp = Math.max(0.5, t * 0.985);
  }

  function isNeighbor(n, hi) {
    return graph.edges.some((e) => (e.a === hi && e.b === n) || (e.b === hi && e.a === n));
  }

  function withAlpha(hex, alpha) {
    let h = String(hex || '#8ea2ff').replace('#', '');
    if (h.length === 3) h = h.split('').map((x) => x + x).join('');
    const v = parseInt(h, 16) || 0x8ea2ff;
    return 'rgba(' + ((v >> 16) & 255) + ',' + ((v >> 8) & 255) + ',' + (v & 255) + ',' + alpha + ')';
  }

  function draw() {
    const c = els.graphCanvas;
    const ctx = c.getContext ? c.getContext('2d') : null;
    if (!ctx) return;
    const { dpr, view } = graph;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.setTransform(dpr * view.scale, 0, 0, dpr * view.scale, dpr * view.ox, dpr * view.oy);
    const hi = graph.selected || graph.hover;
    const now = performance.now();
    const phase = (now % 1600) / 1600;
    const inv = 1 / view.scale;

    for (const e of graph.edges) {
      const focus = hi && (e.a === hi || e.b === hi);
      const conf = Math.max(0, Math.min(1, e.confidence || 0));
      ctx.strokeStyle = focus ? 'rgba(236,238,241,.85)'
        : 'rgba(154,163,178,' + (0.14 + 0.45 * conf).toFixed(2) + ')';
      ctx.lineWidth = focus ? 1.8 * inv : (0.6 + conf) * inv;
      if (focus) {
        ctx.setLineDash([7 * inv, 8 * inv]);
        ctx.lineDashOffset = -(now / 40) * inv;   // бегущий импульс по связи
      } else {
        ctx.setLineDash([]);
      }
      ctx.beginPath();
      ctx.moveTo(e.a.x, e.a.y);
      ctx.lineTo(e.b.x, e.b.y);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    for (const n of graph.nodes) {
      const related = hi && (n === hi || isNeighbor(n, hi));
      const dim = hi && !related;
      ctx.globalAlpha = dim ? 0.25 : 0.95;
      if (related) {
        for (let ring = 0; ring < 2; ring++) {
          const p = (phase + ring * 0.5) % 1;
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 4 + p * 22, 0, Math.PI * 2);
          ctx.strokeStyle = withAlpha(n.color, (1 - p) * 0.55);
          ctx.lineWidth = 1.4 * inv;
          ctx.stroke();
        }
      }
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = n.color;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (n.isNew) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 3.5, 0, Math.PI * 2);
        ctx.strokeStyle = '#4cc38a';
        ctx.lineWidth = 1.5 * inv;
        ctx.stroke();
      }
      if (n === graph.selected) {
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 6, 0, Math.PI * 2);
        ctx.strokeStyle = '#eceef1';
        ctx.lineWidth = 1.5 * inv;
        ctx.stroke();
      }
      if (graph.press && graph.press.node === n && !graph.press.longFired) {
        const p = Math.min(1, (now - graph.press.t0) / LONG_PRESS_MS);
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 9, -Math.PI / 2, -Math.PI / 2 + p * Math.PI * 2);
        ctx.strokeStyle = '#eceef1';
        ctx.lineWidth = 2 * inv;
        ctx.stroke();
      }
    }

    const labelled = new Set([...graph.nodes].sort((a, b) => b.degree - a.degree).slice(0, 14));
    if (hi) labelled.add(hi);
    ctx.font = Math.round(12 * inv) + 'px system-ui, -apple-system, Segoe UI, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#eceef1';
    for (const n of labelled) {
      ctx.fillText(shorten(n.label, 26), n.x + n.r + 5, n.y);
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

  function selectNode(n) {
    graph.selected = n;
    draw();
    const box = els.graphDetail;
    box.innerHTML = '';
    if (!n) {
      box.appendChild(el('div', 'empty', 'Нажмите на узел на карте.'));
      return;
    }
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
      factActions(e.id || e.fact_id, card);
      box.appendChild(card);
    }
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

  function exportCurrent() {
    const s = currentSession();
    const list = s ? (messages[s.id] || []) : [];
    if (!list.length) return;
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
    download(safeName(s.title) + '.md', lines.join('\n'), 'text/markdown;charset=utf-8');
  }

  async function clearCurrent() {
    if (busy) { toast('Сначала остановите ответ'); return; }
    const s = currentSession();
    if (!s) return;
    if (!(await ask('Очистить историю?', 'Сообщения исчезнут из интерфейса, а контекст модели начнётся заново.', 'Очистить'))) return;
    messages[s.id] = [];
    s.chatId = newId();
    s.updated = Date.now();
    saveMessages(s.id);
    saveSessions();
    renderMessages({ force: true });
    updateTitle();
    toast('История очищена');
  }

  async function deleteCurrent() {
    if (busy) { toast('Сначала остановите ответ'); return; }
    const s = currentSession();
    if (!s) return;
    if (!(await ask('Удалить чат?', '«' + (s.title || 'Новый чат') + '» будет удалён вместе с сообщениями.', 'Удалить'))) return;
    try { localStorage.removeItem(LS.msgs + s.id); } catch { /* ignore */ }
    delete messages[s.id];
    sessions = sessions.filter((x) => x.id !== s.id);
    if (!sessions.length) {
      const fresh = newSession();
      sessions = [fresh];
      messages[fresh.id] = [];
      currentId = fresh.id;
    } else {
      currentId = sessions[0].id;
    }
    saveSessions();
    renderSessions();
    updateTitle();
    renderMessages({ force: true });
    toast('Чат удалён');
  }

  function exportAll() {
    const data = { exported: new Date().toISOString(), sessions, messages };
    download('triplex-chats-' + new Date().toISOString().slice(0, 10) + '.json', JSON.stringify(data, null, 2), 'application/json;charset=utf-8');
  }

  async function wipeAll() {
    if (busy) { toast('Сначала остановите ответ'); return; }
    if (!(await ask('Удалить все чаты?', 'Все чаты и сообщения в этом браузере будут удалены. Настройки останутся.', 'Удалить всё'))) return;
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

  els.newChatBtn.addEventListener('click', createChat);
  els.menuBtn.addEventListener('click', openNav);
  els.scrim.addEventListener('click', closeNav);
  els.searchInput.addEventListener('input', renderSessions);
  els.searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { els.searchInput.value = ''; renderSessions(); els.searchInput.blur(); }
  });

  els.renameBtn.addEventListener('click', startRename);
  els.chatTitle.addEventListener('click', () => { if (currentSession()) startRename(); });
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
  els.memoryBtn.addEventListener('click', () => openDrawer('facts'));
  els.memoryTool.addEventListener('click', () => openDrawer('facts'));
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

  els.graphCanvas.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    const s = screenPoint(ev);
    const p = canvasPoint(ev);
    const node = nodeAt(p.x, p.y);
    if (node) {
      graph.press = { node, sx: s.x, sy: s.y, t0: performance.now(), moved: false, longFired: false };
      graph.press.timer = setTimeout(() => {
        if (!graph.press || graph.press.moved) return;
        graph.press.longFired = true;
        selectNode(graph.press.node);
        toast('Нейрон выбран: ' + shorten(graph.press.node.label, 30));
      }, LONG_PRESS_MS);
    } else {
      graph.drag = { sx: s.x, sy: s.y, ox: graph.view.ox, oy: graph.view.oy };
      els.graphCanvas.style.cursor = 'grabbing';
    }
  });
  els.graphCanvas.addEventListener('pointermove', (ev) => {
    const s = screenPoint(ev);
    if (graph.drag) {
      graph.view.ox = graph.drag.ox + (s.x - graph.drag.sx);
      graph.view.oy = graph.drag.oy + (s.y - graph.drag.sy);
      kick();
      return;
    }
    const p = canvasPoint(ev);
    const n = nodeAt(p.x, p.y);
    if (graph.press && !graph.press.moved &&
        Math.hypot(s.x - graph.press.sx, s.y - graph.press.sy) > 6) {
      graph.press.moved = true;
      clearTimeout(graph.press.timer);
    }
    if (n !== graph.hover) {
      graph.hover = n;
      els.graphCanvas.style.cursor = n ? 'pointer' : 'default';
      draw();
    }
  });
  function endPointer() {
    const press = graph.press;
    if (press) {
      clearTimeout(press.timer);
      if (!press.moved && !press.longFired) selectNode(graph.selected === press.node ? null : press.node);
    }
    graph.press = null;
    graph.drag = null;
    els.graphCanvas.style.cursor = graph.hover ? 'pointer' : 'default';
    draw();
  }
  els.graphCanvas.addEventListener('pointerup', endPointer);
  els.graphCanvas.addEventListener('pointercancel', endPointer);
  els.graphCanvas.addEventListener('pointerleave', () => { graph.hover = null; draw(); });
  els.graphCanvas.addEventListener('contextmenu', (ev) => {
    ev.preventDefault();
    const p = canvasPoint(ev);
    const n = nodeAt(p.x, p.y);
    if (n) selectNode(n);
  });
  els.graphCanvas.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const s = screenPoint(ev);
    zoomAt(s.x, s.y, ev.deltaY < 0 ? 1.12 : 1 / 1.12);
  }, { passive: false });
  window.addEventListener('resize', () => { if (graphVisible()) resizeGraph(); });
  // Факты появляются фоном (очередь индексации) — карта обновляется сама, пока открыта.
  setInterval(() => {
    if (graphVisible() && els.graphLive.checked && !busy) loadGraph();
  }, 4000);

  els.sendBtn.addEventListener('click', () => { if (busy) stopGeneration(); else send(); });
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
  els.scrollBottomBtn.addEventListener('click', scrollEnd);
  els.messages.addEventListener('scroll', updateScrollButton, { passive: true });

  els.settingsBtn.addEventListener('click', () => {
    applySettings();
    els.settingsDialog.showModal();
    loadModels();
  });
  els.setModel.addEventListener('change', () => {
    settings.model = els.setModel.value;
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
      if (els.app.classList.contains('nav-open')) closeNav();
      else if (els.drawer.classList.contains('open')) closeDrawer();
    }
  });

  // ---------- start ----------

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

  loadSettings();
  loadState();
  applySettings();
  updateComposer();
  updateCounter();
  renderSessions();
  updateTitle();
  renderMessages({ force: true });
  refreshStatus();
  setInterval(refreshStatus, 10000);
  els.input.focus();
})();
