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


def pick(targets, model):
    """first non-cooled target serving model, else first non-cooled, else None."""
    fresh = [t for t in targets if not is_cool(t.get("name", ""))]
    for t in fresh:
        if model in t.get("models", []):
            return t
    return fresh[0] if fresh else None


def chain(targets, model):
    """ordered fallback list: model-match first, then rest, skipping cooled."""
    first = pick(targets, model)
    rest = [t for t in targets if t is not first and not is_cool(t.get("name", ""))]
    return ([first] if first else []) + rest
