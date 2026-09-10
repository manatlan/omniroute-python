"""Full user journey against the real stack + fake upstreams. No mocks below HTTP."""
import json, os, tempfile, threading, time, unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store
from lite import Handler


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
                text = "hello-stream"
                sse = ("data: %s\n\ndata: [DONE]\n\n" % json.dumps(
                    {"choices": [{"delta": {"content": text}}]})).encode()
                return self._send(sse, "text/event-stream")
            return self._send({"choices": [{"message": {"role": "assistant", "content": "hello"}}]})
        if self.path.endswith("/embeddings"):
            return self._send({"data": [{"embedding": [0.1, 0.2], "index": 0}]})
        out = b'{"error":{"message":"nope"}}'
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
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


class Harness(unittest.TestCase):
    _key = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls._oexp = os.path.expanduser
        os.path.expanduser = lambda p: cls.tmp if p == "~" else cls._oexp(p)
        cls._oenv = os.environ.pop("LITE_KEY", None)
        cls._oload = store.load_targets
        cls.keys = os.path.join(cls.tmp, "keys.toml")
        store.load_targets = lambda *a, **k: cls._oload(cls.keys)
        import admin
        cls._oadmin_path = admin.TARGETS_PATH
        admin.TARGETS_PATH = cls.keys
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), GoodUp)
        cls.uport = cls.up.server_address[1]
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.bad = ThreadingHTTPServer(("127.0.0.1", 0), BadUp)
        cls.badport = cls.bad.server_address[1]
        threading.Thread(target=cls.bad.serve_forever, daemon=True).start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        os.path.expanduser = cls._oexp
        if cls._oenv is not None:
            os.environ["LITE_KEY"] = cls._oenv
        store.load_targets = cls._oload
        import admin
        admin.TARGETS_PATH = cls._oadmin_path
        cls.up.shutdown()
        cls.bad.shutdown()
        cls.srv.shutdown()

    def setUp(self):
        import router, stats
        router._reset()
        stats.reset()
        if os.path.exists(self.keys):
            os.unlink(self.keys)
        store._cache.update(mtime=0, targets=[])

    def req(self, method, path, body=None, key=None):
        c = HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        key = type(self)._key if key is None else key
        if key:
            h["Authorization"] = "Bearer " + key
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        return r.status, r.read()

    def _add(self, name, port):
        code, _ = self.req("POST", "/admin/targets", {"target": {
            "name": name, "base_url": f"http://127.0.0.1:{port}/v1",
            "api_key": "-", "models": ["live-model"]}})
        self.assertEqual(code, 200)


