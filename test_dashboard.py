import re, shutil, subprocess, tempfile, unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread
import os
from lite import Handler

HTML = open(os.path.join(os.path.dirname(__file__), "dashboard.html")).read()

ROUTES = {
    ("GET", "/admin/status"), ("GET", "/admin/quota"), ("GET", "/admin/targets"),
    ("GET", "/admin/presets"), ("GET", "/admin/key"), ("GET", "/admin/webhooks"),
    ("GET", "/health"),
    ("POST", "/admin/targets"), ("POST", "/admin/targets/*/test"), ("POST", "/admin/quota"),
    ("POST", "/admin/key/rotate"), ("POST", "/admin/webhooks"), ("POST", "/admin/webhooks/*/test"),
    ("POST", "/admin/restart"),
    ("DELETE", "/admin/targets/*"), ("DELETE", "/admin/webhooks/*"),
}


def js_calls(html, keyword):
    """Yield raw JS arg expression for each keyword( ... ) call."""
    out, i = [], 0
    while True:
        i = html.find(keyword, i)
        if i < 0:
            return out
        j = html.index("(", i) + 1
        depth, instr, esc, k = 1, False, False, j
        while depth:
            c = html[k]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == "'":
                    instr = False
            elif c == "'":
                instr = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            k += 1
        out.append(html[j:k - 1])
        i = k


def split_top(s):
    """split on top-level commas (respect quotes, brackets, parens)."""
    parts, depth, instr, esc, cur = [], 0, False, False, ""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack = []
    for c in s:
        if instr:
            cur += c
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == "'":
                instr = False
        elif c == "'":
            instr = True
            cur += c
        elif c in pairs:
            stack.append(pairs[c])
            cur += c
        elif stack and c == stack[-1]:
            stack.pop()
            cur += c
        elif c == "," and not stack:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    return parts


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_serves_dashboard(self):
        c = HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/")
        r = c.getresponse()
        body = r.read().decode()
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.getheader("Content-Type"))
        for sec in ["s-home", "s-apis", "s-prov", "s-quota", "s-key", "s-web", "s-restart"]:
            self.assertIn('id="%s"' % sec, body)
        for name in ["Home", "APIs", "Providers", "Quota", "Key", "Webhooks", "Restart"]:
            self.assertIn("'%s'" % name, body)
        self.assertNotIn("<img", body)

    def test_js_calls_only_real_routes(self):
        calls = set()
        for e in js_calls(HTML, "api("):
            m = re.match(r"'([A-Z]+)',\s*(.*)", e, re.S)
            if not m:
                continue  # the api() definition itself
            path_expr = split_top(m.group(2))[0]
            lits = re.findall(r"'((?:[^'\\]|\\.)*)'", path_expr)
            p = "*".join(lits)
            if not path_expr.rstrip().endswith("'"):
                p += "*"  # dynamic tail, e.g. '/x/' + id
            calls.add((m.group(1), p))
        for e in js_calls(HTML, "fetch("):
            e = e.strip()
            if e.startswith("'"):
                calls.add(("GET", e[1:-1]))
        for c in calls:
            self.assertIn(c, ROUTES, "JS calls unknown route %r" % (c,))

    @unittest.skipUnless(shutil.which("node"), "node missing")
    def test_js_parses(self):
        js = re.search(r"<script>(.*)</script>", HTML, re.S).group(1)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js)
            path = f.name
        try:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
