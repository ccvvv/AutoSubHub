import unittest
import hashlib
import json
import tempfile
from pathlib import Path

import yaml

from scripts.candidate_pool import build_manifest, merge_pool, node_id, remove_tested_nodes
from scripts.source_snapshots import node_fingerprint
from scripts.prune_candidate_pool import prune_and_refresh


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
        self.assertTrue(all(len(n["node_id"]) == 64 for n in manifest["nodes"]))
        self.assertTrue(all(n["node_id"] == n["fingerprint"] for n in manifest["nodes"]))
        self.assertTrue(all(set(n["node_id"]) <= set("0123456789abcdef") for n in manifest["nodes"]))

    def test_receipt_removes_only_tested_ids_and_preserves_new_nodes(self):
        pool = {"proxies": [self.a, self.b]}
        result = remove_tested_nodes(pool, {node_id(self.a)})
        self.assertEqual(result["proxies"], [self.b])

    def test_unknown_receipt_id_fails_closed(self):
        with self.assertRaises(ValueError):
            remove_tested_nodes({"proxies": [self.a]}, {"f" * 64})

    def test_receipt_pruning_refreshes_phone_snapshot_and_manifest(self):
        c = {"name": "c", "type": "ss", "server": "three.example", "port": 443}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "candidate"
            root.mkdir()
            pool_path = root / "pool.yaml"
            manifest_path = root / "input-manifest.json"
            receipt_path = root / "receipt.json"
            initial = {"proxies": [self.a, self.b]}
            candidate_bytes = yaml.safe_dump(initial, allow_unicode=True, sort_keys=False).encode()
            manifest = build_manifest(initial, "2026-11-26T00:00:00Z",
                                      hashlib.sha256(candidate_bytes).hexdigest())
            pool_path.write_text(yaml.safe_dump({"proxies": [self.a, self.b, c]}, sort_keys=False))
            pool_nodes = []
            for proxy in [self.a, self.b, c]:
                fingerprint = node_fingerprint(proxy)
                pool_nodes.append({
                    "node_id": fingerprint, "fingerprint": fingerprint,
                    "name": proxy["name"], "sources": ["source-001"],
                })
            (root / "pool-manifest.json").write_text(json.dumps({
                "schema_version": 1, "generated_at": "2026-11-26T00:00:00Z",
                "nodes": pool_nodes,
            }))
            (root / "probe-state.json").write_text(json.dumps({
                "schema_version": 1,
                "nodes": {node["fingerprint"]: {"status": "pending"} for node in pool_nodes},
            }))
            manifest_path.write_text(json.dumps(manifest))
            receipt_path.write_text(json.dumps({"task_id": "full-fingerprint-receipt", "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(), "results": [
                {"node_id": node_id(self.a), "status": "passed"},
                {"node_id": node_id(self.b), "status": "failed"},
            ]}))

            prune_and_refresh(pool_path, manifest_path, receipt_path)

            # Receipt processing changes only probe state and the pending queue.
            pool_after = yaml.safe_load(pool_path.read_text())
            self.assertEqual(pool_after["proxies"], [self.a, c])
            snapshot = yaml.safe_load((root / "subscription.yaml").read_text())
            self.assertEqual(snapshot["proxies"], [c])
            refreshed = json.loads((root / "manifest.json").read_text())
            self.assertEqual(refreshed["candidate_sha256"],
                             hashlib.sha256((root / "subscription.yaml").read_bytes()).hexdigest())
            self.assertEqual([n["node_id"] for n in refreshed["nodes"]], [node_id(c)])
            state = json.loads((root / "probe-state.json").read_text())
            self.assertEqual(state["nodes"][node_fingerprint(self.a)]["result"], "passed")
            self.assertEqual(state["nodes"][node_fingerprint(self.b)]["result"], "failed")


if __name__ == "__main__":
    unittest.main()
