import unittest
import router


class T(unittest.TestCase):
    def setUp(self):
        router._cool_until.clear()

    def test_pick_model_match(self):
        ts = [{"name": "a", "models": ["x"]}, {"name": "b", "models": ["y"]}]
        self.assertEqual(router.pick(ts, "y")["name"], "b")

    def test_skips_cooled(self):
        ts = [{"name": "a", "models": ["x"]}, {"name": "b", "models": ["x"]}]
        router.cool("a", 60)
        self.assertEqual(router.pick(ts, "x")["name"], "b")

    def test_chain_order(self):
        ts = [{"name": "a", "models": ["x"]}, {"name": "b", "models": ["z"]}]
        self.assertEqual([t["name"] for t in router.chain(ts, "z")], ["b", "a"])

    def test_none_when_all_cool(self):
        ts = [{"name": "a", "models": ["x"]}]
        router.cool("a", 60)
        self.assertIsNone(router.pick(ts, "x"))


if __name__ == "__main__":
    unittest.main()


class TBackoff(unittest.TestCase):
    def setUp(self):
        router._cool_until.clear(); router._fails.clear()

    def test_backoff_doubles(self):
        router.cool("a"); first = router._cool_until["a"]
        router._cool_until.clear()
        router.cool("a"); second = router._cool_until["a"]
        import time
        self.assertAlmostEqual(second - time.time(), 2 * (first - time.time()), delta=2)

    def test_retry_after_wins(self):
        router.cool("a", base=5, retry_after=60)
        import time
        self.assertGreater(router._cool_until["a"] - time.time(), 50)

    def test_success_resets(self):
        router.cool("a"); router.note_success("a")
        self.assertFalse(router.is_cool("a"))
        self.assertNotIn("a", router._fails)

    def test_parse_delta(self):
        self.assertEqual(router.parse_retry_after({"retry-after": "120"}), 120)
        self.assertIsNone(router.parse_retry_after({}))
        self.assertIsNone(router.parse_retry_after({"retry-after": "garbage-date!!"}))
