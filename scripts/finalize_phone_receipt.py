#!/usr/bin/env python3
"""Validate a phone probe receipt and build a phone-verified final subscription."""
import base64, hashlib, json, os, sys, yaml
from urllib.parse import parse_qs, quote, urlencode, urlparse

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

proxies = candidate.get("proxies")
nodes = manifest.get("nodes")
results = receipt.get("results")
max_download_bytes = receipt.get("max_download_bytes")
legacy_download_metadata = max_download_bytes is None
if legacy_download_metadata:
    # Older phone clients recorded the exact received byte count per node but
    # did not repeat the requested size in the receipt. Recover that expectation
    # only from the known Cloudflare download URL; still require a full byte match.
    parsed_download_url = urlparse(str(receipt.get("download_url") or ""))
    requested_sizes = parse_qs(parsed_download_url.query).get("bytes", [])
    if (parsed_download_url.scheme != "https"
            or parsed_download_url.hostname != "speed.cloudflare.com"
            or len(requested_sizes) != 1 or not requested_sizes[0].isdigit()):
        raise SystemExit("Legacy receipt has no trustworthy download-size metadata")
    max_download_bytes = int(requested_sizes[0])
if (not isinstance(proxies, list) or not isinstance(nodes, list) or not isinstance(results, list)
        or not isinstance(max_download_bytes, int) or isinstance(max_download_bytes, bool)
        or max_download_bytes <= 0):
    raise SystemExit("Invalid candidate, receipt, or download-integrity metadata")

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
    downloaded_bytes = item.get("downloaded_bytes")
    checks = item.get("application_checks")
    apps_passed = (isinstance(checks, dict) and all(
        isinstance(checks.get(app), dict) and checks[app].get("status") == "passed"
        for app in REQUIRED_APPLICATIONS
    ))
    expected_bytes_match = (
        item.get("expected_download_bytes", max_download_bytes) == max_download_bytes
        if legacy_download_metadata else item.get("expected_download_bytes") == max_download_bytes
    )
    download_complete = (
        isinstance(downloaded_bytes, int) and not isinstance(downloaded_bytes, bool)
        and downloaded_bytes == max_download_bytes
        and expected_bytes_match
    )
    if (item.get("status") == "passed" and apps_passed and download_complete
            and isinstance(speed, (int, float)) and speed >= MIN_SPEED_BPS):
        passed_ids.add(node_id)

if seen != set(id_to_name):
    missing = len(set(id_to_name) - seen)
    raise SystemExit(f"Incomplete receipt: {missing} candidate nodes were not tested")

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

def build_v2raytun_subscription(nodes):
    """Encode supported final nodes as a base64 V2Ray share-link subscription."""
    links = []
    for node in nodes:
        kind = str(node.get("type", "")).lower()
        name = str(node.get("name") or node.get("server") or kind)
        server = str(node.get("server") or "")
        port = int(node.get("port") or 0)
        if not server or not 1 <= port <= 65535:
            continue
        tag = quote(name, safe="")
        try:
            if kind == "vmess":
                obj = {
                    "v": "2", "ps": name, "add": server, "port": str(port),
                    "id": str(node.get("uuid") or ""), "aid": str(node.get("alterId", 0)),
                    "scy": str(node.get("cipher") or "auto"),
                    "net": str(node.get("network") or "tcp"),
                    "type": "none", "host": "", "path": "", "tls": "tls" if node.get("tls") else "",
                    "sni": str(node.get("servername") or ""),
                }
                ws = node.get("ws-opts") or {}
                if isinstance(ws, dict):
                    obj["path"] = str(ws.get("path") or "")
                    headers = ws.get("headers") or {}
                    if isinstance(headers, dict):
                        obj["host"] = str(headers.get("Host") or headers.get("host") or "")
                raw = base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")
                links.append("vmess://" + raw)
            elif kind == "ss":
                method, password = str(node.get("cipher") or ""), str(node.get("password") or "")
                if not method or not password:
                    continue
                userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
                links.append(f"ss://{userinfo}@{server}:{port}#{tag}")
            elif kind in ("vless", "trojan"):
                credential = str((node.get("uuid") if kind == "vless" else node.get("password")) or "")
                if not credential:
                    continue
                params = {}
                network = str(node.get("network") or "tcp")
                if network and network != "tcp":
                    params["type"] = network
                if node.get("tls") or kind == "trojan":
                    params["security"] = "tls"
                    if node.get("servername"):
                        params["sni"] = str(node["servername"])
                    if node.get("client-fingerprint"):
                        params["fp"] = str(node["client-fingerprint"])
                    alpn = node.get("alpn")
                    if isinstance(alpn, list) and alpn:
                        params["alpn"] = ",".join(map(str, alpn))
                if kind == "vless" and node.get("flow"):
                    params["flow"] = str(node["flow"])
                if network == "ws":
                    ws = node.get("ws-opts") or {}
                    if isinstance(ws, dict):
                        if ws.get("path"):
                            params["path"] = str(ws["path"])
                        headers = ws.get("headers") or {}
                        if isinstance(headers, dict) and (headers.get("Host") or headers.get("host")):
                            params["host"] = str(headers.get("Host") or headers.get("host"))
                elif network == "grpc":
                    grpc = node.get("grpc-opts") or {}
                    if isinstance(grpc, dict) and grpc.get("grpc-service-name"):
                        params["serviceName"] = str(grpc["grpc-service-name"])
                if kind == "vless":
                    reality = node.get("reality-opts") or {}
                    if isinstance(reality, dict) and reality:
                        params["security"] = "reality"
                        if reality.get("public-key"):
                            params["pb"] = str(reality["public-key"])
                        if reality.get("short-id"):
                            params["sid"] = str(reality["short-id"])
                query = urlencode(params, quote_via=quote)
                scheme = kind
                links.append(f"{scheme}://{quote(credential, safe='')}@{server}:{port}" + (f"?{query}" if query else "") + f"#{tag}")
            elif kind == "socks5":
                username = str(node.get("username") or "")
                password = str(node.get("password") or "")
                auth = f"{quote(username, safe='')}:{quote(password, safe='')}@" if username else ""
                links.append(f"socks5://{auth}{server}:{port}#{tag}")
        except (TypeError, ValueError, KeyError):
            continue
    payload = "\n".join(links)
    return base64.b64encode(payload.encode("utf-8")).decode("ascii") + ("\n" if links else "")


with open(out_path, "w", encoding="utf-8") as f:
    yaml.safe_dump(final_config, f, allow_unicode=True, sort_keys=False)

if len(sys.argv) > 5:
    v2ray_path = sys.argv[5]
    encoded = build_v2raytun_subscription(published_nodes)
    if not encoded.strip():
        raise SystemExit("No nodes supported by V2Ray share-link format; refusing empty output")
    with open(v2ray_path, "w", encoding="utf-8") as f:
        f.write(encoded)
    print(f"V2Ray share-link subscription generated: {len(base64.b64decode(encoded).decode('utf-8').splitlines())} nodes")


print(f"Phone receipt accepted: {len(published_nodes)}/{len(proxies)} nodes; high={len(high_nodes)}, normal={len(normal_nodes)}, rejected={len(proxies)-len(published_nodes)}; all five app gates passed and speed >= {MIN_SPEED_BPS} B/s")
