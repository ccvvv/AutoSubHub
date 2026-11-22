#!/usr/bin/env python3
"""Track lifecycle only for nodes in the phone-tested final subscription."""
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


def read_json(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "nodes": {}}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: update_lifecycle.py final-subscription.yaml lifecycle.json")

    subscription_path, lifecycle_path = map(Path, sys.argv[1:])
    with subscription_path.open("r", encoding="utf-8") as handle:
        subscription = yaml.safe_load(handle) or {}
    lifecycle = read_json(lifecycle_path)

    proxies = subscription.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise SystemExit("Final subscription has no proxy list")
    if lifecycle.get("schema_version") != 1 or not isinstance(lifecycle.get("nodes"), dict):
        raise SystemExit("Existing lifecycle metadata has an unsupported schema")

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    updated_nodes = dict(lifecycle["nodes"])
    seen = set()

    for proxy in proxies:
        if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str):
            raise SystemExit("Malformed final proxy entry")
        fingerprint = canonical_fingerprint(proxy)
        record = updated_nodes.get(fingerprint, {})
        first_seen = record.get("first_seen_at") if isinstance(record, dict) else None
        if not isinstance(first_seen, str) or not first_seen:
            first_seen = now

        updated_nodes[fingerprint] = {
            "first_seen_at": first_seen,
            "last_seen_at": now,
            "name": proxy["name"],
        }
        seen.add(fingerprint)

    result = {
        "schema_version": 1,
        "updated_at": now,
        "source": "final/latest.yaml",
        "published_node_count": len(proxies),
        "nodes": updated_nodes,
    }
    write_json(lifecycle_path, result)
    print(f"Updated lifecycle for {len(seen)} final-published node identities")


if __name__ == "__main__":
    main()
