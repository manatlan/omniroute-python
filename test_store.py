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


class TKey(unittest.TestCase):
    def test_env_wins_and_file_fallback(self):
        import os
        from store import load_server_key, save_server_key, _key_cache
        home = os.path.expanduser("~")
        o_exp, o_env = os.path.expanduser, os.environ.get("LITE_KEY")
        import tempfile
        tmp = tempfile.mkdtemp()
        os.path.expanduser = lambda p: tmp if p == "~" else o_exp(p)
        os.environ.pop("LITE_KEY", None)
        _key_cache.update(mtime=0, key=None)
        try:
            self.assertIsNone(load_server_key())
            k = save_server_key("abc123")
            self.assertEqual(k, "abc123")
            self.assertEqual(load_server_key(), "abc123")
            os.environ["LITE_KEY"] = "env-key"
            self.assertEqual(load_server_key(), "env-key")
            mode = oct(os.stat(os.path.join(tmp, ".omniroute-lite", "server.key")).st_mode & 0o777)
            self.assertEqual(mode, "0o600")
        finally:
            os.path.expanduser = o_exp
            _key_cache.update(mtime=0, key=None)
            if o_env is not None:
                os.environ["LITE_KEY"] = o_env
            else:
                os.environ.pop("LITE_KEY", None)


class TCorrupt(unittest.TestCase):
    def test_garbage_file_serves_empty_and_recovers(self):
        import os, tempfile
        from store import load_targets, _cache
        p = os.path.join(tempfile.mkdtemp(), "k.toml")
        with open(p, "w") as f:
            f.write("[[targets\n  broken = = =")
        _cache.update(mtime=0, targets=[])
        try:
            self.assertEqual(load_targets(p), [])
            with open(p, "w") as f:
                f.write('[[targets]]\nname = "a"\nbase_url = "https://x"\nmodels = ["m"]\n')
            # no restart needed: next read recovers
            got = load_targets(p)
            self.assertEqual(len(got), 1)
        finally:
            _cache.update(mtime=0, targets=[])
