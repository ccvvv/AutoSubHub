import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import upload_receipt


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
        self.manifest = {"nodes": [
            {"node_id": "a" * 64, "name": "node-a"},
            {"node_id": "b" * 64, "name": "node-b"},
        ]}
        self.receipt = {"results": [
            {"node_id": "a" * 64, "status": "passed"},
            {"node_id": "b" * 64, "status": "failed"},
        ]}

    def test_accepts_exact_complete_receipt(self):
        upload_receipt.validate_complete_receipt_bundle(
            self.receipt, self.candidate, self.manifest
        )

    def test_rejects_partial_receipt_before_cursor_advance(self):
        partial = {"results": [{"node_id": "a" * 64, "status": "passed"}]}
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
        with self.assertRaisesRegex(ValueError, "unknown=1"):
            upload_receipt.validate_complete_receipt_bundle(
                invalid, self.candidate, self.manifest
            )


class GitAuthenticationEnvironmentTests(unittest.TestCase):
    def test_git_helper_passes_https_auth_header_without_printing_it(self):
        secret_header = "AUTHORIZATION: basic dG9rZW46c2VjcmV0"
        previous = dict(upload_receipt.GIT_AUTH_ENV)
        try:
            upload_receipt.GIT_AUTH_ENV.clear()
            upload_receipt.GIT_AUTH_ENV.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": secret_header,
            })
            with patch("upload_receipt.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ""
                run.return_value.stderr = ""
                upload_receipt.git("ls-remote", "https://github.com/ccvvv/AutoSubHub.git")
                env = run.call_args.kwargs["env"]
                self.assertEqual(
                    env["GIT_CONFIG_KEY_0"],
                    "http.https://github.com/.extraheader",
                )
                self.assertEqual(env["GIT_CONFIG_VALUE_0"], secret_header)
        finally:
            upload_receipt.GIT_AUTH_ENV.clear()
            upload_receipt.GIT_AUTH_ENV.update(previous)


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
