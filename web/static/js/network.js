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
  let lastTurn = null;       // последний ответ импульса: с ним работают кнопки оценки
  let busy = false;
  let thoughts = [];

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

  function neuronStatus(text, kind) {
    const el = $('#neuronResult');
    if (!el) return;
    el.textContent = text || '';
    el.className = 'result small' + (kind === 'ok' ? ' ok' : kind === 'err' ? ' err' : ' muted');
  }

  function gradeStatus(text, isError) {
    const el = $('#gradeStatus');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = isError ? '#f0a09d' : '';
  }

  function setBusy(on) {
    busy = !!on;
    for (const id of ['btnAutocorrect', 'btnDistill', 'btnReplay']) {
      const b = document.getElementById(id);
      if (b) b.disabled = busy;
    }
    renderGrade();
  }

  function sourceName(source) {
    return { llm: 'ответ LLM', net: 'ответ сети', manual: 'урок' }[source] || source || '';
  }

  function renderGrade() {
    const hasQuestion = !!(lastTurn && lastTurn.question);
    const hasAnswer = hasQuestion && !!(lastTurn && lastTurn.answer);
    const box = $('#lastTurn');
    if (box) {
      if (!hasQuestion) {
        box.className = 'last-turn muted';
        box.textContent = 'Сначала задайте вопрос и пустите импульс.';
      } else {
        box.className = 'last-turn';
        box.innerHTML = '';
        box.append(h('div', { class: 'q' }, 'Вопрос: ' + lastTurn.question));
        box.append(h('div', { class: 'a' },
          lastTurn.answer || 'сеть не ответила — нажмите «Переучить» и введите правильный ответ'));
        const meta = [sourceName(lastTurn.source)];
        if (lastTurn.confidence !== null && lastTurn.confidence !== undefined) {
          meta.push('уверенность ' + Math.round(Number(lastTurn.confidence) * 100) + '%');
        }
        if (lastTurn.self_error !== null && lastTurn.self_error !== undefined) {
          meta.push('ошибка самопредсказания ' + lastTurn.self_error);
        }
        box.append(h('div', { class: 'meta' }, meta.filter(Boolean).join(' · ')));
      }
    }
    const set = (id, disabled) => {
      const b = document.getElementById(id);
      if (b) b.disabled = !!disabled;
    };
    set('gradeGood', busy || !hasAnswer);
    set('gradeBad', busy || !hasAnswer);
    set('gradeTeach', busy || !hasAnswer);
    set('gradeFix', busy || !hasQuestion);
    set('gradeTrainLLM', busy || !hasQuestion);
    set('gradeThink', busy);
  }

  // el() для обычных HTML-элементов (в сети уже есть SVG-версия el выше).
  function h(tag, attrs, text) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderThoughts(items) {
    const box = $('#thoughts');
    if (!box) return;
    box.innerHTML = '';
    thoughts = items || [];
    if (!thoughts.length) {
      box.append(h('div', { class: 'thought-empty' },
        'Мыслей пока нет: сеть подумает после ответа или по кнопке «Подумать».'));
      return;
    }
    for (const it of thoughts.slice(0, 10)) {
      const row = h('div', { class: 'thought-item' });
      row.append(h('span', { class: 'thought-kind' }, (it.kind === 'turn' ? 'ход' : 'сама') + ': '));
      const text = String(it.text || '');
      row.append(document.createTextNode(text));
      if (it.t) {
        const at = new Date(Number(it.t) * 1000);
        row.append(h('span', { class: 'thought-time' },
          ' ' + at.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })));
      }
      box.append(row);
    }
  }

  async function refreshThoughts() {
    try {
      const data = await api('/net/thoughts');
      renderThoughts(data.items || []);
    } catch (_) { /* страница продолжает работать без нарратива */ }
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
      ['Мыслей в нарративе', fmt(state.thoughts)],
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
    gradeStatus('');
    setBusy(true);
    try {
      const data = await api('/net/respond', { question });
      out.innerHTML = data.text
        ? `<b>Ответ сети:</b> ${escapeHtml(data.text)}`
        : '<span class="muted">сеть не знает слов из вопроса</span>';
      lastTurn = { question, answer: data.text || '', source: 'net',
        confidence: data.confidence, self_error: data.self_error };
      renderGrade();
      data.path.forEach((step, i) => {
        const li = document.createElement('li');
        li.innerHTML = `${escapeHtml(step.word)} <span class="e">→ ансамбль ${step.ensemble}, масса ${step.mass}</span>`;
        list.appendChild(li);
        setTimeout(() => highlight(step.ensemble), i * 350);
      });
      setTimeout(() => document.querySelectorAll('.node.hot').forEach((n) => n.classList.remove('hot')), data.path.length * 350 + 900);
    } finally {
      setBusy(false);
    }
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
    const src = { manual: 'вручную', good: 'хороший', fix: 'исправлено', 'fix-wrong': 'неверный',
      bad: 'неверный', llm: 'из LLM', net: 'сеть', 'auto-fix': 'автокоррекция',
      'auto-wrong': 'автокоррекция: неверный', distill: 'дистилляция',
      'distill-wrong': 'дистилляция: неверный' };
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
    await refreshThoughts();
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

  async function feedbackAction(verdict) {
    if (!lastTurn || !lastTurn.question) return;
    let correct = null;
    if (verdict === 'fix') {
      correct = await window.UI.prompt(
        lastTurn.answer
          ? 'Исправьте ответ. Неверный вариант будет ослаблен, правильный — закреплён.'
          : 'Сеть не ответила. Введите правильный ответ — он будет закреплён.',
        { title: 'Переучить', okText: 'Переучить', value: lastTurn.answer || '',
          placeholder: 'Правильный ответ', required: true });
      if (!correct) return;
      if (lastTurn.answer && correct.trim() === lastTurn.answer.trim()) {
        gradeStatus('Ответ не изменился. Для закрепления нажмите «Обучить».', true);
        return;
      }
    }
    setBusy(true);
    gradeStatus(verdict === 'good' ? 'Закрепляю ответ…'
      : verdict === 'bad' ? 'Ослабляю связь…' : 'Переучиваю…');
    try {
      const r = await api('/net/feedback', {
        question: lastTurn.question, answer: lastTurn.answer, verdict, correct,
      });
      if (!r.ok) throw new Error(r.error || 'сеть не приняла урок');
      gradeStatus(verdict === 'good' ? 'Хороший ответ закреплён: сеть запомнила связь.'
        : verdict === 'bad' ? 'Плохой ответ ослаблен: сеть поняла, что так отвечать не надо.'
        : 'Переучено: правильный ответ закреплён, неверный ослаблен.');
      await refresh();
    } catch (e) {
      gradeStatus('Ошибка: ' + e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function teachLast() {
    if (!lastTurn || !lastTurn.question || !lastTurn.answer) return;
    setBusy(true);
    gradeStatus('Даю урок…');
    try {
      const r = await api('/net/teach', {
        question: lastTurn.question, answer: lastTurn.answer, weight: 1.0,
      });
      if (!r.ok) throw new Error(r.error || 'урок не принят');
      gradeStatus(`Урок принят. Ошибка предсказания: ${r.loss}.`);
      await refresh();
    } catch (e) {
      gradeStatus('Ошибка: ' + e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function trainWithLLM() {
    let question = lastTurn && lastTurn.question;
    if (!question) {
      question = await window.UI.prompt('На какой вопрос обучить сеть на примере ответа LLM?', {
        title: 'Обучить с LLM', okText: 'Дальше', value: '', placeholder: 'Вопрос', required: true,
      });
      if (!question) return;
    }
    const raw = await window.UI.prompt(
      'LLM ответит на вопрос, а сеть выучит её ответ несколько раз подряд. Сколько повторов? (1–20)',
      { title: 'Обучить с LLM', okText: 'Обучить', value: '3', required: true });
    if (!raw) return;
    const n = Math.min(20, Math.max(1, Math.round(Number(String(raw).replace(',', '.')) || 0)));
    if (!n) { gradeStatus('Введите число повторов от 1 до 20', true); return; }
    setBusy(true);
    gradeStatus(`Спрашиваю LLM и обучаюсь (${n} раз)…`);
    try {
      const r = await api('/net/train-with-llm', { question, iterations: n });
      if (!r.ok) throw new Error(r.error || 'LLM не ответила');
      const tail = r.avg_loss !== null && r.avg_loss !== undefined ? ` Средняя ошибка: ${r.avg_loss}.` : '';
      gradeStatus(`Сеть обучилась ${r.iterations} раз.${tail} Ответ учителя: «${r.teacher_answer}»`);
      await refresh();
    } catch (e) {
      gradeStatus('Ошибка: ' + e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function thinkNow() {
    setBusy(true);
    gradeStatus('Сеть думает…');
    try {
      const r = await api('/net/thoughts/step', { n: 1 });
      const t = (r.thoughts || [])[0];
      gradeStatus(t ? 'Сеть подумала: «' + t + '»' : 'Пока думается с трудом: дайте сети пару уроков.', !t);
      renderThoughts(r.narrative || []);
      await refresh();
    } catch (e) {
      gradeStatus('Ошибка: ' + e.message, true);
    } finally {
      setBusy(false);
    }
  }

  async function autocorrect() {
    setBusy(true);
    neuronStatus('Ищу слабые места и закрепляю правильные ответы…');
    try {
      const r = await api('/net/autocorrect', { limit: 6, repeats: 2, threshold: 0.75 });
      if (!r.ok) throw new Error(r.error || 'автокоррекция не удалась');
      const before = r.before === null || r.before === undefined ? '—' : Math.round(r.before * 100) + '%';
      const after = r.after === null || r.after === undefined ? '—' : Math.round(r.after * 100) + '%';
      neuronStatus(`Автокоррекция: проверено ${r.checked}, исправлено ${r.fixed}. ` +
        `Припоминание: ${before} → ${after}.` + (r.message ? ' ' + r.message : ''), 'ok');
      await refresh();
    } catch (e) {
      neuronStatus('Ошибка автокоррекции: ' + e.message, 'err');
    } finally {
      setBusy(false);
    }
  }

  async function distillFix() {
    const raw = await window.UI.prompt(
      'Слабые ответы будут перепроверены у LLM и переданы сети дистилляцией. Сколько вопросов взять? (1–5)',
      { title: 'Автоисправление (дистилляция)', okText: 'Запустить', value: '3', required: true });
    if (!raw) return;
    const n = Math.min(5, Math.max(1, Math.round(Number(String(raw).replace(',', '.')) || 0)));
    if (!n) { neuronStatus('Введите число от 1 до 5', 'err'); return; }
    setBusy(true);
    neuronStatus(`Спрашиваю LLM и дистиллирую ответы (${n} вопросов)…`);
    try {
      const r = await api('/net/distill', { limit: n, iterations: 2, threshold: 0.7 });
      if (!r.ok) throw new Error(r.error || 'дистилляция не удалась');
      const tail = r.errors && r.errors.length ? ' Ошибки: ' + r.errors.join('; ') : '';
      neuronStatus(`Дистилляция: исправлено ${r.fixed} из ${r.checked}.` +
        (r.teacher ? ` Учитель: ${r.teacher}.` : '') + tail, r.fixed ? 'ok' : '');
      await refresh();
    } catch (e) {
      neuronStatus('Ошибка дистилляции: ' + e.message, 'err');
    } finally {
      setBusy(false);
    }
  }

  async function replayExperience() {
    setBusy(true);
    neuronStatus('Повторяю последний опыт…');
    try {
      const r = await api('/net/replay', { limit: 8 });
      const tail = r.avg_loss !== null && r.avg_loss !== undefined ? ` Средняя ошибка: ${r.avg_loss}.` : '';
      neuronStatus(`Повторено уроков: ${r.count}.${tail}`, 'ok');
      await refresh();
    } catch (e) {
      neuronStatus('Ошибка повтора: ' + e.message, 'err');
    } finally {
      setBusy(false);
    }
  }

  $('#gradeGood').addEventListener('click', () => feedbackAction('good'));
  $('#gradeBad').addEventListener('click', () => feedbackAction('bad'));
  $('#gradeTeach').addEventListener('click', teachLast);
  $('#gradeFix').addEventListener('click', () => feedbackAction('fix'));
  $('#gradeTrainLLM').addEventListener('click', trainWithLLM);
  $('#gradeThink').addEventListener('click', thinkNow);
  $('#btnAutocorrect').addEventListener('click', autocorrect);
  $('#btnDistill').addEventListener('click', distillFix);
  $('#btnReplay').addEventListener('click', replayExperience);

  $('#respondForm').addEventListener('submit', (e) => {
    e.preventDefault();
    const q = $('#respondQ').value.trim();
    if (q) run(() => runImpulse(q));
  });

  $('#btnSave').addEventListener('click', () => run(async () => { await api('/net/save', {}); await refresh(); }, 'Сеть сохранена на диск'));
  $('#btnLoad').addEventListener('click', () => run(async () => { await api('/net/load', {}); await refresh(); }, 'Сеть загружена с диска'));
  $('#btnReset').addEventListener('click', async () => {
    const ok = await window.UI.confirm(
      'Сбросить сеть? Текущая сеть сохранится в резервную копию, рабочая станет пустой.',
      { title: 'Сброс сети', okText: 'Сбросить', danger: true });
    if (!ok) return;
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
  $('#btnRestore').addEventListener('click', async () => {
    const ok = await window.UI.confirm(
      'Восстановить сеть из последней резервной копии? Текущая рабочая сеть будет заменена.',
      { title: 'Восстановление копии', okText: 'Восстановить', danger: true });
    if (!ok) return;
    run(async () => { await api('/net/restore', {}); selected = null; pos.clear(); await refresh(); }, 'Сеть восстановлена из копии');
  });

  renderGrade();
  refresh().catch((e) => toast('Нет связи с сервером: ' + e.message, true));
  setInterval(() => refresh().catch(() => {}), 15000);
})();
