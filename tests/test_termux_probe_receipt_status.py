import unittest

from termux_probe import receipt_completion_status


class ReceiptCompletionStatusTests(unittest.TestCase):
    def test_full_uninterrupted_queue_is_completed(self):
        self.assertEqual(receipt_completion_status(False, 500, 500), "completed")

    def test_limited_queue_run_is_partial(self):
        self.assertEqual(receipt_completion_status(False, 300, 500), "partial")

    def test_stopped_queue_run_is_interrupted(self):
        self.assertEqual(receipt_completion_status(True, 300, 500), "interrupted")

    def test_invalid_candidate_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            receipt_completion_status(False, 501, 500)


if __name__ == "__main__":
    unittest.main()
