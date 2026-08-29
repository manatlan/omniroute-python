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
