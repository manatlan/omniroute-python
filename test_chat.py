import json, unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import store, router
from lite import Handler


class Up(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        out = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
        cls.uport = cls.up.server_address[1]
        Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.up.shutdown(); cls.srv.shutdown()

    def post(self, payload):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/v1/chat/completions", json.dumps(payload), {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_no_targets_503(self):
        code, body = self.post({"model": "x", "messages": []})
        self.assertIn(code, (503, 502))  # empty keys.toml in repo

    def test_passthrough(self):
        orig = store.load_targets
        store.load_targets = lambda *a: [{"name": "t", "base_url": f"http://127.0.0.1:{self.uport}/v1", "api_key": "k", "models": ["m"]}]
        try:
            code, body = self.post({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
            self.assertEqual(code, 200)
            self.assertEqual(body["choices"][0]["message"]["content"], "hi")
        finally:
            store.load_targets = orig


if __name__ == "__main__":
    unittest.main()


class TStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        import threading
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_stream_bytes(self):
        import store as _s
        orig = _s.load_targets
        _s.load_targets = lambda *a: [{"name": "u", "base_url": "http://127.0.0.1:1/v1", "api_key": "k", "models": ["m"]}]
        import providers as _p
        o = _p.call_target
        _p.call_target = lambda t, p, timeout=30: (200, json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode())
        try:
            c = HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("POST", "/v1/chat/completions", json.dumps({"model": "m", "messages": [], "stream": True}), {"Content-Type": "application/json"})
            r = c.getresponse()
            body = r.read().decode()
            self.assertEqual(r.status, 200)
            self.assertIn("text/event-stream", r.getheader("Content-Type"))
            self.assertIn("data: [DONE]", body)
            self.assertIn("hello", body)
        finally:
            _s.load_targets = orig; _p.call_target = o

    def test_stream_shape(self):
        import store as _s
        orig = _s.load_targets
        _s.load_targets = lambda *a: []
        try:
            c = HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("POST", "/v1/chat/completions", json.dumps({"model": "x", "messages": [], "stream": True}), {"Content-Type": "application/json"})
            r = c.getresponse()
            self.assertEqual(r.status, 503)  # no targets -> no SSE, proves wiring point
        finally:
            _s.load_targets = orig


class TCacheHit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        import threading
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_cache_hit_skips_upstream(self):
        import store as _s, providers as _p, tempfile, os
        tmp = os.path.join(tempfile.mkdtemp(), "c.db")
        orig_home = os.path.expanduser
        os.path.expanduser = lambda p: tmp if p == "~" else orig_home(p)
        calls = []
        o_call = _p.call_target
        _p.call_target = lambda t, p, timeout=30: (calls.append(1) or (200, b'{"choices":[{"message":{"content":"cached"}}]}'))
        o_load = _s.load_targets
        _s.load_targets = lambda *a: [{"name": "u", "base_url": "http://x/v1", "api_key": "k", "models": ["m"]}]
        try:
            body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}], "cache_ttl": 60})
            for _ in range(2):
                c = HTTPConnection("127.0.0.1", self.port, timeout=5)
                c.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
                self.assertEqual(c.getresponse().status, 200)
            self.assertEqual(len(calls), 1)
        finally:
            os.path.expanduser = orig_home; _p.call_target = o_call; _s.load_targets = o_load
