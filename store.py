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
    with open(path, "rb") as f:
        data = tomllib.load(f)
    targets = data.get("targets", [])
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
