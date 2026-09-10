"""OAuth for CLI-class providers (no browser automation needed).

claude-oauth: PKCE auth-code flow (user pastes code back) -> api.anthropic.com
codex-oauth:  device flow (user types code at auth.openai.com) -> chatgpt.com backend
Public client IDs are XOR-masked exactly like the main repo's publicCreds.ts
(public by design; masking only silences pattern scanners).
"""
import json
import os
import time

MASK = "omniroute-public-v1"
_MASKED_IDS = {
    "claude": [86, 9, 95, 10, 64, 90, 69, 21, 72, 72, 70, 68, 0, 65, 93, 87, 73, 79, 28, 87, 85, 11, 13, 95, 90, 76, 64, 81, 73, 65, 76, 84, 94, 15, 86, 72],
    "codex": [14, 29, 30, 54, 55, 34, 26, 21, 8, 104, 53, 47, 85, 95, 15, 83, 110, 29, 105, 14, 53, 30, 94, 26, 29, 20, 26, 11],
}

CLAUDE_AUTH = "https://claude.ai/oauth/authorize"
CLAUDE_TOKEN = "https://api.anthropic.com/v1/oauth/token"
CLAUDE_REDIRECT = "https://platform.claude.com/oauth/code/callback"
CLAUDE_SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers"

CODEX_AUTH_BASE = "https://auth.openai.com"
CODEX_DEVICE = CODEX_AUTH_BASE + "/api/accounts/deviceauth"
CODEX_TOKEN = CODEX_AUTH_BASE + "/oauth/token"
CODEX_REDIRECT = CODEX_AUTH_BASE + "/deviceauth/callback"
CODEX_VERIFY = CODEX_AUTH_BASE + "/codex/device"


def _unmask(nums):
    return "".join(chr(n ^ ord(MASK[i % len(MASK)])) for i, n in enumerate(nums))


def client_id(provider):
    env = os.environ.get({"claude": "CLAUDE_OAUTH_CLIENT_ID", "codex": "CODEX_OAUTH_CLIENT_ID"}[provider], "")
    if env:
        return env
    plain = _unmask(_MASKED_IDS[provider])
    if plain and all(32 <= ord(c) < 127 for c in plain):
        return plain
    return ""


def pkce():
    import base64
    import hashlib
    import secrets
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _path():
    import store
    return os.path.join(os.path.dirname(store.data_path("oauth.json")), "oauth.json")


def _load_all():
    try:
        with open(_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_all(d):
    import store
    p = store.data_path("oauth.json")
    with open(p, "w") as f:
        json.dump(d, f)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def get_tokens(name):
    return _load_all().get(name)


def save_tokens(name, provider, access, refresh, expires_in, email=None):
    d = _load_all()
    d[name] = {"provider": provider, "access_token": access, "refresh_token": refresh,
               "expires_at": int(time.time()) + int(expires_in or 3600),
               "email": email}
    _save_all(d)


def delete_tokens(name):
    d = _load_all()
    d.pop(name, None)
    _save_all(d)


def _refresh(provider, token_url, client_id_, refresh_token):
    import urllib.parse
    import providers
    body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token,
                                   "client_id": client_id_}).encode()
    code, _, data = providers.post(token_url, {"Content-Type": "application/x-www-form-urlencoded"}, body, 20)
    if code != 200:
        return None
    try:
        return json.loads(data)
    except ValueError:
        return None


def valid_token(name, force=False):
    """Usable access token or None (auto-refreshes when expiring; force re-refreshes)."""
    creds = get_tokens(name)
    if not creds or not creds.get("refresh_token"):
        return None
    if not force and creds.get("expires_at", 0) - time.time() > 120 and creds.get("access_token"):
        return creds["access_token"]
    provider = creds.get("provider")
    urls = {"claude": CLAUDE_TOKEN, "codex": CODEX_TOKEN}
    cid = client_id(provider)
    toks = _refresh(provider, urls[provider], cid, creds["refresh_token"]) if cid else None
    if not toks or not toks.get("access_token"):
        return None
    save_tokens(name, provider, toks["access_token"], toks.get("refresh_token") or creds["refresh_token"],
                toks.get("expires_in", 3600), creds.get("email"))
    return toks["access_token"]


# --- claude auth-code flow ---

