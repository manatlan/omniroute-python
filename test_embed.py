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

    def _post(self, payload):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/v1/embeddings", json.dumps(payload), {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_passthrough(self):
        o_load, o_call = store.load_targets, providers.embed_target
        store.load_targets = lambda *a: [{"name": "u", "models": ["e"]}]
        providers.embed_target = lambda t, p, timeout=30: (200, {}, b'{"data":[{"embedding":[0.1]}]}')
        try:
            code, body = self._post({"model": "e", "input": "hi"})
            self.assertEqual(code, 200)
            self.assertEqual(body["data"][0]["embedding"], [0.1])
        finally:
            store.load_targets, providers.embed_target = o_load, o_call

    def test_anthropic_skipped(self):
        o = store.load_targets
        store.load_targets = lambda *a: [{"name": "u", "kind": "anthropic", "models": ["e"]}]
        try:
            c = HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("POST", "/v1/embeddings", "{}", {"Content-Type": "application/json"})
            self.assertEqual(c.getresponse().status, 400)
        finally:
            store.load_targets = o

    def test_gemini_str_only(self):
        code, _, _ = providers.embed_target({"kind": "gemini", "base_url": "http://x", "api_key": "k"}, {"model": "m", "input": ["a", "b"]})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
