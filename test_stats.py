import unittest
import stats


class T(unittest.TestCase):
    def test_note_and_snap(self):
        stats.reset()
        stats.note("a", True, 100)
        stats.note("a", False, 300)
        s = stats.snap()
        self.assertEqual((s["req"], s["ok"], s["err"], s["avg_ms"]), (2, 1, 1, 200))
        self.assertEqual(s["by_target"]["a"], {"ok": 1, "err": 1, "avg_ms": 200})


if __name__ == "__main__":
    unittest.main()


class THttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from lite import Handler
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _get(self, path, key=None):
        from http.client import HTTPConnection
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Authorization": f"Bearer {key}"} if key else {}
        c.request("GET", path, headers=h)
        r = c.getresponse()
        return r.status, r.read()

    def test_open_without_key(self):
        import os
        os.environ.pop("LITE_KEY", None)
        code, _ = self._get("/v1/stats")
        self.assertEqual(code, 200)

    def test_auth_enforced(self):
        import os
        os.environ["LITE_KEY"] = "s3cr3t"
        try:
            code, _ = self._get("/v1/stats")
            self.assertEqual(code, 401)
            code, _ = self._get("/v1/stats", key="s3cr3t")
            self.assertEqual(code, 200)
            code, _ = self._get("/health")
            self.assertEqual(code, 200)  # health stays open
        finally:
            os.environ.pop("LITE_KEY", None)
