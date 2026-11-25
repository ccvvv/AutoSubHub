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

from scripts.source_cache import load_cache, empty_cache, conditional_headers, record_response, save_cache

USER_AGENT = "AutoSubHub/2.0 (+https://github.com/ccvvv/AutoSubHub)"
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "3") or "3")
HTTP_TIMEOUT_MS = int(os.getenv("HTTP_TEST_TIMEOUT", "8000") or "8000")
WORKERS = max(1, int(os.getenv("MAX_WORKERS", "80") or "80"))
HTTP_WORKERS = max(1, int(os.getenv("HTTP_WORKERS", "60") or "60"))
SOURCE_WORKERS = max(1, int(os.getenv("SOURCE_WORKERS", "10") or "10"))
# v2rayNG's real-delay test URL (changed from google.com/generate_204 upstream).
LATENCY_URL = os.getenv("LATENCY_URL", "https://www.gstatic.com/generate_204")
LATENCY_FALLBACK_URL = os.getenv("LATENCY_FALLBACK_URL", "https://www.apple.com/library/test/success.html")
CONTROLLER = "http://127.0.0.1:9090"
PROBE_WORKERS = max(1, min(50, int(os.getenv("CERT_WORKERS", "50") or "50")))
PROBE_PORT_BASE = 17890

SUPPORTED_PROXY_TYPES = {
    "ss", "vmess", "vless", "trojan", "socks5", "http", "snell",
    "hysteria", "hysteria2", "tuic", "wireguard", "ssh", "anytls",
}


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


def valid_reality_short_id(value: str) -> bool:
    """Mihomo REALITY short IDs are empty or 1-8 bytes encoded as even-length hex."""
    return isinstance(value, str) and len(value) <= 16 and len(value) % 2 == 0 and bool(re.fullmatch(r"[0-9a-fA-F]*", value))


def valid_reality_node(node: dict) -> bool:
    """Validate REALITY short-id and omit an empty optional value.

    Mihomo rejects an explicitly present empty short-id on some proxy entries;
    an absent short-id is the correct representation when the source omits it.
    """
    if "reality-opts" not in node:
        return True
    options = node.get("reality-opts")
    # A present but non-mapping reality-opts value is malformed. Previously
    # this slipped through because non-dicts were treated like no options.
    if not isinstance(options, dict):
        return False
    if not options:
        return True
    short_id = options.get("short-id")
    if short_id is None or (isinstance(short_id, str) and not short_id.strip()):
        options.pop("short-id", None)
        return True
    # YAML may parse an unquoted all-digit hex short-id as an integer.
    # Mihomo expects the value to be a string, so normalize before validation.
    if isinstance(short_id, bool):
        return False
    short_id = str(short_id).strip()
    if not valid_reality_short_id(short_id):
        return False
    options["short-id"] = short_id
    return True


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
            if not valid_reality_node(node):
                return None
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
            # Some upstream VMess YAML feeds serialize an absent security
            # option as `security: null`. Mihomo rejects that explicit null;
            # omission correctly selects the protocol default.
            if node["type"] == "vmess":
                if node.get("security") is None:
                    node.pop("security", None)
                # Mihomo reports a null VMess cipher as an unsupported
                # security type. Use the protocol's normal automatic cipher
                # when an upstream YAML feed explicitly supplies null/empty.
                cipher = node.get("cipher")
                if cipher is None or (isinstance(cipher, str) and cipher.strip().lower() in {"", "null", "none"}):
                    node["cipher"] = "auto"
            if node["type"] not in SUPPORTED_PROXY_TYPES or not 1 <= node["port"] <= 65535:
                continue
            if not valid_reality_node(node):
                print(f"Skip node with invalid REALITY short-id: {node.get('name', '(unnamed)')}")
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


def node_key(node: dict) -> str:
    """Fingerprint the complete proxy configuration, excluding its display name.

    Names are labels, not identity. Every other field is retained so that
    TLS/Reality, transport, authentication, plugin and protocol options cannot
    be accidentally merged into a different node.
    """
    identity = {key: value for key, value in node.items() if key != "name"}
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    import hashlib
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


def final_node_fingerprints(data: dict) -> set[str]:
    """Fingerprint only valid proxy nodes, never Clash proxy-group entries."""
    if not isinstance(data, dict) or not isinstance(data.get("proxies"), list):
        raise RuntimeError("Published final subscription is missing a valid proxies list")
    fingerprints = {
        node_key(node) for node in data["proxies"]
        if isinstance(node, dict)
        and str(node.get("type", "")).lower() in SUPPORTED_PROXY_TYPES
        and node.get("server")
        and isinstance(node.get("port"), int)
        and 1 <= node["port"] <= 65535
    }
    if not fingerprints:
        raise RuntimeError("Published final subscription contains no valid proxy nodes")
    return fingerprints


