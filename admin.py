"""dashboard backend: targets CRUD + probes. Delegated from lite.Handler."""
import urllib.parse

import presets
import providers
import router
import store

TARGETS_PATH = "keys.toml"


def _list():
    return store.load_targets(TARGETS_PATH)


def _save(targets):
    store.save_targets(TARGETS_PATH, targets)


def targets_crud(method, parts, body):
    """parts after /admin/targets. -> (code, obj)."""
    targets = _list()
    if method == "GET" and not parts:
        return 200, {"targets": targets}
    if method == "POST" and not parts:
        t = (body or {}).get("target")
        err = store.validate_target(t) if t else "target required"
        if err:
            return 400, {"error": {"message": err}}
        if any(x.get("name") == t["name"] for x in targets):
            return 400, {"error": {"message": "name exists"}}
        targets.append(t)
        _save(targets)
        return 200, {"targets": targets}
    if len(parts) == 2 and parts[1] == "test" and method == "POST":
        name = urllib.parse.unquote(parts[0])
        t = next((x for x in targets if x.get("name") == name), None)
        if not t:
            return 404, {"error": {"message": "unknown target"}}
        import time
        model = (body or {}).get("model") or (t.get("models") or [""])[0]
        probe = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
        start = time.time()
        code, hdrs, _ = providers.call_target(t, probe)
        ms = int((time.time() - start) * 1000)
        ok = code not in router.FAILOVER
        if ok:
            router.note_success(name)
            router.note_provider_ok(t)
        else:
            router.classify(t, model, code, hdrs)
        return 200, {"ok": ok, "target": name, "status": code, "latency_ms": ms}
    if len(parts) == 1 and method in ("PUT", "DELETE"):
        name = urllib.parse.unquote(parts[0])
        i = next((i for i, x in enumerate(targets) if x.get("name") == name), None)
        if i is None:
            return 404, {"error": {"message": "unknown target"}}
        if method == "DELETE":
            del targets[i]
            _save(targets)
            return 200, {"targets": targets}
        t = (body or {}).get("target")
        err = store.validate_target(t) if t else "target required"
        if err:
            return 400, {"error": {"message": err}}
        t["name"] = name
        targets[i] = t
        _save(targets)
        return 200, {"targets": targets}
    return 404, {"error": {"message": "not found"}}


def handle(h, method):
    """-> True if an /admin/* path was handled (auth enforced)."""
    if not h.path.startswith("/admin/"):
        return False
    if not h._authed():
        h._deny()
        return True
    parts = h.path[len("/admin/"):].split("/")
    if parts[0] == "presets" and method == "GET" and len(parts) == 1:
        h._send(200, {"presets": presets.PRESETS})
        return True
    if parts[0] == "targets":
        if method in ("GET", "POST", "PUT", "DELETE"):
            body, err = (h._body() if method in ("POST", "PUT") else ({}, None))
            if err:
                h._send(400, {"error": {"message": "bad json"}})
                return True
            code, obj = targets_crud(method, parts[1:], body)
            h._send(code, obj)
            return True
    h._send(404, {"error": {"message": "not found"}})
    return True
