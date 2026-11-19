#!/usr/bin/env python3
"""Build a public Clash-compatible subscription from configured source URLs."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, unquote, urlsplit

import requests
import yaml

USER_AGENT = "AutoSubHub/1.0 (+https://github.com/ccvvv/AutoSubHub)"
TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "3") or "3")
WORKERS = int(os.getenv("MAX_WORKERS", "80") or "80")
MAX_NODES = int(os.getenv("MAX_NODES", "500") or "500")


def decode_subscription(text: str) -> str:
    text = text.strip().lstrip("\ufeff")
    if not text:
        return text
    if not re.search(r"(?m)^\s*(proxies:|[- ]?(vmess|vless|trojan|ss|socks5)://)", text, re.I):
        compact = re.sub(r"\s+", "", text)
        if compact and re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
            try:
                padded = compact + "=" * (-len(compact) % 4)
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
                if "://" in decoded or "proxies:" in decoded:
                    return decoded
            except Exception:
                pass
    return text


def vmess_from_uri(uri: str) -> dict:
    raw = uri.split("://", 1)[1].split("#", 1)[0]
    raw += "=" * (-len(raw) % 4)
    obj = json.loads(base64.urlsafe_b64decode(raw).decode("utf-8"))
    node = {
        "name": obj.get("ps") or obj.get("name") or "VMess",
        "type": "vmess",
        "server": obj["add"],
        "port": int(obj["port"]),
        "uuid": obj["id"],
        "alterId": int(obj.get("aid", 0)),
        "cipher": obj.get("scy") or "auto",
        "udp": True,
    }
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

    name = unquote(p.fragment) if p.fragment else scheme.upper()
    query = parse_qs(p.query)
    q = lambda key, default="": query.get(key, [default])[0]
    host = p.hostname
    port = p.port
    if not host or not port:
        return None

    if scheme in {"socks", "socks5"}:
        node = {"name": name, "type": "socks5", "server": host, "port": port, "udp": True}
        if p.username:
            node["username"] = unquote(p.username)
        if p.password:
            node["password"] = unquote(p.password)
        return node

    if scheme == "ss":
        userinfo = p.username or ""
        password = p.password or ""
        method = ""
        if not password and userinfo:
            decoded = userinfo
            try:
                decoded = base64.urlsafe_b64decode(userinfo + "=" * (-len(userinfo) % 4)).decode()
            except Exception:
                pass
            if ":" in decoded:
                method, password = decoded.split(":", 1)
        else:
            method = unquote(userinfo)
            password = unquote(password)
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
            node["reality-opts"] = {"public-key": q("pbk"), "short-id": q("sid")}
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
        return [p for p in data["proxies"] if isinstance(p, dict) and p.get("server") and p.get("port")]
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
            print(f"Skip malformed URI ({line[:18]}…): {exc}")
    return nodes


def node_key(node: dict) -> tuple:
    fields = ("type", "server", "port", "uuid", "password", "cipher", "username")
    return tuple(str(node.get(k, "")) for k in fields)


def tcp_latency(node: dict) -> tuple[dict, float | None]:
    try:
        start = time.perf_counter()
        with socket.create_connection((str(node["server"]), int(node["port"])), timeout=TIMEOUT):
            return node, round((time.perf_counter() - start) * 1000, 1)
    except Exception:
        return node, None


def main() -> None:
    urls = [line.strip() for line in os.getenv("SUBSCRIPTION_URLS", "").splitlines() if line.strip()]
    if not urls:
        raise SystemExit("Missing SUBSCRIPTION_URLS secret. Add one subscription URL per line.")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    all_nodes = []
    for index, url in enumerate(urls, 1):
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            parsed = parse_source(response.text)
            print(f"Source {index}: parsed {len(parsed)} nodes")
            all_nodes.extend(parsed)
        except Exception as exc:
            print(f"Source {index} failed: {type(exc).__name__}: {exc}")

    unique = {}
    for node in all_nodes:
        unique.setdefault(node_key(node), node)
    candidates = list(unique.values())[:MAX_NODES]
    if not candidates:
        raise SystemExit("No supported nodes were parsed; refusing to publish an empty subscription.")

    print(f"Checking TCP reachability for {len(candidates)} unique nodes with {WORKERS} workers")
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(tcp_latency, node) for node in candidates]
        for future in as_completed(futures):
            node, latency = future.result()
            if latency is not None:
                node = dict(node)
                node["name"] = str(node.get("name") or node.get("server"))[:65]
                results.append((latency, node))

    results.sort(key=lambda item: item[0])
    if not results:
        raise SystemExit("Every node failed the TCP check; refusing to publish an empty subscription.")

    proxies = []
    for latency, original in results:
        node = dict(original)
        node["name"] = f'{node.get("name", "node")} [{latency:g}ms]'
        proxies.append(node)

    names = [p["name"] for p in proxies]
    config = {
        "mixed-port": 7890,
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "proxies": proxies,
        "proxy-groups": [
            {"name": "AUTO", "type": "url-test", "proxies": names,
             "url": "https://www.gstatic.com/generate_204", "interval": 300, "tolerance": 80},
            {"name": "手动选择", "type": "select", "proxies": ["AUTO"] + names},
        ],
        "rules": ["MATCH,手动选择"],
    }
    os.makedirs("site", exist_ok=True)
    with open("site/subscription.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    with open("site/index.html", "w", encoding="utf-8") as f:
        f.write("""<!doctype html><meta charset="utf-8"><title>AutoSubHub</title>
<h1>AutoSubHub</h1><p>Automatically generated Clash-compatible subscription.</p>
<p><a href="subscription.yaml">Download subscription.yaml</a></p>
<p>TCP reachability checks are only a preliminary filter, not a full proxy test.</p>""")
    print(f"Published {len(proxies)} reachable nodes to site/subscription.yaml")


if __name__ == "__main__":
    main()
