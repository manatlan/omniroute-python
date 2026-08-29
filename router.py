"""daisy-chain pick + per-key cooldown. stdlib only."""
import time

_cool_until = {}  # ponytail: in-memory only, sqlite persistence when eviction proves needed


def cool(name, secs):
    _cool_until[name] = time.time() + secs


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
