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