def claude_start():
    import secrets
    import urllib.parse
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(16)
    cid = client_id("claude")
    q = urllib.parse.urlencode({"code": "true", "client_id": cid, "response_type": "code",
                                "redirect_uri": CLAUDE_REDIRECT, "scope": CLAUDE_SCOPES,
                                "code_challenge": challenge, "code_challenge_method": "S256",
                                "state": state, "prompt": "login"})
    return {"url": CLAUDE_AUTH + "?" + q, "verifier": verifier, "state": state}


def claude_finish(name, code, verifier, state):
    import providers
    code, st = (code.split("#", 1) + [""])[:2]
    body = json.dumps({"code": code, "state": st or state, "grant_type": "authorization_code",
                       "client_id": client_id("claude"), "redirect_uri": CLAUDE_REDIRECT,
                       "code_verifier": verifier}).encode()
    scode, _, data = providers.post(CLAUDE_TOKEN, {"Content-Type": "application/json"}, body, 20)
    if scode != 200:
        return None, "exchange failed: http %d" % scode
    try:
        toks = json.loads(data)
    except ValueError:
        return None, "exchange failed: bad shape"
    if not toks.get("access_token"):
        return None, "exchange failed: no access_token"
    email = claude_email(toks["access_token"])
    save_tokens(name, "claude", toks["access_token"], toks.get("refresh_token", ""),
                toks.get("expires_in", 3600), email)
    return {"email": email}, None


def claude_email(access):
    import providers
    code, _, data = providers.get("https://api.anthropic.com/api/claude_cli/bootstrap",
                                  {"Authorization": "Bearer " + access,
                                   "anthropic-beta": "oauth-2025-04-20"}, 15)
    if code != 200:
        return None
    try:
        return (json.loads(data).get("oauth_account") or {}).get("account_email")
    except ValueError:
        return None


# --- codex device flow ---

def codex_start():
    import providers
    body = json.dumps({"client_id": client_id("codex")}).encode()
    code, _, data = providers.post(CODEX_DEVICE + "/usercode",
                                   {"Content-Type": "application/json"}, body, 20)
    if code == 404:
        return None, "device login disabled for this account"
    if code != 200:
        return None, "device start failed: http %d" % code
    try:
        d = json.loads(data)
        return {"device_auth_id": d["device_auth_id"], "user_code": d["user_code"],
                "interval": int(d.get("interval") or 5), "verification_uri": CODEX_VERIFY}, None
    except (ValueError, KeyError, TypeError):
        return None, "device start failed: bad shape"


def codex_poll(device_auth_id, user_code, interval=5, deadline_s=25):
    # returns (authorization_code, code_verifier) or (None, pending|timeout|err)
    import providers
    end = time.time() + deadline_s
    while time.time() < end:
        time.sleep(min(interval, max(1, end - time.time())))
        body = json.dumps({"device_auth_id": device_auth_id, "user_code": user_code}).encode()
        try:
            code, _, data = providers.post(CODEX_DEVICE + "/token",
                                           {"Content-Type": "application/json"}, body, 20)
        except Exception:
            continue
        if code in (403, 404):
            continue  # still pending
        if code != 200:
            return None, "poll failed: http %d" % code
        try:
            d = json.loads(data)
            return (d["authorization_code"], d["code_verifier"]), None
        except (ValueError, KeyError, TypeError):
            return None, "poll failed: bad shape"
    return None, "pending"


def codex_finish(name, authorization_code, code_verifier):
    import urllib.parse
    import providers
    body = urllib.parse.urlencode({"grant_type": "authorization_code",
                                   "client_id": client_id("codex"), "code": authorization_code,
                                   "code_verifier": code_verifier,
                                   "redirect_uri": CODEX_REDIRECT}).encode()
    code, _, data = providers.post(CODEX_TOKEN, {"Content-Type": "application/x-www-form-urlencoded"}, body, 20)
    if code != 200:
        return None, "exchange failed: http %d" % code
    try:
        toks = json.loads(data)
    except ValueError:
        return None, "exchange failed: bad shape"
    if not toks.get("access_token"):
        return None, "exchange failed: no access_token"
    save_tokens(name, "codex", toks["access_token"], toks.get("refresh_token", ""),
                toks.get("expires_in", 3600))
    return {"ok": True}, None
