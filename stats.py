"""process-local counters. ponytail: no sqlite rollup until multi-proc needed."""
import threading

_lock = threading.Lock()
_c = {"req": 0, "ok": 0, "err": 0, "lat_ms": 0, "by_target": {}}


def note(target, ok, ms):
    with _lock:
        _c["req"] += 1
        _c["ok" if ok else "err"] += 1
        _c["lat_ms"] += ms
        b = _c["by_target"].setdefault(target or "?", [0, 0])
        b[0 if ok else 1] += 1


def snap():
    with _lock:
        return {"req": _c["req"], "ok": _c["ok"], "err": _c["err"],
                "avg_ms": round(_c["lat_ms"] / _c["req"]) if _c["req"] else 0,
                "by_target": {k: {"ok": v[0], "err": v[1]} for k, v in _c["by_target"].items()}}


def reset():
    with _lock:
        _c.update(req=0, ok=0, err=0, lat_ms=0)
        _c["by_target"].clear()
