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

from scripts.candidate_pool import build_manifest
from scripts.source_snapshots import apply_completed_receipt, build_queue_from_pool, node_fingerprint


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


def atomic_yaml(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    for proxy in value.get("proxies", []):
        if not isinstance(proxy, dict):
            continue
        options = proxy.get("reality-opts")
        if isinstance(options, dict) and isinstance(options.get("short-id"), str):
            options["short-id"] = QuotedYamlString(options["short-id"])
    raw = yaml.dump(value, Dumper=AutoHubSafeDumper, allow_unicode=True, sort_keys=False).encode("utf-8")
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
        # The phone-facing manifest and receipt use only the 20-hex node_id.
        # Resolve those IDs against the trusted full-fingerprint pool metadata;
        # never require the phone to upload internal 256-bit fingerprints.
        pool_meta = json.loads(pool_manifest_path.read_text(encoding="utf-8"))
        state = (json.loads(state_path.read_text(encoding="utf-8"))
                 if state_path.is_file() else {"schema_version": 1, "nodes": {}})
        pool_proxies = pool.get("proxies")
        pool_nodes = pool_meta.get("nodes")
        if not isinstance(pool_proxies, list) or not isinstance(pool_nodes, list) or len(pool_proxies) != len(pool_nodes):
            raise SystemExit("Persistent pool metadata does not match pool; pool left unchanged")

        meta_by_id = {}
        for proxy, meta in zip(pool_proxies, pool_nodes):
            if not isinstance(proxy, dict) or not isinstance(meta, dict):
                raise SystemExit("Persistent pool contains invalid node metadata; pool left unchanged")
            fingerprint = meta.get("fingerprint")
            current_id = node_fingerprint(proxy)[:20]
            if (not isinstance(fingerprint, str) or len(fingerprint) != 64
                    or fingerprint != node_fingerprint(proxy)
                    or meta.get("node_id") != current_id
                    or fingerprint[:20] != current_id or current_id in meta_by_id):
                raise SystemExit("Persistent pool fingerprint/20-character ID mismatch; pool left unchanged")
            meta_by_id[current_id] = fingerprint

        enriched_nodes = []
        for item in manifest_nodes:
            node_id = item.get("node_id")
            fingerprint = meta_by_id.get(node_id)
            if fingerprint is None:
                raise SystemExit("Receipt manifest contains an ID absent from the persistent pool; pool left unchanged")
            enriched_nodes.append({**item, "fingerprint": fingerprint})
        internal_manifest = {**manifest, "nodes": enriched_nodes}
        completed_state = apply_completed_receipt(state, internal_manifest, receipt)
        result_by_id = {item["node_id"]: item for item in results}
        failed_ids = {
            item["node_id"] for item in manifest_nodes
            if result_by_id[item["node_id"]]["status"] == "failed"
        }
        kept_pairs = [
            (proxy, meta) for proxy, meta in zip(pool_proxies, pool_nodes)
            if meta["node_id"] not in failed_ids
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
        candidate_bytes = yaml.safe_dump({"proxies": queue["proxies"]}, allow_unicode=True,
                                         sort_keys=False).encode("utf-8")
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

    # One-time non-destructive migration for the old pool format, which did not
    # store provenance metadata. Reconstruct fingerprints from the existing pool,
    # mark the exact receipt nodes completed, and leave pool.yaml untouched.
    proxies = pool.get("proxies")
    if not isinstance(proxies, list):
        raise SystemExit("Legacy pool has no proxy list; pool left unchanged")
    by_name = {item.get("name"): item for item in proxies if isinstance(item, dict)}
    if len(by_name) != len(proxies):
        raise SystemExit("Legacy pool has invalid or duplicate names; pool left unchanged")
    enriched_nodes = []
    for item in manifest_nodes:
        name = item.get("name")
        proxy = by_name.get(name)
        if proxy is None:
            raise SystemExit("Legacy receipt node is not present in the pool; pool left unchanged")
        fingerprint = node_fingerprint(proxy)
        if fingerprint[:20] != item.get("node_id"):
            raise SystemExit("Legacy receipt fingerprint mismatch; pool left unchanged")
        enriched_nodes.append({**item, "fingerprint": fingerprint, "sources": []})
    enriched_manifest = {**manifest, "nodes": enriched_nodes}
    state = {"schema_version": 1, "nodes": {}}
    completed_state = apply_completed_receipt(state, enriched_manifest, receipt)
    pool_nodes = [
        {
            "node_id": node_fingerprint(proxy)[:20],
            "fingerprint": node_fingerprint(proxy),
            "name": proxy["name"],
            "sources": [],
            "source_names": [proxy["name"]],
        }
        for proxy in proxies
    ]
    queue, completed_state = build_queue_from_pool(
        {"proxies": proxies, "nodes": pool_nodes}, completed_state
    )
    pool_manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": pool_nodes,
    }
    atomic_json(pool_path.parent / "pool-manifest.json", pool_manifest)
    atomic_json(state_path, completed_state)
    candidate_path = pool_path.parent / "subscription.yaml"
    candidate_bytes = atomic_yaml(candidate_path, {"proxies": queue["proxies"]})
    refreshed_manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "nodes": queue["nodes"],
    }
    atomic_json(pool_path.parent / "manifest.json", refreshed_manifest)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: prune_candidate_pool.py pool.yaml manifest.json receipt.json")
    pool_path, manifest_path, receipt_path = map(Path, sys.argv[1:])
    prune_and_refresh(pool_path, manifest_path, receipt_path)


if __name__ == "__main__":
    main()
