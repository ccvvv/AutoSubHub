"""Per-source subscription snapshots and a fingerprint-based incremental probe queue.

Raw subscription URLs are never persisted: only their SHA-256 identifiers are stored.
A 304 response reuses that source's previous snapshot. A failed or empty 200 response
must be rejected by the caller and must never replace a known-good snapshot.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any


def url_fingerprint(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def node_fingerprint(proxy: dict[str, Any]) -> str:
    if not isinstance(proxy, dict):
        raise ValueError("Proxy entry must be an object")
    identity = {key: value for key, value in proxy.items() if key != "name"}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_nodes(nodes: Any) -> list[dict[str, Any]]:
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Subscription snapshot is empty or has no proxy list")
    validated: list[dict[str, Any]] = []
    for index, proxy in enumerate(nodes, 1):
        if not isinstance(proxy, dict):
            raise ValueError(f"Proxy #{index} is not an object")
        if not isinstance(proxy.get("name"), str) or not proxy["name"].strip():
            raise ValueError(f"Proxy #{index} has no valid name")
        if not isinstance(proxy.get("server"), str) or not proxy["server"].strip():
            raise ValueError(f"Proxy #{index} has no valid server")
        port = proxy.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"Proxy #{index} has an invalid port")
        validated.append(dict(proxy))
    return validated


def apply_source_response(
    previous: dict[str, Any] | None,
    source_id: str,
    url: str,
    status_code: int,
    nodes: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
    fetched_at: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Return the next snapshot and whether its node set was replaced."""
    url_hash = url_fingerprint(url)
    now = fetched_at or datetime.now(timezone.utc).isoformat()
    if status_code == 304:
        if not isinstance(previous, dict):
            raise ValueError(f"{source_id}: HTTP 304 without an existing snapshot")
        if previous.get("source_url_sha256") != url_hash:
            raise ValueError(f"{source_id}: HTTP 304 does not match the saved source URL")
        saved_nodes = validate_nodes(previous.get("nodes"))
        snapshot = dict(previous)
        snapshot.update({"checked_at": now, "last_status": 304, "nodes": saved_nodes})
        return snapshot, False
    if status_code != 200:
        raise ValueError(f"{source_id}: unexpected HTTP status {status_code}")
    clean_nodes = validate_nodes(nodes)
    snapshot = {
        "schema_version": 1,
        "source_id": source_id,
        "source_url_sha256": url_hash,
        "fetched_at": now,
        "checked_at": now,
        "last_status": 200,
        "etag": (headers or {}).get("ETag") or (headers or {}).get("etag"),
        "last_modified": (headers or {}).get("Last-Modified") or (headers or {}).get("last-modified"),
        "nodes": clean_nodes,
    }
    return snapshot, True



