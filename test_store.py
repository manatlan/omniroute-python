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
