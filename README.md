# omniroute-lite (stdlib only, zero deps)

`python3 lite.py` → `http://127.0.0.1:20128`. No pip install. No docker. No electron.

## Add an API (no restart)
Append to `keys.toml`:
```toml
[[targets]]
name = "groq"
kind = "openai" # or anthropic | gemini
base_url = "https://api.groq.com/openai/v1"
api_key = "gsk-..."
models = ["llama-3.3-70b-versatile"]
```

## opencode
```json
{ "baseURL": "http://localhost:20128/v1", "apiKey": "local", "models": ["auto"] }
```

## Test one call (1 token)
`curl -X POST localhost:20128/v1/test/call -d '{"model":"llama-3.3-70b-versatile"}'`

## Autostart (survives daily shutdown)
```
cp omniroute-lite.service ~/.config/systemd/user/
systemctl --user enable --now omniroute-lite
loginctl enable-linger $USER
```

## Endpoints
`GET /health` `GET /v1/models` `POST /v1/chat/completions` (`stream:true` → SSE) `POST /v1/test/call`. Opt-in cache: add `"cache_ttl": 300` to a chat body.
