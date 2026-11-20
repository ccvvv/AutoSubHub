#!/usr/bin/env python3
"""Validate a phone probe receipt and build a phone-verified final subscription."""
import hashlib, json, os, sys, yaml

MIN_SPEED_BPS = int(os.getenv("MIN_SPEED_BPS", "512000"))
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
if receipt.get("candidate_sha256") != actual_hash or manifest.get("candidate_sha256") != actual_hash:
    raise SystemExit("Receipt/candidate hash mismatch; refusing to publish")

proxies = candidate.get("proxies")
nodes = manifest.get("nodes")
results = receipt.get("results")
if not isinstance(proxies, list) or not isinstance(nodes, list) or not isinstance(results, list):
    raise SystemExit("Invalid candidate or receipt structure")

proxy_by_name = {p.get("name"): p for p in proxies if isinstance(p, dict) and p.get("name")}
id_to_name = {n.get("node_id"): n.get("name") for n in nodes if isinstance(n, dict)}
if len(id_to_name) != len(nodes) or set(id_to_name.values()) - set(proxy_by_name):
    raise SystemExit("Manifest does not match candidate nodes")

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
    if item.get("status") == "passed" and isinstance(speed, (int, float)) and speed >= MIN_SPEED_BPS:
        passed_ids.add(node_id)

final_nodes = [proxy_by_name[id_to_name[nid]] for nid in id_to_name if nid in passed_ids]
if not final_nodes:
    raise SystemExit("No phone-tested nodes met the minimum download speed; existing final versions remain untouched")

with open(out_path, "w", encoding="utf-8") as f:
    yaml.safe_dump({"proxies": final_nodes}, f, allow_unicode=True, sort_keys=False)

print(f"Phone receipt accepted: {len(final_nodes)}/{len(proxies)} nodes passed at >= {MIN_SPEED_BPS} B/s")
