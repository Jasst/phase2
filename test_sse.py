import os
import os, sys, json, threading, time
os.environ["BRAIN_LM_BACKEND"]="lmstudio"
os.environ["BRAIN_LM_URL"]="http://127.0.0.1:18234/v1/chat/completions"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from http.server import BaseHTTPRequestHandler, HTTPServer
import lm_backend as L

class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        n=int(self.headers["Content-Length"]); req=json.loads(self.rfile.read(n))
        assert req["stream"] is True
        self.send_response(200); self.send_header("Content-Type","text/event-stream"); self.end_headers()
        parts=["<think>", "x", "</think>", "Нейрон", " — клетка", " мозга."]
        for p in parts:
            chunk={"choices":[{"delta":{"content":p}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()); self.wfile.flush()
            time.sleep(0.02)
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()

srv=HTTPServer(("127.0.0.1",18234),H)
threading.Thread(target=srv.serve_forever,daemon=True).start()
b=L.LMBackend()
seen=[]
out=None
for text, done in b.stream("что такое нейрон", ctx=["мозг"], memory=["«а» → «б»"]):
    seen.append((text,done))
print("промежуточных:", sum(1 for _,d in seen if not d), "финал:", repr(seen[-1][0]), seen[-1][1])
assert seen[-1] == ("Нейрон — клетка мозга.", True), seen[-1]
print("скорость:", b.speed_text())
srv.shutdown()
