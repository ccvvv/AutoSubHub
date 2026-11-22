#!/usr/bin/env python3
"""Tests for persistent node lifecycle metadata."""
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
        self.subscription = self.work / "subscription.yaml"
        self.manifest = self.work / "manifest.json"
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
        self.subscription.write_text(
            yaml.safe_dump({"proxies": [proxy]}, allow_unicode=True),
            encoding="utf-8",
        )
        self.manifest.write_text(json.dumps({
            "candidate_sha256": "a" * 64,
            "nodes": [{"node_id": "candidate-id", "name": name}],
        }), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.subscription),
             str(self.manifest), str(self.lifecycle)],
            cwd=ROOT, text=True, capture_output=True,
        )

    def test_first_seen_is_preserved_when_display_name_changes(self):
        first = self.run_update("node-A")
        self.assertEqual(first.returncode, 0, first.stderr)
        initial = json.loads(self.lifecycle.read_text(encoding="utf-8"))
        fingerprint, record = next(iter(initial["nodes"].items()))
        first_seen = record["first_seen_at"]

        second = self.run_update("renamed-node")
        self.assertEqual(second.returncode, 0, second.stderr)
        updated = json.loads(self.lifecycle.read_text(encoding="utf-8"))
        self.assertEqual(updated["nodes"][fingerprint]["first_seen_at"], first_seen)
        self.assertEqual(
            json.loads(self.manifest.read_text(encoding="utf-8"))["nodes"][0]["lifecycle_id"],
            fingerprint,
        )

    def test_secret_values_are_not_written_to_lifecycle_metadata(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        serialized = self.lifecycle.read_text(encoding="utf-8")
        self.assertNotIn("do-not-copy-into-metadata", serialized)
        self.assertNotIn("203.0.113.10", serialized)

    def test_malformed_manifest_does_not_overwrite_lifecycle(self):
        self.lifecycle.write_text(
            json.dumps({"schema_version": 1, "nodes": {"existing": {"first_seen_at": "2026-01-01T00:00:00Z"}}}),
            encoding="utf-8",
        )
        before = self.lifecycle.read_text(encoding="utf-8")
        self.subscription.write_text("proxies: []\n", encoding="utf-8")
        self.manifest.write_text(json.dumps({"nodes": []}), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.subscription),
             str(self.manifest), str(self.lifecycle)],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.lifecycle.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
