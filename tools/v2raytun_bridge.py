#!/usr/bin/env python3
"""Prepare a hash-verified AutoSubHub candidate list for V2RayTun import.

This stage only converts and exports share links. It does NOT run V2RayTun
real-connection tests, delete nodes, or upload test results.
"""
import argparse
import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, urlencode

import yaml


def convert(n):
    kind = str(n.get("type", "")).lower()
    name = str(n.get("name") or n.get("server") or kind)
    server = str(n.get("server") or "")
    try:
        port = int(n.get("port") or 0)
    except (TypeError, ValueError):
        return None, "invalid port"
    if not server or not 1 <= port <= 65535:
        return None, "missing server or valid port"
    tag = quote(name, safe="")
    try:
        if kind == "vmess":
            ws = n.get("ws-opts") or {}
            headers = ws.get("headers") or {} if isinstance(ws, dict) else {}
            obj = {"v":"2", "ps":name, "add":server, "port":str(port),
                   "id":str(n.get("uuid") or ""), "aid":str(n.get("alterId", 0)),
                   "scy":str(n.get("cipher") or "auto"),
                   "net":str(n.get("network") or "tcp"), "type":"none",
                   "host":str(headers.get("Host") or headers.get("host") or ""),
                   "path":str(ws.get("path") or "") if isinstance(ws, dict) else "",
                   "tls":"tls" if n.get("tls") else "",
                   "sni":str(n.get("servername") or "")}
            raw = base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode().rstrip("=")
            return "vmess://" + raw, None
        if kind == "ss":
            method, password = str(n.get("cipher") or ""), str(n.get("password") or "")
            if not method or not password:
                return None, "SS missing cipher/password"
            auth = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
            return f"ss://{auth}@{server}:{port}#{tag}", None
        if kind in ("vless", "trojan"):
            credential = str((n.get("uuid") if kind == "vless" else n.get("password")) or "")
            if not credential:
                return None, f"{kind} missing credential"
            p = {}
            network = str(n.get("network") or "tcp")
            if network != "tcp":
                p["type"] = network
            if n.get("tls") or kind == "trojan":
                p["security"] = "tls"
                if n.get("servername"): p["sni"] = str(n["servername"])
                if n.get("client-fingerprint"): p["fp"] = str(n["client-fingerprint"])
                if isinstance(n.get("alpn"), list) and n["alpn"]: p["alpn"] = ",".join(map(str, n["alpn"]))
            if kind == "vless" and n.get("flow"): p["flow"] = str(n["flow"])
            if network == "ws":
                ws = n.get("ws-opts") or {}
                if isinstance(ws, dict):
                    if ws.get("path"): p["path"] = str(ws["path"])
                    h = ws.get("headers") or {}
                    if isinstance(h, dict) and (h.get("Host") or h.get("host")): p["host"] = str(h.get("Host") or h.get("host"))
            if network == "grpc":
                g = n.get("grpc-opts") or {}
                if isinstance(g, dict) and g.get("grpc-service-name"): p["serviceName"] = str(g["grpc-service-name"])
            if kind == "vless":
                r = n.get("reality-opts") or {}
                if isinstance(r, dict) and r:
                    p["security"] = "reality"
                    if r.get("public-key"): p["pb"] = str(r["public-key"])
                    if r.get("short-id"): p["sid"] = str(r["short-id"])
            query = urlencode(p, quote_via=quote)
            uri = f"{kind}://{quote(credential, safe='')}@{server}:{port}"
            return uri + (("?" + query) if query else "") + "#" + tag, None
        if kind == "socks5":
            user = str(n.get("username") or "")
            password = str(n.get("password") or "")
            auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user else ""
            return f"socks5://{auth}{server}:{port}#{tag}", None
        return None, f"unsupported protocol: {kind or 'unknown'}"
    except (TypeError, ValueError, KeyError) as exc:
        return None, f"invalid config: {type(exc).__name__}"


