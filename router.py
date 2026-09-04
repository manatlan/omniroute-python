"""daisy-chain pick + per-key cooldown with backoff. stdlib only."""
import time

_cool_until = {}  # ponytail: in-memory only, sqlite persistence when eviction proves needed
_fails = {}
MAX_COOL = 300


def parse_retry_after(headers):
    """Retry-After secs from response headers (delta or HTTP-date). None if absent."""
    v = (headers or {}).get("retry-after")
    if v is None:
        return None
    v = v.strip()
    if v.isdigit():
        return int(v)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(v)
        return max(0, int(dt.timestamp() - time.time()))
    except (ValueError, TypeError, OverflowError):
        return None


def cool(name, base=5, retry_after=None):
    """exponential backoff per key, honoring upstream Retry-After hint."""
    n = _fails.get(name, 0) + 1
    _fails[name] = n
    secs = base * 2 ** (n - 1)
    if retry_after:
        secs = max(secs, retry_after)
    _cool_until[name] = time.time() + min(secs, MAX_COOL)


def note_success(name):
    _fails.pop(name, None)
    _cool_until.pop(name, None)


def is_cool(name):
    return _cool_until.get(name, 0) > time.time()


FAILOVER = (401, 403, 404, 408, 429, 500, 502, 503, 504)
BRK_THRESHOLD = 5
BRK_RESET = 30

_brk = {}    # provider -> [fails, opened_until]; lazy recovery, no timers
_mlock = {}  # (name, model) -> until


def _reset():
    _cool_until.clear(); _fails.clear(); _brk.clear(); _mlock.clear()


def _prov(t):
    return t.get("provider", t.get("name", ""))


def breaker_open(t):
    fails, until = _brk.get(_prov(t), (0, 0))
    if until > time.time():
        return True
    if until:
        _brk[_prov(t)] = (fails, 0)  # expired -> half-open probe allowed
    return False


def note_provider_fail(t):
    """-> True when this failure newly opened the breaker."""
    p = _prov(t)
    fails, _ = _brk.get(p, (0, 0))
    fails += 1
    opened = fails == BRK_THRESHOLD
    _brk[p] = (fails, time.time() + BRK_RESET if fails >= BRK_THRESHOLD else 0)
    return opened


def note_provider_ok(t):
    _brk.pop(_prov(t), None)


def lock_model(name, model, secs=60):
    _mlock[(name, model)] = time.time() + secs


def model_locked(name, model):
    until = _mlock.get((name, model), 0)
    if until > time.time():
        return True
    _mlock.pop((name, model), None)
    return False


def pick(targets, model):
    lst = chain(targets, model)
    return lst[0] if lst else None


def chain(targets, model):
    """model-match first (config order kept), skipping cooled keys, locked models, open breakers."""
    out = [t for t in targets
           if not is_cool(t.get("name", "")) and not breaker_open(t)
           and not model_locked(t.get("name", ""), model)]
    out.sort(key=lambda t: model not in t.get("models", []))
    return out


def classify(t, model, code, headers):
    """shared failover bookkeeping. -> provider name if breaker newly opened, else None."""
    name = t.get("name", "")
    if code in (408, 500, 502, 503, 504):
        opened = note_provider_fail(t)
        cool(name, 5, parse_retry_after(headers))
        return _prov(t) if opened else None
    elif code == 429:
        lock_model(name, model, 60)
        cool(name, 5, parse_retry_after(headers))
    elif code == 404:
        lock_model(name, model, 600)
    else:  # 401/403: key itself is bad, park it
        cool(name, 300)


def snapshot():
    """cooldowns/breakers/locks with seconds remaining. Powers the Quota tab."""
    now = time.time()
    return {
        "cooldowns": {k: int(v - now) for k, v in _cool_until.items() if v > now},
        "breakers": {p: {"fails": f, "open_s": int(u - now) if u > now else 0} for p, (f, u) in _brk.items()},
        "model_locks": {f"{n} / {m}": int(u - now) for (n, m), u in _mlock.items() if u > now},
    }
