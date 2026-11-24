#!/usr/bin/env python3
"""Build the cloud-side cumulative pending queue for Android/Termux probe clients."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

from scripts.candidate_queue import collect_pending_candidate_batches


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="candidate")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "ccvvv/AutoSubHub"))
    parser.add_argument("--branch", default="published-subscriptions")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if not remote:
        raise RuntimeError("Git origin URL is empty")

    with tempfile.TemporaryDirectory(prefix="autosubhub-cloud-queue-") as temp:
        state_path = Path(temp) / "no-local-cursor.json"
        previous_cwd = Path.cwd()
        try:
            candidate, manifest, commits, tip = collect_pending_candidate_batches(
                remote, args.repo, args.branch, _Session(), state_path, bootstrap_limit=20
            )
        except RuntimeError as exc:
            if str(exc) == "No new candidate batches since the last successful receipt upload":
                for name in ("pending.yaml", "pending-manifest.json", "pending-meta.json"):
                    (output_dir / name).unlink(missing_ok=True)
                print("No untested candidate batches remain; stale cloud queue files were removed.")
                return 0
            raise
        finally:
            os.chdir(previous_cwd)

    candidate_bytes = yaml.safe_dump(
        candidate, allow_unicode=True, sort_keys=False
    ).encode("utf-8")
    digest = hashlib.sha256(candidate_bytes).hexdigest()
    manifest["candidate_sha256"] = digest
    metadata = {
        "schema_version": 1,
        "candidate_commit": tip,
        "candidate_commits": commits,
        "candidate_batch_count": len(commits),
        "candidate_sha256": digest,
        "candidate_count": len(candidate.get("proxies", [])),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "cloud-prepared cumulative pending queue",
    }
    atomic_write(output_dir / "pending.yaml", candidate_bytes)
    atomic_write(
        output_dir / "pending-manifest.json",
        (json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\\n").encode("utf-8"),
    )
    atomic_write(
        output_dir / "pending-meta.json",
        (json.dumps(metadata, ensure_ascii=False, indent=2) + "\\n").encode("utf-8"),
    )
    print(
        f"Cloud pending queue ready: {metadata['candidate_count']} nodes, "
        f"{metadata['candidate_batch_count']} batches, sha256={digest}"
    )
    return 0


class _Session:
    """Minimal requests-like session wrapper used by the existing verified downloader."""
    def __init__(self):
        import requests
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "AutoSubHub-CloudQueue/1.0"})

    def get(self, url: str, timeout: int):
        return self._session.get(url, timeout=timeout)


if __name__ == "__main__":
    raise SystemExit(main())
