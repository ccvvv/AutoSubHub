#!/usr/bin/env python3
"""Build and verify a public Clash/Mihomo subscription using the Mihomo core."""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urlsplit, quote

import requests
import yaml

USER_AGENT = "AutoSubHub/2.0 (+https://github.com/ccvvv/AutoSubHub)"
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "3") or "3")
HTTP_TIMEOUT_MS = int(os.getenv("HTTP_TEST_TIMEOUT", "8000") or "8000")
WORKERS = max(1, int(os.getenv("MAX_WORKERS", "80") or "80"))
HTTP_WORKERS = max(1, int(os.getenv("HTTP_WORKERS", "60") or "60"))
SOURCE_WORKERS = max(1, int(os.getenv("SOURCE_WORKERS", "10") or "10"))
DOWNLOAD_BYTES = max(524288, int(os.getenv("DOWNLOAD_BYTES", "1048576") or "1048576"))
SPEED_PREFILTER_BYTES = max(65536, int(os.getenv("SPEED_PREFILTER_BYTES", "262144") or "262144"))
DOWNLOAD_MIN_KBPS = max(1, int(os.getenv("DOWNLOAD_MIN_KBPS", "500") or "500"))
LATENCY_URL = os.getenv("LATENCY_URL", "https://www.gstatic.com/generate_204")
DOWNLOAD_URL = os.getenv("DOWNLOAD_URL", "https://speed.cloudflare.com/__down?bytes=" + str(DOWNLOAD_BYTES))
CONTROLLER = "http://127.0.0.1:9090"
SPEED_TEST_WORKERS = max(1, min(50, int(os.getenv("SPEED_TEST_WORKERS", "50") or "50")))
SPEED_PORT_BASE = 17890


def decode_subscription(text: str) -> str:
    text = text.strip().lstrip("\ufeff")
    if not text:
        return text
    if not re.search(r"(?m)^\s*(proxies:|[- ]?(vmess|vless|trojan|ss|socks5)://)", text, re.I):
        compact = re.sub(r"\s+", "", text)
        if compact and re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
            try:
                decoded = base64.urlsafe_b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8")
                if "://" in decoded or "proxies:" in decoded:
                    return decoded
            except Exception:
                pass
    return text


def vmess_from_uri(uri: str) -> dict:
    raw = uri.split("://", 1)[1].split("#", 1)[0]
    obj = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8"))
    node = {"name": obj.get("ps") or obj.get("name") or "VMess", "type": "vmess",
            "server": obj["add"], "port": int(obj["port"]), "uuid": obj["id"],
            "alterId": int(obj.get("aid", 0)), "cipher": obj.get("scy") or "auto", "udp": True}
    if obj.get("tls") == "tls":
        node["tls"] = True
        if obj.get("sni"):
            node["servername"] = obj["sni"]
    net = obj.get("net", "tcp")
    if net == "ws":
        node["network"] = "ws"
        node["ws-opts"] = {"path": obj.get("path") or "/"}
        if obj.get("host"):
            node["ws-opts"]["headers"] = {"Host": obj["host"]}
    elif net == "grpc":
        node["network"] = "grpc"
        if obj.get("path"):
            node["grpc-opts"] = {"grpc-service-name": obj["path"]}
    return node


