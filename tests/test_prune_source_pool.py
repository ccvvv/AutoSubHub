import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.prune_candidate_pool import prune_and_refresh
from scripts.source_snapshots import node_fingerprint


class SourcePoolReceiptTests(unittest.TestCase):
    def test_receipt_completes_nodes_without_deleting_source_pool(self):
        a = {"name": "a", "type": "ss", "server": "one.example", "port": 443}
        b = {"name": "b", "type": "ss", "server": "two.example", "port": 443}
        proxies = [a, b]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "candidate"
            root.mkdir()
            pool_path = root / "pool.yaml"
            manifest_path = root / "manifest.json"
            receipt_path = root / "receipt.json"
            state_path = root / "probe-state.json"
            pool_manifest_path = root / "pool-manifest.json"

            pool_path.write_text(yaml.safe_dump({"proxies": proxies}, sort_keys=False), encoding="utf-8")
            fingerprints = [node_fingerprint(p) for p in proxies]
            pool_manifest_path.write_text(json.dumps({"nodes": [
                {"fingerprint": fp, "node_id": fp[:20], "name": p["name"], "sources": ["source-001"]}
                for p, fp in zip(proxies, fingerprints)
            ]}), encoding="utf-8")
            state_path.write_text(json.dumps({"schema_version": 1, "nodes": {
                fp: {"status": "pending"} for fp in fingerprints
            }}), encoding="utf-8")
            candidate = {"proxies": proxies}
            candidate_bytes = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False).encode()
            manifest = {
                "schema_version": 1,
                "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                "nodes": [
                    {"node_id": fp[:20], "fingerprint": fp, "name": p["name"], "sources": ["source-001"]}
                    for p, fp in zip(proxies, fingerprints)
                ],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            receipt_path.write_text(json.dumps({"task_id": "receipt-1", "results": [
                {"node_id": fingerprints[0][:20], "status": "passed"},
                {"node_id": fingerprints[1][:20], "status": "failed"},
            ]}), encoding="utf-8")

            prune_and_refresh(pool_path, manifest_path, receipt_path)

            pool_after = yaml.safe_load(pool_path.read_text(encoding="utf-8"))
            queue_after = yaml.safe_load((root / "subscription.yaml").read_text(encoding="utf-8"))
            queue_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            state_after = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(pool_after["proxies"], proxies)
            self.assertEqual(queue_after["proxies"], [])
            self.assertEqual(queue_manifest["nodes"], [])
            self.assertEqual(queue_manifest["candidate_sha256"],
                             hashlib.sha256((root / "subscription.yaml").read_bytes()).hexdigest())
            self.assertEqual({state_after["nodes"][fp]["result"] for fp in fingerprints},
                             {"passed", "failed"})
            self.assertTrue(all(state_after["nodes"][fp]["status"] == "completed"
                                for fp in fingerprints))


if __name__ == "__main__":
    unittest.main()
