"""omniroute-lite: stdlib-only OpenAI-compatible router stub."""
import store
import router
import providers
import compress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.1.0"


class Handler(BaseHTTPRequestHandler):
    server_version = "omniroute-lite/" + VERSION

    def _send(self, code, obj, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "version": VERSION})
        if self.path == "/v1/models":
            return self._send(200, {"object": "list", "data": store.model_list(store.load_targets())})
        if self.path == "/":
            html = "<pre>omniroute-lite ok\nGET /health\nGET /v1/models</pre>"
            return self._send(200, html, "text/html")
        return self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if self.path == "/v1/chat/completions":
            import json as _j
            try:
                n = int(self.headers.get("Content-Length", 0))
                payload = _j.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": {"message": "bad json"}})
            if isinstance(payload.get("messages"), list):
                payload["messages"] = compress.trim(payload["messages"])
            import hashlib as _h, os as _o
            model = payload.get("model", "")
            ckey = _h.sha256(_j.dumps(payload, sort_keys=True).encode()).hexdigest() if not payload.get("stream") and payload.get("cache_ttl") else None
            if ckey:
                cpath = _o.path.join(_o.path.expanduser("~"), ".omniroute-lite", "cache.db")
                hit = store.cache_get(cpath, ckey)
                if hit:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(hit)))
                    self.end_headers()
                    return self.wfile.write(hit)
            targets = router.chain(store.load_targets(), model)
            if not targets:
                return self._send(503, {"error": {"message": "no targets configured"}})
            last = (502, b'{"error":{"message":"all targets failed"}}')
            for t in targets:
                code, body = providers.call_target(t, payload)
                if code < 500 and code != 429:
                    import json as _jj
                    if payload.get("stream"):
                        # ponytail: synthesized SSE from full text, true passthrough when latency proves needed
                        try:
                            text = _jj.loads(body)["choices"][0]["message"]["content"]
                        except Exception:
                            text = ""
                        chunk = _jj.dumps({"choices": [{"delta": {"content": text}, "finish_reason": None}]})
                        sse = f"data: {chunk}\n\ndata: [DONE]\n\n".encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(sse)))
                        self.end_headers()
                        return self.wfile.write(sse)
                    if ckey and code == 200:
                        store.cache_set(cpath, ckey, body, ttl=int(payload.get("cache_ttl", 300)))
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                if code in (408, 429, 500, 502, 503, 504):
                    router.cool(t.get("name", ""), 5)  # ponytail: fixed 5s, exponential when proven needed
                last = (code, body)
            code, body = last
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        return self._send(404, {"error": {"message": "not found"}})

    def log_message(self, *a):
        pass  # ponytail: no logging infra until needed


def run(port=20128):
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    run()
