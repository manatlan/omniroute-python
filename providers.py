"""upstream forward. OpenAI-shape passthrough; translators land in next commits."""
import json
import urllib.request


def forward(base_url, api_key, path, payload, timeout=30):
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


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
    import json as _j
    d = _j.loads(body) if isinstance(body, (bytes, bytearray)) else body
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": d.get("stop_reason", "stop")}]}


def to_gemini(payload):
    """OpenAI chat -> Gemini generateContent. ponytail: text-only first."""
    parts = [{"text": m.get("content", "")} for m in payload.get("messages", []) if m.get("role") in ("user", "system")]
    return {"contents": [{"role": "user", "parts": parts}]}


def from_gemini(body):
    """Gemini response -> OpenAI chat shape."""
    import json as _j
    d = _j.loads(body) if isinstance(body, (bytes, bytearray)) else body
    try:
        text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        text = ""
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def call_target(t, payload, timeout=30):
    """kind-aware dispatch: openai (default) | anthropic | gemini."""
    import json as _j
    kind = t.get("kind", "openai")
    if kind == "anthropic":
        import urllib.request as _u
        req = _u.Request(t["base_url"].rstrip("/") + "/v1/messages",
            data=_j.dumps(to_anthropic(payload)).encode(),
            headers={"Content-Type": "application/json", "x-api-key": t["api_key"], "anthropic-version": "2023-06-01"},
            method="POST")
        try:
            with _u.urlopen(req, timeout=timeout) as r:
                return r.status, _j.dumps(from_anthropic(r.read())).encode()
        except _u.HTTPError as e:
            return e.code, e.read()
    if kind == "gemini":
        code, body = forward(t["base_url"], t["api_key"],
            "/v1beta/models/" + payload.get("model", "") + ":generateContent",
            to_gemini(payload), timeout)
        if code == 200:
            return code, _j.dumps(from_gemini(body)).encode()
        return code, body
    return forward(t["base_url"], t["api_key"], "/v1/chat/completions", payload, timeout)
