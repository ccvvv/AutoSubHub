import json
import tempfile
import unittest
from pathlib import Path

from build import parse_source
from scripts.source_cache import (
    conditional_headers,
    empty_cache,
    load_cache,
    record_response,
    save_cache,
    source_key,
)


class SourceCacheTests(unittest.TestCase):
    def test_url_is_hashed_and_never_stored(self):
        url = "https://example.test/sub?token=secret"
        cache = empty_cache()
        record_response(cache, url, 200, {"ETag": '"v1"'})
        encoded = json.dumps(cache)
        self.assertNotIn(url, encoded)
        self.assertNotIn("secret", encoded)
        self.assertIn(source_key(url), cache["sources"])

    def test_conditional_headers_from_persisted_cache(self):
        url = "https://example.test/sub"
        cache = empty_cache()
        record_response(cache, url, 200, {
            "ETag": '"v1"',
            "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT",
        })
        self.assertEqual(conditional_headers(cache, url), {
            "If-None-Match": '"v1"',
            "If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT",
        })

    def test_304_and_errors_do_not_change_validators(self):
        url = "https://example.test/sub"
        cache = empty_cache()
        record_response(cache, url, 200, {"ETag": '"v1"'})
        before = json.dumps(cache, sort_keys=True)
        self.assertFalse(record_response(cache, url, 304, {"ETag": '"v2"'}))
        self.assertFalse(record_response(cache, url, 503, {"ETag": '"v3"'}))
        self.assertEqual(json.dumps(cache, sort_keys=True), before)

    def test_200_without_validators_clears_old_validators(self):
        url = "https://example.test/sub"
        cache = empty_cache()
        record_response(cache, url, 200, {"ETag": '"v1"'})
        self.assertTrue(record_response(cache, url, 200, {}))
        self.assertEqual(conditional_headers(cache, url), {})

    def test_cache_survives_disk_round_trip(self):
        url = "https://example.test/sub"
        cache = empty_cache()
        record_response(cache, url, 200, {"ETag": '"v1"'})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source-cache.json"
            save_cache(path, cache)
            loaded = load_cache(path)
        self.assertEqual(loaded, cache)

    def test_invalid_cache_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source-cache.json"
            path.write_text('{"schema_version":99,"sources":{}}')
            with self.assertRaises(ValueError):
                load_cache(path)


if __name__ == "__main__":
    unittest.main()

class SourcePayloadValidationTests(unittest.TestCase):
    def test_explicit_empty_clash_feed_is_valid(self):
        nodes, rejected = parse_source("proxies: []\\n")
        self.assertEqual(nodes, [])
        self.assertEqual(rejected, 0)

    def test_unrecognized_nonempty_response_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_source("<html>upstream error</html>")

    def test_comment_only_feed_is_valid_empty(self):
        nodes, rejected = parse_source("# temporarily no nodes\\n")
        self.assertEqual(nodes, [])
        self.assertEqual(rejected, 0)

