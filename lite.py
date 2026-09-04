"""omniroute-lite: stdlib-only OpenAI-compatible router stub."""
import store
import router
import providers
import compress
import stats
import admin
import webhooks
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.1.0"

import time as _time

START = _time.time()


def _classify(t, model, code, hdrs):
    return router.classify(t, model, code, hdrs)


def _fire_breaker(t, model, code, hdrs):
    prov = _classify(t, model, code, hdrs)
    if prov:
        webhooks.dispatch("breaker_open", {"provider": prov})


class Handler(BaseHTTPRequestHandler):
    server_version = "omniroute-lite/" + VERSION

    def _send(self, code, obj, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit=10 * 1024 * 1024):
        """-> (payload, None) | (None, 'bad'|'large'). Bounds inbound memory."""
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return None, "bad"
        if n > limit or n < 0:
            return None, "large"
        try:
            return json.loads(self.rfile.read(n) or b"{}"), None
        except Exception:
            return None, "bad"

    def _authed(self):
        key = store.load_server_key()
        return not key or self.headers.get("Authorization") == f"Bearer {key}"

    def _deny(self):
        return self._send(401, {"error": {"message": "unauthorized"}})

    def do_GET(self):
        if admin.handle(self, "GET"):
            return
        if self.path == "/health":
            return self._send(200, {"ok": True, "version": VERSION})
        if self.path == "/admin/status":
            if not self._authed():
                return self._deny()
            import os as _o
            key = store.load_server_key()
            return self._send(200, {"version": VERSION, "uptime_s": int(_time.time() - START),
                "targets": len(store.load_targets()),
                "key_mode": "env" if _o.environ.get("LITE_KEY") else ("file" if key else "open")})
        if self.path == "/v1/stats":
            if not self._authed():
                return self._deny()
            return self._send(200, stats.snap())
        if self.path == "/v1/models":
            if not self._authed():
                return self._deny()
            return self._send(200, {"object": "list", "data": store.model_list(store.load_targets())})
        if self.path == "/":
            try:
                with open("dashboard.html", "rb") as f:
                    dash = f.read()
            except OSError:
                dash = b"<pre>omniroute-lite ok</pre>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(dash)))
            self.end_headers()
            return self.wfile.write(dash)
        return self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        import time as _tt
        if admin.handle(self, "POST"):
            return
        if not self._authed():
            return self._deny()
        if self.path == "/v1/chat/completions":
            _t0 = _tt.time()
            import json as _j
            payload, err = self._body()
            if err:
                return self._send(413 if err == "large" else 400, {"error": {"message": "bad json" if err == "bad" else "body too large"}})
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
                name = t.get("name", "")
                if payload.get("stream") and t.get("kind", "openai") in ("openai", "ollama", "azure"):
                    scode, hdrs, chunks = providers.stream_target(t, payload)
                    if scode == 200:
                        router.note_success(name)
                        router.note_provider_ok(t)
                        stats.note(name, True, int((_tt.time() - _t0) * 1000))
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        try:
                            for b in chunks:
                                self.wfile.write(b)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return
                    code, body = scode, chunks
                else:
                    code, hdrs, body = providers.call_target(t, payload)
                if code not in router.FAILOVER:
                    router.note_success(name)
                    router.note_provider_ok(t)
                    stats.note(name, code < 400, int((_tt.time() - _t0) * 1000))
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
                _fire_breaker(t, model, code, hdrs)
                last = (code, body)
            code, body = last
            stats.note(None, False, int((_tt.time() - _t0) * 1000))
            webhooks.dispatch("all_failed", {"endpoint": "chat", "model": model})
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if self.path == "/v1/embeddings":
            _t0 = _tt.time()
            payload, err = self._body()
            if err:
                return self._send(413 if err == "large" else 400, {"error": {"message": "bad json" if err == "bad" else "body too large"}})
            model = payload.get("model", "")
            targets = [t for t in router.chain(store.load_targets(), model) if t.get("kind", "openai") != "anthropic"]
            if not targets:
                return self._send(400, {"error": {"message": "no embedding-capable target"}})
            last = (502, b'{"error":{"message":"all targets failed"}}')
            for t in targets:
                code, hdrs, body = providers.embed_target(t, payload)
                if code not in router.FAILOVER:
                    router.note_success(t.get("name", ""))
                    router.note_provider_ok(t)
                    stats.note(t.get("name", ""), code < 400, int((_tt.time() - _t0) * 1000))
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    return self.wfile.write(body)
                _fire_breaker(t, model, code, hdrs)
                last = (code, body)
            code, body = last
            stats.note(None, False, int((_tt.time() - _t0) * 1000))
            webhooks.dispatch("all_failed", {"endpoint": "embeddings", "model": model})
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if self.path == "/v1/test/call":
            import json as _j, time as _t
            payload, err = self._body()
            if err:
                return self._send(413 if err == "large" else 400, {"error": {"message": "bad json" if err == "bad" else "body too large"}})
            model = payload.get("model", "")
            targets = router.chain(store.load_targets(), model)
            if not targets:
                return self._send(503, {"error": {"message": "no targets configured"}})
            t = targets[0]
            probe = {"model": model or (t.get("models", [""])[0]), "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
            start = _t.time()
            code, hdrs, body = providers.call_target(t, probe)
            ms = int((_t.time() - start) * 1000)
            ok = code not in router.FAILOVER
            name = t.get("name", "")
            if ok:
                router.note_success(name)
                router.note_provider_ok(t)
            elif code in router.FAILOVER:
                _fire_breaker(t, probe["model"], code, hdrs)
            stats.note(t.get("name", ""), ok, ms)
            return self._send(200 if ok else code, {"ok": ok, "target": t.get("name"), "status": code, "latency_ms": ms})
        return self._send(404, {"error": {"message": "not found"}})

    def log_message(self, *a):
        pass  # ponytail: no logging infra until needed


    def do_PUT(self):
        if admin.handle(self, "PUT"):
            return
        return self._send(404, {"error": {"message": "not found"}})

    def do_DELETE(self):
        if admin.handle(self, "DELETE"):
            return
        return self._send(404, {"error": {"message": "not found"}})


def run(port=20128):
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    run()
