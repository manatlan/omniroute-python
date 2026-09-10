"""keys.toml store with mtime hot-reload. stdlib only."""
import tomllib

_cache = {"mtime": 0, "targets": []}


def load_targets(path="keys.toml"):
    import os
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if mtime == _cache["mtime"]:
        return _cache["targets"]
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        targets = data.get("targets", [])
    except (ValueError, OSError):
        return []  # ponytail: mid-edit garbage serves empty, never 500s; next read retries
    _cache.update(mtime=mtime, targets=targets)
    return targets


def model_list(targets):
    seen = []
    for t in targets:
        for m in t.get("models", []):
            if m not in seen:
                seen.append(m)
    return [{"id": m, "object": "model"} for m in seen]


def _db(path):
    import sqlite3
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY, body BLOB, exp INTEGER)")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def cache_get(path, key):
    import sqlite3, time
    try:
        con = sqlite3.connect(path)
        row = con.execute("SELECT body, exp FROM cache WHERE k=?", (key,)).fetchone()
        con.close()
    except Exception:
        return None
    if row and row[1] > int(time.time()):
        return row[0]
    return None


def cache_set(path, key, body, ttl=300):
    import sqlite3, time, os
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        con = _db(path)
        con.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, body, int(time.time()) + ttl))
        con.commit(); con.close()
    except Exception:
        pass  # ponytail: cache never breaks requests


_key_cache = {"mtime": 0, "key": None}


def data_path(name):
    import os
    d = os.path.join(os.path.expanduser("~"), ".omniroute-lite")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def load_server_key():
    """env LITE_KEY wins; else key file; else None (open mode)."""
    import os
    env = os.environ.get("LITE_KEY")
    if env:
        return env
    p = os.path.join(os.path.expanduser("~"), ".omniroute-lite", "server.key")
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return None
    if mtime != _key_cache["mtime"]:
        with open(p) as f:
            key = f.read().strip()
        _key_cache.update(mtime=mtime, key=key or None)
    return _key_cache["key"]


def save_server_key(key=None):
    import os
    import secrets
    key = key or secrets.token_urlsafe(24)
    p = data_path("server.key")
    with open(p, "w") as f:
        f.write(key + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    _key_cache.update(mtime=os.path.getmtime(p), key=key)
    return key


def validate_target(t):
    if not isinstance(t, dict):
        return "target must be an object"
    if not t.get("name") or not t.get("base_url"):
        return "name and base_url required"
    if t.get("kind", "openai") not in ("openai", "ollama", "anthropic", "gemini", "azure"):
        return "unknown kind"
    if not isinstance(t.get("models", []), list):
        return "models must be a list"
    if "headers" in t and not isinstance(t["headers"], dict):
        return "headers must be an object"
    if "cookies" in t and not isinstance(t["cookies"], str):
        return "cookies must be a string"
    return None


def save_targets(path, targets):
    """write keys.toml from target dicts (known schema only)."""
    def q(s):
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = ["# managed by omniroute-lite dashboard"]
    for t in targets:
        lines.append("\n[[targets]]")
        lines.append(f"name = {q(t.get('name', ''))}")
        if t.get("kind", "openai") != "openai":
            lines.append(f"kind = {q(t['kind'])}")
        if t.get("provider"):
            lines.append(f"provider = {q(t['provider'])}")
        lines.append(f"base_url = {q(t.get('base_url', ''))}")
        lines.append(f"api_key = {q(t.get('api_key', ''))}")
        if t.get("cookies"):
            lines.append(f"cookies = {q(t['cookies'])}")
        if t.get("headers"):
            inner = ", ".join(f"{q(k)} = {q(v)}" for k, v in t["headers"].items())
            lines.append(f"headers = {{ {inner} }}")
        if t.get("api_version"):
            lines.append(f"api_version = {q(t['api_version'])}")
        lines.append(f"models = [{', '.join(q(m) for m in t.get('models', []))}]")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    import os as _o
    _o.replace(tmp, path)  # atomic: readers never see half a file
    _cache["mtime"] = 0  # force reload on next read


def load_chains():
    import json
    import os
    p = os.path.join(os.path.expanduser("~"), ".omniroute-lite", "chains.json")
    try:
        with open(p) as f:
            chains = json.load(f)
        return chains if isinstance(chains, list) else []
    except (OSError, ValueError):
        return []


def save_chains(chains):
    import json
    p = data_path("chains.json")
    with open(p, "w") as f:
        json.dump(chains, f)
    return chains


def validate_chain(c, known):
    if not isinstance(c, dict) or not c.get("name"):
        return "name required"
    members = c.get("members", [])
    if not isinstance(members, list) or not members:
        return "members must be a non-empty list of API names"
    unknown = [m for m in members if m not in known]
    if unknown:
        return "unknown APIs: " + ", ".join(unknown)
    return None
