#!/usr/bin/env python3
"""Generate V2RayTun import artifacts alongside each cloud candidate batch."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

import yaml

from tools.v2raytun_bridge import convert


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="candidates/candidates.yaml")
    parser.add_argument("--manifest", default="candidates/manifest.json")
    parser.add_argument("--output-dir", default="candidates")
    args = parser.parse_args()

    candidate_path = Path(args.candidate)
    manifest_path = Path(args.manifest)
    output_dir = Path(args.output_dir)
    candidate_bytes = candidate_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(candidate_bytes).hexdigest()
    if manifest.get("candidate_sha256") != digest:
        raise SystemExit("candidate YAML / manifest SHA256 mismatch")

    candidate = yaml.safe_load(candidate_bytes) or {}
    proxies = candidate.get("proxies")
    nodes = manifest.get("nodes")
    if not isinstance(proxies, list) or not isinstance(nodes, list):
        raise SystemExit("invalid candidate or manifest structure")
    ids = {item.get("name"): item.get("node_id") for item in nodes if isinstance(item, dict)}
    if len(ids) != len(nodes) or len(ids) != len(proxies):
        raise SystemExit("candidate / manifest node count mismatch")
    if set(ids) != {item.get("name") for item in proxies}:
        raise SystemExit("candidate / manifest names do not match")
    if any(not isinstance(value, str) or not value for value in ids.values()):
        raise SystemExit("manifest contains missing node_id")

    links: list[str] = []
    mapping: list[dict] = []
    skipped: list[dict] = []
    for node in proxies:
        uri, reason = convert(node)
        row = {
            "name": node.get("name", ""),
            "node_id": ids[node["name"]],
            "protocol": str(node.get("type") or "").lower(),
        }
        if uri:
            links.append(uri)
            mapping.append({**row, "line": len(links)})
        else:
            skipped.append({**row, "reason": reason})

    if not links:
        raise SystemExit("no supported nodes; refusing to publish empty V2RayTun subscription")

    output_dir.mkdir(parents=True, exist_ok=True)
    plain = "\n".join(links) + "\n"
    encoded = base64.b64encode(plain.encode("utf-8")).decode("ascii") + "\n"
    (output_dir / "v2raytun-sub.txt").write_text(encoded, encoding="utf-8")
    (output_dir / "v2raytun-links.txt").write_text(plain, encoding="utf-8")
    report = {
        "schema_version": 1,
        "candidate_sha256": digest,
        "candidate_count": len(proxies),
        "exported_count": len(links),
        "skipped_count": len(skipped),
        "nodes": mapping,
        "skipped": skipped,
    }
    (output_dir / "v2raytun-map.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"V2RayTun export: candidates={len(proxies)}, "
        f"exported={len(links)}, skipped={len(skipped)}, sha256={digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
