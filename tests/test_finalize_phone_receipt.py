#!/usr/bin/env python3
"""Integration tests for phone receipt validation and final Mihomo config output."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "finalize_phone_receipt.py"
MIHOMO = Path(os.environ.get("MIHOMO_BIN", ROOT / "mihomo"))


class FinalizePhoneReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.candidate = self.work / "candidate.yaml"
        self.manifest = self.work / "manifest.json"
        self.receipt = self.work / "receipt.json"
        self.output = self.work / "final.yaml"
        config = {"proxies": [
            {"name": "fast-node", "type": "ss", "server": "127.0.0.1", "port": 443, "cipher": "aes-128-gcm", "password": "test-password"},
            {"name": "slow-node", "type": "ss", "server": "127.0.0.1", "port": 444, "cipher": "aes-128-gcm", "password": "test-password"},
            {"name": "failed-node", "type": "ss", "server": "127.0.0.1", "port": 445, "cipher": "aes-128-gcm", "password": "test-password"},
        ]}
        self.candidate.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        digest = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        self.ids = {"fast-node": "id-fast", "slow-node": "id-slow", "failed-node": "id-failed"}
        self.manifest.write_text(json.dumps({
            "candidate_sha256": digest,
            "nodes": [{"node_id": nid, "name": name} for name, nid in self.ids.items()],
        }), encoding="utf-8")
        self.base_receipt = {
            "status": "completed", "candidate_commit": "a" * 40, "candidate_sha256": digest,
            "results": [
                {"node_id": "id-fast", "status": "passed", "download_speed_bps": 800000,
                 "application_checks": {app: {"status": "passed"} for app in ("youtube", "telegram", "google", "chatgpt", "github")}},
                {"node_id": "id-slow", "status": "passed", "download_speed_bps": 400000,
                 "application_checks": {app: {"status": "passed"} for app in ("youtube", "telegram", "google", "chatgpt", "github")}},
                {"node_id": "id-failed", "status": "failed", "download_speed_bps": 0,
                 "application_checks": {app: {"status": "passed"} for app in ("youtube", "telegram", "google", "chatgpt", "github")}},
            ],
        }

    def run_validator(self, receipt=None):
        self.receipt.write_text(json.dumps(receipt or self.base_receipt), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.candidate), str(self.manifest), str(self.receipt), str(self.output)],
            cwd=ROOT, text=True, capture_output=True,
        )

    def test_filters_below_threshold_and_emits_valid_full_config(self):
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        final = yaml.safe_load(self.output.read_text(encoding="utf-8"))
        self.assertEqual([p["name"] for p in final["proxies"]], ["fast-node [高速]", "slow-node [普通]"])
        groups = {g["name"]: g["proxies"] for g in final["proxy-groups"]}
        self.assertEqual(groups["高速节点"], ["fast-node [高速]"])
        self.assertEqual(groups["普通节点"], ["slow-node [普通]"])
        self.assertEqual(final["mixed-port"], 7890)
        self.assertIn("AUTO", groups)
        self.assertIn("手动选择", groups)
        self.assertEqual(groups["手动选择"][:3], ["AUTO", "高速节点", "普通节点"])
        self.assertEqual(final["rules"], ["MATCH,手动选择"])
        self.assertTrue(MIHOMO.exists(), f"Mihomo binary not found: {MIHOMO}")
        checked = subprocess.run([str(MIHOMO), "-t", "-f", str(self.output)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)

    def test_incomplete_receipt_is_rejected(self):
        receipt = dict(self.base_receipt)
        receipt["results"] = receipt["results"][:-1]
        result = self.run_validator(receipt)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete receipt", result.stderr + result.stdout)
        self.assertFalse(self.output.exists())

    def test_hash_mismatch_is_rejected(self):
        receipt = dict(self.base_receipt)
        receipt["candidate_sha256"] = "0" * 64
        result = self.run_validator(receipt)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash mismatch", (result.stderr + result.stdout).lower())
        self.assertFalse(self.output.exists())

    def test_failed_application_gate_excludes_node(self):
        receipt = dict(self.base_receipt)
        receipt["results"] = [dict(item) for item in self.base_receipt["results"]]
        receipt["results"][0]["application_checks"] = dict(receipt["results"][0]["application_checks"])
        receipt["results"][0]["application_checks"]["chatgpt"] = {"status": "failed"}
        result = self.run_validator(receipt)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        final = yaml.safe_load(self.output.read_text(encoding="utf-8"))
        self.assertEqual([p["name"] for p in final["proxies"]], ["slow-node [普通]"])

    def test_no_passing_nodes_is_rejected_without_output(self):
        receipt = dict(self.base_receipt)
        receipt["results"] = [
            {"node_id": "id-fast", "status": "passed", "download_speed_bps": 199999,
             "application_checks": {app: {"status": "passed"} for app in ("youtube", "telegram", "google", "chatgpt", "github")}},
            {"node_id": "id-slow", "status": "failed", "download_speed_bps": 0,
             "application_checks": {app: {"status": "passed"} for app in ("youtube", "telegram", "google", "chatgpt", "github")}},
            {"node_id": "id-failed", "status": "failed", "download_speed_bps": 0,
             "application_checks": {app: {"status": "passed"} for app in ("youtube", "telegram", "google", "chatgpt", "github")}},
        ]
        result = self.run_validator(receipt)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No phone-tested nodes", result.stderr + result.stdout)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
