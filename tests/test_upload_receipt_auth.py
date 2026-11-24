import unittest
from unittest.mock import patch

import upload_receipt


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
