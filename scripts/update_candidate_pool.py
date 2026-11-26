#!/usr/bin/env python3
"""Merge a newly built candidate into the persistent data-branch pool."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.candidate_pool import merge_pool, build_manifest


def read_yaml_bytes(raw: bytes) -> dict:
    value = yaml.safe_load(raw.decode("utf-8")) or {}
    if not isinstance(value, dict) or not isinstance(value.get("proxies"), list):
        raise ValueError("Invalid historical candidate snapshot")
    return value


def historical_pool() -> dict:
    # Walk every revision that introduced/changed the old candidate snapshot.
    commits = subprocess.run(
        ["git", "log", "--diff-filter=AM", "--format=%H", "HEAD", "--",
         "candidate/subscription.yaml"],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout.splitlines()
    pool = {"proxies": []}
    for commit in reversed(commits):
        raw = subprocess.run(
            ["git", "show", f"{commit}:candidate/subscription.yaml"],
            check=True, capture_output=True, timeout=30,
        ).stdout
        pool = merge_pool(pool, read_yaml_bytes(raw))
    return pool


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: update_candidate_pool.py incoming.yaml pool.yaml candidate.yaml")
    incoming_path, pool_path, candidate_path = map(Path, sys.argv[1:])
    incoming = read_yaml_bytes(incoming_path.read_bytes())
    pool = read_yaml_bytes(pool_path.read_bytes()) if pool_path.exists() else historical_pool()
    pool = merge_pool(pool, incoming)
    if not pool["proxies"]:
        raise SystemExit("Refusing to publish an empty candidate pool")

    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text(yaml.safe_dump(pool, allow_unicode=True, sort_keys=False), encoding="utf-8")
    candidate_path.write_text(yaml.safe_dump(pool, allow_unicode=True, sort_keys=False), encoding="utf-8")
    candidate_bytes = candidate_path.read_bytes()
    digest = hashlib.sha256(candidate_bytes).hexdigest()
    manifest = build_manifest(pool, datetime.now(timezone.utc).isoformat(), digest)
    Path("candidate/manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
