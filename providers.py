"""upstream HTTP: keep-alive pool, bounded reads, kind dispatch. stdlib only."""
import http.client
import json
import threading
import urllib.parse

MAX_BODY = 10 * 1024 * 1024  # ponytail: fixed cap, per-target override when proven needed

_pool = {}
_plock = threading.Lock()


def _take(scheme, host, port, timeout):
    # -> (conn, reused). Reused conns may be half-open corpses; callers retry once.
    with _plock:
        lst = _pool.get((scheme, host, port)) or []
        while lst:
            c = lst.pop()
            try:
                c.sock.getpeername()
                return c, True
            except (OSError, AttributeError):
                try:
                    c.close()
                except OSError:
                    pass
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    return cls(host, port, timeout=timeout), False


def _close(conn):
    try:
        conn.close()
    except OSError:
        pass


def _give(scheme, host, port, conn):
    # capped: extra idle conns are closed, not hoarded (fd ceiling).
    with _plock:
        lst = _pool.setdefault((scheme, host, port), [])
        if len(lst) >= 4:
            try:
                conn.close()
            except OSError:
                pass
            return
        lst.append(conn)


def post(url, headers, body, timeout=30, max_body=MAX_BODY):
    """-> (status, lowercased headers dict, body bytes). Never raises."""
    sp = urllib.parse.urlsplit(url)
    scheme, host = sp.scheme, sp.hostname
    port = sp.port or (443 if scheme == "https" else 80)
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query
    for _try in (0, 1):
        conn, reused = _take(scheme, host, port, timeout)
        try:
            conn.request("POST", path, body=body, headers=headers)
            r = conn.getresponse()
            h = {k.lower(): v for k, v in r.getheaders()}
            data = r.read(max_body + 1)
            if len(data) > max_body:
                _close(conn)
                return 502, {}, b'{"error":{"message":"upstream body too large"}}'
            if r.will_close:
                _close(conn)
            else:
                _give(scheme, host, port, conn)
            return r.status, h, data
        except (OSError, http.client.HTTPException):
            _close(conn)
            if reused and _try == 0:
                continue  # half-open corpse; one fresh retry
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


def get(url, headers, timeout=15, max_body=MAX_BODY):
    # same pool as post, GET flavor. Never raises.
    sp = urllib.parse.urlsplit(url)
    scheme, host = sp.scheme, sp.hostname
    if not host:
        return 502, {}, b'{"error":{"message":"bad url"}}'
    port = sp.port or (443 if scheme == "https" else 80)
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query
    for _try in (0, 1):
        conn, reused = _take(scheme, host, port, timeout)
        try:
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            h = {k.lower(): v for k, v in r.getheaders()}
            data = r.read(max_body + 1)
            if len(data) > max_body:
                _close(conn)
                return 502, {}, b'{"error":{"message":"upstream body too large"}}'
            if r.will_close:
                _close(conn)
            else:
                _give(scheme, host, port, conn)
            return r.status, h, data
        except (OSError, http.client.HTTPException):
            _close(conn)
            if reused and _try == 0:
                continue  # half-open corpse; one fresh retry
            return 503, {}, b'{"error":{"message":"upstream unreachable"}}'


