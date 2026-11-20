# -*- coding: utf-8 -*-
"""Перенос памяти в единое хранилище MCP-сервера.

  python -m pulsenet.migrate_memory --from data/triplex --from data_unified/users/owner/triplex \\
      --to data_unified/triplex [--dry-run]

Сливает файлы журналов построчно и идемпотентно:
  docs.jsonl    — дубликат = тот же doc_id (id — хэш содержимого, поэтому факты ссылаются верно);
  meta.jsonl    — дубликат = та же строка (сущности, отношения);
  journal.jsonl — дубликат = та же строка (факт с тем же id, временем и источником).
Журнал только дописывается, поэтому порядок записей сохраняется. Исходные папки не меняются.
После переноса проверяются инварианты журнала. Запускайте при остановленных серверах.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Set


def _lines(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip()]


def _doc_key(line: str) -> str:
    return json.loads(line)["doc_id"]


def merge_into(src_root: str, dst_root: str, dry_run: bool = False) -> dict:
    report = {"src": src_root}
    os.makedirs(dst_root, exist_ok=True)
    for name, keyer in (("docs.jsonl", _doc_key), ("meta.jsonl", None), ("journal.jsonl", None)):
        src_lines = _lines(os.path.join(src_root, name))
        dst_path = os.path.join(dst_root, name)
        dst_lines = _lines(dst_path)
        if keyer:
            seen: Set[str] = {keyer(ln) for ln in dst_lines}
            keys = [keyer(ln) for ln in src_lines]
            new = [ln for ln, k in zip(src_lines, keys) if k not in seen and not seen.add(k)]
        else:
            seen = set(dst_lines)
            new = [ln for ln in src_lines if ln not in seen and not seen.add(ln)]
        report[name] = {"in_src": len(src_lines), "added": len(new)}
        if new and not dry_run:
            with open(dst_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(new) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
    return report


def check(dst_root: str) -> List[str]:
    """Загружает хранилище и проверяет инварианты журнала. Пустой список — всё в порядке."""
    from .triplex import TriplexMemory
    mem = TriplexMemory(dst_root)
    return mem.journal.check_invariants()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pulsenet.migrate_memory", description=__doc__.split("\n")[0])
    p.add_argument("--from", dest="sources", action="append", required=True, help="папка журнала-источника (можно несколько)")
    p.add_argument("--to", dest="dst", required=True, help="папка единого хранилища")
    p.add_argument("--dry-run", action="store_true", help="только показать, что будет добавлено")
    args = p.parse_args(argv)
    for src in args.sources:
        if not os.path.isdir(src):
            print(f"пропуск: нет папки {src}")
            continue
        rep = merge_into(src, args.dst, dry_run=args.dry_run)
        print(json.dumps(rep, ensure_ascii=False))
    if args.dry_run:
        print("dry-run: файлы не изменены")
        return 0
    violations = check(args.dst)
    if violations:
        print("ВНИМАНИЕ: инварианты журнала нарушены:", *violations[:10], sep="\n  ", file=sys.stderr)
        return 1
    print("готово: инварианты журнала в порядке")
    return 0


if __name__ == "__main__":
    sys.exit(main())