def parse_uri(uri: str) -> dict | None:
    uri = uri.strip()
    scheme = uri.split("://", 1)[0].lower()
    if scheme == "vmess":
        return vmess_from_uri(uri)
    p = urlsplit(uri)
    if scheme not in {"vless", "trojan", "ss", "socks", "socks5"}:
        return None
    host, port = p.hostname, p.port
    if not host or not port:
        return None
    name = unquote(p.fragment) if p.fragment else scheme.upper()
    query = parse_qs(p.query)
    q = lambda key, default="": query.get(key, [default])[0]
    if scheme in {"socks", "socks5"}:
        node = {"name": name, "type": "socks5", "server": host, "port": port, "udp": True}
        if p.username:
            node["username"] = unquote(p.username)
        if p.password:
            node["password"] = unquote(p.password)
        return node
    if scheme == "ss":
        userinfo, password, method = p.username or "", p.password or "", ""
        if not password and userinfo:
            try:
                decoded = base64.urlsafe_b64decode(userinfo + "=" * (-len(userinfo) % 4)).decode()
            except Exception:
                decoded = userinfo
            if ":" in decoded:
                method, password = decoded.split(":", 1)
        else:
            method, password = unquote(userinfo), unquote(password)
        if not method or not password:
            return None
        return {"name": name, "type": "ss", "server": host, "port": port,
                "cipher": method, "password": password, "udp": True}
    if scheme == "trojan":
        node = {"name": name, "type": "trojan", "server": host, "port": port,
                "password": unquote(p.username or ""), "udp": True}
    else:
        node = {"name": name, "type": "vless", "server": host, "port": port,
                "uuid": unquote(p.username or ""), "udp": True}
        if q("flow"):
            node["flow"] = q("flow")
    security = q("security")
    if security in {"tls", "reality"} or q("tls") == "1":
        node["tls"] = True
        node["servername"] = q("sni") or q("serverName") or host
        if q("alpn"):
            node["alpn"] = q("alpn").split(",")
        if q("fp"):
            node["client-fingerprint"] = q("fp")
        if security == "reality":
            node["reality-opts"] = {"public-key": q("pb"), "short-id": q("sid")}
    network = q("type", "tcp")
    if network == "ws":
        node["network"] = "ws"
        ws = {"path": unquote(q("path", "/"))}
        if q("host"):
            ws["headers"] = {"Host": q("host")}
        node["ws-opts"] = ws
    elif network == "grpc":
        node["network"] = "grpc"
        node["grpc-opts"] = {"grpc-service-name": q("serviceName")}
    return node


def parse_source(text: str) -> tuple[list[dict], int]:
    text = decode_subscription(text)
    try:
        data = yaml.safe_load(text)
    except Exception:
        data = None
    if isinstance(data, dict) and isinstance(data.get("proxies"), list):
        # Normalize common YAML variations and discard incomplete/unsupported entries
        # before they reach TCP and Mihomo testing.
        valid_types = {"ss", "vmess", "vless", "trojan", "socks5", "snell",
                       "hysteria", "hysteria2", "tuic", "wireguard", "ssh", "anytls"}
        nodes = []
        http_rejected = 0
        for raw in data["proxies"]:
            if not isinstance(raw, dict) or not raw.get("server"):
                continue
            node = dict(raw)
            try:
                node["port"] = int(node.get("port"))
            except (TypeError, ValueError):
                continue
            node["type"] = str(node.get("type", "")).lower()
            if node["type"] == "http":
                http_rejected += 1
                continue
            if node["type"] not in valid_types or not 1 <= node["port"] <= 65535:
                continue
            nodes.append(node)
        return nodes, http_rejected
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            node = parse_uri(line)
            if node:
                nodes.append(node)
        except Exception as exc:
            print(f"Skip malformed URI: {type(exc).__name__}")
    return nodes, 0


def node_key(node: dict) -> tuple:
    return tuple(str(node.get(k, "")) for k in
                 ("type", "server", "port", "uuid", "password", "cipher", "username"))


UDP_PROTOCOLS = {"hysteria", "hysteria2", "tuic", "wireguard"}
REALITY_PROTOCOLS = {"vless"}


def tcp_check(node: dict) -> tuple[dict, float | None]:
    # UDP-based protocols must not be rejected by a TCP port probe.
    if str(node.get("type", "")).lower() in UDP_PROTOCOLS:
        return node, 0.0
    try:
        start = time.perf_counter()
        with socket.create_connection((str(node["server"]), int(node["port"])), timeout=CONNECT_TIMEOUT):
            return node, round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        return node, None


