import unittest

from scripts.candidate_pool import build_manifest, merge_pool, node_id, remove_tested_nodes


class CandidatePoolTests(unittest.TestCase):
    def setUp(self):
        self.a = {"name": "a", "type": "ss", "server": "one.example", "port": 443}
        self.a_renamed = {**self.a, "name": "renamed"}
        self.b = {"name": "b", "type": "ss", "server": "two.example", "port": 443}

    def test_merge_deduplicates_by_config_not_name(self):
        result = merge_pool({"proxies": [self.a]}, {"proxies": [self.a_renamed, self.b]})
        self.assertEqual(result["proxies"], [self.a, self.b])

    def test_manifest_matches_client_contract(self):
        candidate = {"proxies": [self.a, self.b]}
        manifest = build_manifest(candidate, "2026-11-26T00:00:00Z", "a" * 64)
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["candidate_sha256"], "a" * 64)
        self.assertTrue(all(len(n["node_id"]) == 20 for n in manifest["nodes"]))
        self.assertTrue(all(set(n["node_id"]) <= set("0123456789abcdef") for n in manifest["nodes"]))

    def test_receipt_removes_only_tested_ids_and_preserves_new_nodes(self):
        pool = {"proxies": [self.a, self.b]}
        result = remove_tested_nodes(pool, {node_id(self.a)})
        self.assertEqual(result["proxies"], [self.b])

    def test_unknown_receipt_id_fails_closed(self):
        with self.assertRaises(ValueError):
            remove_tested_nodes({"proxies": [self.a]}, {"f" * 20})


if __name__ == "__main__":
    unittest.main()
