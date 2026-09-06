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


class TCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_catalog_shape(self):
        import catalog
        self.assertGreater(len(catalog.CATALOG), 200)
        kinds = {p["kind"] for p in catalog.CATALOG}
        self.assertIn("openai", kinds)
        for p in catalog.CATALOG:
            self.assertTrue(p["base"] == "" or "://" in p["base"])
            for m in p["models"]:
                self.assertIn("id", m)

    def test_presets_endpoint(self):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/admin/presets")
        r = c.getresponse()
        body = json.loads(r.read())
        self.assertEqual(r.status, 200)
        self.assertGreater(len(body["presets"]), 200)
        self.assertIn("generated", body)
        groq = next(p for p in body["presets"] if p["provider"] == "groq")
        self.assertTrue(groq["lite"])
        self.assertTrue(any(m["id"] == "llama-3.3-70b-versatile" for m in groq["models"]))


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


class TKeyRestart(unittest.TestCase):
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
        self.tmp = tempfile.mkdtemp()
        self._o = os.path.expanduser
        os.path.expanduser = lambda p: self.tmp if p == "~" else self._o(p)
        self._old_key = os.environ.pop("LITE_KEY", None)
        store._cache.update(mtime=0, targets=[])
        store._key_cache.update(mtime=0, key=None)

    def tearDown(self):
        os.path.expanduser = self._o
        if self._old_key is not None:
            os.environ["LITE_KEY"] = self._old_key

    def req(self, method, path, body=None, key=None):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_status_shape(self):
        code, out = self.req("GET", "/admin/status")
        self.assertEqual(code, 200)
        self.assertEqual(out["key_mode"], "open")
        self.assertIn("uptime_s", out)

    def test_key_roundtrip(self):
        code, out = self.req("GET", "/admin/key")
        self.assertEqual((code, out["key"]), (200, None))
        code, out = self.req("POST", "/admin/key/rotate", {})
        self.assertEqual(code, 200)
        key1 = out["key"]
        self.assertTrue(len(key1) > 20)
        code, _ = self.req("GET", "/admin/key")
        self.assertEqual(code, 401)  # rotation locked the server
        code, out = self.req("GET", "/admin/key", key=key1)
        self.assertEqual(out["key"], key1)
        code, out = self.req("POST", "/admin/key/rotate", {}, key=key1)
        self.assertNotEqual(out["key"], key1)

    def test_restart_responds_and_reexecs(self):
        import os as _os
        calls = []
        real = _os.execv
        _os.execv = lambda *a: calls.append(a)
        try:
            code, out = self.req("POST", "/admin/restart", {})
            self.assertEqual((code, out), (200, {"ok": True}))
            for _ in range(100):
                if calls:
                    break
                import time as _t
                _t.sleep(0.05)
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0][1][1].endswith("lite.py"))
        finally:
            _os.execv = real


class TModels(unittest.TestCase):
    def test_map_shape(self):
        import admin as _a
        old = _a.TARGETS_PATH
        import tempfile
        _a.TARGETS_PATH = os.path.join(tempfile.mkdtemp(), "k.toml")
        try:
            store.save_targets(_a.TARGETS_PATH, [
                {"name": "a", "base_url": "https://x/v1", "api_key": "k",
                 "models": ["llama-3.3-70b-versatile", "mine"]}])
            m = _a.configured_model_map()
            by_id = {e["id"]: e for e in m}
            self.assertEqual(by_id["mine"]["targets"], ["a"])
            self.assertFalse(by_id["mine"]["free"])
            self.assertIn("targets", by_id["llama-3.3-70b-versatile"])
        finally:
            _a.TARGETS_PATH = old


class TLogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_last_lines_newest_first(self):
        import json as _j
        import tempfile
        tmp = tempfile.mkdtemp()
        os.environ["LITE_DATA_DIR"] = tmp
        try:
            import lite as _l
            _l._log("chat", "a", "m", 200, 5)
            _l._log("chat", "b", "m", 500, 9)
            c = HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request("GET", "/admin/logs?limit=10")
            body = _j.loads(c.getresponse().read())
            self.assertEqual([e["target"] for e in body["logs"]], ["b", "a"])
        finally:
            del os.environ["LITE_DATA_DIR"]
