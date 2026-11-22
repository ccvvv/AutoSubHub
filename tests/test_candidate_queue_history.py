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
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.requested = []

    def get(self, url, timeout):
        self.requested.append(url)
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
                type("Result", (), {"stdout": f"{A}\\n{B}\\n{C}\\n"})(),
            ]
            with patch("scripts.candidate_queue.subprocess.run", side_effect=git_results):
                candidate, manifest, commits, tip = collect_pending_candidate_batches(
                    "https://github.com/ccvvv/AutoSubHub.git",
                    "ccvvv/AutoSubHub", "published-subscriptions",
                    session, state_path,
                )

        self.assertEqual(commits, [B, A])
        self.assertEqual(tip, A)
        self.assertEqual(len(candidate["proxies"]), 2)
        self.assertEqual(len(manifest["nodes"]), 2)
        self.assertEqual(manifest["nodes"][0]["source_commits"], [B, A])
        self.assertEqual(manifest["nodes"][0]["source_names"], ["old-name", "new-name"])

    def test_unknown_cursor_fails_closed_instead_of_skipping_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "queue.json"
            state_path.write_text(json.dumps({"last_uploaded_candidate_commit": "d" * 40}))
            git_results = [
                type("Result", (), {"stdout": "false"})(),
                type("Result", (), {"stdout": ""})(),
                type("Result", (), {"stdout": f"{A}\\n{B}\\n"})(),
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
