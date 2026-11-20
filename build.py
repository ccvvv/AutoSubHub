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


def parse_source(text: str) -> list[dict]:
    text = decode_subscription(text)
    try:
        data = yaml.safe_load(text)
    except Exception:
        data = None
    if isinstance(data, dict) and isinstance(data.get("proxies"), list):
        # Normalize common YAML variations and discard incomplete/unsupported entries
        # before they reach TCP and Mihomo testing.
        valid_types = {"ss", "vmess", "vless", "trojan", "socks5", "http", "snell",
                       "hysteria", "hysteria2", "tuic", "wireguard", "ssh", "anytls"}
        nodes = []
        for raw in data["proxies"]:
            if not isinstance(raw, dict) or not raw.get("server"):
                continue
            node = dict(raw)
            try:
                node["port"] = int(node.get("port"))
            except (TypeError, ValueError):
                continue
            node["type"] = str(node.get("type", "")).lower()
            if node["type"] not in valid_types or not 1 <= node["port"] <= 65535:
                continue
            nodes.append(node)
        return nodes
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
    return nodes


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
    valid_types = {"ss", "vmess", "vless", "trojan", "socks5", "http", "snell", "hysteria",
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

    def fetch_source(item: tuple[int, str]) -> tuple[int, list[dict], str | None]:
        index, url = item
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
            response.raise_for_status()
            return index, parse_source(response.text), None
        except Exception as exc:
            return index, [], f"{type(exc).__name__}: {exc}"

    print(f"Fetching {len(urls)} subscription sources with {min(SOURCE_WORKERS, len(urls))} workers")
    with ThreadPoolExecutor(max_workers=min(SOURCE_WORKERS, len(urls))) as pool:
        futures = [pool.submit(fetch_source, item) for item in enumerate(urls, 1)]
        for future in as_completed(futures):
            index, parsed, error = future.result()
            source_nodes[index - 1] = parsed
            if error is None:
                print(f"Source {index}: {len(parsed)} nodes")
                successes += 1
            else:
                print(f"Source {index} failed: {error}")
    if successes == 0:
        raise RuntimeError("All subscription sources failed; refusing to publish")

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

    candidates = [node for bucket in source_unique for node in bucket]
    print(f"Parsed {parsed_total} nodes; globally unique: {unique_total}; "
          f"all unique nodes selected for testing: {len(candidates)}")
    if not candidates:
        raise RuntimeError("No supported nodes parsed; refusing to publish")

    print(f"TCP prefilter: {len(candidates)} candidates, {WORKERS} workers")
    tcp_ok = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(tcp_check, n) for n in candidates]
        for future in as_completed(futures):
            node, latency = future.result()
            if latency is not None:
                tcp_ok.append(node)
    if not tcp_ok:
        raise RuntimeError("No node passed TCP/UDP candidate prefilter")
    print(f"TCP/UDP candidate pass: {len(tcp_ok)} / {len(candidates)}")

    handshake_workers = max(1, int(os.getenv("HANDSHAKE_WORKERS", "80") or "80"))
    print(f"Protocol handshake check: {len(tcp_ok)} candidates, {handshake_workers} workers")
    handshake_ok = []
    handshake_failures = {}
    with ThreadPoolExecutor(max_workers=handshake_workers) as pool:
        futures = [pool.submit(protocol_handshake_check, n) for n in tcp_ok]
        for future in as_completed(futures):
            node, passed, reason = future.result()
            if passed:
                handshake_ok.append(node)
            else:
                handshake_failures[reason] = handshake_failures.get(reason, 0) + 1
    print(f"Protocol handshake passed: {len(handshake_ok)} / {len(tcp_ok)}; "
          f"rejected: {sum(handshake_failures.values())}; reasons: {handshake_failures}")
    if not handshake_ok:
        raise RuntimeError("No node passed protocol handshake prefilter")

    tcp_ok = unique_names(handshake_ok)
    print(f"Handshake passed: {len(tcp_ok)}; starting Mihomo real proxy tests")
    os.makedirs("site", exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="autohub-") as tmp:
        test_path = os.path.join(tmp, "test.yaml")
        write_yaml(test_path, test_config(tcp_ok, SPEED_TEST_WORKERS))
        check = subprocess.run([os.getenv("MIHOMO_BIN", "./mihomo"), "-t", "-f", test_path],
                               capture_output=True, text=True, timeout=45)
        if check.returncode:
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
                try:
                    endpoint = CONTROLLER + "/proxies/" + quote(node["name"], safe="") + "/delay"
                    response = api.get(endpoint, params={"url": LATENCY_URL, "timeout": HTTP_TIMEOUT_MS},
                                       timeout=HTTP_TIMEOUT_MS / 1000 + 5)
                    response.raise_for_status()
                    delay = response.json().get("delay")
                    return node, int(delay) if delay and int(delay) > 0 else None
                except Exception:
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

            print(f"HTTP proxy test passed: {len(tested)} / {len(tcp_ok)}")
            speed_map = {}
            speed_failures = 0
            speed_prefilter_rejected = 0
            print(f"Parallel download speed test: {SPEED_TEST_WORKERS} isolated workers; "
                  f"first {SPEED_PREFILTER_BYTES // 1024} KB prefilter; "
                  f"full target {DOWNLOAD_BYTES // 1024} KB; minimum {DOWNLOAD_MIN_KBPS} KB/s; "
                  "one retry for transient/slow results")

            def speed_worker(worker_index: int, batch: list[tuple[int, dict]]) -> list[tuple[dict, float | None, str]]:
                # Each worker owns a distinct Mihomo selector group and listener port.
                # No two threads can change the route used by another worker.
                worker_api = requests.Session()
                worker_api.headers.update({"Authorization": "Bearer autohub-local-test"})
                selector = CONTROLLER + f"/proxies/TEST-{worker_index:02d}"
                proxy_url = f"http://127.0.0.1:{SPEED_PORT_BASE + worker_index}"
                worker_results = []
                for delay, node in batch:
                    measured = None
                    reason = "request-failed"
                    for attempt in range(2):
                        response = None
                        try:
                            selected = worker_api.put(selector, json={"name": node["name"]}, timeout=8)
                            selected.raise_for_status()
                            start_time = time.perf_counter()
                            response = requests.get(DOWNLOAD_URL, proxies={
                                "http": proxy_url, "https": proxy_url,
                            }, stream=True, timeout=(10, 20),
                                headers={"User-Agent": USER_AGENT})
                            response.raise_for_status()
                            total = 0
                            prefilter_speed = None
                            for chunk in response.iter_content(65536):
                                if chunk:
                                    total += len(chunk)
                                if total >= SPEED_PREFILTER_BYTES:
                                    elapsed = max(time.perf_counter() - start_time, 0.001)
                                    prefilter_speed = total / elapsed / 1024
                                    break
                            if not total or prefilter_speed is None:
                                reason = "no-data"
                                continue
                            if prefilter_speed < DOWNLOAD_MIN_KBPS:
                                reason = f"prefilter-slow-{prefilter_speed:.0f}"
                                continue

                            for chunk in response.iter_content(65536):
                                if chunk:
                                    total += len(chunk)
                                if total >= DOWNLOAD_BYTES:
                                    break
                            elapsed_full = max(time.perf_counter() - start_time, 0.001)
                            measured = round(total / elapsed_full / 1024, 1)
                            if measured < DOWNLOAD_MIN_KBPS:
                                reason = f"full-test-slow-{measured:.0f}"
                                measured = None
                                continue
                            reason = "passed"
                            break
                        except Exception as exc:
                            reason = type(exc).__name__
                        finally:
                            if response is not None:
                                response.close()
                    worker_results.append((node, measured, reason))
                return worker_results

            batches = [tested[i::SPEED_TEST_WORKERS] for i in range(SPEED_TEST_WORKERS)]
            batches = [(i, batch) for i, batch in enumerate(batches) if batch]
            completed_nodes = 0
            with ThreadPoolExecutor(max_workers=SPEED_TEST_WORKERS) as pool:
                futures = {pool.submit(speed_worker, worker_index, batch): (worker_index, len(batch))
                           for worker_index, batch in batches}
                for future in as_completed(futures):
                    worker_index, batch_size = futures[future]
                    results = future.result()
                    completed_nodes += batch_size
                    for node, speed, reason in results:
                        if speed is not None:
                            speed_map[node["name"]] = speed
                        elif reason.startswith(("prefilter-slow-", "full-test-slow-")):
                            speed_prefilter_rejected += 1
                        else:
                            speed_failures += 1
                    print(f"Speed progress: {completed_nodes}/{len(tested)}; "
                          f"full-tested {len(speed_map)}; slow-rejected {speed_prefilter_rejected}; "
                          f"failed {speed_failures}; finished worker {worker_index + 1}/{len(batches)}")

            print(f"Download speed measured: {len(speed_map)} / {len(tested)}; "
                  f"slow/retry-rejected: {speed_prefilter_rejected}; failures after retry: {speed_failures}; "
                  f"parallel workers: {SPEED_TEST_WORKERS}; threshold: {DOWNLOAD_MIN_KBPS} KB/s")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    output = []
    speed_rejected = 0
    for delay, original in tested:
        speed = speed_map.get(original["name"])
        if speed is None or speed < DOWNLOAD_MIN_KBPS:
            speed_rejected += 1
            continue
        node = dict(original)
        node["name"] = f"{node['name']} [{delay}ms {speed:.0f}KB/s]"
        output.append(node)
    print(f"Speed threshold passed: {len(output)} / {len(tested)}; hidden below {DOWNLOAD_MIN_KBPS} KB/s or unmeasured: {speed_rejected}")
    if not output:
        raise RuntimeError(f"No node reached the minimum download speed of {DOWNLOAD_MIN_KBPS} KB/s")

    config = final_config(output)
    validate_structure(config)
    final_path = os.path.join("site", "subscription.yaml")
    write_yaml(final_path, config)
    # Parse our own output again and ask Mihomo to validate the exact published file.
    with open(final_path, encoding="utf-8") as f:
        roundtrip = yaml.safe_load(f)
    validate_structure(roundtrip)
    final_check = subprocess.run([os.getenv("MIHOMO_BIN", "./mihomo"), "-t", "-f", final_path],
                                 capture_output=True, text=True, timeout=45)
    if final_check.returncode:
        raise RuntimeError("Mihomo rejected final subscription: " + (final_check.stderr or final_check.stdout)[-2500:])

    html = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AutoSubHub</title><body style="font-family:system-ui;max-width:760px;margin:40px auto;padding:0 16px">
<h1>AutoSubHub</h1><p>最后构建：{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</p>
<ul><li>TCP 初筛通过：{len(tcp_ok)}</li><li>真实 HTTP 代理测试通过：{len(tested)}</li>
<li>完成下载测速：{len(speed_map)} / {len(tested)}</li><li>速度门槛：{DOWNLOAD_MIN_KBPS} KB/s</li><li>下载测速并发：{SPEED_TEST_WORKERS}</li><li>最终节点：{len(output)}</li></ul>
<p><a href="subscription.yaml">下载 Clash / Mihomo 订阅</a></p>
<p>说明：所有通过真实 HTTP 代理测试的节点均进行下载测速；低于速度门槛或测速失败的节点不会发布。</p>
</body></html>"""
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Validated and prepared {len(output)} nodes for Pages deployment")


if __name__ == "__main__":
    main()
