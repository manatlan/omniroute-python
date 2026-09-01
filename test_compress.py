import unittest
from compress import trim


class T(unittest.TestCase):
    def test_keeps_system_and_newest(self):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "old" * 999}, {"role": "user", "content": "new"}]
        out = trim(msgs, budget=100)
        self.assertEqual(out[0]["content"], "s")
        self.assertEqual(out[-1]["content"], "new")

    def test_small_passthrough(self):
        msgs = [{"role": "user", "content": "hi"}]
        self.assertEqual(trim(msgs), msgs)


if __name__ == "__main__":
    unittest.main()
