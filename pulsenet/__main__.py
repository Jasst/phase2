# -*- coding: utf-8 -*-
"""Точка входа:
  python -m pulsenet serve  [--host 127.0.0.1] [--port 8000] [--data data]   веб-интерфейс
  python -m pulsenet chat   [--state state] [--no-teacher]                  чат в терминале
  python -m pulsenet train  FILE... [--state state]                         предобучение на тексте

Настройки читаются из переменных окружения и из файла .env в текущей папке.
"""
import argparse
import os
import sys

from . import store
from .chat import load_or_new, run as run_chat
from .envfile import load_dotenv
from .llm import make_backend
from .teacher import HTTPTeacher


def _teacher_from_env(env):
    backend = make_backend(env)
    if backend.name == "llamacpp":
        backend.ensure(backend.default_rel)
    return HTTPTeacher(backend.base_url + "/chat/completions", env.get("PULSENET_TEACHER_MODEL", "local"))


def main(argv=None):
    load_dotenv(".env")
    p = argparse.ArgumentParser(prog="pulsenet", description="Онлайн-обучаемая сеть импульсов.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="веб-интерфейс")
    s.add_argument("--host", default=os.environ.get("PULSENET_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("PULSENET_PORT", "8000")))
    s.add_argument("--data", default=os.environ.get("PULSENET_DATA", "data"))
    s.add_argument("--web", default="web")

    c = sub.add_parser("chat", help="чат в терминале с онлайн-обучением")
    c.add_argument("--state", default="state")
    c.add_argument("--no-teacher", action="store_true")
    c.add_argument("--show-teacher", action="store_true")

    t = sub.add_parser("train", help="предобучить на текстовых файлах")
    t.add_argument("files", nargs="+")
    t.add_argument("--state", default="state")

    args = p.parse_args(argv)
    if args.cmd == "serve":
        from .app import App
        from .server import serve_forever
        app = App(os.environ, data_dir=args.data)
        serve_forever(app, args.host, args.port, args.web)
    elif args.cmd == "chat":
        teacher = None if args.no_teacher else _teacher_from_env(os.environ)
        run_chat(args.state, teacher, args.show_teacher)
    else:
        net = load_or_new(args.state)
        for path in args.files:
            with open(path, encoding="utf-8-sig") as f:
                print(f"{path}: уроков {net.pretrain(f.read())}")
        store.save(net, args.state)
        print(net.stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
