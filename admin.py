"""dashboard backend: targets CRUD + probes. Delegated from lite.Handler."""
import urllib.parse

import presets
import providers
import router
import stats
import store
import webhooks

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
    if parts[0] == "quota" and len(parts) == 1:
        if method == "GET":
            h._send(200, {"stats": stats.snap(), "state": router.snapshot()})
            return True
        if method == "POST" and (h._body()[0] or {}).get("reset"):
            stats.reset()
            router._reset()
            h._send(200, {"stats": stats.snap(), "state": router.snapshot()})
            return True
    if parts[0] == "key" and len(parts) == 1 and method == "GET":
        h._send(200, {"key": store.load_server_key(), "base_url": "http://localhost:20128/v1"})
        return True
    if parts[0] == "key" and parts[1:] == ["rotate"] and method == "POST":
        h._send(200, {"key": store.save_server_key(), "base_url": "http://localhost:20128/v1"})
        return True
    if parts[0] == "restart" and len(parts) == 1 and method == "POST":
        import os as _o
        import sys as _s
        import threading as _t
        h._send(200, {"ok": True})
        me = _o.path.abspath(_o.path.join(_o.path.dirname(__file__), "lite.py"))
        _t.Timer(0.3, lambda: _o.execv(_s.executable, [_s.executable, me])).start()
        return True
    if parts[0] == "webhooks":
        if method == "GET" and len(parts) == 1:
            h._send(200, {"webhooks": webhooks.list_hooks(), "events": list(webhooks.EVENTS)})
            return True
        if method == "POST" and len(parts) == 1:
            body, err = h._body()
            if err:
                h._send(400, {"error": {"message": "bad json"}})
                return True
            hid, herr = webhooks.add_hook((body or {}).get("url"), (body or {}).get("events"))
            if herr:
                h._send(400, {"error": {"message": herr}})
                return True
            h._send(200, {"webhooks": webhooks.list_hooks()})
            return True
        if method == "DELETE" and len(parts) == 2:
            import urllib.parse as _up
            h._send(200, {"webhooks": webhooks.delete_hook(_up.unquote(parts[1]))})
            return True
        if method == "POST" and len(parts) == 3 and parts[2] == "test":
            import urllib.parse as _up
            hook = next((x for x in webhooks.list_hooks() if str(x.get("id")) == _up.unquote(parts[1])), None)
            if not hook:
                h._send(404, {"error": {"message": "unknown webhook"}})
                return True
            webhooks._fire(hook["url"], {"event": "test", "msg": "hello from omniroute-lite"})
            h._send(200, {"ok": True})
            return True
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
    return False
