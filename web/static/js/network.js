/* Страница «Сеть импульсов»: состояние, граф ансамблей, урок, импульс, развитие.
 * Все данные — с сервера pulsenet (/net/*). Ничего не хранится в браузере. */
(function () {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const $ = (sel) => document.querySelector(sel);
  const fmt = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('ru-RU'));

  let graph = null;          // последний ответ /net/graph
  const pos = new Map();     // id ансамбля -> {x, y}: позиции сохраняются между обновлениями
  let selected = null;

  // ---------- сеть запросов ----------

  async function api(path, body) {
    const opt = body === undefined
      ? { cache: 'no-store' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    let data = {};
    try { data = await r.json(); } catch (_) { /* пустой ответ */ }
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    return data;
  }

  function toast(text, isError) {
    const t = $('#toast');
    t.textContent = text;
    t.style.color = isError ? '#f0a09d' : '';
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.textContent = ''; }, 4000);
  }

  // ---------- состояние и карточки ----------

  function renderStats(state) {
    const s = state.stats;
    const cards = [
      ['Слова (нейроны)', fmt(s['слов'])],
      ['Ансамбли', fmt(s['ансамблей'])],
      ['Связи', fmt(s['связей'])],
      ['Уроки', fmt(state.lessons)],
      ['Опыт для повтора', fmt(s['в_памяти_опыта'])],
      ['Размер на диске', state.on_disk ? Math.round(state.size_bytes / 1024) + ' КБ' : 'нет'],
      ['Мысль (фокус)', (state.focus || []).map((f) => f.words.join(' ') || ('ансамбль ' + f.ensemble)).slice(0, 2).join(' · ') || '—'],
      ['Ошибка самопредсказания', state.self_error === null || state.self_error === undefined ? '—' : state.self_error],
      ['Припоминание', state.eval_last === null || state.eval_last === undefined ? '—' : Math.round(state.eval_last * 100) + '%'],
    ];
    $('#stats').innerHTML = cards.map(([k, v]) =>
      `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
    const pill = $('#saveState');
    if (state.on_disk) {
      const when = state.saved_at ? new Date(state.saved_at * 1000).toLocaleString('ru-RU') : '';
      pill.textContent = 'сохранено ' + when;
      pill.className = 'pill ok';
    } else {
      pill.textContent = 'не сохранено';
      pill.className = 'pill dirty';
    }
  }

  // ---------- граф: силовая раскладка ----------

  function layout(nodes, edges) {
    const W = 900, H = 520;
    nodes.forEach((n, i) => {
      if (!pos.has(n.id)) {
        const a = (i / Math.max(1, nodes.length)) * Math.PI * 2;
        pos.set(n.id, { x: W / 2 + Math.cos(a) * 180 + (i % 7), y: H / 2 + Math.sin(a) * 180 + (i % 5), vx: 0, vy: 0 });
      }
    });
    const P = nodes.map((n) => pos.get(n.id));
    const idx = new Map(nodes.map((n, i) => [n.id, i]));
    const links = edges.map((e) => [idx.get(e.source), idx.get(e.target)]).filter(([a, b]) => a !== undefined && b !== undefined);
    const iters = nodes.length > 250 ? 60 : 160;
    const k = Math.sqrt((W * H) / Math.max(1, nodes.length));
    for (let it = 0; it < iters; it++) {
      const temp = 40 * (1 - it / iters) + 1;
      for (const p of P) { p.dx = 0; p.dy = 0; }
      for (let i = 0; i < P.length; i++) {
        for (let j = i + 1; j < P.length; j++) {
          let dx = P[i].x - P[j].x, dy = P[i].y - P[j].y;
          const d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
          const f = (k * k) / d;
          dx = (dx / d) * f; dy = (dy / d) * f;
          P[i].dx += dx; P[i].dy += dy; P[j].dx -= dx; P[j].dy -= dy;
        }
      }
      for (const [a, b] of links) {
        const dx = P[a].x - P[b].x, dy = P[a].y - P[b].y;
        const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
        const f = (d * d) / k;
        P[a].dx -= (dx / d) * f; P[a].dy -= (dy / d) * f;
        P[b].dx += (dx / d) * f; P[b].dy += (dy / d) * f;
      }
      for (const p of P) {
        p.dx += (W / 2 - p.x) * 0.02; p.dy += (H / 2 - p.y) * 0.02;
        const m = Math.sqrt(p.dx * p.dx + p.dy * p.dy) + 0.01;
        const s = Math.min(m, temp) / m;
        p.x = Math.max(30, Math.min(W - 30, p.x + p.dx * s));
        p.y = Math.max(30, Math.min(H - 30, p.y + p.dy * s));
      }
    }
  }

  function el(tag, attrs, parent) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }

  function drawGraph() {
    const svg = $('#graph');
    svg.setAttribute('viewBox', '0 0 900 520');
    svg.innerHTML = '';
    const defs = el('defs', {}, svg);
    const marker = el('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 22, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' }, defs);
    el('path', { d: 'M0,0 L10,5 L0,10 z', fill: '#6b7385' }, marker);
    if (!graph || !graph.nodes.length) {
      el('text', { x: 450, y: 260, 'text-anchor': 'middle', fill: '#8b93a1', 'font-size': 14 }, svg).textContent =
        'Сеть пока пуста. Дайте ей урок справа или задайте вопрос в чате.';
      return;
    }
    // показываем ансамбли, у которых есть хотя бы одна связь или больше одного нейрона
    const shown = graph.nodes.filter((n) => n.size > 1 || n.degree > 0);
    const ids = new Set(shown.map((n) => n.id));
    const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    layout(shown, edges);
    const maxW = Math.max(0.0001, ...edges.map((e) => e.weight));
    for (const e of edges) {
      const a = pos.get(e.source), b = pos.get(e.target);
      el('line', { class: 'edge', 'data-s': e.source, 'data-t': e.target,
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        'stroke-width': (0.8 + 3.2 * e.weight / maxW).toFixed(2) }, svg);
    }
    const nodeG = {};
    for (const n of shown) {
      const p = pos.get(n.id);
      const g = el('g', { class: 'node', 'data-id': n.id, transform: `translate(${p.x},${p.y})` }, svg);
      el('circle', { r: (6 + 3 * Math.sqrt(n.size)).toFixed(1), fill: n.size > 1 ? '#4c5568' : '#2c313b' }, g);
      el('text', { x: 0, y: -(10 + 3 * Math.sqrt(n.size)), 'text-anchor': 'middle' }, g).textContent =
        n.words.slice(0, 3).join(' ');
      g.addEventListener('click', () => selectNode(n.id));
      nodeG[n.id] = g;
    }
    if (selected !== null && nodeG[selected]) nodeG[selected].classList.add('sel');
    const hidden = graph.nodes.length - shown.length;
    $('#graphInfo').textContent = `показано ${shown.length} из ${graph.total_ensembles}` +
      (hidden ? ` (${hidden} одиночных скрыто)` : '');
  }

  async function selectNode(id) {
    selected = id;
    document.querySelectorAll('.node.sel').forEach((n) => n.classList.remove('sel'));
    const g = document.querySelector(`.node[data-id="${id}"]`);
    if (g) g.classList.add('sel');
    const info = $('#nodeInfo');
    try {
      const data = await api('/net/neurons?ensemble=' + id);
      info.classList.remove('muted');
      info.innerHTML = `<b>Ансамбль ${id}</b>: ` + data.neurons.map((n) => {
        const nb = n.neighbours.map(([w, c]) => `${escapeHtml(w)} (${c})`).join(', ');
        return `<div class="nb"><b>${escapeHtml(n.word)}</b> — соседи: ${nb || 'нет'}</div>`;
      }).join('');
    } catch (e) {
      info.textContent = 'Не удалось загрузить: ' + e.message;
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ---------- график развития ----------

  function drawChart(history) {
    const svg = $('#chart');
    svg.setAttribute('viewBox', '0 0 360 180');
    svg.innerHTML = '';
    if (!history || history.length < 2) {
      el('text', { x: 180, y: 95, 'text-anchor': 'middle', fill: '#8b93a1', 'font-size': 12 }, svg).textContent =
        'Нужно хотя бы два урока, чтобы увидеть динамику.';
      $('#chartLegend').innerHTML = '';
      return;
    }
    const series = [
      ['слова', 'words', '#9aa3b2'],
      ['ансамбли', 'ensembles', '#7fb3e0'],
      ['связи', 'links', '#f5c16c'],
    ];
    const n = history.length;
    const maxV = Math.max(1, ...history.flatMap((h) => series.map(([, key]) => h[key] || 0)));
    const xs = (i) => 10 + (i / (n - 1)) * 340;
    const ys = (v) => 170 - (v / maxV) * 150;
    for (const [, key, color] of series) {
      const pts = history.map((h, i) => `${xs(i).toFixed(1)},${ys(h[key] || 0).toFixed(1)}`).join(' ');
      el('polyline', { points: pts, fill: 'none', stroke: color, 'stroke-width': 1.8 }, svg);
    }
    const losses = history.map((h) => h.loss).filter((v) => v !== null && v !== undefined);
    if (losses.length > 1) {
      const lmax = Math.max(...losses), lmin = Math.min(...losses);
      const span = Math.max(1e-6, lmax - lmin);
      const pts = history.map((h, i) => h.loss == null ? null : `${xs(i).toFixed(1)},${(170 - ((h.loss - lmin) / span) * 60 - 5).toFixed(1)}`)
        .filter(Boolean).join(' ');
      el('polyline', { points: pts, fill: 'none', stroke: '#e5544f', 'stroke-width': 1.2, 'stroke-dasharray': '4 3' }, svg);
    }
    $('#chartLegend').innerHTML = series.map(([name, , color]) =>
      `<span style="color:${color}">━ ${name}</span>`).join(' ') +
      ' <span style="color:#e5544f">┄ ошибка урока</span>';
  }

  // ---------- импульс ----------

  async function runImpulse(question) {
    const out = $('#respondText');
    const list = $('#path');
    out.textContent = 'думаю…';
    list.innerHTML = '';
    const data = await api('/net/respond', { question });
    out.innerHTML = data.text ? `<b>Ответ сети:</b> ${escapeHtml(data.text)}` : '<span class="muted">сеть не знает слов из вопроса</span>';
    data.path.forEach((step, i) => {
      const li = document.createElement('li');
      li.innerHTML = `${escapeHtml(step.word)} <span class="e">→ ансамбль ${step.ensemble}, масса ${step.mass}</span>`;
      list.appendChild(li);
      setTimeout(() => highlight(step.ensemble), i * 350);
    });
    setTimeout(() => document.querySelectorAll('.node.hot').forEach((n) => n.classList.remove('hot')), data.path.length * 350 + 900);
  }

  function highlight(ensembleId) {
    document.querySelectorAll('.node.hot').forEach((n) => n.classList.remove('hot'));
    const g = document.querySelector(`.node[data-id="${ensembleId}"]`);
    if (g) g.classList.add('hot');
    const from = document.querySelectorAll('.edge.hot');
    from.forEach((e) => e.classList.remove('hot'));
    document.querySelectorAll(`.edge[data-t="${ensembleId}"]`).forEach((e) => e.classList.add('hot'));
  }

  // ---------- обновление всей страницы ----------

  function renderEpisodes(list) {
    const ol = $('#episodes');
    if (!list || !list.length) { ol.innerHTML = '<li class="muted">уроков пока нет</li>'; return; }
    const src = { manual: 'вручную', good: 'хороший', fix: 'исправлено', 'fix-wrong': 'неверный', bad: 'неверный', llm: 'из LLM', net: 'сеть' };
    ol.innerHTML = list.map((e) => `<li>${escapeHtml(e.question)} → ${escapeHtml(e.answer)}
      <span class="e">(${src[e.source] || e.source}${e.weight < 0 ? ', ослаблено' : ''})</span></li>`).join('');
  }

  async function refresh() {
    const [state, g] = await Promise.all([api('/net/state'), api('/net/graph')]);
    graph = g;
    renderStats(state);
    renderEpisodes(state.recent);
    drawGraph();
    drawChart(state.history);
    return state;
  }

  async function run(fn, okText) {
    try {
      await fn();
      if (okText) toast(okText);
    } catch (e) {
      toast('Ошибка: ' + e.message, true);
    }
  }

  // ---------- обработчики ----------

  $('#teachForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const question = $('#teachQ').value.trim(), answer = $('#teachA').value.trim();
    run(async () => {
      const r = await api('/net/teach', { question, answer, weight: 1.0 });
      $('#teachResult').textContent = `Урок принят. Ошибка предсказания: ${r.loss}. ` +
        `Слов: ${fmt(r.stats['слов'])}, ансамблей: ${fmt(r.stats['ансамблей'])}, связей: ${fmt(r.stats['связей'])}.`;
      await refresh();
    }, 'Сеть обучилась и сохранена');
  });

  $('#btnBad').addEventListener('click', () => {
    const question = $('#teachQ').value.trim(), answer = $('#teachA').value.trim();
    if (!question || !answer) { toast('Заполните вопрос и ответ', true); return; }
    run(async () => {
      const r = await api('/net/teach', { question, answer, weight: -1.0 });
      $('#teachResult').textContent = `Связь ослаблена. Ошибка: ${r.loss}.`;
      await refresh();
    }, 'Связь ослаблена и сохранена');
  });

  $('#respondForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = $('#respondQ').value.trim();
    if (q) run(() => runImpulse(q));
  });

  $('#btnSave').addEventListener('click', () => run(async () => { await api('/net/save', {}); await refresh(); }, 'Сеть сохранена на диск'));
  $('#btnLoad').addEventListener('click', () => run(async () => { await api('/net/load', {}); await refresh(); }, 'Сеть загружена с диска'));
  $('#btnReset').addEventListener('click', () => {
    if (!confirm('Сбросить сеть? Текущая сеть сохранится в резервную копию, рабочая станет пустой.')) return;
    run(async () => {
      const r = await api('/net/reset', {});
      selected = null; pos.clear(); await refresh();
      toast(r.backup ? 'Сеть сброшена. Копия: ' + r.backup.split(/[\\/]/).pop() : 'Сеть сброшена');
    });
  });
  $('#btnEval').addEventListener('click', () => run(async () => {
    const r = await api('/net/eval', {});
    if (r.score === null) {
      $('#evalResult').textContent = 'Проверочных вопросов пока нет: отмечайте хорошие ответы в чате.';
    } else {
      const lines = r.details.map((d) => `${escapeHtml(d.question)}: ${Math.round(d.score * 100)}%`);
      const note = r.score < 0.3
        ? '<br><span class="muted">Сеть пока слабо помнит эти ответы. Повторите «Хороший ответ» или дайте урок ещё раз: закрепление требует повторов.</span>'
        : '';
      $('#evalResult').innerHTML = `<b>Припоминание: ${Math.round(r.score * 100)}%</b> по ${r.count} вопросам<br>` + lines.join('<br>') + note;
    }
    await refresh();
  }, 'Проверка выполнена'));
  $('#btnRestore').addEventListener('click', () => {
    if (!confirm('Восстановить сеть из последней резервной копии? Текущая рабочая сеть будет заменена.')) return;
    run(async () => { await api('/net/restore', {}); selected = null; pos.clear(); await refresh(); }, 'Сеть восстановлена из копии');
  });

  refresh().catch((e) => toast('Нет связи с сервером: ' + e.message, true));
  setInterval(() => refresh().catch(() => {}), 15000);
})();
