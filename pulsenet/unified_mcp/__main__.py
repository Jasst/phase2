# -*- coding: utf-8 -*-
"""CLI:
  python -m pulsenet.unified_mcp serve               запуск (PULSENET_UNIFIED_HOST/PORT, по умолчанию 127.0.0.1:8010)
  python -m pulsenet.unified_mcp issue-token <user>  выпустить токен пользователю
  python -m pulsenet.unified_mcp rotate <user>       отозвать старые токены и выпустить новый
  python -m pulsenet.unified_mcp revoke <user>       отозвать все токены пользователя
  python -m pulsenet.unified_mcp list-tokens         список токенов: кто, когда, активен ли (значения не показываются)
"""
import os
import sys

from ..envfile import load_dotenv
from .auth import TokenStore
from .server import Settings, create_app


def main(argv=None) -> int:
    load_dotenv(".env")
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = Settings.from_env()
    if not argv or argv[0] not in ("serve", "issue-token", "revoke", "rotate", "list-tokens"):
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "serve":
        import uvicorn
        app = create_app(cfg)
        host = os.getenv("PULSENET_UNIFIED_HOST", "127.0.0.1")
        port = int(os.getenv("PULSENET_UNIFIED_PORT", "8010"))
        print(f"pulsenet-unified MCP: http://{host}:{port}/mcp  (данные: {cfg.data_dir})")
        uvicorn.run(app, host=host, port=port, log_level="info")
        return 0
    os.makedirs(cfg.data_dir, exist_ok=True)
    store = TokenStore(
        os.path.join(cfg.data_dir, "tokens.sqlite"), cfg.owner_token, cfg.owner_id,
        secret=cfg.env.get("MCP_TOKEN_SECRET", ""),
        key_path=cfg.env.get("MCP_TOKEN_KEY_PATH")
        or os.path.join(cfg.data_dir, "mcp_token.key"))
    if cmd == "list-tokens":
        for t in store.list_tokens():
            state = "активен" if t["active"] else "отозван"
            src = f" ({t['source']})" if t.get("source") else ""
            print(f"{t['user_id']:<16} {t['fingerprint']}  {state}{src}")
        return 0
    if len(argv) < 2:
        print("укажите user_id")
        return 2
    try:
        if cmd == "issue-token":
            token = store.issue(argv[1])
            print(f"токен для {argv[1]} (сохраните сейчас, повторно не покажем):\n{token}")
        elif cmd == "rotate":
            token = store.rotate(argv[1])
            print(f"новый токен для {argv[1]} (старый больше не работает, сохраните сейчас):\n{token}")
        else:
            print(f"отозвано токенов: {store.revoke_user(argv[1])}")
    except ValueError as exc:
        print(f"ошибка: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
