#!/usr/bin/env python3
"""Validate a Xiaomi domestic-probe receipt and build a final subscription."""
from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

SITE = Path("site")
DATA = Path("data/domestic-probe")
NODE_TAG = re.compile(r"^probe_([a-f0-9]{24})__(.*)$")


def fail(message: str) -> None:
    raise SystemExit(message)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Cannot read {path}: {exc}")


def main() -> None:
    event_path = Path(os.environ["GITHUB_EVENT_PATH"])
    event = load_json(event_path)
    payload = event.get("client_payload")
    if not isinstance(payload, dict):
        fail("Missing repository_dispatch client_payload")

    revision = payload.get("candidate_revision")
    device_id = payload.get("device_id")
    network = payload.get("network")
    results = payload.get("results")
    if not isinstance(revision, str) or not revision:
        fail("candidate_revision is required")
    if device_id != "mi6-probe-01":
        fail("Unrecognized probe device")
    if network != "mainland-wifi":
        fail("Unexpected probe network")
    if not isinstance(results, list) or not results:
        fail("Receipt results must be a non-empty list")
    if len(results) > 1000:
        fail("Receipt contains too many results")

    manifest = load_json(SITE / "candidate-manifest.json")
    if manifest.get("schema_version") != 1 or manifest.get("generated_at") != revision:
        fail("Receipt candidate revision does not match the current manifest")
    manifest_nodes = manifest.get("nodes")
    if not isinstance(manifest_nodes, list) or not manifest_nodes:
        fail("Candidate manifest is empty or invalid")

    known_ids = set()
    display_names = {}
    for node in manifest_nodes:
        if not isinstance(node, dict):
            fail("Invalid node in candidate manifest")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not re.fullmatch(r"[a-f0-9]{24}", node_id):
            fail("Invalid node ID in candidate manifest")
        if node_id in known_ids:
            fail("Duplicate node ID in candidate manifest")
        known_ids.add(node_id)
        display_names[node_id] = str(node.get("name") or "node")

    config = yaml.safe_load((SITE / "candidates.yaml").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("proxies"), list):
        fail("Candidate profile is invalid")
    candidate_name_to_id = {}
    for proxy in config["proxies"]:
        if not isinstance(proxy, dict):
            fail("Invalid candidate proxy")
        match = NODE_TAG.match(str(proxy.get("name", "")))
        if not match:
            fail("Candidate proxy is missing its stable ID tag")
        node_id, _ = match.groups()
        if node_id not in known_ids or node_id in candidate_name_to_id.values():
            fail("Candidate profile and manifest do not match")
        candidate_name_to_id[proxy["name"]] = node_id
    if set(candidate_name_to_id.values()) != known_ids:
        fail("Candidate profile node IDs differ from the manifest")

    accepted = {}
    for item in results:
        if not isinstance(item, dict):
            fail("Invalid result entry")
        node_id = item.get("node_id")
        status = item.get("status")
        if not isinstance(node_id, str) or node_id not in known_ids:
            fail("Receipt contains an unknown node ID")
        if node_id in accepted:
            fail("Receipt contains duplicate node IDs")
        if status not in ("passed", "failed", "inconclusive"):
            fail("Invalid node test status")
        speed = item.get("speed_kbps")
        latency = item.get("latency_ms")
        if speed is not None and (isinstance(speed, bool) or not isinstance(speed, (int, float))
                                  or not math.isfinite(speed) or speed < 0):
            fail("Invalid speed_kbps value")
        if latency is not None and (isinstance(latency, bool) or not isinstance(latency, (int, float))
                                    or not math.isfinite(latency) or latency < 0):
            fail("Invalid latency_ms value")
        accepted[node_id] = {
            "node_id": node_id,
            "status": status,
            "speed_kbps": speed,
            "latency_ms": latency,
        }

    DATA.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "candidate_revision": revision,
        "device_id": device_id,
        "network": network,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "result_count": len(accepted),
        "results": list(accepted.values()),
    }
    (DATA / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )

    passed_ids = {node_id for node_id, result in accepted.items() if result["status"] == "passed"}
    if not passed_ids:
        print("publish_final=false")
        print("No node passed; the previous final subscription must remain untouched.")
        return

    name_map = {}
    final_proxies = []
    for proxy in config["proxies"]:
        node_id = candidate_name_to_id[proxy["name"]]
        if node_id not in passed_ids:
            continue
        final_proxy = dict(proxy)
        final_name = display_names[node_id]
        name_map[proxy["name"]] = final_name
        final_proxy["name"] = final_name
        final_proxies.append(final_proxy)

    group_names = {
        str(group.get("name"))
        for group in config.get("proxy-groups", [])
        if isinstance(group, dict) and group.get("name")
    }
    for group in config.get("proxy-groups", []):
        if not isinstance(group, dict) or not isinstance(group.get("proxies"), list):
            continue
        kept = []
        for member in group["proxies"]:
            member = str(member)
            if member in name_map:
                kept.append(name_map[member])
            elif member in group_names or not member.startswith("probe_"):
                kept.append(member)
        group["proxies"] = kept

    final_config = dict(config)
    final_config["proxies"] = final_proxies
    (SITE / "subscription.yaml").write_text(
        yaml.safe_dump(final_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"publish_final=true")
    print(f"passed_nodes={len(final_proxies)}")
    print(f"receipt_nodes={len(accepted)}")


if __name__ == "__main__":
    main()
