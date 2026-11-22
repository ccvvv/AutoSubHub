#!/usr/bin/env python3
"""Tests that lifecycle metadata is derived only from final subscriptions."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "update_lifecycle.py"


class UpdateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.final_subscription = self.work / "final.yaml"
        self.lifecycle = self.work / "lifecycle.json"

    def run_update(self, name="node-A"):
        proxy = {
            "name": name,
            "type": "ss",
            "server": "203.0.113.10",
            "port": 443,
            "cipher": "aes-256-gcm",
            "password": "do-not-copy-into-metadata",
        }
        self.final_subscription.write_text(
            yaml.safe_dump({"proxies": [proxy]}, allow_unicode=True),
            encoding="utf-8",
        )
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.final_subscription), str(self.lifecycle)],
            cwd=ROOT, text=True, capture_output=True,
        )

    def test_final_published_name_suffix_does_not_reset_first_seen(self):
        first = self.run_update("node-A [高速]")
        self.assertEqual(first.returncode, 0, first.stderr)
        initial = json.loads(self.lifecycle.read_text(encoding="utf-8"))
        fingerprint, record = next(iter(initial["nodes"].items()))
        first_seen = record["first_seen_at"]

        second = self.run_update("node-A [普通]")
        self.assertEqual(second.returncode, 0, second.stderr)
        updated = json.loads(self.lifecycle.read_text(encoding="utf-8"))
        self.assertEqual(updated["nodes"][fingerprint]["first_seen_at"], first_seen)
        self.assertEqual(updated["source"], "final/latest.yaml")
        self.assertEqual(updated["published_node_count"], 1)

    def test_secret_values_are_not_written_to_lifecycle_metadata(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        serialized = self.lifecycle.read_text(encoding="utf-8")
        self.assertNotIn("do-not-copy-into-metadata", serialized)
        self.assertNotIn("203.0.113.10", serialized)

    def test_empty_or_malformed_final_does_not_overwrite_lifecycle(self):
        self.lifecycle.write_text(
            json.dumps({"schema_version": 1, "nodes": {"existing": {"first_seen_at": "2026-01-01T00:00:00Z"}}}),
            encoding="utf-8",
        )
        before = self.lifecycle.read_text(encoding="utf-8")
        self.final_subscription.write_text("proxies: []\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.final_subscription), str(self.lifecycle)],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.lifecycle.read_text(encoding="utf-8"), before)

    def test_candidate_manifest_is_not_an_input(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "candidate.yaml", "manifest.json", str(self.lifecycle)],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.lifecycle.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
