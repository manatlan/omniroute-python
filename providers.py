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
