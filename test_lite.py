import json
import unittest
from http.client import HTTPConnection
from threading import Thread
from lite import Handler
from http.server import ThreadingHTTPServer


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read()

    def test_health(self):
        code, body = self.get("/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_models_shape(self):
        code, body = self.get("/v1/models")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["object"], "list")

    def test_404(self):
        code, _ = self.get("/nope")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
