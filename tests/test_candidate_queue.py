import unittest

from scripts.candidate_queue import merge_candidate_snapshots, canonical_fingerprint


class CandidateQueueTests(unittest.TestCase):
    def test_merges_batches_and_deduplicates_by_config_not_name(self):
        first = {"proxies": [
            {"name": "node-a", "type": "ss", "server": "one.example", "port": 443},
            {"name": "node-b", "type": "ss", "server": "two.example", "port": 443},
        ]}
        second = {"proxies": [
            {"name": "renamed-a", "type": "ss", "server": "one.example", "port": 443},
            {"name": "node-c", "type": "ss", "server": "three.example", "port": 443},
        ]}
        def manifest(candidate):
            return {"nodes": [
                {"name": p["name"], "node_id": canonical_fingerprint(p),
                 "fingerprint": canonical_fingerprint(p)}
                for p in candidate["proxies"]
            ]}
        candidate, metadata, commits = merge_candidate_snapshots([
            ("a" * 40, first, manifest(first)),
            ("b" * 40, second, manifest(second)),
        ])
        self.assertEqual([p["name"] for p in candidate["proxies"]], ["node-a", "node-b", "node-c"])
        self.assertEqual(commits, ["a" * 40, "b" * 40])
        self.assertEqual(len(metadata["nodes"]), 3)
        self.assertTrue(all(len(item["node_id"]) == 64 for item in metadata["nodes"]))
        self.assertTrue(all(item["node_id"] == item["fingerprint"] for item in metadata["nodes"]))
        self.assertEqual(metadata["nodes"][0]["source_commits"], ["a" * 40, "b" * 40])
        self.assertEqual(metadata["nodes"][0]["source_names"], ["node-a", "renamed-a"])

    def test_rejects_mismatched_manifest(self):
        with self.assertRaises(ValueError):
            merge_candidate_snapshots([
                ("a" * 40, {"proxies": [{"name": "x", "server": "a"}]},
                 {"nodes": [{"name": "not-x"}]}),
            ])

    def test_normalizes_historical_short_id_to_full_fingerprint(self):
        proxy = {"name": "x", "type": "ss", "server": "a.example", "port": 443}
        fingerprint = canonical_fingerprint(proxy)
        candidate, manifest, _ = merge_candidate_snapshots([
            ("b" * 40, {"proxies": [proxy]},
             {"nodes": [{"name": "x", "node_id": fingerprint[:20]}]}),
        ])
        self.assertEqual(manifest["nodes"][0]["node_id"], fingerprint)
        self.assertEqual(manifest["nodes"][0]["fingerprint"], fingerprint)

    def test_rejects_forged_short_or_full_manifest_identity(self):
        proxy = {"name": "x", "type": "ss", "server": "a.example", "port": 443}
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            merge_candidate_snapshots([
                ("c" * 40, {"proxies": [proxy]},
                 {"nodes": [{"name": "x", "node_id": "0" * 20}]}),
            ])


if __name__ == "__main__":
    unittest.main()