def discover_models(kind, base_url, api_key, timeout=15):
    # ask the company what it serves right now. -> (ids|None, err|None)
    import json as _j
    base = (base_url or "").rstrip("/")
    if not base.startswith(("https://", "http://")):
        return None, "bad url"
    auth = {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "-" else {}
    if kind in ("openai", "ollama"):
        code, _, body = get(base + "/v1/models", auth, timeout)
    elif kind == "azure":
        code, _, body = get(base + "/openai/models?api-version=2024-06-01", {"api-key": api_key or ""}, timeout)
    elif kind == "gemini":
        code, _, body = get(base + "/v1beta/models", {"x-goog-api-key": api_key or ""}, timeout)
    else:
        return None, "no list API for this kind; type the names"
    if code != 200:
        return None, "http " + str(code)
    try:
        d = _j.loads(body)
    except ValueError:
        return None, "bad shape"
    try:
        if kind == "gemini":
            ids = [m["name"].split("/")[-1] for m in d.get("models", []) if m.get("name")]
        else:
            ids = [m["id"] for m in d.get("data", []) if m.get("id")]
    except (AttributeError, TypeError):
        return None, "bad shape"
    return ids[:200], None


def forward(base_url, api_key, path, payload, timeout=30, extra=None):
    url = base_url.rstrip("/") + path
    auth = {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "-" else {}
    return post(url, t_headers(extra or {}, auth), json.dumps(payload).encode(), timeout)[:3]




def valid_chat(body):
    """True if body is OpenAI-shape chat with usable text. Rejects HTML/error pages."""
    try:
        import json as _j
        ch = _j.loads(body).get("choices")
        if not ch:
            return False
        msg = ch[0].get("message", {})
        return isinstance(msg, dict) and (isinstance(msg.get("content"), (str, list)) or isinstance(msg.get("tool_calls"), list))
    except (ValueError, AttributeError, IndexError, TypeError):
        return False


def probe_ok(body):
    """Looser: any parsed choices+message (probe uses max_tokens 1, content may be empty)."""
    try:
        import json as _j
        ch = _j.loads(body).get("choices")
        return bool(ch) and isinstance(ch[0].get("message"), dict)
    except (ValueError, AttributeError, IndexError, TypeError):
        return False


def to_anthropic(payload):
    """OpenAI chat -> Anthropic messages. ponytail: text-only, images when proven needed."""
    system = "\n".join(m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "system")
    msgs = [{"role": m["role"], "content": m.get("content") or ""} for m in payload.get("messages", []) if m.get("role") in ("user", "assistant")]
    body = {"model": payload.get("model", ""), "max_tokens": payload.get("max_tokens", 1024), "messages": msgs}
    if system:
        body["system"] = system
    return body


def from_anthropic(body):
    """Anthropic response -> OpenAI chat shape."""
    try:
        d = json.loads(body) if isinstance(body, (bytes, bytearray)) else body
        text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": d.get("stop_reason", "stop")}]}
    except (ValueError, AttributeError, TypeError):
        return {"choices": []}


def to_gemini(payload):
    # OpenAI chat -> Gemini contents. Assistant turns kept as model; same-role runs merged.
    contents = []
    for m in payload.get("messages", []) or []:
        role = "model" if m.get("role") == "assistant" else "user"
        part = {"text": m.get("content") or ""}
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append(part)
        else:
            contents.append({"role": role, "parts": [part]})
    if not contents or contents[0]["role"] != "user":
        contents.insert(0, {"role": "user", "parts": [{"text": ""}]})
    return {"contents": contents}


def from_gemini(body):
    """Gemini response -> OpenAI chat shape. Never raises; [] means unparseable."""
    try:
        d = json.loads(body) if isinstance(body, (bytes, bytearray)) else body
        text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return {"choices": []}
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def _call_raw(t, payload, timeout=30):
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
    # -> (200, headers, chunk-iterator) or (errcode, headers, error-bytes). Never raises.
    import urllib.parse as _up
    import http.client as _hc
    sp = _up.urlsplit(url)
    scheme, host = sp.scheme, sp.hostname
    port = sp.port or (443 if scheme == "https" else 80)
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query
    r = None
    for _try in (0, 1):
        conn, reused = _take(scheme, host, port, timeout)
        try:
            conn.request("POST", path, body=body, headers=headers)
            r = conn.getresponse()
            break
        except (OSError, _hc.HTTPException):
            _close(conn)
            if reused and _try == 0:
                continue  # half-open corpse; one fresh retry
            return 503, {}, b'{"error":{"message":"upstream unreachable"}}'
    try:
        h = {k.lower(): v for k, v in r.getheaders()}
        if r.status != 200:
            data = r.read(MAX_BODY + 1)
            _close(conn)
            return r.status, h, data

        def gen():
            try:
                while True:
                    b = r.read(chunk)
                    if not b:
                        break
                    yield b
            finally:
                _close(conn)
        return r.status, h, gen()
    except (OSError, _hc.HTTPException):
        _close(conn)
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


def valid_embed(body):
    # True if body carries a non-empty embedding vector.
    try:
        import json as _j
        d = _j.loads(body).get("data")
        return bool(d) and isinstance(d[0].get("embedding"), list) and bool(d[0]["embedding"])
    except (ValueError, AttributeError, IndexError, TypeError):
        return False


def embed_target(t, payload, timeout=30):
    """embeddings -> (status, headers, body). gemini: str input only."""
    kind = t.get("kind", "openai")
    if kind == "azure":
        ver = t.get("api_version", "2024-06-01")
        code, h, body = post(t["base_url"].rstrip("/") + "/openai/deployments/" + payload.get("model", "") + "/embeddings?api-version=" + ver,
            t_headers(t, {"api-key": t["api_key"]}),
            json.dumps(payload).encode(), timeout)
        return _gate_embed(code, h, body)
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
            except (KeyError, ValueError, TypeError, AttributeError):
                vec = []
            body = json.dumps({"data": [{"embedding": vec, "index": 0}], "model": payload.get("model", "")}).encode()
        return _gate_embed(code, h, body)
    code, h, body = forward(t["base_url"], t.get("api_key", "-"), "/v1/embeddings", payload, timeout, extra=t)
    return _gate_embed(code, h, body)


def _gate_embed(code, h, body):
    if code == 200 and not valid_embed(body):
        return 502, h, b'{"error":{"message":"upstream bad shape"}}'
    return code, h, body


def call_target(t, payload, timeout=30):
    # shape gate: HTTP 200 with a non-chat body becomes 502 (failover).
    code, h, body = _call_raw(t, payload, timeout)
    if code == 200 and not valid_chat(body):
        return 502, h, b'{"error":{"message":"upstream bad shape"}}'
    return code, h, body
