#!/usr/bin/env python3
"""Prune a pool only after finalize_phone_receipt.py has validated the receipt."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from scripts.candidate_pool import remove_tested_nodes


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: prune_candidate_pool.py pool.yaml manifest.json receipt.json")
    pool_path, manifest_path, receipt_path = map(Path, sys.argv[1:])
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


if __name__ == "__main__":
    main()
