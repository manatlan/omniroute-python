# omniroute-lite (stdlib only, zero deps)

`python3 lite.py` → `http://127.0.0.1:20128`. No pip install. No docker. No electron.

Measured on this laptop: ~20 MB warm RSS, ~2.8 ms routing overhead per request
(loopback; <1% next to real 300 ms+ upstreams). Keep-alive pool, bounded
10 MB reads, thread-per-request server.

## Add an API (no restart)
Append a block to `keys.toml` (see commented examples in the file).
`kind`: `openai` (default — Groq/Together/DeepSeek/xAI/Mistral/OpenRouter/…)
| `ollama` | `anthropic` | `gemini` | `azure` (model = deployment name).

## opencode
```json
{ "baseURL": "http://localhost:20128/v1", "apiKey": "local", "models": ["auto"] }
```
Set `LITE_KEY=...` env to require `Authorization: Bearer ...` (health stays open).

## Endpoints
`GET /health` · `GET /v1/models` · `POST /v1/chat/completions`
(`stream:true` → true SSE relay for openai-shape, synthesized for translated)
· `POST /v1/embeddings` (not anthropic-kind) · `POST /v1/test/call`
(1-token probe → `{ok, target, status, latency_ms}`) · `GET /v1/stats`.

Opt-in chat cache: add `"cache_ttl": 300` to a chat body (exact-match, sqlite).

## Routing (daisy-chain, same 3 layers as OmniRoute)
1. provider circuit breaker (5 fails → 30 s open, lazy half-open probe),
2. per-key cooldown (5 s base, exponential, honors upstream `Retry-After`),
3. per-model lockout (429 → 60 s, 404 → 10 min; 401/403 parks the key 5 min).
Failover set: 401/403/404/408/429/5xx. First try serves model-match targets first.

## Autostart (survives daily shutdown)
```
cp omniroute-lite.service ~/.config/systemd/user/
systemctl --user enable --now omniroute-lite
loginctl enable-linger $USER
```

## Dashboard (http://localhost:20128)
Black and white, no manual needed. Seven tabs:

- **Home** — APIs, calls, avg ms, one-line verdict.
- **APIs** — add (start from a preset), per-row Test + Remove. Keys shown masked.
- **Providers** — preset cards with FREE tags, Free-only toggle, Use fills the Add form.
- **Quota** — per-API ok/bad/avg-ms plus live state (OK / resting Ns), Reset counts.
- **Key** — the two values opencode needs (URL + key). Copy, New key (auto-saved in browser).
- **Webhooks** — POST JSON on `breaker_open` / `all_failed`, per-row Try + Remove.
- **Restart** — one button, page reloads when back.

First run is open; press New key once and the server + dashboard lock to it.

## Everything the full router has, lite edition
- **245 providers, 1213 models** in the Providers tab, generated from the main
  OmniRoute registry (`python3 /tmp/gen_catalog.py` to refresh). FREE tags come
  from its hand-curated free-tier catalog. Search to find anything.
- **Web cookies**: cookie-auth providers are flagged; the Add form has Cookies and
  Extra-headers boxes, and the key box tells you when a cookie goes there.
- **Ollama is optional**: the preset points at your laptop, but the Address box takes
  any remote OpenAI-compatible server. Nothing must be installed locally.
- **Web cookies**: cookie-auth providers are flagged; paste the browser cookie
  as the key, or add extra `cookies` / `headers` per API (keys.toml or dashboard).
- **Click around**: every model button Tests that model; Chat tab streams full
  conversations; Logs tab shows every call.
- **Chains**: named ordered failover lists, usable as `chain:name` anywhere
  (opencode included).
- Same 3-layer resilience, keep-alive pool, opt-in cache, trim, webhooks.

- **Fetch live list**: the Add form asks the company itself what models it serves
  right now, so you use their names, not a stale catalog.

## Tests
`python3 -m unittest` (41 tests, stdlib only).
