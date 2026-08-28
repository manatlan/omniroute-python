"""omniroute-lite: stdlib-only OpenAI-compatible router stub."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.1.0"


class Handler(BaseHTTPRequestHandler):
    server_version = "omniroute-lite/" + VERSION

    def _send(self, code, obj, ctype="application/json"):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "version": VERSION})
        if self.path == "/v1/models":
            return self._send(200, {"object": "list", "data": []})
        if self.path == "/":
            html = "<pre>omniroute-lite ok\nGET /health\nGET /v1/models</pre>"
            return self._send(200, html, "text/html")
        return self._send(404, {"error": {"message": "not found"}})

    def log_message(self, *a):
        pass  # ponytail: no logging infra until needed


def run(port=20128):
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    run()
