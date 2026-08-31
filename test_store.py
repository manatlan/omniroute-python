import unittest
from store import load_targets, model_list


class T(unittest.TestCase):
    def test_empty_missing(self):
        self.assertEqual(load_targets("/nonexistent.toml"), [])

    def test_model_dedupe(self):
        ts = [{"models": ["a", "b"]}, {"models": ["b", "c"]}]
        self.assertEqual([m["id"] for m in model_list(ts)], ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()


class TCache(unittest.TestCase):
    def test_roundtrip_and_expiry(self):
        import tempfile, os
        from store import cache_get, cache_set
        p = os.path.join(tempfile.mkdtemp(), "c.db")
        cache_set(p, "k", b"v", ttl=60)
        self.assertEqual(cache_get(p, "k"), b"v")
        cache_set(p, "k", b"v", ttl=-1)
        self.assertIsNone(cache_get(p, "k"))