def load_verified(path):
    manifest_path = path.with_suffix(".manifest.json")
    meta_path = path.with_suffix(".meta.json")
    if not path.is_file() or not manifest_path.is_file():
        raise ValueError("candidate YAML or matching .manifest.json is missing")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    candidate = yaml.safe_load(raw) or {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if manifest.get("candidate_sha256") != digest:
        raise ValueError("candidate YAML / manifest SHA256 mismatch")
    if meta and meta.get("candidate_sha256") != digest:
        raise ValueError("candidate YAML / metadata SHA256 mismatch")
    proxies, nodes = candidate.get("proxies"), manifest.get("nodes")
    if not isinstance(proxies, list) or not proxies or not isinstance(nodes, list):
        raise ValueError("invalid candidate or manifest structure")
    if any(not isinstance(x, dict) or not x.get("name") for x in proxies):
        raise ValueError("candidate node missing name")
    if any(not isinstance(x, dict) or not x.get("name") or not x.get("node_id") for x in nodes):
        raise ValueError("manifest node missing name/node_id")
    by_name = {x["name"]: x for x in proxies}
    ids = {x["name"]: x["node_id"] for x in nodes}
    if len(by_name) != len(proxies) or len(ids) != len(nodes) or set(by_name) != set(ids):
        raise ValueError("candidate and manifest node names do not match exactly")
    if len(set(ids.values())) != len(ids) or any(not isinstance(v, str) or not v for v in ids.values()):
        raise ValueError("manifest node_id duplicated or invalid")
    return proxies, ids, digest


def main():
    ap = argparse.ArgumentParser(description="Convert verified AutoSubHub candidates to V2RayTun share links")
    ap.add_argument("--candidate-file", default="candidate-cache/candidate-local.yaml")
    ap.add_argument("--output", default="candidate-cache/v2raytun-import.txt")
    ap.add_argument("--copy", action="store_true", help="copy Base64 subscription to Android clipboard via Termux:API")
    args = ap.parse_args()
    src, out = Path(args.candidate_file).expanduser(), Path(args.output).expanduser()
    try:
        proxies, ids, digest = load_verified(src)
        links, mapping, skipped = [], [], []
        for node in proxies:
            uri, reason = convert(node)
            row = {"name": node["name"], "node_id": ids[node["name"]], "protocol": str(node.get("type") or "").lower()}
            if uri:
                links.append(uri)
                mapping.append({**row, "line": len(links)})
            else:
                skipped.append({**row, "reason": reason})
        if not links:
            raise ValueError("no supported nodes; empty subscription will not be written")
        plain = "\n".join(links)
        encoded = base64.b64encode(plain.encode()).decode() + "\n"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(encoded, encoding="utf-8")
        out.with_name(out.stem + ".links.txt").write_text(plain + "\n", encoding="utf-8")
        report = {"schema_version":1, "candidate_sha256":digest, "candidate_file":str(src.resolve()),
                  "input_count":len(proxies), "exported_count":len(links), "skipped_count":len(skipped),
                  "nodes":mapping, "skipped":skipped}
        out.with_name(out.stem + ".map.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        if args.copy:
            cmd = shutil.which("termux-clipboard-set")
            if not cmd: raise ValueError("termux-clipboard-set missing; install Termux:API app and run pkg install termux-api")
            subprocess.run([cmd], input=encoded, text=True, check=True)
        print(f"导入文件：{out}")
        print(f"原始链接：{out.with_name(out.stem + '.links.txt')}")
        print(f"节点 ID 对照：{out.with_name(out.stem + '.map.json')}")
        print(f"候选 {len(proxies)}；导出 {len(links)}；暂不支持 {len(skipped)}")
        print(f"SHA256：{digest}")
        print("此版本只负责导入准备，尚未执行 V2RayTun 真实测速或删除节点。")
        if args.copy: print("订阅已复制到剪贴板，请在 V2RayTun 中使用剪贴板导入。")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError, subprocess.CalledProcessError) as exc:
        print(f"失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
