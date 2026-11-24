import unittest

from scripts.streaming_checker import _classify_response, summarize_streaming


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

    def test_disney_redirect_is_detected(self):
        self.assertEqual(_classify_response("disney_plus", 302, b"")["status"], "yes")

    def test_bilibili_nonzero_api_code_is_partial_not_no(self):
        result = _classify_response("bilibili", 200, b'{"code":-101}')
        self.assertEqual(result["status"], "partial")

    def test_bilibili_code_zero_passes(self):
        result = _classify_response("bilibili", 200, b'{"code":0}')
        self.assertEqual(result["status"], "yes")


if __name__ == "__main__":
    unittest.main()
