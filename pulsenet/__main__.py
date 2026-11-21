# -*- coding: utf-8 -*-
"""Точка входа.

  python -m pulsenet serve                 веб-интерфейс и MCP (порт PULSENET_PORT)
  python -m pulsenet ingest FILE...        загрузить текстовые файлы в память (с экстракцией)
  python -m pulsenet consolidate           «Сон»: датасет без обучения
  python -m pulsenet train-sleep DATASET --base HF_DIR --out DIR [--llama-cpp DIR --gguf FILE]

Настройки — из переменных окружения и файла .env в текущей папке.
"""
import argparse
import os
import sys

from .envfile import load_dotenv


def main(argv=None):
    load_dotenv(".env")
    p = argparse.ArgumentParser(prog="pulsenet")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="веб-интерфейс и MCP")
    s.add_argument("--host", default=os.environ.get("PULSENET_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PULSENET_PORT", "8000")))
    s.add_argument("--data", default=os.environ.get("PULSENET_DATA", "data"))
    s.add_argument("--web", default="web")

    i = sub.add_parser("ingest", help="загрузить файлы в память")
    i.add_argument("files", nargs="+")
    i.add_argument("--data", default=os.environ.get("PULSENET_DATA", "data"))

    c = sub.add_parser("consolidate", help="«Сон»: только отбор и датасет")
    c.add_argument("--data", default=os.environ.get("PULSENET_DATA", "data"))

    t = sub.add_parser("train-sleep", help="LoRA-обучение и конвертация (нужен GPU)")
    t.add_argument("dataset")
    t.add_argument("--base", required=True, help="HF-папка базовой модели")
    t.add_argument("--out", required=True, help="папка адаптера PEFT")
    t.add_argument("--llama-cpp", default="", help="папка llama.cpp: конвертация в GGUF")
    t.add_argument("--gguf", default="", help="файл GGUF на выходе")
    t.add_argument("--steps", type=int, default=200)

    args = p.parse_args(argv)
    if args.cmd == "serve":
        from .app import App
        from .server import serve_forever
        app = App(os.environ, data_dir=args.data)
        serve_forever(app, args.host, args.port, args.web)
    elif args.cmd == "ingest":
        from .app import App
        app = App(os.environ, data_dir=args.data)
        for path in args.files:
            with open(path, encoding="utf-8-sig") as f:
                out = app.ingest(f.read(), session_id=os.path.basename(path), role="user")
            print(f"{path}: {out['status']}, фактов: {sum(1 for x in out['facts'] if x.get('status') == 'ok')}")
    elif args.cmd == "consolidate":
        from .app import App
        app = App(os.environ, data_dir=args.data)
        print(app.consolidate_dry_run())
    elif args.cmd == "train-sleep":
        from .triplex import sleep
        try:
            sleep.train(args.dataset, args.base, args.out, steps=args.steps)
            print(f"адаптер сохранён: {args.out}")
            if args.llama_cpp and args.gguf:
                sleep.convert(args.out, args.base, args.gguf, args.llama_cpp)
                print(f"GGUF: {args.gguf}. Добавьте в .env: LLAMACPP_EXTRA_ARGS=--lora-scaled {args.gguf} 0.5")
            print("Проверку (гейт) выполните скриптом eval/ и вызовом sleep.accept_adapter")
        except sleep.SleepUnavailable as exc:
            print(f"не запущено: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
