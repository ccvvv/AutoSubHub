"""Pure helpers for maintaining the single persistent phone-test candidate pool.

The published candidate files remain the client-facing snapshot; callers persist the
returned pool separately and publish a snapshot derived from it.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict


def node_fingerprint(proxy: dict) -> str:
    config = {key: value for key, value in proxy.items() if key != "name"}
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def node_id(proxy: dict) -> str:
    return node_fingerprint(proxy)[:20]


def merge_pool(pool: dict, incoming: dict) -> dict:
    """Merge incoming proxies into pool by full config fingerprint; keep first names."""
    merged = OrderedDict()
    for source in (pool, incoming):
        proxies = source.get("proxies")
        if not isinstance(proxies, list):
            raise ValueError("Pool/candidate must contain a proxies list")
        for proxy in proxies:
            if not isinstance(proxy, dict) or not proxy.get("name"):
                raise ValueError("Pool contains an invalid or unnamed proxy")
            fingerprint = node_fingerprint(proxy)
            merged.setdefault(fingerprint, dict(proxy))

    result = []
    used_names = set()
    for fingerprint, source_proxy in merged.items():
        proxy = dict(source_proxy)
        name = str(proxy["name"])
        if name in used_names:
            name = f"{name} [{fingerprint[:6]}]"
        if name in used_names:
            raise ValueError("Unable to create unique proxy names")
        proxy["name"] = name
        used_names.add(name)
        result.append(proxy)
    return {"proxies": result}


def build_manifest(candidate: dict, generated_at: str) -> dict:
    proxies = candidate.get("proxies")
    if not isinstance(proxies, list):
        raise ValueError("Candidate must contain a proxies list")
    names = [p.get("name") for p in proxies if isinstance(p, dict)]
    if len(names) != len(proxies) or any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError("Candidate proxy names must be valid and unique")
    ids = [node_id(p) for p in proxies]
    if len(set(ids)) != len(ids):
        raise ValueError("Node ID collision; refusing to publish")
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "nodes": [{"node_id": node_id(p), "name": p["name"]} for p in proxies],
    }


def remove_tested_nodes(pool: dict, tested_ids: set[str]) -> dict:
    """Remove only IDs explicitly listed by a fully validated receipt."""
    proxies = pool.get("proxies")
    if not isinstance(proxies, list):
        raise ValueError("Pool must contain a proxies list")
    current_ids = {node_id(p) for p in proxies}
    unknown = tested_ids - current_ids
    if unknown:
        raise ValueError("Receipt contains IDs not present in the current pool")
    return {"proxies": [p for p in proxies if node_id(p) not in tested_ids]}
