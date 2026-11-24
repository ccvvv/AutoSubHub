import unittest

from build import valid_reality_short_id


class RealityShortIdValidationTests(unittest.TestCase):
    def test_missing_reality_options_is_valid(self):
        self.assertTrue(valid_reality_short_id({"type": "vless"}))

    def test_empty_short_id_is_valid(self):
        self.assertTrue(valid_reality_short_id({"reality-opts": {"public-key": "abc", "short-id": ""}}))

    def test_valid_even_hex_short_ids_are_valid(self):
        for value in ("ab", "aB09", "0123456789abcdef"):
            with self.subTest(value=value):
                self.assertTrue(valid_reality_short_id({"reality-opts": {"short-id": value}}))

    def test_odd_length_or_non_hex_short_ids_are_invalid(self):
        for value in ("a", "abc", "gh", "0123456789abcdef0"):
            with self.subTest(value=value):
                self.assertFalse(valid_reality_short_id({"reality-opts": {"short-id": value}}))

    def test_non_string_short_id_is_invalid(self):
        self.assertFalse(valid_reality_short_id({"reality-opts": {"short-id": 123}}))


if __name__ == "__main__":
    unittest.main()
