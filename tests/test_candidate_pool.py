import unittest
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml
from unittest.mock import patch, Mock

from scripts.candidate_pool import build_manifest, merge_pool, node_id, remove_tested_nodes
from scripts.source_snapshots import node_fingerprint
from scripts.update_candidate_pool import historical_pool
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
        self.assertTrue(all(len(n["node_id"]) == 20 for n in manifest["nodes"]))
        self.assertTrue(all(set(n["node_id"]) <= set("0123456789abcdef") for n in manifest["nodes"]))

    def test_receipt_removes_only_tested_ids_and_preserves_new_nodes(self):
        pool = {"proxies": [self.a, self.b]}
        result = remove_tested_nodes(pool, {node_id(self.a)})
        self.assertEqual(result["proxies"], [self.b])

    def test_unknown_receipt_id_fails_closed(self):
        with self.assertRaises(ValueError):
            remove_tested_nodes({"proxies": [self.a]}, {"f" * 20})

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
            manifest_path.write_text(json.dumps(manifest))
            receipt_path.write_text(json.dumps({"task_id": "legacy-receipt", "results": [
                {"node_id": node_id(self.a), "status": "passed"},
                {"node_id": node_id(self.b), "status": "failed"},
            ]}))

            prune_and_refresh(pool_path, manifest_path, receipt_path)

            # Receipt processing changes only probe state and the pending queue.
            pool_after = yaml.safe_load(pool_path.read_text())
            self.assertEqual(pool_after["proxies"], [self.a, self.b, c])
            snapshot = yaml.safe_load((root / "subscription.yaml").read_text())
            self.assertEqual(snapshot["proxies"], [c])
            refreshed = json.loads((root / "manifest.json").read_text())
            self.assertEqual(refreshed["candidate_sha256"],
                             hashlib.sha256((root / "subscription.yaml").read_bytes()).hexdigest())
            self.assertEqual([n["node_id"] for n in refreshed["nodes"]], [node_id(c)])
            state = json.loads((root / "probe-state.json").read_text())
            self.assertEqual(state["nodes"][node_fingerprint(self.a)]["result"], "passed")
            self.assertEqual(state["nodes"][node_fingerprint(self.b)]["result"], "failed")

    @patch("scripts.update_candidate_pool.subprocess.run")
    def test_first_run_rebuilds_history_oldest_to_newest_and_deduplicates(self, run):
        log = Mock(stdout="new\nold\n")
        old = Mock(stdout=b"proxies:\n- name: a\n  type: ss\n  server: one.example\n  port: 443\n")
        new = Mock(stdout=b"proxies:\n- name: renamed\n  type: ss\n  server: one.example\n  port: 443\n- name: b\n  type: ss\n  server: two.example\n  port: 443\n")
        run.side_effect = [log, old, new]
        self.assertEqual(historical_pool()["proxies"], [self.a, self.b])
        self.assertEqual(run.call_args_list[1].args[0][2], "old:candidate/subscription.yaml")
        self.assertEqual(run.call_args_list[2].args[0][2], "new:candidate/subscription.yaml")

    @patch("scripts.update_candidate_pool.subprocess.run")
    def test_corrupt_historical_snapshot_aborts_migration(self, run):
        run.side_effect = [Mock(stdout="bad\n"), Mock(stdout=b"not: [valid")]
        with self.assertRaises(Exception):
            historical_pool()

    def test_shallow_data_clone_fetches_all_history_for_first_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = root / "data.git"
            work = root / "work"
            clone = root / "shallow"
            subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
            subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(work), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(work), "config", "user.email", "test@example.invalid"], check=True)
            candidate = work / "candidate"
            candidate.mkdir()
            (candidate / "subscription.yaml").write_text(
                yaml.safe_dump({"proxies": [self.a]}, sort_keys=False), encoding="utf-8")
            subprocess.run(["git", "-C", str(work), "add", "."], check=True)
            subprocess.run(["git", "-C", str(work), "commit", "-m", "old snapshot"], check=True, capture_output=True)
            (candidate / "subscription.yaml").write_text(
                yaml.safe_dump({"proxies": [self.a_renamed, self.b]}, sort_keys=False), encoding="utf-8")
            subprocess.run(["git", "-C", str(work), "add", "."], check=True)
            subprocess.run(["git", "-C", str(work), "commit", "-m", "new snapshot"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(work), "branch", "-M", "published-subscriptions"], check=True)
            subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(bare)], check=True)
            subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "published-subscriptions"],
                           check=True, capture_output=True)

            subprocess.run(["git", "clone", "--depth", "1", "--branch", "published-subscriptions",
                            f"file://{bare}", str(clone)], check=True, capture_output=True)
            shallow = subprocess.run(["git", "-C", str(clone), "rev-parse", "--is-shallow-repository"],
                                     check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(shallow, "true")
            subprocess.run(["git", "-C", str(clone), "fetch", "--unshallow", "origin",
                            "published-subscriptions"], check=True, capture_output=True)

            previous = os.getcwd()
            try:
                os.chdir(clone)
                self.assertEqual(historical_pool()["proxies"], [self.a, self.b])
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
