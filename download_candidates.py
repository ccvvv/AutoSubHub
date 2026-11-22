#!/usr/bin/env python3
"""Download and verify the cumulative pending candidate queue for offline reuse."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
import yaml

from scripts.candidate_queue import collect_pending_candidate_batches

REPO = "ccvvv/AutoSubHub"
BRANCH = "published-subscriptions"
USER_AGENT = "AutoSubHub-Termux-Probe/1.0"


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the pending candidate queue for local phone testing")
    parser.add_argument("--output", default="candidate-local.yaml")
    args = parser.parse_args()
    output = Path(args.output).expanduser()
    manifest_path = output.with_suffix(".manifest.json")
    metadata_path = output.with_suffix(".meta.json")

    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"], check=True,
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    if not remote:
        raise SystemExit("Git origin URL is empty")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    state_path = Path.home() / ".autosubhub_probe_state.json"
    candidate, manifest, commits, tip = collect_pending_candidate_batches(
        remote, REPO, BRANCH, session, state_path, bootstrap_limit=20
    )
    candidate_bytes = yaml.safe_dump(
        candidate, allow_unicode=True, sort_keys=False
    ).encode("utf-8")
    digest = hashlib.sha256(candidate_bytes).hexdigest()
    manifest["candidate_sha256"] = digest

    raw_time = subprocess.run(
        ["git", "show", "-s", "--format=%cI", "FETCH_HEAD"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    parsed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    beijing = parsed.astimezone(timezone(timedelta(hours=8)))
    now = datetime.now(timezone(timedelta(hours=8)))
    metadata = {
        "schema_version": 1,
        "candidate_commit": tip,
        "candidate_commits": commits,
        "candidate_batch_count": len(commits),
        "candidate_updated_at": beijing.strftime("%Y-%m-%d %H:%M:%S"),
        "candidate_fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "candidate_sha256": digest,
        "candidate_count": len(candidate.get("proxies", [])),
        "source": "cumulative immutable candidate history",
    }

    # Write data files first and metadata last. The metadata file is the commit
    # marker; readers reject any incomplete or mismatched set by checking SHA256.
    atomic_write(output, candidate_bytes)
    atomic_write(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    atomic_write(metadata_path, (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode())
    print(f"候选列表已保存：{output}")
    print(f"节点数：{metadata['candidate_count']}；累计批次：{len(commits)}")
    print(f"候选版本 SHA256：{digest}")
    print(f"更新时间（北京时间）：{metadata['candidate_updated_at']}")
    print(f"本机下载时间（北京时间）：{metadata['candidate_fetched_at']}")
    print("完整性校验通过；现在可以关闭系统 VPN，再运行本地列表测速。")


if __name__ == "__main__":
    main()
