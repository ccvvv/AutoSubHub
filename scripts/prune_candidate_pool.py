#!/usr/bin/env python3
"""Complete a validated phone queue without deleting source-derived pool nodes."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from scripts.candidate_pool import remove_tested_nodes, build_manifest
from scripts.source_snapshots import apply_completed_receipt, build_queue_from_pool


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def atomic_yaml(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return raw


def prune_and_refresh(pool_path: Path, manifest_path: Path, receipt_path: Path) -> None:
    pool = yaml.safe_load(pool_path.read_text(encoding="utf-8")) or {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    manifest_nodes = manifest.get("nodes")
    results = receipt.get("results")
    if not isinstance(manifest_nodes, list) or not isinstance(results, list):
        raise SystemExit("Malformed manifest or receipt; pool left unchanged")
    expected = [item.get("node_id") for item in manifest_nodes if isinstance(item, dict)]
    actual = [item.get("node_id") for item in results if isinstance(item, dict)]
    if (len(expected) != len(manifest_nodes) or len(actual) != len(results)
            or len(set(expected)) != len(expected) or set(actual) != set(expected)
            or len(actual) != len(expected)):
        raise SystemExit("Receipt is not a complete exact match for candidate manifest")

    pool_manifest_path = pool_path.parent / "pool-manifest.json"
    state_path = pool_path.parent / "probe-state.json"
    if pool_manifest_path.is_file():
        # New architecture: pool membership is controlled only by source snapshots.
        # A phone result updates lifecycle state and rebuilds the pending queue;
        # it never removes a node from pool.yaml.
        pool_meta = json.loads(pool_manifest_path.read_text(encoding="utf-8"))
        state = (json.loads(state_path.read_text(encoding="utf-8"))
                 if state_path.is_file() else {"schema_version": 1, "nodes": {}})
        completed_state = apply_completed_receipt(state, manifest, receipt)
        queue, completed_state = build_queue_from_pool(
            {"proxies": pool.get("proxies"), "nodes": pool_meta.get("nodes")},
            completed_state,
        )
        candidate_path = pool_path.parent / "subscription.yaml"
        candidate_bytes = atomic_yaml(candidate_path, {"proxies": queue["proxies"]})
        next_manifest = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "nodes": queue["nodes"],
        }
        atomic_json(manifest_path, next_manifest)
        atomic_json(state_path, completed_state)
        return

    # Legacy migration path only. Once pool-manifest.json exists, this destructive
    # behavior is unreachable and receipt processing no longer prunes the pool.
    pruned = remove_tested_nodes(pool, set(expected))
    pool_path.write_text(yaml.safe_dump(pruned, allow_unicode=True, sort_keys=False), encoding="utf-8")
    candidate_path = pool_path.parent / "subscription.yaml"
    candidate_path.write_text(
        yaml.safe_dump(pruned, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    candidate_bytes = candidate_path.read_bytes()
    refreshed_manifest = build_manifest(
        pruned,
        datetime.now(timezone.utc).isoformat(),
        hashlib.sha256(candidate_bytes).hexdigest(),
    )
    (pool_path.parent / "manifest.json").write_text(
        json.dumps(refreshed_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: prune_candidate_pool.py pool.yaml manifest.json receipt.json")
    pool_path, manifest_path, receipt_path = map(Path, sys.argv[1:])
    prune_and_refresh(pool_path, manifest_path, receipt_path)


if __name__ == "__main__":
    main()
