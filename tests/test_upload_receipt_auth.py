import unittest
import json
from unittest.mock import patch

import upload_receipt


class ReceiptBundleCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.candidate = b"proxies:\\n  - name: node-a\\n    type: ss\\n    server: a.example\\n    port: 443\\n  - name: node-b\\n    type: ss\\n    server: b.example\\n    port: 443\\n"
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


if __name__ == "__main__":
    unittest.main()
