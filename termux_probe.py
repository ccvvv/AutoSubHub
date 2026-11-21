#!/usr/bin/env python3
"""Run domestic-network download tests for an AutoSubHub candidate subscription.

Requires Termux packages: python, curl; Python packages: requests, pyyaml;
and a Mihomo binary available as ./mihomo or via MIHOMO_BIN.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

import requests
import yaml

REPO = os.getenv("GITHUB_REPOSITORY", "ccvvv/AutoSubHub")
BRANCH = "published-subscriptions"
CANDIDATE_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/candidate/subscription.yaml"
MANIFEST_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/candidate/manifest.json"
API_COMMIT_URL = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
PORT_BASE = int(os.getenv("PROBE_PORT_BASE", "17890"))
SECRET = "autohub-phone-probe"
USER_AGENT = "AutoSubHub-Termux-Probe/1.0"


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def make_config(proxies: list[dict], workers: int, api_port: int, port_base: int) -> dict:
    names = [p["name"] for p in proxies]
    groups = [{"name": f"PHONE-{i:02d}", "type": "select", "proxies": names}
              for i in range(workers)]
    listeners = [{"name": f"phone-{i:02d}", "type": "mixed",
                  "listen": "127.0.0.1", "port": port_base + i,
                  "proxy": f"PHONE-{i:02d}"} for i in range(workers)]
    return {
        "allow-lan": False, "mode": "rule", "log-level": "error", "ipv6": False,
        "external-controller": f"127.0.0.1:{api_port}", "secret": SECRET,
        "proxies": proxies, "proxy-groups": groups, "listeners": listeners,
        "rules": ["MATCH,DIRECT"],
    }


def mirror_urls(url: str, repo: str, branch: str) -> list[tuple[str, str]]:
    """Return ordered direct and mirror URLs for a GitHub Raw file."""
    prefix = f"https://raw.githubusercontent.com/{repo}/{branch}/"
    if not url.startswith(prefix):
        # Custom candidate URLs can still use the two generic GitHub proxy services.
        return [
            ("GitHub Raw/custom", url),
            ("GHFast", f"https://ghfast.top/{url}"),
            ("GH-Proxy", f"https://gh-proxy.com/{url}"),
        ]
    relative = url[len(prefix):]
    return [
        ("GitHub Raw", url),
        ("GHFast", f"https://ghfast.top/{url}"),
        ("GH-Proxy", f"https://gh-proxy.com/{url}"),
        ("jsDelivr", f"https://cdn.jsdelivr.net/gh/{repo}@{branch}/{relative}"),
    ]


def pin_raw_url(url: str, repo: str, commit: str) -> str:
    prefix = f"https://raw.githubusercontent.com/{repo}/"
    if not url.startswith(prefix):
        raise ValueError("Candidate URLs must be raw.githubusercontent.com files in the configured repository")
    remainder = url[len(prefix):].split("/", 1)
    if len(remainder) != 2 or not remainder[1]:
        raise ValueError("Invalid candidate URL path")
    return prefix + commit + "/" + remainder[1]


def download_verified_pair(session: requests.Session, candidate_url: str,
                           manifest_url: str, timeout: int,
                           repo: str, branch: str) -> tuple[bytes, dict, dict, str]:
    """Try each source as a pair so candidate and manifest come from the same CDN."""
    candidate_sources = mirror_urls(candidate_url, repo, branch)
    manifest_sources = dict(mirror_urls(manifest_url, repo, branch))
    errors = []
    for source, candidate_mirror in candidate_sources:
        manifest_mirror = manifest_sources.get(source)
        if not manifest_mirror:
            continue
        for attempt in range(1, 3):
            try:
                candidate_response = session.get(candidate_mirror, timeout=timeout)
                candidate_response.raise_for_status()
                manifest_response = session.get(manifest_mirror, timeout=timeout)
                manifest_response.raise_for_status()
                manifest = manifest_response.json()
                digest = hashlib.sha256(candidate_response.content).hexdigest()
                if manifest.get("candidate_sha256") != digest:
                    raise ValueError("candidate/manifest SHA256 mismatch")
                print(f"Candidate and manifest downloaded via {source}; SHA256 verified")
                return candidate_response.content, dict(candidate_response.headers), manifest, source
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{source} attempt {attempt}: {type(exc).__name__}: {exc}")
                print(f"Source {source} failed (attempt {attempt}/2): {type(exc).__name__}")
    raise RuntimeError("All candidate sources failed verification: " + " | ".join(errors[-8:]))


def main() -> None:
    parser = argparse.ArgumentParser(description="AutoSubHub domestic phone download probe")
    parser.add_argument("--candidate-url", default=CANDIDATE_URL)
    parser.add_argument("--manifest-url", default=MANIFEST_URL)
    parser.add_argument("--workers", type=int, default=int(os.getenv("PROBE_WORKERS", "5")))
    parser.add_argument("--min-speed-kbps", type=float, default=200.0,
                        help="Minimum accepted download speed in KB/s (default 200)")
    parser.add_argument("--high-speed-kbps", type=float, default=500.0,
                        help="High-speed category threshold in KB/s (default 500)")
    parser.add_argument("--max-bytes", type=int, default=5 * 1024 * 1024,
                        help="Maximum bytes downloaded per node (default 5 MiB)")
    parser.add_argument("--download-url", default="https://speed.cloudflare.com/__down?bytes=5242880")
    parser.add_argument("--youtube-url", default="https://www.youtube.com/")
    parser.add_argument("--telegram-url", default="https://web.telegram.org/",
                        help="Telegram Web HTTPS check; not a native MTProto login test")
    parser.add_argument("--google-url", default="https://www.google.com/generate_204")
    parser.add_argument("--chatgpt-url", default="https://chatgpt.com/")
    parser.add_argument("--github-url", default="https://github.com/")
    parser.add_argument("--service-timeout", type=int, default=3,
                        help="Per-application connection/read timeout in seconds (default 3)")
    parser.add_argument("--timeout", type=int, default=5,
                        help="Connection and read-inactivity timeout in seconds (default 5)")
    parser.add_argument("--api-port", type=int, default=int(os.getenv("PROBE_API_PORT", "19090")),
                        help="Mihomo controller port; change it if another core uses the default")
    parser.add_argument("--port-base", type=int, default=PORT_BASE,
                        help="First local mixed-listener port")
    parser.add_argument("--mihomo", default=os.getenv("MIHOMO_BIN", "./mihomo"))
    parser.add_argument("--output", default="receipt.json")
    args = parser.parse_args()

    if not 1 <= args.workers <= 200:
        raise SystemExit("--workers must be between 1 and 200; increase gradually on phones")
    if args.timeout < 1 or args.service_timeout < 1:
        raise SystemExit("timeouts must be at least 1 second")
    if args.min_speed_kbps <= 0 or args.high_speed_kbps <= args.min_speed_kbps:
        raise SystemExit("speed thresholds must be positive and high-speed must exceed minimum speed")
    if args.max_bytes < 256 * 1024:
        raise SystemExit("--max-bytes must be at least 256 KiB")

    if not 1024 <= args.api_port <= 65535 or not 1024 <= args.port_base <= 65535 - args.workers:
        raise SystemExit("API/listener ports must be valid non-privileged TCP ports")
    api_base = f"http://127.0.0.1:{args.api_port}"

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        commit_response = session.get(API_COMMIT_URL, timeout=30)
        commit_response.raise_for_status()
        candidate_commit = commit_response.json().get("sha")
        if not isinstance(candidate_commit, str) or len(candidate_commit) != 40 or any(c not in "0123456789abcdef" for c in candidate_commit):
            raise ValueError("GitHub returned an invalid candidate commit SHA")
        pinned_candidate_url = pin_raw_url(args.candidate_url, REPO, candidate_commit)
        pinned_manifest_url = pin_raw_url(args.manifest_url, REPO, candidate_commit)
        print(f"Candidate version locked: {candidate_commit}")
        candidate_bytes, _, manifest, source_used = download_verified_pair(
            session, pinned_candidate_url, pinned_manifest_url, 30, REPO, candidate_commit
        )
    except (RuntimeError, ValueError, requests.RequestException, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to lock and download candidate version: {type(exc).__name__}: {exc}") from exc
    candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()

    candidate = yaml.safe_load(candidate_bytes) or {}
    proxies = candidate.get("proxies")
    manifest_nodes = manifest.get("nodes")
    if not isinstance(proxies, list) or not proxies or not isinstance(manifest_nodes, list):
        raise SystemExit("Candidate or manifest structure is invalid")
    by_name = {p.get("name"): p for p in proxies if isinstance(p, dict) and p.get("name")}
    valid_manifest_nodes = [n for n in manifest_nodes if isinstance(n, dict)]
    id_by_name = {n.get("name"): n.get("node_id") for n in valid_manifest_nodes}
    node_ids = list(id_by_name.values())
    if (len(by_name) != len(proxies) or len(valid_manifest_nodes) != len(manifest_nodes)
            or len(id_by_name) != len(manifest_nodes) or len(set(node_ids)) != len(node_ids)
            or set(by_name) != set(id_by_name)
            or any(not isinstance(value, str) or not value for value in node_ids)):
        raise SystemExit("Manifest node IDs do not exactly match candidate proxy names")
    proxies = list(by_name.values())
    print(f"Candidate loaded: {len(proxies)} nodes; SHA256 {candidate_hash[:16]}…")

    api = requests.Session()
    api.headers.update({"Authorization": f"Bearer {SECRET}"})
    receipt_results = []
    result_lock = threading.Lock()
    mihomo = None

    with tempfile.TemporaryDirectory(prefix="autohub-phone-") as temp:
        config_path = Path(temp) / "config.yaml"
        write_yaml(config_path, make_config(proxies, args.workers, args.api_port, args.port_base))
        check = subprocess.run([args.mihomo, "-t", "-f", str(config_path)],
                               capture_output=True, text=True, timeout=30)
        if check.returncode:
            raise SystemExit("Mihomo rejected candidate config: " + (check.stderr or check.stdout)[-1500:])
        mihomo = subprocess.Popen([args.mihomo, "-d", temp, "-f", str(config_path)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        try:
            for _ in range(40):
                if mihomo.poll() is not None:
                    raise RuntimeError("Mihomo exited during startup")
                try:
                    if api.get(api_base + "/version", timeout=1).ok:
                        break
                except requests.RequestException:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Mihomo external controller did not start")

            batches = [proxies[i::args.workers] for i in range(args.workers)]

            def run_service_check(service: str, target_url: str,
                                  proxy_url: str, timeout: int) -> dict:
                started = time.perf_counter()
                try:
                    with requests.get(target_url,
                                      proxies={"http": proxy_url, "https": proxy_url},
                                      stream=True, timeout=(timeout, timeout),
                                      headers={"User-Agent": USER_AGENT}) as response:
                        response.raise_for_status()
                        next(response.iter_content(chunk_size=1024), b"")
                        status_code = response.status_code
                    return {"status": "passed", "status_code": status_code,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000)}
                except Exception as exc:
                    return {"status": "failed", "error": type(exc).__name__,
                            "elapsed_ms": round((time.perf_counter() - started) * 1000)}

            service_pool = ThreadPoolExecutor(max_workers=min(25, max(5, args.workers * 5)))

            def probe_worker(worker_index: int, batch: list[dict]) -> None:
                selector = api_base + f"/proxies/PHONE-{worker_index:02d}"
                proxy_url = f"http://127.0.0.1:{args.port_base + worker_index}"
                worker_api = requests.Session()
                worker_api.headers.update({"Authorization": f"Bearer {SECRET}"})
                for node in batch:
                    node_id = next((item.get("node_id") for item in manifest_nodes
                                    if item.get("name") == node["name"]), None)
                    record = {"node_id": node_id, "name": node["name"],
                              "status": "failed", "download_speed_bps": 0}
                    try:
                        selected = worker_api.put(selector, json={"name": node["name"]}, timeout=8)
                        selected.raise_for_status()
                        confirmed = worker_api.get(selector, timeout=8)
                        confirmed.raise_for_status()
                        if confirmed.json().get("now") != node["name"]:
                            raise RuntimeError("Mihomo selector did not switch to requested node")
                        # All application checks are hard gates and run concurrently.
                        # Telegram Web verifies HTTPS reachability, not native MTProto login.
                        service_targets = {
                            "youtube": args.youtube_url,
                            "telegram": args.telegram_url,
                            "google": args.google_url,
                            "chatgpt": args.chatgpt_url,
                            "github": args.github_url,
                        }
                        record["application_checks"] = {}
                        # Run the five lightweight checks concurrently with a bounded global pool.
                        service_futures = {
                            service_pool.submit(run_service_check, service, target_url,
                                                proxy_url, args.service_timeout): service
                            for service, target_url in service_targets.items()
                        }
                        for future in as_completed(service_futures):
                            service = service_futures[future]
                            check_result = future.result()
                            record["application_checks"][service] = check_result
                            if check_result["status"] == "failed":
                                record.setdefault("failure_stage", service)
                        service_failed = any(
                            result["status"] != "passed"
                            for result in record["application_checks"].values()
                        )

                        if not service_failed:
                            start = time.perf_counter()
                            received = 0
                            with requests.get(args.download_url,
                                              proxies={"http": proxy_url, "https": proxy_url},
                                              stream=True, timeout=(args.timeout, args.timeout),
                                              headers={"User-Agent": USER_AGENT}) as response:
                                response.raise_for_status()
                                for chunk in response.iter_content(chunk_size=64 * 1024):
                                    if not chunk:
                                        continue
                                    remaining = args.max_bytes - received
                                    received += min(len(chunk), remaining)
                                    if received >= args.max_bytes:
                                        break
                            elapsed = max(time.perf_counter() - start, 0.001)
                            speed = int(received / elapsed)
                            category = ("high" if speed >= args.high_speed_kbps * 1000
                                        else "normal" if speed >= args.min_speed_kbps * 1000
                                        else "rejected")
                            record.update({"download_speed_bps": speed, "downloaded_bytes": received,
                                           "elapsed_seconds": round(elapsed, 3),
                                           "speed_category": category,
                                           "status": "passed" if category != "rejected" else "failed"})
                    except Exception as exc:
                        record["error"] = type(exc).__name__
                    with result_lock:
                        receipt_results.append(record)
                        done = len(receipt_results)
                        checks = ",".join(f"{k}:{v['status']}" for k, v in record.get("application_checks", {}).items())
                        print(f"[{done}/{len(proxies)}] {node['name'][:36]}: "
                              f"Apps={checks or 'not-run'} "
                              f"{record['download_speed_bps']/1000:.0f} KB/s "
                              f"{record.get('speed_category', 'rejected')} {record['status']}")
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(probe_worker, i, batch)
                           for i, batch in enumerate(batches) if batch]
                for future in as_completed(futures):
                    future.result()
            service_pool.shutdown(wait=True)
        finally:
            mihomo.terminate()
            try:
                mihomo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mihomo.kill()

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt = {
        "schema_version": 1,
        "status": "completed",
        "created_at": completed_at,
        "completed_at": completed_at,
        "candidate_sha256": candidate_hash,
        "candidate_commit": candidate_commit,
        "candidate_source": source_used,
        "device": "android-termux",
        "workers": args.workers,
        "min_speed_bps": int(args.min_speed_kbps * 1000),
        "high_speed_bps": int(args.high_speed_kbps * 1000),
        "service_timeout_seconds": args.service_timeout,
        "service_urls": {"youtube": args.youtube_url, "telegram": args.telegram_url,
                         "google": args.google_url, "chatgpt": args.chatgpt_url,
                         "github": args.github_url},
        "download_url": args.download_url,
        "results": receipt_results,
    }
    Path(args.output).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    passed = sum(r["status"] == "passed" for r in receipt_results)
    high = sum(r.get("status") == "passed" and r.get("speed_category") == "high" for r in receipt_results)
    normal = sum(r.get("status") == "passed" and r.get("speed_category") == "normal" for r in receipt_results)
    rejected = len(receipt_results) - high - normal
    print(f"Application gates + download: high={high}, normal={normal}, rejected={rejected}")
    print(f"Download probe: {passed}/{len(receipt_results)} passed; receipt: {args.output}")



if __name__ == "__main__":
    main()
