import unittest

from download_candidates import require_testable_candidates


class DownloadCandidateGuardTests(unittest.TestCase):
    def test_rejects_empty_queue_before_cache_replacement(self):
        for candidate in ({}, {"proxies": []}, {"proxies": None}):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(SystemExit, "no testable nodes"):
                    require_testable_candidates(candidate)

    def test_accepts_nonempty_queue(self):
        proxies = [{"name": "node-a"}]
        self.assertIs(require_testable_candidates({"proxies": proxies}), proxies)


if __name__ == "__main__":
    unittest.main()
