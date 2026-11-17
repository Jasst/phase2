/* Панель «Сеть» над полем ввода чата.
 * Переключатель: отвечает LLM или сама сеть. Кнопки для последнего ответа:
 * «Хороший ответ» — закрепить; «Обучить» — дать урок с этим вопросом и ответом;
 * «Переучить» — ввести правильный ответ, неверный ослабится;
 * «Обучить с LLM» — LLM отвечает заново, сеть учится на этом примере несколько раз.
 * Данные последнего хода приходят из события turn в потоке /ai/chat. */
(function () {
  'use strict';

  const state = { mode: 'llm', autoLearn: true, last: null, busy: false };

  async function api(path, body) {
    const opt = body === undefined ? { cache: 'no-store' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(path, opt);
    let data = {};
    try { data = await r.json(); } catch (_) { /* пусто */ }
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    return data;
  }

  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function build() {
    if (document.getElementById('brainPanel')) return;
    const anchor = document.querySelector('.composer');
    if (!anchor || !anchor.parentNode) return;
    const box = el('div', { id: 'brainPanel', class: 'brain-panel', role: 'region', 'aria-label': 'Обучение сети' });
    const head = el('div', { class: 'bp-head' });
    const seg = el('div', { class: 'bp-seg', role: 'group', 'aria-label': 'Кто отвечает' });
    const bLlm = el('button', { type: 'button', id: 'bpModeLlm', 'aria-pressed': 'true' }, 'LLM');
    const bNet = el('button', { type: 'button', id: 'bpModeNet', 'aria-pressed': 'false' }, 'Моя модель');
    seg.append(bLlm, bNet);
    const learnLbl = el('label', { class: 'bp-check' });
    const chk = el('input', { type: 'checkbox', id: 'bpAutoLearn' });
    learnLbl.append(chk, document.createTextNode(' учить из ответов LLM'));
    const status = el('span', { id: 'bpStatus', class: 'bp-status', role: 'status' });
    head.append(seg, learnLbl, status);

    const last = el('div', { id: 'bpLast', class: 'bp-last' }, 'Пока нет ответа для оценки.');
    const actions = el('div', { class: 'bp-actions' });
    const bGood = el('button', { type: 'button', id: 'bpGood', title: 'Закрепить этот ответ в сети' }, 'Хороший ответ');
    const bTeach = el('button', { type: 'button', id: 'bpTeach', title: 'Дать сети урок: этот вопрос и этот ответ' }, 'Обучить');
    const bFix = el('button', { type: 'button', id: 'bpFix', class: 'danger', title: 'Ответ неверный: введите правильный' }, 'Переучить');
    const bTrainLLM = el('button', { type: 'button', id: 'bpTrainLLM', class: 'primary',
      title: 'LLM заново ответит на этот вопрос, сеть выучит её ответ несколько раз подряд' }, 'Обучить с LLM');
    actions.append(bGood, bTeach, bFix, bTrainLLM);
    box.append(head, last, actions);
    anchor.parentNode.insertBefore(box, anchor);

    bLlm.addEventListener('click', () => setMode('llm'));
    bNet.addEventListener('click', () => setMode('net'));
    chk.addEventListener('change', () => setAuto(chk.checked));
    bGood.addEventListener('click', () => verdict('good'));
    bTeach.addEventListener('click', teachCurrent);
    bFix.addEventListener('click', fixCurrent);
    bTrainLLM.addEventListener('click', () => trainLLMFlow());
    paintButtons();
  }

  function say(text, isError) {
    const s = document.getElementById('bpStatus');
    if (!s) return;
    s.textContent = text;
    s.style.color = isError ? '#f0a09d' : '';
    clearTimeout(say._t);
    say._t = setTimeout(() => { s.textContent = ''; }, 4500);
  }

  function paintButtons() {
    const llm = document.getElementById('bpModeLlm');
    const net = document.getElementById('bpModeNet');
    if (!llm) return;
    llm.setAttribute('aria-pressed', String(state.mode === 'llm'));
    net.setAttribute('aria-pressed', String(state.mode === 'net'));
    llm.classList.toggle('on', state.mode === 'llm');
    net.classList.toggle('on', state.mode === 'net');
    document.getElementById('bpAutoLearn').checked = state.autoLearn;
    const okQ = !!(state.last && state.last.question) && !state.busy;
    const ok = okQ && !!state.last.answer;
    ['bpGood', 'bpTeach', 'bpFix'].forEach((id) => { document.getElementById(id).disabled = !ok; });
    document.getElementById('bpTrainLLM').disabled = !okQ;
    const lastBox = document.getElementById('bpLast');
    if (state.last && state.last.question) {
      const src = { llm: 'ответ LLM', net: 'ответ сети', manual: 'урок' }[state.last.source] || state.last.source || '';
      lastBox.innerHTML = '';
      lastBox.append(el('b', {}, 'Вопрос: '), document.createTextNode(state.last.question + ' '));
      lastBox.append(el('span', { class: 'bp-src' }, '(' + src + ')'));
      lastBox.append(el('div', { class: 'bp-ans' }, state.last.answer || 'сеть не ответила — дайте урок «Переучить»'));
    }
  }

  // ---------- настройки: раздел «Сеть импульсов» ----------

  const TUNE = [
    ['tuneTemperature', 'outTemperature', 'temperature', 2],
    ['tuneMinConfidence', 'outMinConfidence', 'min_confidence', 3],
    ['tuneSettleSteps', 'outSettleSteps', 'settle_steps', 0],
    ['tuneFocusGain', 'outFocusGain', 'focus_gain', 2],
    ['tuneReplayN', 'outReplayN', 'replay_n', 0],
    ['tuneFixWeight', 'outFixWeight', 'fix_weight', 1],
  ];

  function paintSettings() {
    const mode = document.getElementById('setBrainMode');
    if (!mode) return;
    mode.value = state.mode;
    document.getElementById('setAutoLearn').checked = state.autoLearn;
  }

  async function loadTuning() {
    const data = await api('/net/tuning');
    for (const [inputId, outId, key, digits] of TUNE) {
      const input = document.getElementById(inputId);
      const item = data[key];
      if (!input || !item) continue;
      input.min = item.min; input.max = item.max;
      input.value = item.value;
      document.getElementById(outId).textContent = Number(item.value).toFixed(digits);
    }
  }

  function bindSettings() {
    const mode = document.getElementById('setBrainMode');
    const auto = document.getElementById('setAutoLearn');
    const openBtn = document.getElementById('openSettings');
    const apply = document.getElementById('netTuneApply');
    const trainLLM = document.getElementById('trainWithLLMBtn');
    if (!mode || !auto || !apply) return;
    mode.addEventListener('change', () => setMode(mode.value));
    auto.addEventListener('change', () => setAuto(auto.checked));
    if (trainLLM) trainLLM.addEventListener('click', () => trainLLMFlow('netTuneStatus'));
    for (const [inputId, outId, , digits] of TUNE) {
      const input = document.getElementById(inputId);
      input.addEventListener('input', () => {
        document.getElementById(outId).textContent = Number(input.value).toFixed(digits);
      });
    }
    if (openBtn) openBtn.addEventListener('click', () => {
      paintSettings();
      loadTuning().catch((e) => say('Не удалось загрузить параметры: ' + e.message, true));
    });
    apply.addEventListener('click', async () => {
      const status = document.getElementById('netTuneStatus');
      const values = {};
      for (const [inputId, , key] of TUNE) values[key] = Number(document.getElementById(inputId).value);
      try {
        const r = await api('/net/tuning', values);
        status.textContent = r.ok ? 'Применено' : ('Ошибка: ' + r.errors.join('; '));
        status.style.color = r.ok ? '' : '#f0a09d';
        if (r.ok) await loadTuning();
      } catch (e) {
        status.textContent = 'Ошибка: ' + e.message;
        status.style.color = '#f0a09d';
      }
    });
  }

  async function setMode(mode) {
    try {
      await api('/net/mode', { mode });
      state.mode = mode;
      paintButtons();
      paintSettings();
      say(mode === 'net' ? 'Отвечает моя модель' : 'Отвечает LLM');
    } catch (e) { say('Не удалось переключить: ' + e.message, true); }
  }

  async function setAuto(on) {
    try {
      await api('/net/mode', { auto_learn: on });
      state.autoLearn = on;
      paintButtons();
      paintSettings();
      say(on ? 'Сеть учится на ответах LLM' : 'Сеть не учится на ответах LLM');
    } catch (e) { say('Ошибка: ' + e.message, true); }
  }

  async function verdict(v) {
    if (!state.last) return;
    state.busy = true; paintButtons();
    try {
      const r = await api('/net/feedback', { question: state.last.question, answer: state.last.answer, verdict: v });
      say(v === 'good' ? 'Закреплено. Сеть запомнила ответ' : 'Принято');
      return r;
    } catch (e) { say('Ошибка: ' + e.message, true); }
    finally { state.busy = false; paintButtons(); }
  }

  async function teachCurrent() {
    if (!state.last) return;
    state.busy = true; paintButtons();
    try {
      await api('/net/teach', { question: state.last.question, answer: state.last.answer, weight: 1.0 });
      say('Урок принят: сеть обучилась на этой паре');
    } catch (e) { say('Ошибка: ' + e.message, true); }
    finally { state.busy = false; paintButtons(); }
  }

  async function fixCurrent() {
    if (!state.last) return;
    const correct = await window.UI.prompt('Какой ответ правильный? Неверный ответ будет ослаблен.', {
      title: 'Переучить', okText: 'Переучить', value: '', placeholder: 'Правильный ответ', required: true,
    });
    if (!correct) return;
    state.busy = true; paintButtons();
    try {
      await api('/net/feedback', { question: state.last.question, answer: state.last.answer, verdict: 'fix', correct });
      say('Сеть переучилась: правильный ответ закреплён');
    } catch (e) { say('Ошибка: ' + e.message, true); }
    finally { state.busy = false; paintButtons(); }
  }

  // ---------- «Обучить с LLM»: пример от учителя, несколько повторов ----------

  function statusOut(id) {
    const s = document.getElementById(id);
    if (!s) return null;
    return { set(text, isError) { s.textContent = text; s.style.color = isError ? '#f0a09d' : ''; } };
  }

  async function trainLLMFlow(statusId) {
    const out = statusOut(statusId || 'bpStatus') || { set: (t, e) => say(t, e) };
    let question = state.last && state.last.question;
    if (!question) {
      question = await window.UI.prompt('На какой вопрос обучить сеть на примере ответа LLM?', {
        title: 'Обучить с LLM', okText: 'Дальше', value: '', placeholder: 'Вопрос', required: true,
      });
      if (!question) return;
    }
    const iters = await window.UI.prompt(
      'LLM ответит на этот вопрос, и сеть выучит её ответ. Сколько раз обучить на этом примере? (1–20)',
      { title: 'Обучить с LLM', okText: 'Обучить', value: '3', placeholder: '3', required: true },
    );
    if (!iters) return;
    const n = Math.min(20, Math.max(1, Math.round(Number(String(iters).replace(',', '.')) || 0)));
    if (!n) { out.set('Введите число повторов от 1 до 20', true); return; }
    state.busy = true; paintButtons();
    out.set(`Спрашиваю LLM и обучаюсь на примере (${n} раз)…`);
    try {
      const r = await api('/net/train-with-llm', { question, iterations: n });
      if (!r.ok) throw new Error(r.error || 'LLM не ответила');
      const tail = r.avg_loss !== null && r.avg_loss !== undefined ? ` средняя ошибка ${r.avg_loss}` : '';
      out.set(`Сеть обучилась на примере ${r.iterations} раз подряд.${tail} Ответ учителя: «${r.teacher_answer}»`);
    } catch (e) { out.set('Ошибка: ' + e.message, true); }
    finally { state.busy = false; paintButtons(); }
  }

  // Перехват потока чата: берём событие turn из ответа сервера
  function watchChatStream() {
    const orig = window.fetch.bind(window);
    window.fetch = async function (input, init) {
      const url = typeof input === 'string' ? input : (input && input.url) || '';
      const res = await orig(input, init);
      if (url.endsWith('/ai/chat') && init && init.method === 'POST' && res.body) {
        const reader = res.clone().body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        (async () => {
          try {
            for (;;) {
              const { value, done } = await reader.read();
              if (done) break;
              buf += dec.decode(value, { stream: true });
              let i;
              while ((i = buf.indexOf('\n\n')) >= 0) {
                const line = buf.slice(0, i).trim();
                buf = buf.slice(i + 2);
                if (!line.startsWith('data:')) continue;
                try {
                  const ev = JSON.parse(line.slice(5));
                  if (ev.turn) {
                    state.last = { question: ev.turn.question, answer: ev.turn.answer, source: ev.turn.source };
                    paintButtons();
                  }
                } catch (_) { /* не JSON — пропускаем */ }
              }
            }
          } catch (_) { /* поток закрыт */ }
        })();
      }
      return res;
    };
  }

  async function init() {
    build();
    bindSettings();
    watchChatStream();
    try {
      const st = await api('/net/state');
      state.mode = st.mode || 'llm';
      state.autoLearn = st.auto_learn !== false;
      const last = await api('/ai/chat/last_response');
      if (last && last.user) state.last = { question: last.user, answer: last.assistant, source: last.source };
    } catch (_) { /* сервер недоступен — панель останется в исходном виде */ }
    paintButtons();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
