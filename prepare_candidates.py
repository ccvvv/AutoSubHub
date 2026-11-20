#!/usr/bin/env python3
"""Publish the GitHub-screened subscription as a phone-test candidate feed.

The existing subscription is preserved as the last published final feed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import yaml


SITE = Path("site")
GENERATED = SITE / "subscription.yaml"
CANDIDATES = SITE / "candidates.yaml"
MANIFEST = SITE / "candidate-manifest.json"
PREVIOUS = Path(os.environ.get("PREVIOUS_SUBSCRIPTION", ""))


def stable_node_id(proxy: dict) -> str:
    identity = {key: value for key, value in proxy.items() if key != "name"}
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def main() -> None:
    if not GENERATED.is_file():
        raise SystemExit("build.py did not create site/subscription.yaml")

    config = yaml.safe_load(GENERATED.read_text(encoding="utf-8"))
    proxies = config.get("proxies") if isinstance(config, dict) else None
    if not isinstance(proxies, list) or not proxies:
        raise SystemExit("Generated subscription contains no proxies")

    candidate_config = dict(config)
    candidate_proxies = []
    manifest_nodes = []
    seen_ids = set()

    for proxy in proxies:
        if not isinstance(proxy, dict):
            raise SystemExit("Generated subscription contains an invalid proxy entry")
        node_id = stable_node_id(proxy)
        if node_id in seen_ids:
            raise SystemExit(f"Duplicate stable node ID: {node_id}")
        seen_ids.add(node_id)

        original_name = str(proxy.get("name") or "node")
        display_name = re.sub(r"\s+\[\d+ms\s+[\d.]+KB/s\]$", "", original_name)
        candidate = dict(proxy)
        candidate["name"] = f"probe_{node_id}__{display_name}"
        candidate_proxies.append(candidate)
        manifest_nodes.append({
            "id": node_id,
            "name": display_name,
            "type": str(proxy.get("type", "")),
            "server": str(proxy.get("server", "")),
            "port": int(proxy.get("port", 0)),
        })

    name_map = {
        str(original.get("name")): str(candidate.get("name"))
        for original, candidate in zip(proxies, candidate_proxies)
    }
    for group in candidate_config.get("proxy-groups", []):
        if isinstance(group, dict) and isinstance(group.get("proxies"), list):
            group["proxies"] = [
                name_map.get(str(name), name) for name in group["proxies"]
            ]

    candidate_config["proxies"] = candidate_proxies
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    SITE.mkdir(parents=True, exist_ok=True)
    CANDIDATES.write_text(
        yaml.safe_dump(candidate_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    MANIFEST.write_text(
        json.dumps({
            "schema_version": 1,
            "generated_at": generated_at,
            "candidate_count": len(manifest_nodes),
            "nodes": manifest_nodes,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    index_path = SITE / "index.html"
    if index_path.is_file():
        page = index_path.read_text(encoding="utf-8")
        links = (
            '<p><a href="candidates.yaml">下载国内复测候选订阅</a> · '
            '<a href="candidate-manifest.json">候选节点清单</a></p>'
        )
        page = page.replace("</body>", links + "</body>") if "</body>" in page else page + links
        index_path.write_text(page, encoding="utf-8")

    if PREVIOUS and PREVIOUS.is_file():
        GENERATED.write_bytes(PREVIOUS.read_bytes())
        print("Restored the previous final subscription after publishing candidates.")
    else:
        GENERATED.unlink(missing_ok=True)
        print("No previous final subscription exists; only the candidate feed will be published.")

    print(f"Prepared {len(candidate_proxies)} domestic-probe candidates.")
    print(f"Candidate revision: {generated_at}")


if __name__ == "__main__":
    main()
