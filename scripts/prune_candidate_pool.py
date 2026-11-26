#!/usr/bin/env python3
"""Prune a pool only after finalize_phone_receipt.py has validated the receipt."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# This script is invoked from the published-data checkout, not the code repo.
# Add the code repository root so the shared scripts package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from scripts.candidate_pool import remove_tested_nodes, build_manifest


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

    pruned = remove_tested_nodes(pool, set(expected))
    pool_path.write_text(yaml.safe_dump(pruned, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # The phone downloads subscription.yaml, not pool.yaml. Keep the public
    # snapshot and its manifest in lockstep with the pruned persistent pool.
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
