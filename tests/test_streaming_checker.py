import unittest

from scripts.streaming_checker import summarize_streaming


class StreamingVerdictTests(unittest.TestCase):
    def test_one_yes_is_enough(self):
        self.assertEqual(summarize_streaming({
            "netflix": {"status": "yes"},
            "spotify": {"status": "no"},
            "tiktok": {"status": "error"},
        }), "passed")

    def test_partial_and_error_do_not_pass(self):
        self.assertEqual(summarize_streaming({
            "youtube_premium": {"status": "partial"},
            "disney_plus": {"status": "error"},
            "bilibili": {"status": "no"},
        }), "failed")

    def test_all_errors_fail(self):
        self.assertEqual(summarize_streaming({
            "netflix": {"status": "error"},
            "spotify": {"status": "error"},
        }), "failed")

    def test_empty_checks_fail(self):
        self.assertEqual(summarize_streaming({}), "failed")


if __name__ == "__main__":
    unittest.main()
