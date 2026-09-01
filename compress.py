"""one-heuristic prompt trim: keep system + newest within budget."""


def trim(messages, budget=6000):
    """messages: [{role, content}]. ponytail: char-count proxy, tokenizer when proven needed."""
    sys = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    used = sum(len(m.get("content", "")) for m in sys)
    kept = []
    for m in reversed(rest):
        n = len(m.get("content", ""))
        if used + n > budget and kept:
            break
        used += n
        kept.append(m)
    return sys + list(reversed(kept))
