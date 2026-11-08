# -*- coding: utf-8 -*-
"""Менеджер ансамбля: открывает сети, думает цепочкой, учится по наградам
через изолированную песочницу и сохраняет всё в общую папку nets/.

Поток данных:
  think(вопрос)      → Chain.run → мысль + трасса в _logs/
  feedback(q, a, w)  → пара в _store/feedback.jsonl (опыт копится без предела)
                       и в очередь pending; при накоплении adapt_every пар —
                       Sandbox.adapt() на копии, коммит только при росте качества
  save()             → _store/*.json (сети), очередь pending
Пути вычисляются при вызове, поэтому тесты перенаправляют их во временную папку.
"""
import copy
import json
import os
import time

from .build import build_net, tokenize
from .chain import Chain
from .cluster import score_ensemble_gen
from .llm_tasks import run_sync
from .sandbox import Sandbox
from .store import load_ensemble, save_ensemble
from .teach import explain_prompt, plan_from_text

PKG = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(PKG)
STORE_DIR = os.path.join(PKG, "_store")
LOG_DIR = os.path.join(PKG, "_logs")
SANDBOX_DIR = os.path.join(PKG, "_sandbox")
SOURCES = ["corpus.txt", "tech.txt", "nature.txt", "daily.txt"]
MAX_OUT = 60          # ограничение исходящих связей узла (активный граф)
MAX_LOG_LINES = 500   # история цепочек в логе (старое обрезается)


