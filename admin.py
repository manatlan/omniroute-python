"""dashboard backend: targets CRUD + probes. Delegated from lite.Handler."""
import urllib.parse

import catalog
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
        code, hdrs, body = providers.call_target(t, probe)
        ms = int((time.time() - start) * 1000)
        ok = code not in router.FAILOVER and providers.probe_ok(body)
        shaped = b"bad shape" in body
        reason = None if ok else ("bad shape" if shaped else "http " + str(code))
        if ok:
            router.note_success(name)
            router.note_provider_ok(t)
        else:
            router.classify(t, model, code, hdrs)
        out = {"ok": ok, "target": name, "status": code, "latency_ms": ms}
        if reason:
            out["reason"] = reason
        return 200, out
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
    parts = h.path.split("?", 1)[0][len("/admin/"):].split("/")
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
    if parts[0] == "chains":
        if method == "GET" and len(parts) == 1:
            h._send(200, {"chains": store.load_chains()})
            return True
        if method == "POST" and len(parts) == 1:
            body, err = h._body()
            if err:
                h._send(400, {"error": {"message": "bad json"}})
                return True
            chains = store.load_chains()
            c = (body or {}).get("chain")
            known = [t.get("name") for t in _list()]
            err = store.validate_chain(c, known) if c else "chain required"
            if err:
                h._send(400, {"error": {"message": err}})
                return True
            if any(x["name"] == c["name"] for x in chains):
                h._send(400, {"error": {"message": "name exists"}})
                return True
            chains.append({"name": c["name"], "members": c["members"]})
            h._send(200, {"chains": store.save_chains(chains)})
            return True
        if method == "DELETE" and len(parts) == 2:
            import urllib.parse as _up
            chains = [c for c in store.load_chains() if c["name"] != _up.unquote(parts[1])]
            h._send(200, {"chains": store.save_chains(chains)})
            return True
    if parts[0] == "logs" and method == "GET" and len(parts) == 1:
        import json as _j
        import os as _o
        import urllib.parse as _up
        qs = _up.urlparse(h.path).query
        try:
            limit = max(1, min(500, int(_up.parse_qs(qs).get("limit", ["100"])[0])))
        except ValueError:
            limit = 100
        p = _o.path.join(_o.environ.get("LITE_DATA_DIR") or _o.path.join(_o.path.expanduser("~"), ".omniroute-lite"), "requests.jsonl")
        lines = []
        try:
            with open(p, "rb") as f:
                lines = f.read().splitlines()[-limit:]
        except OSError:
            pass
        out = []
        for ln in reversed(lines):
            try:
                out.append(_j.loads(ln))
            except ValueError:
                continue
        h._send(200, {"logs": out})
        return True
    if parts[0] == "discover" and method == "POST" and len(parts) == 1:
        body, err = h._body()
        if err:
            h._send(400, {"error": {"message": "bad json"}})
            return True
        b = body or {}
        ids, derr = providers.discover_models(b.get("kind", "openai"), b.get("base_url", ""), b.get("api_key", "-"))
        if derr:
            h._send(502, {"error": {"message": derr}})
            return True
        h._send(200, {"models": ids})
        return True
    if parts[0] == "models" and method == "GET" and len(parts) == 1:
        h._send(200, {"models": configured_model_map()})
        return True
    if parts[0] == "presets" and method == "GET" and len(parts) == 1:
        h._send(200, {"presets": catalog_presets(), "generated": catalog.GENERATED})
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


AUTH_NOTE = {
    "cookie": "Needs a browser cookie — open the site, copy it, paste as the key.",
    "oauth": "Needs app login — not routable here yet.",
    "none": "No key needed.",
}


def catalog_presets():
    out = []
    for p in catalog.CATALOG:
        note = AUTH_NOTE.get(p["auth"], "")
        if not p["kind"] and not note:
            note = "Special format — not routable here yet."
        http = p["base"].startswith(("https://", "http://"))
        out.append({"provider": p["id"], "kind": p["kind"] if http else None, "base_url": p["base"],
                    "auth": p["auth"], "free": p["free"], "tos": p["tos"], "note": note,
                    "lite": http and bool(p["kind"]) and p["auth"] in ("key", "x-api-key", "api-key", "cookie", "none"),
                    "models": [{"id": m["id"], "name": m["name"], "free": m["free"]} for m in p["models"]]})
    return out


_free_idx = None


def configured_model_map():
    """configured models -> {id, free (per main catalog), targets [names]}."""
    global _free_idx
    if _free_idx is None:
        _free_idx = {m["id"] for p in catalog.CATALOG for m in p["models"] if m["free"]}
    out = {}
    for t in _list():
        for m in t.get("models", []):
            e = out.setdefault(m, {"id": m, "free": m in _free_idx, "targets": []})
            e["targets"].append(t.get("name", ""))
    return sorted(out.values(), key=lambda e: e["id"])
