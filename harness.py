#!/usr/bin/env python3
"""Black-box workflow harness: boots a REAL server + fake upstreams, acts like opencode.

Usage:
  python3 harness.py                 # self-contained: own server, temp HOME, full suite
  python3 harness.py http://host:p   # external server: safe subset (no rotate/restart)
Exit 0 = all checks pass. Stdlib only.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LITE_DIR = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" | " + str(detail)[:160] if detail and not cond else ""))
    if not cond:
        raise AssertionError(name)


class Client:
    def __init__(self, base, key=None):
        self.base, self.key = base, key

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = "Bearer " + self.key
        r = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def jreq(self, method, path, body=None):
        code, raw = self.req(method, path, body)
        try:
            return code, json.loads(raw)
        except ValueError:
            return code, {"_raw": raw[:200].decode("utf-8", "replace")}


class GoodUp(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _read(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _send(self, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self._read()
        if self.path.endswith("/chat/completions"):
            if body.get("stream"):
                sse = ("data: %s\n\ndata: [DONE]\n\n" % json.dumps(
                    {"choices": [{"delta": {"content": "hello-stream"}}]})).encode()
                return self._send(sse, "text/event-stream")
            return self._send({"choices": [{"message": {"role": "assistant", "content": "hello"}}]})
        if self.path.endswith("/embeddings"):
            return self._send({"data": [{"embedding": [0.1, 0.2], "index": 0}]})
        out = b"bad"
        self.send_response(400)
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        if self.path.endswith("/v1/models"):
            return self._send({"data": [{"id": "live-model"}]})
        self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


class BadUp(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(500)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class HtmlUp(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        out = b"<html>login</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main():
    external = sys.argv[1] if len(sys.argv) > 1 else None
    srv_proc, home = None, None
    if external:
        base = external.rstrip("/")
        c = Client(base)
        full = False
    else:
        home = tempfile.mkdtemp(prefix="lite-harness-")
        work = tempfile.mkdtemp(prefix="lite-work-")
        os.symlink(os.path.join(LITE_DIR, "dashboard.html"), os.path.join(work, "dashboard.html"))
        port = free_port()
        env = dict(os.environ, HOME=home)
        env.pop("LITE_KEY", None)
        srv_proc = subprocess.Popen([sys.executable, os.path.join(LITE_DIR, "lite.py"), str(port)],
                                    cwd=work, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        c = Client(base)
        for _ in range(100):
            try:
                if urllib.request.urlopen(base + "/health", timeout=2).status == 200:
                    break
            except OSError:
                time.sleep(0.2)
        else:
            print("FAIL server never booted")
            srv_proc.kill()
            return 1
        full = True
    try:
        run(c, full)
    except AssertionError:
        pass
    finally:
        if srv_proc:
            srv_proc.kill()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


def run(c, full):
    base = c.base
    up = serve(GoodUp)
    bad = serve(BadUp)
    html = serve(HtmlUp)
    upb = f"http://127.0.0.1:{up.server_address[1]}/v1"
    badb = f"http://127.0.0.1:{bad.server_address[1]}/v1"
    htmlb = f"http://127.0.0.1:{html.server_address[1]}/v1"

    code, raw = c.req("GET", "/health")
    check("health", code == 200 and b'"ok": true' in raw, raw[:80])
    code, raw = c.req("GET", "/")
    check("dashboard", code == 200 and b"s-chat" in raw and b"<img" not in raw)
    code, st = c.jreq("GET", "/admin/status")
    check("status open", code == 200 and st["key_mode"] == "open", st)
    code, pr = c.jreq("GET", "/admin/presets")
    check("presets", code == 200 and len(pr["presets"]) > 200, len(pr.get("presets", [])))

    code, d = c.jreq("POST", "/admin/discover", {"kind": "openai", "base_url": upb, "api_key": "-"})
    check("discover", d.get("models") == ["live-model"], d)

    t = {"name": "local", "base_url": upb, "api_key": "-", "models": ["live-model"]}
    code, d = c.jreq("POST", "/admin/targets", {"target": t})
    check("add", code == 200 and len(d["targets"]) == 1, d)
    code, d = c.jreq("GET", "/v1/models")
    check("models lists it", code == 200 and "live-model" in [m["id"] for m in d["data"]], d)
    code, d = c.jreq("POST", "/admin/targets/local/test", {"model": "live-model"})
    check("probe ok", code == 200 and d["ok"] is True, d)

    code, d = c.jreq("POST", "/v1/chat/completions",
                     {"model": "live-model", "messages": [{"role": "user", "content": "hi"}]})
    check("chat", d["choices"][0]["message"]["content"] == "hello", d)

    req = urllib.request.Request(base + "/v1/chat/completions",
                                 data=json.dumps({"model": "live-model", "messages": [], "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        sse = r.read().decode()
    check("stream relay", "hello-stream" in sse and "[DONE]" in sse, sse[:80])

    code, d = c.jreq("POST", "/v1/embeddings", {"model": "live-model", "input": "hi"})
    check("embeddings", d["data"][0]["embedding"] == [0.1, 0.2], d)

    code, d = c.jreq("POST", "/v1/chat/completions", {"model": "typo", "messages": []})
    check("unknown model", code == 404 and "live-model" in d["error"].get("available", []), d)

    code, d = c.jreq("POST", "/admin/targets",
                     {"target": {"name": "web", "base_url": htmlb, "api_key": "-",
                                 "models": ["web-model"]}})
    check("add web", code == 200)
    code, d = c.jreq("POST", "/admin/targets/web/test", {"model": "web-model"})
    check("web honest fail", code == 200 and d["ok"] is False and d.get("reason") == "bad shape", d)
    code, d = c.jreq("GET", "/admin/quota")
    check("probe parks bad key", "web" in d["state"]["cooldowns"], d["state"]["cooldowns"])

    for n, b in (("flaky", badb), ("local2", upb)):
        code, d = c.jreq("POST", "/admin/targets",
                         {"target": {"name": n, "base_url": b, "api_key": "-",
                                     "models": ["fo-model"]}})
        check("add " + n, code == 200, d)
    code, d = c.jreq("POST", "/v1/chat/completions",
                     {"model": "fo-model", "messages": [{"role": "user", "content": "hi"}]})
    check("failover serves", d["choices"][0]["message"]["content"] == "hello", d)
    code, d = c.jreq("GET", "/admin/quota")
    check("flaky parked", "flaky" in d["state"]["cooldowns"], d["state"]["cooldowns"])

    c.jreq("POST", "/admin/quota", {"reset": True})
    code, d = c.jreq("POST", "/admin/chains", {"chain": {"name": "c", "members": ["flaky", "local"]}})
    check("chain add", code == 200, d)
    code, d = c.jreq("POST", "/v1/chat/completions", {"model": "chain:c", "messages": []})
    check("chain chat", d["choices"][0]["message"]["content"] == "hello", d)

    code, d = c.jreq("GET", "/admin/logs?limit=50")
    chats = [e for e in d["logs"] if e["ep"] == "chat"]
    check("logs trail", any(e["target"] == "flaky" and e["code"] == 500 for e in chats)
          and any(e["target"] == "local" and e["code"] == 200 for e in chats))

    code, d = c.jreq("POST", "/admin/quota", {"reset": True})
    check("quota reset", d["stats"]["req"] == 0 and d["state"]["cooldowns"] == {}, d)

    if full:
        received = []

        class Recv(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                received.append(json.loads(self.rfile.read(n)))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        recv = serve(Recv)
        recvb = f"http://127.0.0.1:{recv.server_address[1]}/h"
        code, d = c.jreq("POST", "/admin/webhooks", {"url": recvb, "events": ["all_failed"]})
        check("webhook add", code == 200, d)
        code, d = c.jreq("POST", "/admin/targets",
                         {"target": {"name": "dead", "base_url": "http://127.0.0.1:1/v1",
                                     "api_key": "-", "models": ["m99"]}})
        c.jreq("POST", "/v1/chat/completions", {"model": "m99", "messages": []})
        for _ in range(100):
            if received:
                break
            time.sleep(0.05)
        check("webhook fired", received and received[0]["event"] == "all_failed", received)
        c.jreq("DELETE", "/admin/targets/dead")

        code, d = c.jreq("POST", "/admin/key/rotate", {})
        c.key = d["key"]
        check("rotate", code == 200 and len(c.key) > 20)
        code, _ = c.jreq("GET", "/v1/models")
        # no-key client still denied
        anon = Client(base)
        code, _ = anon.jreq("GET", "/v1/models")
        check("locked", code == 401, code)

        errs = []
        def one():
            try:
                code, d = c.jreq("POST", "/v1/chat/completions",
                                 {"model": "live-model", "messages": []})
                if code != 200:
                    errs.append(code)
            except OSError as e:
                errs.append(str(e))
        ts = [threading.Thread(target=one) for _ in range(20)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        check("20 parallel", not errs, errs[:3])

        code, d = c.jreq("GET", "/admin/status")
        up1 = d["uptime_s"]
        code, d = c.jreq("POST", "/admin/restart", {})
        check("restart ack", code == 200 and d.get("ok") is True, d)
        for _ in range(60):
            try:
                code, d = c.jreq("GET", "/admin/status")
                if code == 200:
                    break
            except OSError:
                time.sleep(0.5)
        check("restart back", code == 200 and d["uptime_s"] <= up1 + 8, d)

    for name in ["web", "flaky", "local", "local2"]:
        c.jreq("DELETE", "/admin/targets/" + name)
    c.jreq("DELETE", "/admin/chains/c")
    code, d = c.jreq("GET", "/admin/targets")
    check("cleanup", d.get("targets") == [], d)


if __name__ == "__main__":
    sys.exit(main())
