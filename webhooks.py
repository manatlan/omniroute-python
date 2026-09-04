"""event webhooks: fire-and-forget POSTs. Never break requests."""
import json
import threading
import urllib.request

EVENTS = ("breaker_open", "all_failed")


def _path():
    import os
    d = os.path.join(os.path.expanduser("~"), ".omniroute-lite")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "webhooks.json")


def list_hooks():
    try:
        with open(_path()) as f:
            hooks = json.load(f)
        return hooks if isinstance(hooks, list) else []
    except (OSError, ValueError):
        return []


def add_hook(url, events):
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None, "url must be http(s)"
    events = [e for e in (events or []) if e in EVENTS]
    if not events:
        return None, "events must be a subset of " + ",".join(EVENTS)
    hooks = list_hooks()
    hid = str(max([int(h["id"]) for h in hooks if str(h.get("id", "0")).isdigit()] or [0]) + 1)
    hooks.append({"id": hid, "url": url, "events": events})
    with open(_path(), "w") as f:
        json.dump(hooks, f)
    return hid, None


def delete_hook(hid):
    hooks = [h for h in list_hooks() if str(h.get("id")) != str(hid)]
    with open(_path(), "w") as f:
        json.dump(hooks, f)
    return hooks


def _fire(url, payload):
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).read(1024)
    except Exception:
        pass  # ponytail: webhooks never break requests


def dispatch(event, data=None):
    for h in list_hooks():
        if event in h.get("events", []) or event == "test":
            threading.Thread(target=_fire, args=(h["url"], {"event": event, **(data or {})}), daemon=True).start()
