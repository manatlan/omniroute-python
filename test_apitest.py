import json, unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
import store, providers
from lite import Handler


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_call_ok(self):
        o_load, o_call = store.load_targets, providers.call_target
        store.load_targets = lambda *a: [{"name": "u", "base_url": "http://x", "api_key": "k", "models": ["m"]}]
        providers.call_target = lambda t, p, timeout=30: (200, {}, b"{}")
        try:
            c = HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("POST", "/v1/test/call", json.dumps({"model": "m"}), {"Content-Type": "application/json"})
            r = c.getresponse()
            body = json.loads(r.read())
            self.assertEqual(r.status, 200)
            self.assertEqual(body, {"ok": True, "target": "u", "status": 200, "latency_ms": body["latency_ms"]})
        finally:
            store.load_targets, providers.call_target = o_load, o_call

    def test_call_no_targets(self):
        o = store.load_targets
        store.load_targets = lambda *a: []
        try:
            c = HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("POST", "/v1/test/call", "{}", {"Content-Type": "application/json"})
            self.assertEqual(c.getresponse().status, 503)
        finally:
            store.load_targets = o


if __name__ == "__main__":
    unittest.main()
