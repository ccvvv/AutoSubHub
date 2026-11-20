#!/usr/bin/env python3
"""Build and verify a public Clash/Mihomo subscription using the Mihomo core."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urlsplit, quote

import requests
import yaml

USER_AGENT = "AutoSubHub/2.0 (+https://github.com/ccvvv/AutoSubHub)"
HTTP_TIMEOUT_MS = int(os.getenv("HTTP_TEST_TIMEOUT", "8000") or "8000")
HTTP_WORKERS = max(1, int(os.getenv("HTTP_WORKERS", "60") or "60"))
SOURCE_WORKERS = max(1, int(os.getenv("SOURCE_WORKERS", "10") or "10"))
_DEFAULT_LATENCY_URL = "https://www.gstatic.com/generate_204"
_configured_latency_url = os.getenv("LATENCY_URL", "").strip()
if _configured_latency_url and urlsplit(_configured_latency_url).scheme.lower() == "https":
    LATENCY_URL = _configured_latency_url
else:
    LATENCY_URL = _DEFAULT_LATENCY_URL
    if _configured_latency_url:
        print("Ignoring non-HTTPS LATENCY_URL; plain HTTP captive-portal checks are disabled.")
CONTROLLER = "http://127.0.0.1:9090"
PROBE_WORKERS = max(1, min(50, int(os.getenv("CERT_WORKERS", "50") or "50")))
PROBE_PORT_BASE = 17890


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
    # Pure Clash HTTP proxies were excluded while parsing each source, before
    # global deduplication and all network tests. TCP transports remain allowed.
    candidates = all_unique_candidates
    print(f"Parsed {parsed_total} nodes; globally unique: {unique_total}; "
          f"pure HTTP proxies excluded: {http_type_rejected}; "
          f"remaining candidates: {len(candidates)}")
    if not candidates:
        raise RuntimeError("No supported non-HTTP nodes parsed; refusing to publish")

    # Keep every supported, unique candidate. Do not run standalone TCP or
    # generic TLS/WS handshake probes here: they can reject usable proxy nodes.
    tcp_ok = unique_names(candidates)
    print(f"Format/dedup candidates retained: {len(tcp_ok)}; "
          "standalone connectivity prefilters disabled.")
    os.makedirs("site", exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="autohub-") as tmp:
        test_path = os.path.join(tmp, "test.yaml")
        write_yaml(test_path, test_config(tcp_ok, PROBE_WORKERS))
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

            http_tested_count = len(tested)
            print(f"Mihomo HTTPS latency passed: {http_tested_count} / {len(tcp_ok)}")

            # Validate real HTTPS certificates through each node's isolated Mihomo listener.
            # HTTP status codes are deliberately ignored: 401/403/404 can still prove TLS validity.
            # Only validate the ChatGPT HTTPS certificate to minimize per-node checks.
            certificate_targets = {
                "ChatGPT": "https://chatgpt.com/",
            }
            certificate_workers = max(1, min(PROBE_WORKERS,
                int(os.getenv("CERT_WORKERS", "50") or "50")))
            print(f"ChatGPT HTTPS certificate validation: {len(tested)} nodes, "
                  f"{len(certificate_targets)} platform targets, {certificate_workers} workers")
            certificate_ok = []
            certificate_rejected = 0
            certificate_inconclusive = 0
            certificate_platform_passes = {platform: 0 for platform in certificate_targets}
            delay_by_name = {node["name"]: delay for delay, node in tested}

            def certificate_worker(worker_index: int,
                                   batch: list[tuple[int, dict]]) -> list[tuple[dict, bool, str]]:
                worker_api = requests.Session()
                worker_api.headers.update({"Authorization": "Bearer autohub-local-test"})
                selector = CONTROLLER + f"/proxies/TEST-{worker_index:02d}"
                proxy_url = f"http://127.0.0.1:{PROBE_PORT_BASE + worker_index}"
                results = []
                for delay, node in batch:
                    try:
                        selected = worker_api.put(selector, json={"name": node["name"]}, timeout=8)
                        selected.raise_for_status()
                    except Exception as exc:
                        results.append((node, False, f"selector-error:{type(exc).__name__}"))
                        continue

                    verified = []
                    cert_errors = []
                    unreachable = []
                    # ChatGPT is the sole certificate-validation target.
                    # This is a TLS sanity check, not an app-compatibility guarantee.
                    for platform, target_url in certificate_targets.items():
                        try:
                            # requests verifies CA chain, hostname and validity dates by default.
                            # Do not require a 2xx response; a valid TLS session is sufficient.
                            response = requests.get(target_url, proxies={
                                "http": proxy_url, "https": proxy_url,
                            }, timeout=(8, 12), allow_redirects=False,
                                headers={"User-Agent": USER_AGENT})
                            verified.append(platform)
                            response.close()
                        except requests.exceptions.SSLError as exc:
                            detail = str(exc).lower()
                            if any(marker in detail for marker in (
                                "certificate verify failed", "hostname mismatch",
                                "certificate has expired", "self-signed certificate",
                                "unable to get local issuer", "certificate is not yet valid",
                            )):
                                cert_errors.append(f"{platform}:{type(exc).__name__}")
                            else:
                                unreachable.append(f"{platform}:tls-error")
                        except requests.RequestException as exc:
                            unreachable.append(f"{platform}:{type(exc).__name__}")


                    if cert_errors:
                        results.append((node, False, "certificate-invalid:" + ",".join(cert_errors)))
                    elif verified:
                        results.append((node, True, "verified:" + ",".join(verified)))
                    else:
                        results.append((node, False, "inconclusive:no-platform-tls-verified"))
                return results

            cert_batches = [tested[i::certificate_workers] for i in range(certificate_workers)]
            cert_batches = [(i, batch) for i, batch in enumerate(cert_batches) if batch]
            with ThreadPoolExecutor(max_workers=certificate_workers) as pool:
                futures = {pool.submit(certificate_worker, worker_index, batch): worker_index
                           for worker_index, batch in cert_batches}
                completed_cert = 0
                for future in as_completed(futures):
                    results = future.result()
                    completed_cert += len(results)
                    for node, passed, reason in results:
                        if passed:
                            certificate_ok.append((delay_by_name[node["name"]], node))
                            for platform in reason.removeprefix("verified:").split(","):
                                if platform in certificate_platform_passes:
                                    certificate_platform_passes[platform] += 1
                        elif reason.startswith("certificate-invalid:"):
                            certificate_rejected += 1
                        else:
                            certificate_inconclusive += 1
                    print(f"Certificate progress: {completed_cert}/{len(tested)}; "
                          f"passed {len(certificate_ok)}; invalid {certificate_rejected}; "
                          f"inconclusive {certificate_inconclusive}")
            tested = certificate_ok
            print(f"HTTPS certificate stage passed: {len(tested)}; "
                  f"certificate-invalid rejected: {certificate_rejected}; "
                  f"inconclusive rejected: {certificate_inconclusive}; "
                  f"platform TLS verified: {certificate_platform_passes}")
            if not tested:
                raise RuntimeError("No node passed HTTPS certificate validation")

            # Candidate stage ends after ChatGPT HTTPS certificate validation.
            # Download throughput is intentionally reserved for the Android phone.
            import hashlib
            passed_nodes = [dict(node) for delay, node in tested]
            if not passed_nodes:
                raise RuntimeError("No node passed GitHub-side checks; refusing to publish")

            os.makedirs("candidates", exist_ok=True)
            candidate_path = os.path.join("candidates", "candidates.yaml")
            write_yaml(candidate_path, {"proxies": passed_nodes})
            with open(candidate_path, "rb") as candidate_file:
                candidate_sha256 = hashlib.sha256(candidate_file.read()).hexdigest()

            manifest_nodes = []
            delay_by_name = {node["name"]: delay for delay, node in tested}
            for node in passed_nodes:
                canonical = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                node_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
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
                "standalone_connectivity_prefilters": "disabled",
                "plain_http_captive_check": "disabled",
                "mihomo_https_latency_passed": http_tested_count,
                "https_certificate_passed": len(passed_nodes),
                "certificate_invalid_rejected": certificate_rejected,
                "certificate_inconclusive_rejected": certificate_inconclusive,
                "nodes": manifest_nodes,
            }
            with open(os.path.join("candidates", "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
                f.write("\n")

            print(
                "GitHub prefilter complete: "
                f"parsed={parsed_total}, unique={unique_total}, "
                f"standalone TCP/handshake checks=disabled, "
                f"plain HTTP checks=disabled, "
                f"Mihomo HTTPS latency={http_tested_count}, "
                f"ChatGPT HTTPS certificate={len(passed_nodes)}. "
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
