import json, os, unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
import admin
import store
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

    def setUp(self):
        import tempfile
        self.tmp = os.path.join(tempfile.mkdtemp(), "keys.toml")
        self._old_path, admin.TARGETS_PATH = admin.TARGETS_PATH, self.tmp
        self._old_key = os.environ.pop("LITE_KEY", None)
        store._cache.update(mtime=0, targets=[])

    def tearDown(self):
        admin.TARGETS_PATH = self._old_path
        if self._old_key is not None:
            os.environ["LITE_KEY"] = self._old_key

    def req(self, method, path, body=None):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_crud_roundtrip(self):
        code, out = self.req("GET", "/admin/targets")
        self.assertEqual((code, out), (200, {"targets": []}))
        t = {"name": "g", "base_url": "https://x/v1", "api_key": "k", "models": ["m"]}
        code, out = self.req("POST", "/admin/targets", {"target": t})
        self.assertEqual(code, 200)
        self.assertEqual(len(out["targets"]), 1)
        code, _ = self.req("POST", "/admin/targets", {"target": t})
        self.assertEqual(code, 400)  # dup name
        t2 = dict(t, base_url="https://y/v1")
        code, out = self.req("PUT", "/admin/targets/g", {"target": t2})
        self.assertEqual(out["targets"][0]["base_url"], "https://y/v1")
        code, out = self.req("DELETE", "/admin/targets/g")
        self.assertEqual(out, {"targets": []})
        # file round-trips through real TOML parser
        self.assertEqual(store.load_targets(self.tmp), [])

    def test_validation(self):
        code, out = self.req("POST", "/admin/targets", {"target": {"name": "x"}})
        self.assertEqual(code, 400)
        code, _ = self.req("PUT", "/admin/targets/nope", {"target": {"name": "n", "base_url": "b"}})
        self.assertEqual(code, 404)


class TPresets(unittest.TestCase):
    def test_shape_and_free_flags(self):
        import presets
        for p in presets.PRESETS:
            self.assertTrue(p["base_url"].startswith(("https://", "http://localhost")))
            self.assertIsInstance(p["free"], bool)
            for m in p["models"]:
                self.assertIn("id", m)
                self.assertIsInstance(m.get("free", False), bool)
        self.assertGreaterEqual(sum(1 for p in presets.PRESETS if p["free"]), 3)


class TQuota(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        import tempfile
        self.tmp = os.path.join(tempfile.mkdtemp(), "keys.toml")
        self._old_path, admin.TARGETS_PATH = admin.TARGETS_PATH, self.tmp
        self._old_key = os.environ.pop("LITE_KEY", None)

    def tearDown(self):
        admin.TARGETS_PATH = self._old_path
        if self._old_key is not None:
            os.environ["LITE_KEY"] = self._old_key

    def test_snapshot_and_reset(self):
        import router as _r, stats as _s
        _r._reset(); _s.reset()
        _r.cool("a", 60)
        _r.lock_model("a", "m", 60)
        s = _r.snapshot()
        self.assertGreater(s["cooldowns"]["a"], 50)
        self.assertIn("a / m", s["model_locks"])
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/admin/quota")
        r = c.getresponse()
        body = json.loads(r.read())
        self.assertIn("stats", body)
        self.assertIn("a", body["state"]["cooldowns"])
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/admin/quota", json.dumps({"reset": True}), {"Content-Type": "application/json"})
        body = json.loads(c.getresponse().read())
        self.assertEqual(body["state"]["cooldowns"], {})
