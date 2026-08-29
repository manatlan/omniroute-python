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
