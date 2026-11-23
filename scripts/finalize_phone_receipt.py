#!/usr/bin/env python3
"""Validate a phone probe receipt and build a phone-verified final subscription."""
import hashlib, json, os, sys, yaml

MIN_SPEED_BPS = int(os.getenv("MIN_SPEED_BPS", "200000"))
HIGH_SPEED_BPS = int(os.getenv("HIGH_SPEED_BPS", "500000"))
REQUIRED_APPLICATIONS = ("youtube", "telegram", "google", "chatgpt", "github")
candidate_path = sys.argv[1]
manifest_path = sys.argv[2]
receipt_path = sys.argv[3]
out_path = sys.argv[4]

with open(candidate_path, encoding="utf-8") as f:
    candidate = yaml.safe_load(f) or {}
with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)
with open(receipt_path, encoding="utf-8") as f:
    receipt = json.load(f)

with open(candidate_path, "rb") as f:
    actual_hash = hashlib.sha256(f.read()).hexdigest()
candidate_commit = receipt.get("candidate_commit")
if not isinstance(candidate_commit, str) or len(candidate_commit) != 40 or any(c not in "0123456789abcdef" for c in candidate_commit):
    raise SystemExit("Receipt candidate_commit is missing or invalid; refusing to publish")
if receipt.get("candidate_sha256") != actual_hash or manifest.get("candidate_sha256") != actual_hash:
    raise SystemExit("Receipt/candidate hash mismatch; refusing to publish")
if receipt.get("status") != "completed":
    raise SystemExit("Phone receipt is not marked completed; refusing to publish")
queue_count = receipt.get("queue_candidate_count")
receipt_count = receipt.get("receipt_candidate_count")
if queue_count is not None or receipt_count is not None:
    if (type(queue_count) is not int or type(receipt_count) is not int
            or queue_count < 1 or receipt_count < 1
            or queue_count != receipt_count):
        raise SystemExit("Receipt covers only part of the cumulative queue; refusing to publish")

proxies = candidate.get("proxies")
nodes = manifest.get("nodes")
results = receipt.get("results")
if not isinstance(proxies, list) or not isinstance(nodes, list) or not isinstance(results, list):
    raise SystemExit("Invalid candidate or receipt structure")

proxy_by_name = {p.get("name"): p for p in proxies if isinstance(p, dict) and p.get("name")}
id_to_name = {n.get("node_id"): n.get("name") for n in nodes if isinstance(n, dict)}
if (len(id_to_name) != len(nodes) or len(set(id_to_name.values())) != len(nodes)
        or set(id_to_name.values()) != set(proxy_by_name)):
    raise SystemExit("Manifest does not exactly match candidate nodes")

passed_ids = set()
seen = set()
for item in results:
    if not isinstance(item, dict):
        raise SystemExit("Malformed receipt result")
    node_id = item.get("node_id")
    if not isinstance(node_id, str) or node_id not in id_to_name or node_id in seen:
        raise SystemExit("Unknown or duplicate node_id in receipt")
    seen.add(node_id)
    speed = item.get("download_speed_bps")
    checks = item.get("application_checks")
    apps_passed = (isinstance(checks, dict) and all(
        isinstance(checks.get(app), dict) and checks[app].get("status") == "passed"
        for app in REQUIRED_APPLICATIONS
    ))
    if (item.get("status") == "passed" and apps_passed
            and isinstance(speed, (int, float)) and speed >= MIN_SPEED_BPS):
        passed_ids.add(node_id)

if seen != set(id_to_name):
    missing = len(set(id_to_name) - seen)
    raise SystemExit(f"Incomplete receipt: {missing} candidate nodes were not tested")
if receipt_count is not None and receipt_count != len(seen):
    raise SystemExit("Receipt tested-node count does not match its results")

final_nodes = [proxy_by_name[id_to_name[nid]] for nid in id_to_name if nid in passed_ids]
if not final_nodes:
    raise SystemExit("No phone-tested nodes met the minimum download speed; existing final versions remain untouched")

# Keep one subscription, with speed categories exposed as selectable proxy groups.
high_nodes = []
normal_nodes = []
for node in final_nodes:
    speed = next(item["download_speed_bps"] for item in results
                 if item.get("node_id") in id_to_name
                 and id_to_name[item["node_id"]] == node["name"])
    tagged = dict(node)
    if speed >= HIGH_SPEED_BPS:
        tagged["name"] = f'{node["name"]} [高速]'
        high_nodes.append(tagged)
    else:
        tagged["name"] = f'{node["name"]} [普通]'
        normal_nodes.append(tagged)

published_nodes = high_nodes + normal_nodes
node_names = [node["name"] for node in published_nodes]
groups = []
if high_nodes:
    groups.append({"name": "高速节点", "type": "select",
                   "proxies": [node["name"] for node in high_nodes]})
if normal_nodes:
    groups.append({"name": "普通节点", "type": "select",
                   "proxies": [node["name"] for node in normal_nodes]})
groups.extend([
    {"name": "AUTO", "type": "url-test", "proxies": node_names,
     "url": "https://www.gstatic.com/generate_204", "interval": 300, "tolerance": 80},
    {"name": "手动选择", "type": "select",
     "proxies": ["AUTO"] + [group["name"] for group in groups] + node_names},
])
final_config = {
    "mixed-port": 7890,
    "allow-lan": False,
    "mode": "rule",
    "log-level": "warning",
    "ipv6": False,
    "proxies": published_nodes,
    "proxy-groups": groups,
    "rules": ["MATCH,手动选择"],
}

with open(out_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(final_config, f, allow_unicode=True, sort_keys=False)

print(f"Phone receipt accepted: {len(published_nodes)}/{len(proxies)} nodes; high={len(high_nodes)}, normal={len(normal_nodes)}, rejected={len(proxies)-len(published_nodes)}; all five app gates passed and speed >= {MIN_SPEED_BPS} B/s")
