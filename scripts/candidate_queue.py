"""Helpers for merging immutable phone-probe candidate batches."""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict


def canonical_fingerprint(proxy: dict) -> str:
    """Identify a proxy by its configuration, excluding its display name."""
    config = {key: value for key, value in proxy.items() if key != "name"}
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def merge_candidate_snapshots(snapshots: list[tuple[str, dict, dict]]) -> tuple[dict, dict, list[str]]:
    """Merge (commit, candidate, manifest) snapshots; retain first name and all provenance."""
    merged = OrderedDict()
    commits = []
    for commit, candidate, manifest in snapshots:
        if commit not in commits:
            commits.append(commit)
        proxies = candidate.get("proxies")
        nodes = manifest.get("nodes")
        if not isinstance(proxies, list) or not isinstance(nodes, list):
            raise ValueError(f"Invalid candidate snapshot at {commit}")
        by_name = {item.get("name"): item for item in proxies
                   if isinstance(item, dict) and isinstance(item.get("name"), str)}
        if len(by_name) != len(proxies):
            raise ValueError(f"Invalid or duplicate proxy names at {commit}")
        manifest_by_name = {item.get("name"): item for item in nodes
                            if isinstance(item, dict) and isinstance(item.get("name"), str)}
        if len(manifest_by_name) != len(nodes) or set(manifest_by_name) != set(by_name):
            raise ValueError(f"Manifest does not match candidate at {commit}")
        for name, proxy in by_name.items():
            fingerprint = canonical_fingerprint(proxy)
            entry = merged.setdefault(fingerprint, {
                "proxy": dict(proxy), "source_commits": [], "source_names": [],
            })
            if commit not in entry["source_commits"]:
                entry["source_commits"].append(commit)
            if name not in entry["source_names"]:
                entry["source_names"].append(name)

    proxies = []
    nodes = []
    for fingerprint, entry in merged.items():
        proxy = entry["proxy"]
        proxies.append(proxy)
        nodes.append({
            "node_id": fingerprint,
            "name": proxy["name"],
            "source_commits": entry["source_commits"],
            "source_names": entry["source_names"],
        })
    return {"proxies": proxies}, {"nodes": nodes}, commits
