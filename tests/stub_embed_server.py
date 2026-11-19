# -*- coding: utf-8 -*-
"""Заглушка llama-server --embedding для тестов: считает запуски и тексты в файле STUB_COUNTER."""
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COUNTER = os.environ["STUB_COUNTER"]


def bump(line):
    with open(COUNTER, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1])
    bump(f"start {os.getpid()}")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.endswith("/models"):
                out = json.dumps({"data": [{"id": "qwen-chat"}, {"id": "text-embedding-bge-m3"}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            items = body["input"]
            bump(f"texts {len(items)}")
            data = []
            for i, t in enumerate(items):
                h = hashlib.sha256(t.encode("utf-8")).digest()
                vec = [b / 255.0 + 0.01 for b in h[:16]]
                data.append({"index": i, "embedding": vec})
            out = json.dumps({"data": data}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    main()
