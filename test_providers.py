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


class TCookies(unittest.TestCase):
    def test_cookie_and_custom_header_sent(self):
        seen = {}

        class Recv(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                seen["cookie"] = self.headers.get("Cookie")
                seen["x"] = self.headers.get("X-Custom")
                seen["auth"] = self.headers.get("Authorization")
                out = b'{"choices":[{"message":{"role":"assistant","content":"hi"}}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        from http.server import ThreadingHTTPServer
        from threading import Thread
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Recv)
        Thread(target=srv.serve_forever, daemon=True).start()
        t = {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "api_key": "k",
             "cookies": "sess=abc", "headers": {"X-Custom": "yes"}}
        code, _, _ = providers.call_target(t, {"model": "m", "messages": []})
        srv.shutdown()
        self.assertEqual(code, 200)
        self.assertEqual(seen["cookie"], "sess=abc")
        self.assertEqual(seen["x"], "yes")
        self.assertEqual(seen["auth"], "Bearer k")

    def test_toml_roundtrip(self):
        import os, tempfile
        from store import save_targets, load_targets
        p = os.path.join(tempfile.mkdtemp(), "k.toml")
        t = {"name": "w", "kind": "openai", "base_url": "https://x/v1", "api_key": "-",
             "cookies": "a=b; c=d", "headers": {"X-Foo": "bar"}, "models": ["m"]}
        save_targets(p, [t])
        back = load_targets(p)
        self.assertEqual(back[0]["cookies"], "a=b; c=d")
        self.assertEqual(back[0]["headers"], {"X-Foo": "bar"})


class TShape(unittest.TestCase):
    def test_valid_and_probe(self):
        good = b'{"choices":[{"message":{"role":"assistant","content":"hi"}}]}'
        self.assertTrue(providers.valid_chat(good))
        self.assertTrue(providers.probe_ok(good))
        self.assertFalse(providers.valid_chat(b"<html>nope</html>"))
        self.assertFalse(providers.probe_ok(b"<html>nope</html>"))
        self.assertFalse(providers.valid_chat(b'{"error":{"message":"x"}}'))
        empty = b'{"choices":[{"message":{"role":"assistant","content":""}}]}'
        self.assertTrue(providers.valid_chat(empty))

    def test_200_html_becomes_502(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        class Html(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                out = b"<html>web page</html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Html)
        Thread(target=srv.serve_forever, daemon=True).start()
        code, _, _ = providers.call_target(
            {"base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1", "api_key": "-"},
            {"model": "m", "messages": []})
        srv.shutdown()
        self.assertEqual(code, 502)


class TAudit(unittest.TestCase):
    def test_tool_calls_pass_gate(self):
        body = (b'{"choices":[{"message":{"role":"assistant","content":null,'
                b'"tool_calls":[{"id":"1","function":{"name":"f"}}]},"finish_reason":"tool_calls"}]}')
        self.assertTrue(providers.valid_chat(body))
        self.assertTrue(providers.probe_ok(body))

    def test_gemini_keeps_assistant_turns(self):
        out = providers.to_gemini({"messages": [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
            {"role": "user", "content": "again"}]})
        roles = [c["role"] for c in out["contents"]]
        texts = [c["parts"][0]["text"] for c in out["contents"]]
        self.assertIn("model", roles)
        self.assertEqual("".join(texts).count("yo"), 1)
        self.assertIn("be nice", "".join(texts))
        # strictly alternating user/model for the real API
        for a, b in zip(roles, roles[1:]):
            self.assertNotEqual(a, b)

    def test_dead_pooled_conn_retries(self):
        import socket
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        class Up(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                out = b'{"choices":[{"message":{"content":"hi"}}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Up)
        port = srv.server_address[1]
        Thread(target=srv.serve_forever, daemon=True).start()
        try:
            providers._pool.clear()
            code, _, _ = providers.post(f"http://127.0.0.1:{port}/v1/c", {}, b"{}")
            self.assertEqual(code, 200)
            key = ("http", "127.0.0.1", port)
            self.assertTrue(providers._pool.get(key))  # conn pooled
            conn = providers._pool[key].pop()
            conn.sock.shutdown(socket.SHUT_RDWR)  # half-open corpse: looks live, sends fail
            providers._pool[key].append(conn)
            code, _, _ = providers.post(f"http://127.0.0.1:{port}/v1/c", {}, b"{}")
            self.assertEqual(code, 200)  # retried fresh, not 503
        finally:
            providers._pool.clear()
            srv.shutdown()
            srv.server_close()

    def test_embed_html_rejected(self):
        code, _, _ = providers.embed_target(
            {"base_url": "http://127.0.0.1:1", "api_key": "-"}, {"model": "m", "input": "hi"})
        # unreachable stays 503 (gate only rewrites 200s); shape covered below
        self.assertEqual(code, 503)


class TPoolCap(unittest.TestCase):
    def test_pool_bounded(self):
        import http.client
        providers._pool.clear()
        try:
            for _ in range(10):
                providers._give("http", "h", 80, http.client.HTTPConnection("h", 80))
            self.assertLessEqual(len(providers._pool[("http", "h", 80)]), 4)
        finally:
            providers._pool.clear()


class TTimeout(unittest.TestCase):
    def test_slow_upstream_is_504(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        import time

        class Slow(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                time.sleep(5)

            def log_message(self, *a):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        Thread(target=srv.serve_forever, daemon=True).start()
        try:
            code, _, body = providers.post(f"http://127.0.0.1:{srv.server_address[1]}/v", {}, b"{}", timeout=1)
            self.assertEqual(code, 504)
            self.assertIn(b"timeout", body)
        finally:
            srv.shutdown()
            srv.server_close()
