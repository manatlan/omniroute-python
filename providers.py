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


def forward(base_url, api_key, path, payload, timeout=30):
    url = base_url.rstrip("/") + path
    h = {"Content-Type": "application/json"}
    if api_key and api_key != "-":
        h["Authorization"] = f"Bearer {api_key}"
    return post(url, h, json.dumps(payload).encode(), timeout)[:3]


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
            {"Content-Type": "application/json", "x-api-key": t["api_key"], "anthropic-version": "2023-06-01"},
            json.dumps(to_anthropic(payload)).encode(), timeout)
        if code == 200:
            body = json.dumps(from_anthropic(body)).encode()
        return code, h, body
    if kind == "gemini":
        code, h, body = forward(t["base_url"], t["api_key"],
            "/v1beta/models/" + payload.get("model", "") + ":generateContent", to_gemini(payload), timeout)
        if code == 200:
            body = json.dumps(from_gemini(body)).encode()
        return code, h, body
    if kind == "azure":
        ver = t.get("api_version", "2024-06-01")
        code, h, body = post(t["base_url"].rstrip("/") + "/openai/deployments/" + payload.get("model", "") + "/chat/completions?api-version=" + ver,
            {"Content-Type": "application/json", "api-key": t["api_key"]},
            json.dumps(payload).encode(), timeout)
        return code, h, body
    # openai + ollama (ollama: api_key empty/"-" omits auth)
    return forward(t["base_url"], t.get("api_key", "-"), "/v1/chat/completions", payload, timeout)
