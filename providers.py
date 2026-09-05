"""upstream HTTP: keep-alive pool, bounded reads, kind dispatch. stdlib only."""
import http.client
import json
import threading
import urllib.parse

MAX_BODY = 10 * 1024 * 1024  # ponytail: fixed cap, per-target override when proven needed

_pool = {}
_plock = threading.Lock()


def _take(scheme, host, port, timeout):
    with _plock:
        lst = _pool.get((scheme, host, port)) or []
        while lst:
            c = lst.pop()
            try:
                c.sock.getpeername()
                return c
            except (OSError, AttributeError):
                try:
                    c.close()
                except OSError:
                    pass
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    return cls(host, port, timeout=timeout)


def _give(scheme, host, port, conn):
    with _plock:
        _pool.setdefault((scheme, host, port), []).append(conn)


def post(url, headers, body, timeout=30, max_body=MAX_BODY):
    """-> (status, lowercased headers dict, body bytes). Never raises."""
    sp = urllib.parse.urlsplit(url)
    scheme, host = sp.scheme, sp.hostname
    port = sp.port or (443 if scheme == "https" else 80)
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query
    conn = _take(scheme, host, port, timeout)
    try:
        conn.request("POST", path, body=body, headers=headers)
        r = conn.getresponse()
        h = {k.lower(): v for k, v in r.getheaders()}
        data = r.read(max_body + 1)
        if len(data) > max_body:
            conn.close()
            return 502, {}, b'{"error":{"message":"upstream body too large"}}'
        if r.will_close:
            conn.close()
        else:
            _give(scheme, host, port, conn)
        return r.status, h, data
    except (OSError, http.client.HTTPException):
        try:
            conn.close()
        except OSError:
            pass
        return 503, {}, b'{"error":{"message":"upstream unreachable"}}'


def t_headers(t, base):
    """user headers/cookies first, kind auth wins, Content-Type always ours."""
    h = {}
    for k, v in (t.get("headers") or {}).items():
        if k.lower() != "content-length":
            h[k] = str(v)
    if t.get("cookies"):
        prior = h.get("Cookie", "")
        h["Cookie"] = (prior + "; " + t["cookies"]).strip("; ") if prior else t["cookies"]
    h.update(base)
    h["Content-Type"] = "application/json"
    return h