def load_final_node_fingerprints() -> set[str]:
    """Load the currently published final subscription; fail closed on bad data."""
    url = os.getenv(
        "FINAL_SUBSCRIPTION_URL",
        "https://raw.githubusercontent.com/ccvvv/AutoSubHub/published-subscriptions/final/latest.yaml",
    )
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return final_node_fingerprints(yaml.safe_load(response.text))


def exclude_final_nodes(nodes: list[dict], final_fingerprints: set[str]) -> list[dict]:
    """Exclude nodes already present in the current official final subscription."""
    return [node for node in nodes if node_key(node) not in final_fingerprints]


def set_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


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
         "port": PROBE_PORT_BASE + worker, "listen": "127.0.0.1",
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


def find_mihomo_rejected_proxies(proxies: list[dict], mihomo_bin: str) -> list[dict]:
    """Bisect a rejected proxy list to identify entries Mihomo itself rejects."""
    rejected = []

    def check_subset(items: list[dict]) -> bool:
        with tempfile.TemporaryDirectory(prefix="autohub-diagnose-") as tmp:
            path = os.path.join(tmp, "test.yaml")
            write_yaml(path, test_config(items, 1))
            result = subprocess.run(
                [mihomo_bin, "-t", "-f", path],
                capture_output=True, text=True, timeout=45
            )
            return result.returncode == 0

    def inspect(items: list[dict]) -> None:
        if not items or check_subset(items):
            return
        if len(items) == 1:
            rejected.append(items[0])
            return
        middle = len(items) // 2
        inspect(items[:middle])
        inspect(items[middle:])

    inspect(proxies)
    return rejected


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


class QuotedYamlString(str):
    """A string that must stay a YAML string even when it resembles a number."""


class AutoHubSafeDumper(yaml.SafeDumper):
    pass


def _represent_quoted_yaml_string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="'")

 
AutoHubSafeDumper.add_representer(QuotedYamlString, _represent_quoted_yaml_string)


def write_yaml(path: str, data: dict) -> None:
    # YAML 1.1 treats strings such as 172066e9 as floating-point values.
    # Mihomo then converts that float back to a different string and rejects
    # the REALITY short-id. Force these fields to remain quoted YAML strings.
    proxies = data.get("proxies")
    if isinstance(proxies, list):
        for proxy in proxies:
            if not isinstance(proxy, dict):
                continue
            options = proxy.get("reality-opts")
            if isinstance(options, dict):
                short_id = options.get("short-id")
                if isinstance(short_id, str):
                    options["short-id"] = QuotedYamlString(short_id)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, Dumper=AutoHubSafeDumper, allow_unicode=True, sort_keys=False)