def protocol_handshake_check(node: dict) -> tuple[dict, bool, str]:
    """FreeNodePanel-style TCP -> TLS -> WebSocket/HTTPUpgrade handshake.

    Reality and UDP transports are deliberately left to Mihomo's native
    protocol implementation to avoid false negatives from a generic TLS/TCP probe.
    """
    kind = str(node.get("type", "")).lower()
    if kind in UDP_PROTOCOLS:
        return node, True, "native-udp"
    reality = isinstance(node.get("reality-opts"), dict) and bool(node.get("reality-opts"))
    if kind in REALITY_PROTOCOLS and reality:
        return node, True, "reality-deferred-to-mihomo"

    host = str(node.get("server", ""))
    port = int(node.get("port", 0) or 0)
    if not host or not 1 <= port <= 65535:
        return node, False, "invalid-endpoint"

    use_tls = bool(node.get("tls")) or kind == "trojan"
    network = str(node.get("network", "tcp")).lower()
    is_ws = network in {"ws", "httpupgrade"}
    ws_options = node.get("ws-opts", {}) if network == "ws" else node.get("http-upgrade-opts", {})
    if not isinstance(ws_options, dict):
        ws_options = {}
    path = str(ws_options.get("path") or "/")
    headers = ws_options.get("headers", {})
    if not isinstance(headers, dict):
        headers = {}
    ws_host = str(headers.get("Host") or headers.get("host") or node.get("servername") or host)

    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception:
        return node, False, "dns-failed"
    if not addresses:
        return node, False, "dns-empty"

    last_error = "connect-failed"
    for family, socktype, proto, _, sockaddr in addresses:
        raw = None
        try:
            raw = socket.socket(family, socktype, proto)
            raw.settimeout(max(CONNECT_TIMEOUT, 4.0))
            raw.connect(sockaddr)
            conn = raw
            if use_tls:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                sni = str(node.get("servername") or host)
                conn = context.wrap_socket(raw, server_hostname=sni)
                raw = None  # wrapped socket now owns the file descriptor
            if is_ws:
                request = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {ws_host}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                )
                conn.sendall(request.encode("utf-8"))
                response = b""
                while b"\r\n" not in response and len(response) < 4096:
                    chunk = conn.recv(512)
                    if not chunk:
                        break
                    response += chunk
                status = response.split(b"\r\n", 1)[0]
                if b" 101 " not in status and not status.endswith(b" 101"):
                    last_error = "websocket-upgrade-rejected"
                    conn.close()
                    continue
                conn.close()
            elif use_tls:
                conn.close()
            else:
                conn.close()
            return node, True, "tcp-tls-ws-ok"
        except ssl.SSLError:
            last_error = "tls-handshake-failed"
        except Exception:
            last_error = "connect-or-upgrade-failed"
        finally:
            if raw is not None:
                try:
                    raw.close()
                except Exception:
                    pass
    return node, False, last_error


def unique_names(nodes: list[dict]) -> list[dict]:
    used, result = set(), []
    for i, source in enumerate(nodes, 1):
        node = dict(source)
        base = str(node.get("name") or node.get("server") or f"node-{i}")[:55]
        name, suffix = base, 2
        while name in used:
            name = f"{base[:48]}-{suffix}"
            suffix += 1
        used.add(name)
        node["name"] = name
        result.append(node)
    return result


def test_config(proxies: list[dict], speed_workers: int) -> dict:
    names = [p["name"] for p in proxies]
    groups = [
        {"name": f"TEST-{worker:02d}", "type": "select", "proxies": names}
        for worker in range(speed_workers)
    ]
    listeners = [
        {"name": f"speed-{worker:02d}", "type": "mixed",
         "port": SPEED_PORT_BASE + worker, "listen": "127.0.0.1",
         "proxy": f"TEST-{worker:02d}"}
        for worker in range(speed_workers)
    ]
    return {
        "allow-lan": False, "mode": "rule",
        "log-level": "error", "ipv6": False,
        "external-controller": "127.0.0.1:9090", "secret": "autohub-local-test",
        "proxies": proxies, "proxy-groups": groups, "listeners": listeners,
        "rules": ["MATCH,DIRECT"],
    }


def final_config(proxies: list[dict]) -> dict:
    names = [p["name"] for p in proxies]
    return {
        "mixed-port": 7890, "allow-lan": False, "mode": "rule",
        "log-level": "warning", "ipv6": False, "proxies": proxies,
        "proxy-groups": [
            {"name": "AUTO", "type": "url-test", "proxies": names,
             "url": LATENCY_URL, "interval": 300, "tolerance": 80},
            {"name": "手动选择", "type": "select", "proxies": ["AUTO"] + names},
        ],
        "rules": ["MATCH,手动选择"],
    }


