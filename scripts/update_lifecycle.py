#!/usr/bin/env python3
"""Maintain first-observed timestamps for candidate nodes.

The fingerprint excludes the display name so renaming a node does not reset
its lifecycle record. It includes the remaining proxy configuration.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def canonical_fingerprint(proxy: dict) -> str:
    identity = {key: value for key, value in proxy.items() if key != "name"}
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "Usage: update_lifecycle.py subscription.yaml manifest.json lifecycle.json"
        )

    subscription_path, manifest_path, lifecycle_path = map(Path, sys.argv[1:])
    with subscription_path.open("r", encoding="utf-8") as handle:
        subscription = yaml.safe_load(handle) or {}
    manifest = read_json(manifest_path, {})
    lifecycle = read_json(lifecycle_path, {"schema_version": 1, "nodes": {}})

    proxies = subscription.get("proxies")
    manifest_nodes = manifest.get("nodes")
    if not isinstance(proxies, list) or not proxies:
        raise SystemExit("Candidate subscription has no proxy list")
    if not isinstance(manifest_nodes, list):
        raise SystemExit("Candidate manifest has no nodes list")
    if lifecycle.get("schema_version") != 1 or not isinstance(lifecycle.get("nodes"), dict):
        raise SystemExit("Existing lifecycle metadata has an unsupported schema")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest_by_name = {}
    for item in manifest_nodes:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise SystemExit("Malformed manifest node")
        if item["name"] in manifest_by_name:
            raise SystemExit("Duplicate node name in manifest")
        manifest_by_name[item["name"]] = item

    seen_names = set()
    for proxy in proxies:
        if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
            raise SystemExit("Malformed proxy entry")
        name = proxy["name"]
        if name in seen_names:
            raise SystemExit("Duplicate proxy name in candidate subscription")
        seen_names.add(name)
        item = manifest_by_name.get(name)
        if item is None:
            raise SystemExit(f"Proxy missing from manifest: {name}")

        fingerprint = canonical_fingerprint(proxy)
        item["lifecycle_id"] = fingerprint
        record = lifecycle["nodes"].get(fingerprint, {})
        first_seen = record.get("first_seen_at") if isinstance(record, dict) else None
        if not isinstance(first_seen, str) or not first_seen:
            first_seen = now

        lifecycle["nodes"][fingerprint] = {
            "first_seen_at": first_seen,
            "last_seen_at": now,
            "name": name,
            "node_id": item.get("node_id"),
        }

    manifest["lifecycle_schema_version"] = 1
    manifest["lifecycle_updated_at"] = now
    lifecycle["schema_version"] = 1
    lifecycle["updated_at"] = now
    lifecycle["candidate_sha256"] = manifest.get("candidate_sha256")

    write_json(manifest_path, manifest)
    write_json(lifecycle_path, lifecycle)


if __name__ == "__main__":
    main()
