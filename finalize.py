#!/usr/bin/env python3
"""Create a final Mihomo subscription only from a matching phone probe receipt."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import yaml


def fail(message: str) -> None:
    raise SystemExit(f"REFUSING TO PUBLISH: {message}")


def main() -> None:
    candidate_path = Path(os.getenv("CANDIDATE_FILE", "candidate/subscription.yaml"))
    manifest_path = Path(os.getenv("MANIFEST_FILE", "candidate/manifest.json"))
    receipt_path = Path(os.getenv("RECEIPT_FILE", "receipt.json"))
    output_path = Path(os.getenv("OUTPUT_FILE", "final.yaml"))
    output_manifest_path = Path(os.getenv("OUTPUT_MANIFEST_FILE", "final-manifest.json"))

    for path in (candidate_path, manifest_path, receipt_path):
        if not path.is_file():
            fail(f"required input missing: {path}")

    candidate_bytes = candidate_path.read_bytes()
    actual_digest = hashlib.sha256(candidate_bytes).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    if manifest.get("candidate_sha256") != actual_digest:
        fail("candidate YAML does not match its manifest")
    if receipt.get("status") != "completed":
        fail("phone receipt is not marked completed")
    if receipt.get("candidate_sha256") != actual_digest:
        fail("receipt belongs to a different candidate snapshot")

    candidate = yaml.safe_load(candidate_bytes)
    proxies = candidate.get("proxies") if isinstance(candidate, dict) else None
    if not isinstance(proxies, list) or not proxies:
        fail("candidate subscription is empty or malformed")

    manifest_nodes = manifest.get("nodes")
    if not isinstance(manifest_nodes, list):
        fail("candidate manifest has no node index")
    id_by_name = {
        str(item.get("name")): str(item.get("node_id"))
        for item in manifest_nodes
        if isinstance(item, dict) and item.get("name") and item.get("node_id")
    }
    if len(id_by_name) != len(manifest_nodes):
        fail("manifest contains missing or duplicate node names")

    proxy_by_id = {}
    for proxy in proxies:
        if not isinstance(proxy, dict) or not proxy.get("name"):
            fail("candidate contains an invalid proxy")
        node_id = id_by_name.get(str(proxy["name"]))
        if not node_id or node_id in proxy_by_id:
            fail("candidate and manifest node index do not match")
        proxy_by_id[node_id] = proxy

    results = receipt.get("results")
    if not isinstance(results, list) or not results:
        fail("receipt has no per-node results")

    seen = set()
    passed = []
    for result in results:
        if not isinstance(result, dict):
            fail("receipt contains a malformed result")
        node_id = str(result.get("node_id", ""))
        if not node_id or node_id in seen:
            fail("receipt contains a missing or duplicate node ID")
        seen.add(node_id)
        if node_id not in proxy_by_id:
            fail("receipt references a node outside this candidate snapshot")
        if result.get("status") == "passed":
            passed.append(proxy_by_id[node_id])

    if not passed:
        fail("phone returned zero usable nodes")

    names = [str(proxy.get("name", "")) for proxy in passed]
    if len(names) != len(set(names)) or any(not name for name in names):
        fail("passed nodes have missing or duplicate names")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump({"proxies": passed}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    output_manifest = {
        "schema_version": 1,
        "candidate_sha256": actual_digest,
        "receipt_completed_at": receipt.get("completed_at"),
        "passed_nodes": len(passed),
        "total_tested": len(results),
        "node_ids": [str(id_by_name[str(proxy["name"])]) for proxy in passed],
    }
    output_manifest_path.write_text(
        json.dumps(output_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Validated phone receipt: {len(passed)}/{len(results)} nodes passed")
    print(f"Generated final subscription: {output_path}")


if __name__ == "__main__":
    main()