def write_yaml(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


def validate_structure(config: dict) -> None:
    proxies = config.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise RuntimeError("Final config has no proxies")
    names = [p.get("name") for p in proxies]
    if len(names) != len(set(names)) or any(not n for n in names):
        raise RuntimeError("Proxy names are missing or duplicated")
    valid_types = {"ss", "vmess", "vless", "trojan", "socks5", "snell", "hysteria",
                   "hysteria2", "tuic", "wireguard", "ssh", "anytls"}
    for p in proxies:
        if p.get("type") not in valid_types or not p.get("server") or not isinstance(p.get("port"), int):
            raise RuntimeError(f"Invalid proxy definition: {p.get('name', '(unnamed)')}")
    available = set(names) | {"DIRECT", "REJECT"}
    for group in config.get("proxy-groups", []):
        missing = set(group.get("proxies", [])) - available - {g.get("name") for g in config["proxy-groups"]}
        if missing:
            raise RuntimeError(f"Proxy group {group.get('name')} references missing entries: {sorted(missing)[:5]}")
    for rule in config.get("rules", []):
        target = rule.rsplit(",", 1)[-1]
        if target not in available and target not in {g.get("name") for g in config.get("proxy-groups", [])}:
            raise RuntimeError(f"Rule references missing group: {target}")


def main() -> None:
    configured = os.getenv("SUBSCRIPTION_URLS", "").strip()
    if configured:
        urls = [x.strip() for x in configured.splitlines() if x.strip()]
        source_mode = "Actions secret"
    else:
        with open("subscriptions.txt", encoding="utf-8") as f:
            urls = [x.strip() for x in f if x.strip() and not x.lstrip().startswith("#")]
        source_mode = "subscriptions.txt"
    if not urls:
        raise SystemExit("No subscription sources configured")
    print(f"Loaded {len(urls)} sources from {source_mode}")

    source_nodes = [[] for _ in urls]
    successes = 0
    http_type_rejected = 0

    def fetch_source(item: tuple[int, str]) -> tuple[int, list[dict], int, str | None]:
        index, url = item
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
            response.raise_for_status()
            parsed, rejected_http = parse_source(response.text)
            return index, parsed, rejected_http, None
        except Exception as exc:
            return index, [], 0, f"{type(exc).__name__}: {exc}"

    print(f"Fetching {len(urls)} subscription sources with {min(SOURCE_WORKERS, len(urls))} workers")
    with ThreadPoolExecutor(max_workers=min(SOURCE_WORKERS, len(urls))) as pool:
        futures = [pool.submit(fetch_source, item) for item in enumerate(urls, 1)]
        for future in as_completed(futures):
            index, parsed, rejected_http, error = future.result()
            source_nodes[index - 1] = parsed
            http_type_rejected += rejected_http
            if error is None:
                print(f"Source {index}: {len(parsed)} usable nodes; excluded {rejected_http} pure HTTP proxies")
                successes += 1
            else:
                print(f"Source {index} failed: {error}")
    if successes == 0:
        raise RuntimeError("All subscription sources failed; refusing to publish")

    # Candidate generation only: GitHub performs inexpensive parsing/deduplication.
    # Domestic reachability, TLS validation and download speed are delegated to the phone probe.
    unique = []
    seen = set()
    parsed_total = 0
    for bucket in source_nodes:
        parsed_total += len(bucket)
        for node in bucket:
            key = node_key(node)
            if key in seen:
                continue
            seen.add(key)
            unique.append(node)

    candidates = unique_names(unique)
    if not candidates:
        raise RuntimeError("No supported non-HTTP nodes parsed; refusing to publish candidates")

    os.makedirs("candidates", exist_ok=True)
    candidate_path = os.path.join("candidates", "candidates.yaml")
    write_yaml(candidate_path, {"proxies": candidates})

    # Stable IDs are hashes of canonical node definitions; credentials are never written
    # into the manifest. The candidate YAML itself may contain credentials and must be
    # distributed only through an access-controlled channel.
    import hashlib
    manifest_nodes = []
    for node in candidates:
        canonical = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        node_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
        manifest_nodes.append({"node_id": node_id, "name": node.get("name", "")})

    manifest = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_sources": len(urls),
        "successful_sources": successes,
        "parsed_nodes": parsed_total,
        "unique_candidates": len(candidates),
        "excluded_http_proxies": http_type_rejected,
        "nodes": manifest_nodes,
    }
    with open(os.path.join("candidates", "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Candidate generation complete: {len(candidates)} unique nodes")
    print(f"Pure HTTP proxies excluded during parsing: {http_type_rejected}")
    print(f"Candidate files: {candidate_path}, candidates/manifest.json")


if __name__ == "__main__":
    main()
