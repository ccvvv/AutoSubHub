import unittest

from scripts.candidate_queue import merge_candidate_snapshots


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
            return {"nodes": [{"name": p["name"]} for p in candidate["proxies"]]}
        candidate, metadata, commits = merge_candidate_snapshots([
            ("a" * 40, first, manifest(first)),
            ("b" * 40, second, manifest(second)),
        ])
        self.assertEqual([p["name"] for p in candidate["proxies"]], ["node-a", "node-b", "node-c"])
        self.assertEqual(commits, ["a" * 40, "b" * 40])
        self.assertEqual(len(metadata["nodes"]), 3)
        self.assertEqual(metadata["nodes"][0]["source_commits"], ["a" * 40, "b" * 40])
        self.assertEqual(metadata["nodes"][0]["source_names"], ["node-a", "renamed-a"])

    def test_rejects_mismatched_manifest(self):
        with self.assertRaises(ValueError):
            merge_candidate_snapshots([
                ("a" * 40, {"proxies": [{"name": "x", "server": "a"}]},
                 {"nodes": [{"name": "not-x"}]}),
            ])


if __name__ == "__main__":
    unittest.main()