def forward(base_url, api_key, path, payload, timeout=30, extra=None):
    url = base_url.rstrip("/") + path
    auth = {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "-" else {}
    return post(url, t_headers(extra or {}, auth), json.dumps(payload).encode(), timeout)[:3]


def to_anthropic(payload):
    """OpenAI chat -> Anthropic messages. ponytail: text-only, images when proven needed."""
    system = "\n".join(m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "system")
    msgs = [{"role": m["role"], "content": m.get("content", "")} for m in payload.get("messages", []) if m.get("role") in ("user", "assistant")]
    body = {"model": payload.get("model", ""), "max_tokens": payload.get("max_tokens", 1024), "messages": msgs}
    if system:
        body["system"] = system
    return body


def from_anthropic(body):
    """Anthropic response -> OpenAI chat shape."""
    d = json.loads(body) if isinstance(body, (bytes, bytearray)) else body
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": d.get("stop_reason", "stop")}]}


def to_gemini(payload):
    """OpenAI chat -> Gemini generateContent. ponytail: text-only first."""
    parts = [{"text": m.get("content", "")} for m in payload.get("messages", []) if m.get("role") in ("user", "system")]
    return {"contents": [{"role": "user", "parts": parts}]}


def from_gemini(body):
    """Gemini response -> OpenAI chat shape."""
    d = json.loads(body) if isinstance(body, (bytes, bytearray)) else body
    try:
        text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        text = ""
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def call_target(t, payload, timeout=30):
    """kind-aware dispatch -> (status, headers, body). kinds: openai | ollama | anthropic | gemini | azure."""
    kind = t.get("kind", "openai")
    if kind == "anthropic":
        code, h, body = post(t["base_url"].rstrip("/") + "/v1/messages",
            t_headers(t, {"x-api-key": t["api_key"], "anthropic-version": "2023-06-01"}),
            json.dumps(to_anthropic(payload)).encode(), timeout)
        if code == 200:
            body = json.dumps(from_anthropic(body)).encode()
        return code, h, body
    if kind == "gemini":
        code, h, body = forward(t["base_url"], t["api_key"],
            "/v1beta/models/" + payload.get("model", "") + ":generateContent", to_gemini(payload), timeout, extra=t)
        if code == 200:
            body = json.dumps(from_gemini(body)).encode()
        return code, h, body
    if kind == "azure":
        ver = t.get("api_version", "2024-06-01")
        code, h, body = post(t["base_url"].rstrip("/") + "/openai/deployments/" + payload.get("model", "") + "/chat/completions?api-version=" + ver,
            t_headers(t, {"api-key": t["api_key"]}),
            json.dumps(payload).encode(), timeout)
        return code, h, body
    # openai + ollama (ollama: api_key empty/"-" omits auth)
    return forward(t["base_url"], t.get("api_key", "-"), "/v1/chat/completions", payload, timeout, extra=t)


def post_stream(url, headers, body, timeout=30, chunk=65536):
    """-> (200, headers, chunk-iterator) or (errcode, headers, error-bytes). Never raises."""
    import urllib.parse as _up
    import http.client as _hc
    sp = _up.urlsplit(url)
    scheme, host = sp.scheme, sp.hostname
    port = sp.port or (443 if scheme == "https" else 80)
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query
    conn = _take(scheme, host, port, timeout)
    try:
        conn.request("POST", path, body=body, headers=headers)
        r = conn.getresponse()
        h = {k.lower(): v for k, v in r.getheaders()}
        if r.status != 200:
            data = r.read(MAX_BODY + 1)
            conn.close()
            return r.status, h, data
        def gen():
            try:
                while True:
                    b = r.read(chunk)
                    if not b:
                        break
                    yield b
            finally:
                conn.close()
        return r.status, h, gen()
    except (OSError, _hc.HTTPException):
        try:
            conn.close()
        except OSError:
            pass
        return 503, {}, b'{"error":{"message":"upstream unreachable"}}'


def stream_target(t, payload, timeout=30):
    """openai-shape SSE relay -> (status, headers, iterator|bytes)."""
    kind = t.get("kind", "openai")
    if kind == "azure":
        ver = t.get("api_version", "2024-06-01")
        return post_stream(t["base_url"].rstrip("/") + "/openai/deployments/" + payload.get("model", "") + "/chat/completions?api-version=" + ver,
            t_headers(t, {"api-key": t["api_key"]}),
            json.dumps({**payload, "stream": True}).encode(), timeout)
    return post_stream(t["base_url"].rstrip("/") + "/v1/chat/completions",
        t_headers(t, dict({"Authorization": f"Bearer {t['api_key']}"} if t.get("api_key") and t.get("api_key") != "-" else {})),
        json.dumps({**payload, "stream": True}).encode(), timeout)


def embed_target(t, payload, timeout=30):
    """embeddings -> (status, headers, body). gemini: str input only."""
    kind = t.get("kind", "openai")
    if kind == "azure":
        ver = t.get("api_version", "2024-06-01")
        return post(t["base_url"].rstrip("/") + "/openai/deployments/" + payload.get("model", "") + "/embeddings?api-version=" + ver,
            t_headers(t, {"api-key": t["api_key"]}),
            json.dumps(payload).encode(), timeout)
    if kind == "gemini":
        text = payload.get("input", "")
        if not isinstance(text, str):
            return 400, {}, b'{"error":{"message":"gemini kind takes str input; use openai-kind target for lists"}}'
        code, h, body = post(t["base_url"].rstrip("/") + "/v1beta/models/" + payload.get("model", "") + ":embedContent",
            t_headers(t, {"x-goog-api-key": t["api_key"]}),
            json.dumps({"content": {"parts": [{"text": text}]}}).encode(), timeout)
        if code == 200:
            try:
                vec = json.loads(body)["embedding"]["values"]
            except (KeyError, ValueError):
                vec = []
            body = json.dumps({"data": [{"embedding": vec, "index": 0}], "model": payload.get("model", "")}).encode()
        return code, h, body
    return forward(t["base_url"], t.get("api_key", "-"), "/v1/embeddings", payload, timeout, extra=t)
