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


class TNone(unittest.TestCase):
    def test_none_content_never_crashes(self):
        msgs = [{"role": "user", "content": None},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
                {"role": "user", "content": "hi"}]
        out = trim(msgs)
        self.assertEqual(len(out), 3)
