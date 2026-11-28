#!/usr/bin/env python3
"""Apply a validated phone receipt and remove failed nodes from the live candidate pool."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from scripts.source_snapshots import apply_completed_receipt, build_queue_from_pool
from scripts.node_identity import node_fingerprint, is_full_fingerprint


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


class QuotedYamlString(str):
    pass


class AutoHubSafeDumper(yaml.SafeDumper):
    pass


def represent_quoted_string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="'")


AutoHubSafeDumper.add_representer(QuotedYamlString, represent_quoted_string)


def yaml_bytes(value: dict) -> bytes:
    for proxy in value.get("proxies", []):
        if not isinstance(proxy, dict):
            continue
        options = proxy.get("reality-opts")
        if isinstance(options, dict) and isinstance(options.get("short-id"), str):
            options["short-id"] = QuotedYamlString(options["short-id"])
    return yaml.dump(value, Dumper=AutoHubSafeDumper, allow_unicode=True,
                     sort_keys=False).encode("utf-8")


def atomic_yaml(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml_bytes(value)
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
            or any(not is_full_fingerprint(value) for value in expected + actual)
            or len(set(expected)) != len(expected) or set(actual) != set(expected)
            or len(actual) != len(expected)):
        raise SystemExit("Receipt must exactly match the complete 64-character fingerprint manifest")
    if (not manifest.get("candidate_sha256")
            or receipt.get("candidate_sha256") != manifest.get("candidate_sha256")):
        raise SystemExit("Receipt/manifest candidate SHA256 mismatch; pool left unchanged")

    pool_manifest_path = pool_path.parent / "pool-manifest.json"
    state_path = pool_path.parent / "probe-state.json"
    if not pool_manifest_path.is_file():
        raise SystemExit("Persistent pool manifest missing; refusing receipt processing")

    # Phone, candidate manifest, receipt and cloud pool share the complete
    # SHA-256 fingerprint. No short-ID or name-based resolution is permitted.
    pool_meta = json.loads(pool_manifest_path.read_text(encoding="utf-8"))
    if not state_path.is_file():
        raise SystemExit("Persistent probe state missing; refusing receipt processing")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pool_proxies = pool.get("proxies")
    pool_nodes = pool_meta.get("nodes")
    if not isinstance(pool_proxies, list) or not isinstance(pool_nodes, list) or len(pool_proxies) != len(pool_nodes):
        raise SystemExit("Persistent pool metadata does not match pool; pool left unchanged")

    meta_by_id = {}
    for proxy, meta in zip(pool_proxies, pool_nodes):
        if not isinstance(proxy, dict) or not isinstance(meta, dict):
            raise SystemExit("Persistent pool contains invalid node metadata; pool left unchanged")
        fingerprint = node_fingerprint(proxy)
        if (not is_full_fingerprint(meta.get("fingerprint"))
                or meta.get("fingerprint") != fingerprint
                or meta.get("node_id") != fingerprint
                or fingerprint in meta_by_id):
            raise SystemExit("Persistent pool full fingerprint mismatch; pool left unchanged")
        meta_by_id[fingerprint] = fingerprint

    # A phone may finish a snapshot after the live pool has refreshed. Missing
    # fingerprints are skipped safely; present fingerprints must match exactly.
    matched_nodes = []
    for item in manifest_nodes:
        fingerprint = item.get("fingerprint")
        if item.get("node_id") != fingerprint or not is_full_fingerprint(fingerprint):
            raise SystemExit("Phone manifest lacks a valid full SHA-256 fingerprint")
        if fingerprint in meta_by_id:
            matched_nodes.append(item)
    matched_ids = {item["node_id"] for item in matched_nodes}
    missing_ids = [item["node_id"] for item in manifest_nodes if item["node_id"] not in matched_ids]
    matched_results = [item for item in results if item["node_id"] in matched_ids]
    internal_manifest = {**manifest, "nodes": matched_nodes}
    internal_receipt = {**receipt, "results": matched_results}
    completed_state = apply_completed_receipt(state, internal_manifest, internal_receipt)
    result_by_id = {item["node_id"]: item for item in matched_results}
    failed_ids = {
        item["node_id"] for item in matched_nodes
        if result_by_id[item["node_id"]]["status"] == "failed"
    }
    if missing_ids:
        print(f"Receipt snapshot has {len(missing_ids)} full fingerprints already absent from the live pool; skipped safely")
    kept_pairs = [
        (proxy, meta) for proxy, meta in zip(pool_proxies, pool_nodes)
        if meta["fingerprint"] not in failed_ids
    ]
    next_pool = {"proxies": [proxy for proxy, _ in kept_pairs]}
    next_pool_meta = {"schema_version": pool_meta.get("schema_version", 1),
                      "generated_at": pool_meta.get("generated_at"),
                      "nodes": [meta for _, meta in kept_pairs]}
    queue, completed_state = build_queue_from_pool(
        {"proxies": next_pool["proxies"], "nodes": next_pool_meta["nodes"]},
        completed_state,
    )
    candidate_path = pool_path.parent / "subscription.yaml"
    candidate_bytes = yaml_bytes({"proxies": queue["proxies"]})
    next_manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "nodes": queue["nodes"],
    }
    # All validation and derived outputs are complete before any live file changes.
    atomic_yaml(pool_path, next_pool)
    atomic_json(pool_manifest_path, next_pool_meta)
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = candidate_path.with_suffix(candidate_path.suffix + ".tmp")
    temporary.write_bytes(candidate_bytes)
    temporary.replace(candidate_path)
    atomic_json(pool_path.parent / "manifest.json", next_manifest)
    atomic_json(state_path, completed_state)
    return


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: prune_candidate_pool.py pool.yaml manifest.json receipt.json")
    pool_path, manifest_path, receipt_path = map(Path, sys.argv[1:])
    prune_and_refresh(pool_path, manifest_path, receipt_path)


if __name__ == "__main__":
    main()
