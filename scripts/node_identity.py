"""Canonical full SHA-256 identity for proxy configurations."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")


def node_fingerprint(proxy: dict[str, Any]) -> str:
    """Hash the complete proxy configuration, excluding its mutable display name."""
    if not isinstance(proxy, dict):
        raise ValueError("Proxy entry must be an object")
    identity = {key: value for key, value in proxy.items() if key != "name"}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_candidate_identity(candidate: dict[str, Any], manifest: dict[str, Any]) -> dict[str, str]:
    """Require every manifest ID and fingerprint to equal its proxy's full SHA-256."""
    proxies = candidate.get("proxies") if isinstance(candidate, dict) else None
    nodes = manifest.get("nodes") if isinstance(manifest, dict) else None
    if not isinstance(proxies, list) or not isinstance(nodes, list) or len(proxies) != len(nodes):
        raise ValueError("Candidate and fingerprint manifest must contain matching node lists")
    proxy_by_name: dict[str, dict[str, Any]] = {}
    for proxy in proxies:
        if not isinstance(proxy, dict) or not isinstance(proxy.get("name"), str) or not proxy["name"]:
            raise ValueError("Candidate contains an invalid or unnamed proxy")
        if proxy["name"] in proxy_by_name:
            raise ValueError("Candidate contains duplicate proxy names")
        proxy_by_name[proxy["name"]] = proxy
    node_by_name: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("name"), str):
            raise ValueError("Fingerprint manifest contains an invalid node")
        if node["name"] in node_by_name:
            raise ValueError("Fingerprint manifest contains duplicate node names")
        node_by_name[node["name"]] = node
    if set(proxy_by_name) != set(node_by_name):
        raise ValueError("Fingerprint manifest does not exactly match candidate proxy names")
    fingerprints: dict[str, str] = {}
    for name, proxy in proxy_by_name.items():
        expected = node_fingerprint(proxy)
        node = node_by_name[name]
        if (node.get("node_id") != expected or node.get("fingerprint") != expected
                or not isinstance(node.get("node_id"), str)
                or not _FINGERPRINT_RE.fullmatch(node["node_id"])):
            raise ValueError(f"Full SHA-256 fingerprint mismatch for candidate node: {name}")
        if expected in fingerprints.values():
            raise ValueError("Candidate contains duplicate full SHA-256 fingerprints")
        fingerprints[name] = expected
    return fingerprints


def is_full_fingerprint(value: Any) -> bool:
    return isinstance(value, str) and _FINGERPRINT_RE.fullmatch(value) is not None
