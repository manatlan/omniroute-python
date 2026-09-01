import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import providers


class Up(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        try:
            echo = json.loads(body)
        except Exception:
            echo = len(body)
        out = json.dumps({"echo": echo}).encode()
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
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Up)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_forward(self):
        code, hdrs, body = providers.forward(f"http://127.0.0.1:{self.port}", "k", "/v1/chat/completions", {"a": 1})
        self.assertEqual(code, 200)
        self.assertIn("content-type", hdrs)
        self.assertEqual(json.loads(body)["echo"], {"a": 1})

    def test_pool_reuses_connection(self):
        base = f"http://127.0.0.1:{self.port}"
        providers.forward(base, "k", "/v1/chat/completions", {"a": 1})
        providers.forward(base, "k", "/v1/chat/completions", {"a": 2})
        import urllib.parse
        sp = urllib.parse.urlsplit(base)
        pool = providers._pool.get(("http", sp.hostname, self.port)) or []
        self.assertEqual(len(pool), 1)

    def test_body_cap(self):
        code, _, _ = providers.post(f"http://127.0.0.1:{self.port}/x", {}, b"{}", max_body=64)
        self.assertEqual(code, 200)  # small body passes
        big = json.dumps({"x": "y" * 100}).encode()
        code, _, _ = providers.post(f"http://127.0.0.1:{self.port}/big", {}, big, max_body=10)
        self.assertEqual(code, 502)

    def test_unreachable(self):
        code, _, _ = providers.post("http://127.0.0.1:1/x", {}, b"{}", timeout=1)
        self.assertEqual(code, 503)


if __name__ == "__main__":
    unittest.main()


class T2(unittest.TestCase):
    def test_anthropic_roundtrip(self):
        out = providers.to_anthropic({"model": "m", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]})
        self.assertEqual(out["system"], "s")
        back = providers.from_anthropic({"content": [{"type": "text", "text": "yo"}], "stop_reason": "end_turn"})
        self.assertEqual(back["choices"][0]["message"]["content"], "yo")


class T3(unittest.TestCase):
    def test_gemini_roundtrip(self):
        out = providers.to_gemini({"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(out["contents"][0]["parts"], [{"text": "hi"}])
        back = providers.from_gemini({"candidates": [{"content": {"parts": [{"text": "yo"}]}}]})
        self.assertEqual(back["choices"][0]["message"]["content"], "yo")
