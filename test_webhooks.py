import json, os, tempfile, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import webhooks


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._o = os.path.expanduser
        os.path.expanduser = lambda p: self.tmp if p == "~" else self._o(p)

    def tearDown(self):
        os.path.expanduser = self._o

    def test_crud_and_validation(self):
        self.assertEqual(webhooks.list_hooks(), [])
        hid, err = webhooks.add_hook("ftp://x", ["all_failed"])
        self.assertIsNone(hid)
        hid, err = webhooks.add_hook("https://x/hook", ["bogus"])
        self.assertIsNone(hid)
        hid, err = webhooks.add_hook("https://x/hook", ["all_failed"])
        self.assertEqual(hid, "1")
        self.assertEqual(len(webhooks.list_hooks()), 1)
        webhooks.delete_hook("1")
        self.assertEqual(webhooks.list_hooks(), [])

    def test_dispatch_posts_json(self):
        received = []

        class Recv(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                received.append(json.loads(self.rfile.read(n)))
                out = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Recv)
        Thread(target=srv.serve_forever, daemon=True).start()
        webhooks.add_hook(f"http://127.0.0.1:{srv.server_address[1]}/h", ["all_failed"])
        webhooks.dispatch("all_failed", {"model": "m"})
        for _ in range(100):
            if received:
                break
            time.sleep(0.05)
        srv.shutdown()
        self.assertEqual(received, [{"event": "all_failed", "model": "m"}])

    def test_breaker_event_from_classify(self):
        import router
        router._reset()
        t = {"name": "a", "provider": "p"}
        got = None
        for _ in range(router.BRK_THRESHOLD):
            got = router.classify(t, "m", 500, {})
        self.assertEqual(got, "p")


if __name__ == "__main__":
    unittest.main()