def build_queue_from_pool(
    pool: dict[str, Any],
    previous_state: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild the pending phone queue from the current source-derived pool."""
    now = generated_at or datetime.now(timezone.utc).isoformat()
    proxies = pool.get("proxies")
    nodes = pool.get("nodes")
    if not isinstance(proxies, list) or not isinstance(nodes, list):
        raise ValueError("Candidate pool requires proxies and node metadata lists")
    by_fingerprint = {node_fingerprint(proxy): proxy for proxy in validate_nodes(proxies)}
    if len(by_fingerprint) != len(proxies):
        raise ValueError("Candidate pool contains duplicate node fingerprints")
    if {item.get("fingerprint") for item in nodes if isinstance(item, dict)} != set(by_fingerprint):
        raise ValueError("Candidate pool manifest does not match its proxy list")

    state = previous_state if isinstance(previous_state, dict) else {}
    if state.get("schema_version", 1) != 1 or not isinstance(state.get("nodes", {}), dict):
        raise ValueError("Unsupported probe state schema")
    state_nodes = {key: dict(value) for key, value in state.get("nodes", {}).items()
                   if isinstance(value, dict)}
    queue_proxies: list[dict[str, Any]] = []
    queue_nodes: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for meta in nodes:
        fingerprint = meta["fingerprint"]
        proxy = dict(by_fingerprint[fingerprint])
        record = state_nodes.get(fingerprint, {})
        if record.get("status") in {"completed", "passed", "failed", "precheck_failed"}:
            continue
        name = str(meta.get("name") or proxy.get("name") or fingerprint[:12])
        if name in used_names:
            name = f"{name} [{fingerprint[:6]}]"
        if name in used_names:
            raise ValueError("Unable to generate unique queue node names")
        used_names.add(name)
        proxy["name"] = name
        queue_proxies.append(proxy)
        queue_nodes.append({
            "node_id": fingerprint[:20],
            "fingerprint": fingerprint,
            "name": name,
            "sources": list(meta.get("sources") or []),
        })
        record.update({"status": "pending", "last_seen_at": now})
        record.setdefault("first_seen_at", now)
        state_nodes[fingerprint] = record

    return (
        {"schema_version": 1, "generated_at": now, "proxies": queue_proxies,
         "nodes": queue_nodes},
        {"schema_version": 1, "updated_at": now, "nodes": state_nodes},
    )


def apply_completed_receipt(
    previous_state: dict[str, Any],
    manifest: dict[str, Any],
    receipt: dict[str, Any],
    processed_at: str | None = None,
) -> dict[str, Any]:
    """Mark every node in a fully matched receipt as completed, regardless of pass/fail."""
    nodes = manifest.get("nodes")
    results = receipt.get("results")
    if not isinstance(nodes, list) or not isinstance(results, list):
        raise ValueError("Receipt or manifest is malformed")
    by_id: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Manifest contains an invalid node")
        node_id = node.get("node_id")
        fingerprint = node.get("fingerprint")
        if (not isinstance(node_id, str) or not isinstance(fingerprint, str)
                or len(fingerprint) != 64 or fingerprint[:20] != node_id):
            raise ValueError("Manifest is missing a verifiable full node fingerprint")
        if node_id in by_id:
            raise ValueError("Manifest contains duplicate node IDs")
        by_id[node_id] = fingerprint
    seen: set[str] = set()
    result_by_id: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Receipt contains an invalid result")
        node_id = item.get("node_id")
        if not isinstance(node_id, str) or node_id not in by_id or node_id in seen:
            raise ValueError("Receipt contains an unknown or duplicate node ID")
        if item.get("status") not in {"passed", "failed"}:
            raise ValueError("Receipt contains a non-final node status")
        seen.add(node_id)
        result_by_id[node_id] = item
    if seen != set(by_id):
        raise ValueError("Receipt does not cover the exact candidate manifest")
    now = processed_at or datetime.now(timezone.utc).isoformat()
    state = previous_state if isinstance(previous_state, dict) else {}
    state_nodes = {key: dict(value) for key, value in state.get("nodes", {}).items()
                   if isinstance(value, dict)}
    receipt_id = receipt.get("task_id") or receipt.get("receipt_id") or receipt.get("candidate_sha256")
    for node_id, fingerprint in by_id.items():
        item = result_by_id[node_id]
        record = state_nodes.get(fingerprint, {})
        if record.get("status") == "completed":
            prior_receipt = record.get("receipt_id")
            if prior_receipt and prior_receipt != receipt_id:
                raise ValueError("Node was already completed by a different receipt")
            if record.get("result") and record.get("result") != item["status"]:
                raise ValueError("Duplicate receipt conflicts with the recorded node result")
        result_status = item["status"]
        record.update({
            "status": result_status,
            "result": result_status,
            "completed_at": now,
            "receipt_id": receipt_id or record.get("receipt_id"),
        })
        state_nodes[fingerprint] = record
    return {"schema_version": 1, "updated_at": now, "nodes": state_nodes}


def reconcile_sources(
    snapshots: dict[str, dict[str, Any]],
    previous_state: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Merge current source snapshots and enqueue only never-tested fingerprints.

    A fingerprint is retained while it exists in at least one source. Completed
    results survive source removal so an identical node is not automatically retested
    if it later reappears.
    """
    now = generated_at or datetime.now(timezone.utc).isoformat()
    merged: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for source_id in sorted(snapshots):
        snapshot = snapshots[source_id]
        if not isinstance(snapshot, dict) or snapshot.get("source_id") != source_id:
            raise ValueError(f"Invalid source snapshot: {source_id}")
        for proxy in validate_nodes(snapshot.get("nodes")):
            fingerprint = node_fingerprint(proxy)
            entry = merged.setdefault(fingerprint, {
                "proxy": dict(proxy), "sources": [], "source_names": [],
            })
            if source_id not in entry["sources"]:
                entry["sources"].append(source_id)
            if proxy["name"] not in entry["source_names"]:
                entry["source_names"].append(proxy["name"])

    state = previous_state if isinstance(previous_state, dict) else {}
    if state.get("schema_version", 1) != 1 or not isinstance(state.get("nodes", {}), dict):
        raise ValueError("Unsupported probe state schema")
    state_nodes = {key: dict(value) for key, value in state.get("nodes", {}).items()
                   if isinstance(value, dict)}
    current_ids = set(merged)

    proxies: list[dict[str, Any]] = []
    manifest_nodes: list[dict[str, Any]] = []
    used_names: set[str] = set()
    queue_nodes: list[dict[str, Any]] = []
    queue_proxies: list[dict[str, Any]] = []
    for fingerprint, entry in merged.items():
        record = state_nodes.get(fingerprint, {})
        # Failed phone-test nodes are removed from the live pool and must not
        # re-enter merely because their source snapshot still contains them.
        if record.get("status") == "failed":
            continue

        proxy = dict(entry["proxy"])
        base_name = str(proxy["name"])
        name = base_name
        if name in used_names:
            name = f"{base_name} [{fingerprint[:6]}]"
        if name in used_names:
            name = f"{base_name} [{fingerprint[:12]}]"
        if name in used_names:
            raise ValueError("Unable to generate unique node names")
        proxy["name"] = name
        used_names.add(name)
        proxies.append(proxy)
        manifest_nodes.append({
            "node_id": fingerprint[:20],
            "fingerprint": fingerprint,
            "name": name,
            "sources": entry["sources"],
            "source_names": entry["source_names"],
        })
        status = record.get("status")
        if status == "precheck_failed":
            record["last_seen_at"] = now
            state_nodes[fingerprint] = record
            continue

        # Passed nodes remain in the pool and are intentionally retested each
        # build. New/unseen nodes enter the queue as pending.
        if status not in {"passed", "failed"}:
            record = {
                "status": "pending",
                "first_seen_at": record.get("first_seen_at") or now,
                "last_seen_at": now,
            }
        else:
            record["last_seen_at"] = now
        state_nodes[fingerprint] = record
        queue_proxies.append(dict(proxy))
        queue_nodes.append({
            "node_id": fingerprint[:20],
            "fingerprint": fingerprint,
            "name": name,
            "sources": entry["sources"],
        })

    # Pending entries that disappeared from every source are cancelled, not tested.
    for fingerprint, record in list(state_nodes.items()):
        if fingerprint not in current_ids and record.get("status") == "pending":
            record["status"] = "removed"
            record["removed_at"] = now
            state_nodes[fingerprint] = record

    pool = {
        "schema_version": 1,
        "generated_at": now,
        "proxies": proxies,
        "nodes": manifest_nodes,
    }
    queue = {
        "schema_version": 1,
        "generated_at": now,
        "proxies": queue_proxies,
        "nodes": queue_nodes,
    }
    next_state = {
        "schema_version": 1,
        "updated_at": now,
        "nodes": state_nodes,
    }
    return pool, queue, next_state
