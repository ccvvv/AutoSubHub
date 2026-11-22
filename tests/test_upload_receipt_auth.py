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


if __name__ == "__main__":
    unittest.main()