def validate_structure(config: dict) -> None:
    proxies = config.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise RuntimeError("Final config has no proxies")
    names = [p.get("name") for p in proxies]
    if len(names) != len(set(names)) or any(not n for n in names):
        raise RuntimeError("Proxy names are missing or duplicated")
    for p in proxies:
        if p.get("type") not in SUPPORTED_PROXY_TYPES or not p.get("server") or not isinstance(p.get("port"), int):
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

    cache_path = os.getenv("SOURCE_CACHE_PATH", "source-cache.json")
    try:
        source_cache = load_cache(cache_path)
    except ValueError as exc:
        print(f"Source validator cache is invalid; performing unconditional fetches: {exc}")
        source_cache = empty_cache()
    cache_changed = False

    source_nodes = [[] for _ in urls]
    successes = 0
    unchanged = 0
    http_type_rejected = 0

    def fetch_source(item: tuple[int, str]) -> tuple[int, list[dict], int, str | None, int, dict]:
        index, url = item
        try:
            headers = {"User-Agent": USER_AGENT}
            headers.update(conditional_headers(source_cache, url))
            response = requests.get(url, headers=headers, timeout=25)
            if response.status_code == 304:
                return index, [], 0, None, 304, {}
            response.raise_for_status()
            parsed, rejected_http = parse_source(response.text)
            return index, parsed, rejected_http, None, response.status_code, dict(response.headers)
        except Exception as exc:
            return index, [], 0, f"{type(exc).__name__}: {exc}", 0, {}

    print(f"Fetching {len(urls)} subscription sources with {min(SOURCE_WORKERS, len(urls))} workers")
    with ThreadPoolExecutor(max_workers=min(SOURCE_WORKERS, len(urls))) as pool:
        futures = [pool.submit(fetch_source, item) for item in enumerate(urls, 1)]
        for future in as_completed(futures):
            index, parsed, rejected_http, error, status, response_headers = future.result()
            source_nodes[index - 1] = parsed
            http_type_rejected += rejected_http
            if error is None:
                successes += 1
                if status == 304:
                    unchanged += 1
                    print(f"Source {index}: unchanged (HTTP 304); skipped download and parsing")
                else:
                    if status == 200:
                        cache_changed = record_response(
                            source_cache, urls[index - 1], status, response_headers
                        ) or cache_changed
                    print(f"Source {index}: {len(parsed)} usable nodes; excluded {rejected_http} pure HTTP proxies")
            else:
                print(f"Source {index} failed: {error}")

    if successes == 0:
        raise RuntimeError("All subscription sources failed; refusing to publish")
    if cache_changed:
        save_cache(cache_path, source_cache)
    set_github_output("has_cache_updates", "true" if cache_changed else "false")
    print(f"Source fetch summary: successful={successes}, unchanged={unchanged}, failed={len(urls) - successes}")

    # Deduplicate globally, then test every unique node without a sampling cap.
    source_unique = []
    seen = set()
    parsed_total = 0
    unique_total = 0
    for nodes in source_nodes:
        parsed_total += len(nodes)
        bucket = []
        for node in nodes:
            key = node_key(node)
            if key in seen:
                continue
            seen.add(key)
            bucket.append(node)
            unique_total += 1
        source_unique.append(bucket)

    all_unique_candidates = [node for bucket in source_unique for node in bucket]
    # The official final list is the live exclusion set: once a node leaves
    # final/latest.yaml it can be considered again on a later source pull.
    final_fingerprints = load_final_node_fingerprints()
    candidates = exclude_final_nodes(all_unique_candidates, final_fingerprints)
    excluded_final = len(all_unique_candidates) - len(candidates)
    print(f"Parsed {parsed_total} nodes; globally unique: {unique_total}; "
          f"pure HTTP proxies excluded: {http_type_rejected}; "
          f"already-final nodes excluded: {excluded_final}; "
          f"remaining candidates: {len(candidates)}")
    if not candidates:
        print("No new candidates remain after excluding the current official final subscription.")
        set_github_output("has_candidates", "false")
        return

    # TCP and generic handshake checks are diagnostic only. A generic probe can
    # misclassify WS/TLS/Reality/UDP transports; Mihomo performs the decisive test.
    print(f"TCP diagnostic: {len(candidates)} candidates, {WORKERS} workers")
    tcp_passed_count = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(tcp_check, n) for n in candidates]
        for future in as_completed(futures):
            _, latency = future.result()
            if latency is not None:
                tcp_passed_count += 1
    print(f"TCP diagnostic passed: {tcp_passed_count} / {len(candidates)}; failures retained for Mihomo")

    handshake_workers = max(1, int(os.getenv("HANDSHAKE_WORKERS", "80") or "80"))
    print(f"Protocol handshake diagnostic: {len(candidates)} candidates, {handshake_workers} workers")
    handshake_passed_count = 0
    handshake_failures = {}
    with ThreadPoolExecutor(max_workers=handshake_workers) as pool:
        futures = [pool.submit(protocol_handshake_check, n) for n in candidates]
        for future in as_completed(futures):
            _, passed, reason = future.result()
            if passed:
                handshake_passed_count += 1
            else:
                handshake_failures[reason] = handshake_failures.get(reason, 0) + 1
    handshake_failed_count = sum(handshake_failures.values())
    print(f"Protocol handshake diagnostic passed: {handshake_passed_count} / {len(candidates)}; "
          f"failures retained for Mihomo: {handshake_failed_count}; reasons: {handshake_failures}")

    tcp_ok = []
    invalid_reality = 0
    for node in unique_names(candidates):
        if valid_reality_node(node):
            tcp_ok.append(node)
        else:
            invalid_reality += 1
            print(
                "Skip malformed REALITY options at final gate: "
                f"name={node.get('name', '(unnamed)')!r}, "
                f"short-id={((node.get('reality-opts') or {}).get('short-id') if isinstance(node.get('reality-opts'), dict) else '(non-mapping reality-opts)')!r}"
            )
    print(
        f"Final REALITY validation: kept {len(tcp_ok)}; "
        f"skipped {invalid_reality} malformed candidates"
    )
    if not tcp_ok:
        raise RuntimeError("No candidates remain after final REALITY validation")
    print(f"Starting Mihomo real proxy tests on {len(tcp_ok)} validated candidates")
    os.makedirs("site", exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="autohub-") as tmp:
        test_path = os.path.join(tmp, "test.yaml")
        write_yaml(test_path, test_config(tcp_ok, PROBE_WORKERS))
        check = subprocess.run([os.getenv("MIHOMO_BIN", "./mihomo"), "-t", "-f", test_path],
                               capture_output=True, text=True, timeout=45)
        if check.returncode:
            print("Mihomo rejected the combined config; bisecting proxy list to locate invalid entries")
            bad_nodes = find_mihomo_rejected_proxies(tcp_ok, os.getenv("MIHOMO_BIN", "./mihomo"))
            if bad_nodes:
                for bad in bad_nodes:
                    options = bad.get("reality-opts")
                    short_id = options.get("short-id") if isinstance(options, dict) else None
                    print(
                        "Mihomo-rejected proxy: "
                        f"name={bad.get('name', '(unnamed)')!r}, "
                        f"type={bad.get('type')!r}, "
                        f"reality-opts-type={type(options).__name__}, "
                        f"short-id={short_id!r}, "
                        f"short-id-type={type(short_id).__name__}"
                    )
            else:
                print("Bisect found no individually rejected proxy; failure may be caused by config interaction")
            raise RuntimeError("Mihomo rejected test config: " + (check.stderr or check.stdout)[-2500:])
        proc = subprocess.Popen([os.getenv("MIHOMO_BIN", "./mihomo"), "-d", tmp, "-f", test_path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        try:
            api = requests.Session()
            api.headers.update({"Authorization": "Bearer autohub-local-test"})
            for _ in range(40):
                if proc.poll() is not None:
                    raise RuntimeError("Mihomo exited before API became ready")
                try:
                    if api.get(CONTROLLER + "/version", timeout=1).ok:
                        break
                except requests.RequestException:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Mihomo API did not become ready")

            def proxy_delay(node: dict) -> tuple[dict, int | None]:
                endpoint = CONTROLLER + "/proxies/" + quote(node["name"], safe="") + "/delay"
                for target_url in (LATENCY_URL, LATENCY_FALLBACK_URL):
                    try:
                        response = api.get(endpoint, params={"url": target_url, "timeout": HTTP_TIMEOUT_MS},
                                           timeout=HTTP_TIMEOUT_MS / 1000 + 5)
                        response.raise_for_status()
                        delay = response.json().get("delay")
                        if delay and int(delay) > 0:
                            return node, int(delay)
                    except Exception:
                        continue
                return node, None

            tested = []
            with ThreadPoolExecutor(max_workers=HTTP_WORKERS) as pool:
                futures = [pool.submit(proxy_delay, n) for n in tcp_ok]
                for future in as_completed(futures):
                    node, delay = future.result()
                    if delay is not None:
                        tested.append((delay, node))
            tested.sort(key=lambda x: x[0])
            if not tested:
                raise RuntimeError("No node passed real HTTP proxy latency test")

            http_tested_count = len(tested)
            print(f"HTTP proxy test passed: {http_tested_count} / {len(tcp_ok)}")

            # Mihomo's successful real-delay request is the candidate gate.
            # A separate ChatGPT TLS check is intentionally not a hard requirement:
            # regional blocking or destination-specific failures must not discard a node.
            import hashlib
            passed_nodes = [dict(node) for delay, node in tested]
            if not passed_nodes:
                raise RuntimeError("No node passed Mihomo real HTTP proxy latency test")

            # Download throughput is intentionally reserved for the Android phone.

            os.makedirs("candidates", exist_ok=True)
            candidate_path = os.path.join("candidates", "candidates.yaml")
            write_yaml(candidate_path, {"proxies": passed_nodes})
            with open(candidate_path, "rb") as candidate_file:
                candidate_sha256 = hashlib.sha256(candidate_file.read()).hexdigest()

            manifest_nodes = []
            delay_by_name = {node["name"]: delay for delay, node in tested}
            for node in passed_nodes:
                # Keep the phone-side node identity stable when only its display name changes.
                node_id = node_key(node)[:20]
                manifest_nodes.append({
                    "node_id": node_id,
                    "name": node.get("name", ""),
                    "github_delay_ms": delay_by_name.get(node.get("name")),
                })

            manifest = {
                "schema_version": 1,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "candidate_sha256": candidate_sha256,
                "total_sources": len(urls),
                "successful_sources": successes,
                "parsed_nodes": parsed_total,
                "unique_candidates": unique_total,
                "excluded_http_proxies": http_type_rejected,
                "tcp_diagnostic_passed": tcp_passed_count,
                "tcp_diagnostic_failed_retained": len(candidates) - tcp_passed_count,
                "protocol_handshake_diagnostic_passed": handshake_passed_count,
                "protocol_handshake_failed_retained": handshake_failed_count,
                "mihomo_http_passed": http_tested_count,
                "latency_test_url": LATENCY_URL,
                "latency_fallback_url": LATENCY_FALLBACK_URL,
                "nodes": manifest_nodes,
            }
            with open(os.path.join("candidates", "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
                f.write("\n")

            set_github_output("has_candidates", "true")

            print(
                "GitHub prefilter complete: "
                f"parsed={parsed_total}, unique={unique_total}, "
                f"TCP diagnostic={tcp_passed_count}/{len(candidates)}, "
                f"handshake diagnostic={handshake_passed_count}/{len(candidates)}, "
                f"Mihomo HTTP={http_tested_count}. "
                "Phone download-speed testing remains pending."
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()