class Journey(Harness):
    def test_01_onboard_add_discover_test(self):
        code, body = self.req("GET", "/admin/status")
        self.assertEqual(code, 200)
        code, body = self.req("GET", "/admin/presets")
        self.assertGreater(len(json.loads(body)["presets"]), 200)
        code, body = self.req("POST", "/admin/discover", {
            "kind": "openai", "base_url": f"http://127.0.0.1:{self.uport}/v1", "api_key": "-"})
        self.assertEqual(json.loads(body), {"models": ["live-model"]})
        self._add("local", self.uport)
        code, body = self.req("GET", "/v1/models")
        self.assertIn("live-model", [m["id"] for m in json.loads(body)["data"]])
        code, body = self.req("POST", "/admin/targets/local/test", {"model": "live-model"})
        d = json.loads(body)
        self.assertEqual((code, d["ok"]), (200, True))

    def test_02_chat_stream_embed(self):
        self._add("local", self.uport)
        code, body = self.req("POST", "/v1/chat/completions",
                              {"model": "live-model", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "hello")
        c = HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", "/v1/chat/completions",
                  json.dumps({"model": "live-model", "messages": [], "stream": True}),
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        sse = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertIn("hello-stream", sse)
        self.assertIn("[DONE]", sse)
        code, body = self.req("POST", "/v1/embeddings", {"model": "live-model", "input": "hi"})
        self.assertEqual(json.loads(body)["data"][0]["embedding"], [0.1, 0.2])

    def test_03_failover_then_key_lockdown(self):
        self._add("flaky", self.badport)
        self._add("local", self.uport)
        code, body = self.req("POST", "/v1/chat/completions",
                              {"model": "live-model", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "hello")
        code, body = self.req("GET", "/admin/quota")
        state = json.loads(body)["state"]
        self.assertIn("flaky", state["cooldowns"])
        code, body = self.req("POST", "/admin/key/rotate", {})
        key = json.loads(body)["key"]
        type(self)._key = key
        code, _ = self.req("GET", "/v1/models", key=False)
        self.assertEqual(code, 401)
        code, body = self.req("GET", "/v1/models", key=key)
        self.assertEqual(code, 200)


class Edge(Harness):
    def test_04_unknown_model_suggests(self):
        self._add("local", self.uport)
        code, body = self.req("POST", "/v1/chat/completions",
                              {"model": "typo-model", "messages": []})
        d = json.loads(body)
        self.assertEqual(code, 404)
        self.assertIn("live-model", d["error"].get("available", []))

    def test_05_parallel_chats(self):
        self._add("local", self.uport)
        codes = []
        port = self.port

        def one():
            c = HTTPConnection("127.0.0.1", port, timeout=15)
            c.request("POST", "/v1/chat/completions",
                      json.dumps({"model": "live-model", "messages": []}),
                      {"Content-Type": "application/json"})
            codes.append(c.getresponse().status)

        ts = [threading.Thread(target=one) for _ in range(10)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(codes, [200] * 10)

    def test_06_cache_expiry(self):
        self._add("local", self.uport)
        code, body = self.req("GET", "/admin/logs?limit=500")
        before = len([e for e in json.loads(body)["logs"]
                      if e["ep"] == "chat" and e["target"] == "local"])
        payload = {"model": "live-model", "messages": [{"role": "user", "content": "x"}],
                   "cache_ttl": 1}
        for _ in range(2):
            self.req("POST", "/v1/chat/completions", payload)
        time.sleep(1.3)
        self.req("POST", "/v1/chat/completions", payload)
        code, body = self.req("GET", "/admin/logs?limit=500")
        ups = [e for e in json.loads(body)["logs"]
               if e["ep"] == "chat" and e["target"] == "local"]
        self.assertEqual(len(ups) - before, 2)  # miss, hit (cache row), miss after expiry

    def test_07_chain_order(self):
        self._add("flaky", self.badport)
        self._add("local", self.uport)
        code, body = self.req("POST", "/admin/chains",
                              {"chain": {"name": "c", "members": ["flaky", "local"]}})
        self.assertEqual(code, 200)
        code, body = self.req("POST", "/v1/chat/completions",
                              {"model": "chain:c", "messages": []})
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "hello")
        code, body = self.req("GET", "/admin/logs?limit=5")
        got = [(e["target"], e["code"]) for e in json.loads(body)["logs"] if e["ep"] == "chat"]
        self.assertIn(("flaky", 500), got)
        self.assertIn(("local", 200), got)

    def test_08_breaker_opens_then_skips(self):
        import router
        self._add("flaky", self.badport)
        # cooldown parks a dead key after each attempt, so trip the provider
        # breaker directly, then prove the router skips it end to end
        t = {"name": "flaky", "models": ["live-model"]}
        for _ in range(router.BRK_THRESHOLD):
            router.note_provider_fail(t)
        code, body = self.req("GET", "/admin/quota")
        brk = json.loads(body)["state"]["breakers"]
        self.assertGreater(brk["flaky"]["open_s"], 0)
        self._add("local", self.uport)
        code, body = self.req("POST", "/v1/chat/completions",
                              {"model": "live-model", "messages": []})
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "hello")


if __name__ == "__main__":
    unittest.main()