class NetsManager:
    def __init__(self, ensemble, store=None, temp=1.0, seed=0,
                 adapt_every=4, time_budget=0.5):
        self.ensemble = ensemble
        self.store = store
        self.temp = temp
        self.seed = seed
        self.adapt_every = adapt_every
        self.time_budget = time_budget
        self.pending = []           # пары, ожидающие адаптации
        self.last = None            # последний результат цепочки
        self.last_text = ""         # трасса последнего ответа (для /chain)
        self.reports = []           # отчёты адаптаций (для /nets)
        self.distill_reports = []   # отчёты дистилляций от LLM
        self.adapt_due = False      # пора адаптировать (LLM-драйвер запустит)

    # ---------- открытие ----------

    @classmethod
    def open(cls, store=None, sources=None, **kw):
        store = store or STORE_DIR
        nets = load_ensemble(store, seed=kw.get("seed", 0))
        if not nets:                # первый запуск: сети строятся из текстов проекта
            names = sources or SOURCES
            paths = [os.path.join(PROJECT, s) for s in names
                     if os.path.isfile(os.path.join(PROJECT, s))]
            nets = [build_net(os.path.splitext(os.path.basename(p))[0], [p], seed=i)
                    for i, p in enumerate(paths)]
        mgr = cls(nets, store=store, **kw)
        mgr._load_pending()
        return mgr

    def _store(self):
        return self.store or STORE_DIR

    # ---------- мышление ----------

    def think(self, question, max_rounds=8, min_rounds=2):
        toks = tokenize(question)
        if not toks or not self.ensemble:
            return None
        chain = Chain(self.ensemble, temp=self.temp, seed=self.seed)
        res = chain.run(toks, max_rounds=max_rounds, min_rounds=min_rounds,
                        time_budget=self.time_budget)
        self.last = res
        self.last_text = chain.format_trace(res)
        self._log(question, res)
        return res

    def _log(self, question, res):
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "last_chain.txt"), "w", encoding="utf-8") as f:
            f.write(f"вопрос: {question}\n" + self.last_text + "\n")
        hist = os.path.join(LOG_DIR, "chain_history.jsonl")
        row = {"t": round(time.time(), 1), "q": question, "thought": res["thought"],
               "rounds": res["rounds"], "reason": res["reason"], "sec": res["seconds"]}
        with open(hist, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        with open(hist, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LOG_LINES:
            with open(hist, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LOG_LINES:])

    # ---------- обучение по наградам ----------

    def feedback(self, question, answer, weight=1.0, auto=True):
        """Пара вопрос→ответ после /good или /teach. Граф сразу не меняется:
        изменение происходит только через песочницу (adapt)."""
        q, a = tokenize(question), tokenize(answer)
        if not q or not a or weight <= 0:
            return False
        pair = {"q": q, "a": a, "w": round(float(weight), 3),
                "qt": str(question), "at": str(answer)}
        self.pending.append(pair)
        self._append_experience(pair)
        if len(self.pending) >= self.adapt_due_at():
            if auto:
                self.adapt()
            else:
                self.adapt_due = True        # адаптацию запустит LLM-драйвер
        self._save_pending()
        return True

    def adapt_due_at(self):
        return self.adapt_every

    def adapt_gen(self, margin=0.0):
        """Адаптация в песочнице как генератор (оценки — LLM-судья). Очередь очищается."""
        self.adapt_due = False
        pairs = [(p["q"], p["a"]) for p in self.pending]
        if len(pairs) < 2:
            return None
        cut = max(1, (len(pairs) * 3) // 4)
        train, val = pairs[:cut], pairs[cut:]
        sandbox = Sandbox(self.ensemble, root=SANDBOX_DIR)
        # Бюджет запросов к LLM: 1 поколение, 3 мутанта — около десятка оценок.
        report = yield from sandbox.adapt_gen(train, val, temp=self.temp, generations=1,
                                              margin=margin, seed=self.seed, pop=3)
        if report["commit"]:
            for net in self.ensemble:
                net.prune(MAX_OUT)
        self.reports.append(report)
        self.reports = self.reports[-20:]
        self.pending = []
        self.save()
        return report

    def adapt(self, margin=0.0):
        """Синхронная адаптация (без LLM: оценка по совпадению слов)."""
        return run_sync(self.adapt_gen(margin))

    def distill_gen(self, teacher, alpha=0.2, temp=2.0):
        """Дистилляция знаний учителя (LLM) в ансамбль на копии. Коммит, если на
        последних подтверждённых парах качество не упало. Возвращает True/False."""
        work = copy.deepcopy(self.ensemble)
        for net in work:
            net.distill_from(teacher, alpha=alpha, temp=temp)
            net.prune(MAX_OUT)
        val = [(p["q"], p["a"]) for p in self.pending[-3:]] or \
              [(p["q"], p["a"]) for p in self._experience_tail(3)]
        if val:
            base = yield from score_ensemble_gen(self.ensemble, val, self.temp, self.seed)
            after = yield from score_ensemble_gen(work, val, self.temp, self.seed)
            commit = after >= base - 0.02
        else:
            commit = True
        if commit:
            self.ensemble[:] = work
            self.save()
        self.distill_reports.append({"commit": commit, "teacher": teacher.name})
        self.distill_reports = self.distill_reports[-20:]
        return commit

    def teach_gen(self, word):
        """Обучение слова через LLM: объяснение (показывается) → план → дистилляция."""
        text = yield ("explain", explain_prompt(word))
        if not text:
            return {"ok": False, "reason": "LLM не ответила", "text": ""}
        plan = plan_from_text(word, text, seed=self.seed)
        committed = yield from self.distill_gen(plan["teacher"])
        return {"ok": True, "plan": plan, "text": text, "committed": committed}

    def _experience_tail(self, k):
        path = os.path.join(self._store(), "feedback.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-k:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def _append_experience(self, pair):
        root = self._store()
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "feedback.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    # ---------- сохранение ----------

    def _save_pending(self):
        root = self._store()
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "pending.json"), "w", encoding="utf-8") as f:
            json.dump(self.pending, f, ensure_ascii=False)

    def _load_pending(self):
        path = os.path.join(self._store(), "pending.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.pending = json.load(f)
            except (OSError, ValueError):
                self.pending = []

    def save(self):
        save_ensemble(self.ensemble, root=self._store())
        self._save_pending()

    # ---------- диагностика ----------

    def status(self):
        lines = ["сети: " + ", ".join(f"{n.name}({n.n_edges()} связей)" for n in self.ensemble),
                 f"ожидают адаптации: {len(self.pending)} из {self.adapt_every}"]
        if self.last:
            lines.append(f"последний ответ: раундов {self.last['rounds']}, "
                         f"{self.last['reason']}, {self.last['seconds']} с")
        if self.reports:
            r = self.reports[-1]
            verdict = "принята" if r["commit"] else "отклонена"
            lines.append(f"адаптация: {r['before']} → {r['after']} ({verdict})")
        return lines
