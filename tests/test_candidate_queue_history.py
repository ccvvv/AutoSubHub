import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.candidate_queue import collect_pending_candidate_batches


A = "a" * 40
B = "b" * 40
C = "c" * 40


class FakeResponse:
    def __init__(self, content=b"", data=None):
        self.content = content
        self._data = data
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, snapshots, receipt_data=None):
        self.snapshots = snapshots
        self.receipt_data = receipt_data
        self.requested = []

    def get(self, url, timeout):
        self.requested.append(url)
        if url.endswith("/published-subscriptions/receipts/latest.json"):
            if self.receipt_data is not None:
                return FakeResponse(data=self.receipt_data)
            missing = FakeResponse(data={})
            missing.status_code = 404
            return missing
        for commit, (candidate_bytes, manifest) in self.snapshots.items():
            base = f"https://raw.githubusercontent.com/ccvvv/AutoSubHub/{commit}/candidate/"
            if url == base + "subscription.yaml":
                return FakeResponse(content=candidate_bytes)
            if url == base + "manifest.json":
                return FakeResponse(data=manifest)
        raise AssertionError(f"Unexpected URL: {url}")


class CandidateQueueHistoryTests(unittest.TestCase):
    def test_collects_only_batches_after_cursor_oldest_first_and_deduplicates(self):
        snapshots = {}
        candidates = {
            B: {"proxies": [{"name": "old-name", "type": "ss", "server": "same.example", "port": 443}]},
            A: {"proxies": [
                {"name": "new-name", "type": "ss", "server": "same.example", "port": 443},
                {"name": "new-node", "type": "ss", "server": "new.example", "port": 443},
            ]},
        }
        for commit, candidate in candidates.items():
            raw = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False).encode()
            manifest = {
                "candidate_sha256": hashlib.sha256(raw).hexdigest(),
                "nodes": [{"name": proxy["name"]} for proxy in candidate["proxies"]],
            }
            snapshots[commit] = (raw, manifest)

        session = FakeSession(snapshots)
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "queue.json"
            state_path.write_text(json.dumps({"last_uploaded_candidate_commit": C}))
            git_results = [
                type("Result", (), {"stdout": "false"})(),
                type("Result", (), {"stdout": ""})(),
                type("Result", (), {"stdout": f"{A}" + chr(10) + f"{B}" + chr(10) + f"{C}" + chr(10)})(),
            ]
            with patch("scripts.candidate_queue.subprocess.run", side_effect=git_results) as mocked_git:
                candidate, manifest, commits, tip = collect_pending_candidate_batches(
                    "https://github.com/ccvvv/AutoSubHub.git",
                    "ccvvv/AutoSubHub", "published-subscriptions",
                    session, state_path,
                )

        log_command = mocked_git.call_args_list[2].args[0]
        self.assertIn("--diff-filter=AM", log_command)
        self.assertEqual(commits, [B, A])
        self.assertEqual(tip, A)
        self.assertEqual(len(candidate["proxies"]), 2)
        self.assertEqual(len(manifest["nodes"]), 2)
        self.assertEqual(manifest["nodes"][0]["source_commits"], [B, A])
        self.assertEqual(manifest["nodes"][0]["source_names"], ["old-name", "new-name"])

    def test_first_run_recovers_cursor_from_latest_uploaded_receipt(self):
        candidate = {"proxies": [
            {"name": "new-node", "type": "ss", "server": "new.example", "port": 443}
        ]}
        raw = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False).encode()
        manifest = {"candidate_sha256": hashlib.sha256(raw).hexdigest(),
                    "nodes": [{"name": "new-node"}]}
        session = FakeSession(
            {A: (raw, manifest)},
            receipt_data={"candidate_commit": B},
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "missing-state.json"
            git_results = [
                type("Result", (), {"stdout": "false"})(),
                type("Result", (), {"stdout": ""})(),
                type("Result", (), {"stdout": f"{A}" + chr(10) + f"{B}" + chr(10) + f"{C}" + chr(10)})(),
            ]
            with patch("scripts.candidate_queue.subprocess.run", side_effect=git_results):
                candidate_result, manifest_result, commits, tip = collect_pending_candidate_batches(
                    "https://github.com/ccvvv/AutoSubHub.git",
                    "ccvvv/AutoSubHub", "published-subscriptions",
                    session, state_path,
                )

        self.assertEqual(commits, [A])
        self.assertEqual(tip, A)
        self.assertEqual(len(candidate_result["proxies"]), 1)
        self.assertTrue(any(url.endswith("/published-subscriptions/receipts/latest.json")
                            for url in session.requested))

    def test_remote_receipt_advances_stale_local_cursor_after_upload_crash(self):
        candidate = {"proxies": [
            {"name": "new-node", "type": "ss", "server": "new.example", "port": 443}
        ]}
        raw = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False).encode()
        manifest = {"candidate_sha256": hashlib.sha256(raw).hexdigest(),
                    "nodes": [{"name": "new-node"}]}
        session = FakeSession(
            {A: (raw, manifest)},
            receipt_data={"candidate_commit": B},
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "queue.json"
            state_path.write_text(json.dumps({"last_uploaded_candidate_commit": C}))
            git_results = [
                type("Result", (), {"stdout": "false"})(),
                type("Result", (), {"stdout": ""})(),
                type("Result", (), {"stdout": f"{A}" + chr(10) + f"{B}" + chr(10) + f"{C}" + chr(10)})(),
            ]
            with patch("scripts.candidate_queue.subprocess.run", side_effect=git_results):
                candidate_result, manifest_result, commits, tip = collect_pending_candidate_batches(
                    "https://github.com/ccvvv/AutoSubHub.git",
                    "ccvvv/AutoSubHub", "published-subscriptions",
                    session, state_path,
                )

        self.assertEqual(commits, [A])
        self.assertEqual(tip, A)
        self.assertEqual(len(candidate_result["proxies"]), 1)

    def test_unknown_cursor_fails_closed_instead_of_skipping_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "queue.json"
            state_path.write_text(json.dumps({"last_uploaded_candidate_commit": "d" * 40}))
            git_results = [
                type("Result", (), {"stdout": "false"})(),
                type("Result", (), {"stdout": ""})(),
                type("Result", (), {"stdout": f"{A}" + chr(10) + f"{B}" + chr(10)})(),
            ]
            with patch("scripts.candidate_queue.subprocess.run", side_effect=git_results):
                with self.assertRaisesRegex(RuntimeError, "cursor is not present"):
                    collect_pending_candidate_batches(
                        "https://github.com/ccvvv/AutoSubHub.git",
                        "ccvvv/AutoSubHub", "published-subscriptions",
                        FakeSession({}), state_path,
                    )


if __name__ == "__main__":
    unittest.main()
