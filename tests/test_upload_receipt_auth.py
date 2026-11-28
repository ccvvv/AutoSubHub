import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import upload_receipt
from scripts.node_identity import node_fingerprint
import yaml


class ReceiptBundleCompletenessTests(unittest.TestCase):
    def test_receipt_uploader_defaults_to_the_unified_data_branch(self):
        self.assertEqual(upload_receipt.DEFAULT_BRANCH, "published-subscriptions")

    def setUp(self):
        self.candidate = b"""proxies:
  - name: node-a
    type: ss
    server: a.example
    port: 443
  - name: node-b
    type: ss
    server: b.example
    port: 443
"""
        proxies = yaml.safe_load(self.candidate)["proxies"]
        fingerprints = [node_fingerprint(proxy) for proxy in proxies]
        self.manifest = {"nodes": [
            {"node_id": fp, "fingerprint": fp, "name": proxy["name"]}
            for proxy, fp in zip(proxies, fingerprints)
        ]}
        self.receipt = {"results": [
            {"node_id": fingerprints[0], "status": "passed"},
            {"node_id": fingerprints[1], "status": "failed"},
        ]}

    def test_accepts_exact_complete_receipt(self):
        upload_receipt.validate_complete_receipt_bundle(
            self.receipt, self.candidate, self.manifest
        )

    def test_rejects_partial_receipt_before_cursor_advance(self):
        partial = {"results": [{"node_id": self.manifest["nodes"][0]["node_id"], "status": "passed"}]}
        with self.assertRaisesRegex(ValueError, "missing=1"):
            upload_receipt.validate_complete_receipt_bundle(
                partial, self.candidate, self.manifest
            )

    def test_rejects_limited_queue_receipt_even_if_marked_completed(self):
        receipt = {
            "schema_version": 1,
            "status": "completed",
            "candidate_sha256": "a" * 64,
            "candidate_commit": "b" * 40,
            "queue_candidate_count": 500,
            "receipt_candidate_count": 300,
            "results": [{"node_id": "c" * 64, "status": "passed"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "part of the cumulative queue"):
                upload_receipt.validate_receipt(path)

    def test_rejects_receipt_result_count_mismatch(self):
        receipt = {
            "schema_version": 1,
            "status": "completed",
            "candidate_sha256": "a" * 64,
            "candidate_commit": "b" * 40,
            "queue_candidate_count": 2,
            "receipt_candidate_count": 2,
            "results": [{"node_id": "c" * 64, "status": "passed"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "result count"):
                upload_receipt.validate_receipt(path)

    def test_rejects_unknown_receipt_node(self):
        invalid = {"results": [
            {"node_id": "a" * 64}, {"node_id": "c" * 64}
        ]}
        with self.assertRaisesRegex(ValueError, "unknown=2"):
            upload_receipt.validate_complete_receipt_bundle(
                invalid, self.candidate, self.manifest
            )


class GitAuthenticationEnvironmentTests(unittest.TestCase):
    def test_remote_always_uses_ssh_even_when_token_is_set(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "stale-token"}):
            self.assertEqual(
                upload_receipt.github_ssh_remote("ccvvv/AutoSubHub"),
                "ssh://git@ssh.github.com:443/ccvvv/AutoSubHub.git",
            )


class UploadProgressStateTests(unittest.TestCase):
    def test_successful_upload_persists_uploaded_state_and_preserves_probe_counts(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            progress = Path(tmp) / "progress.json"
            progress.write_text(json.dumps({
                "status": "completed", "done": 250, "total": 250,
                "passed": 31, "failed": 219, "stage": "测速完成",
            }))
            changed = upload_receipt.mark_progress_uploaded(progress, "a" * 40)
            self.assertTrue(changed)
            data = json.loads(progress.read_text())
            self.assertEqual(data["status"], "uploaded")
            self.assertEqual(data["upload_status"], "uploaded")
            self.assertEqual(data["done"], 250)
            self.assertEqual(data["passed"], 31)
            self.assertEqual(data["receipt_commit"], "a" * 40)
            self.assertEqual(data["stage"], "回执已成功上传至 GitHub")


if __name__ == "__main__":
    unittest.main()
