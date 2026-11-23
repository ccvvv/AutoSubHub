#!/usr/bin/env python3
"""Run domestic-network download tests for an AutoSubHub candidate subscription.

Requires Termux packages: python, curl; Python packages: requests, pyyaml;
and a Mihomo binary available as ./mihomo or via MIHOMO_BIN.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
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
from urllib.parse import quote, urlparse

import requests
import yaml

from scripts.candidate_queue import collect_pending_candidate_batches

REPO = os.getenv("GITHUB_REPOSITORY", "ccvvv/AutoSubHub")
BRANCH = "published-subscriptions"
CANDIDATE_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/candidate/subscription.yaml"
MANIFEST_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/candidate/manifest.json"
PORT_BASE = int(os.getenv("PROBE_PORT_BASE", "17890"))
SECRET = "autohub-phone-probe"
USER_AGENT = "AutoSubHub-Termux-Probe/1.0"
CANDIDATE_UPDATED_AT = ""
CANDIDATE_FETCHED_AT = ""


def write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def exception_details(exc: Exception) -> dict:
    """Return actionable HTTP diagnostics without storing unbounded response bodies."""
    details = {"error": type(exc).__name__, "message": str(exc)[:500]}
    response = getattr(exc, "response", None)
    request = getattr(exc, "request", None)
    if response is not None:
        details["http_status"] = response.status_code
        details["url"] = response.url
        body = (response.text or "").strip()
        if body:
            details["response_body"] = body[:300]
    elif request is not None and getattr(request, "url", None):
        details["url"] = request.url
    return details



def write_progress(path: Path, status: str, done: int, total: int, results: list[dict],
                   started: float, current_node: str = "", latest: dict | None = None,
                   stage: str = "running") -> None:
    high = sum(r.get("status") == "passed" and r.get("speed_category") == "high" for r in results)
    normal = sum(r.get("status") == "passed" and r.get("speed_category") == "normal" for r in results)
    passed = high + normal
    failed = done - passed
    latest = latest or {}
    payload = {
        "status": status, "stage": stage, "done": done, "total": total,
        "candidate_updated_at": CANDIDATE_UPDATED_AT,
        "candidate_fetched_at": CANDIDATE_FETCHED_AT,
        "passed": passed, "failed": failed, "high": high, "normal": normal,
        "rejected": failed, "success_rate": round(passed / done * 100, 1) if done else 0.0,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "current_node": current_node,
        "latest_speed_kbps": round(latest.get("download_speed_bps", 0) / 1000, 1),
        "latest_category": latest.get("speed_category", "checking"),
        "app_checks": latest.get("application_checks", {}),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(path)


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



def load_source_preference(path: Path) -> str:
    """Read the last source that successfully passed candidate/manifest SHA256 verification."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        source = data.get("last_successful_source") if isinstance(data, dict) else None
        return source if isinstance(source, str) else ""
    except (OSError, ValueError, TypeError):
        return ""


def save_source_preference(path: Path, source: str) -> None:
    """Persist a verified source atomically for the next probe run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_successful_source": source,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temp_path.replace(path)

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
                           repo: str, branch: str,
                           preference_path: Path) -> tuple[bytes, dict, dict, str]:
    """Try the last verified source first, then fall back through all mirrors."""
    candidate_sources = mirror_urls(candidate_url, repo, branch)
    manifest_sources = dict(mirror_urls(manifest_url, repo, branch))
    preferred_source = load_source_preference(preference_path)
    source_names = {name for name, _ in candidate_sources}
    if preferred_source in source_names:
        candidate_sources.sort(key=lambda item: item[0] != preferred_source)
        print(f"Source preference: trying last successful source first ({preferred_source})")
    else:
        print("Source preference: no usable history; using default source order")
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
                save_source_preference(preference_path, source)
                print(f"Candidate and manifest downloaded via {source}; SHA256 verified")
                print(f"Next run will prefer this verified source; preference saved to {preference_path}")
                return candidate_response.content, dict(candidate_response.headers), manifest, source
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{source} attempt {attempt}: {type(exc).__name__}: {exc}")
                print(f"Source {source} failed (attempt {attempt}/2): {type(exc).__name__}")
    raise RuntimeError("All candidate sources failed verification: " + " | ".join(errors[-8:]))


def main() -> None:
    global CANDIDATE_UPDATED_AT, CANDIDATE_FETCHED_AT
    parser = argparse.ArgumentParser(description="AutoSubHub domestic phone download probe")
    parser.add_argument("--candidate-url", default=CANDIDATE_URL)
    parser.add_argument("--candidate-file", default="", help="Use a previously downloaded local candidate YAML without fetching GitHub")
    parser.add_argument("--manifest-url", default=MANIFEST_URL)
    parser.add_argument("--workers", type=int, default=int(os.getenv("PROBE_WORKERS", "5")))
    parser.add_argument("--limit", type=int, default=0,
                        help="Optional maximum number of candidate nodes to probe; 0 means all")
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
    parser.add_argument("--progress", default="progress.json", help="Live progress snapshot for the Android app")
    parser.add_argument("--mihomo-diagnostic", default="diagnostic_mihomo.json",
                        help="Save Mihomo loaded-proxy and selector membership diagnostics")
    args = parser.parse_args()
    started = time.monotonic()
    progress_path = Path(args.progress)
    write_progress(progress_path, "preparing", 0, 0, [], started, stage="正在获取候选节点")

    if not 1 <= args.workers <= 300:
        raise SystemExit("--workers must be between 1 and 300; increase gradually on phones")
    if args.limit < 0:
        raise SystemExit("--limit must be zero (all nodes) or a positive integer")
    if args.timeout < 1 or args.service_timeout < 1:
        raise SystemExit("timeouts must be at least 1 second")
    if args.min_speed_kbps <= 0 or args.high_speed_kbps <= args.min_speed_kbps:
        raise SystemExit("speed thresholds must be positive and high-speed must exceed minimum speed")
    if args.max_bytes < 256 * 1024:
        raise SystemExit("--max-bytes must be at least 256 KiB")

    if not 1024 <= args.api_port <= 65535 or not 1024 <= args.port_base <= 65535 - args.workers:
        raise SystemExit("API/listener ports must be valid non-privileged TCP ports")
    api_base = f"http://127.0.0.1:{args.api_port}"

    queue_state_path = Path.home() / ".autosubhub_probe_state.json"
    if args.candidate_file:
        candidate_path = Path(args.candidate_file).expanduser()
        manifest_path = candidate_path.with_suffix(".manifest.json")
        metadata_path = candidate_path.with_suffix(".meta.json")
        try:
            candidate_bytes = candidate_path.read_bytes()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
            if manifest.get("candidate_sha256") != candidate_hash or metadata.get("candidate_sha256") != candidate_hash:
                raise ValueError("Local candidate/manifest/metadata SHA256 mismatch")
            candidate_commits = metadata.get("candidate_commits", [])
            candidate_commit = metadata.get("candidate_commit", "")
            CANDIDATE_UPDATED_AT = metadata.get("candidate_updated_at", "")
            CANDIDATE_FETCHED_AT = metadata.get("candidate_fetched_at", "")
            source_used = "verified local candidate cache"
            print(f"使用本地候选文件：{candidate_path}")
            print(f"本地文件 SHA256 校验通过：{candidate_hash[:16]}…")
            print(f"候选版本更新时间（北京时间）：{CANDIDATE_UPDATED_AT or '未知'}")
            print(f"本机获取时间（北京时间）：{CANDIDATE_FETCHED_AT or '未知'}")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"Unable to load verified local candidate file: {type(exc).__name__}: {exc}") from exc
    else:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        try:
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            if not remote:
                raise RuntimeError("Git origin URL is empty")
            merged_candidate, manifest, candidate_commits, candidate_commit = (
                collect_pending_candidate_batches(
                    remote, REPO, BRANCH, session, queue_state_path, bootstrap_limit=20
                )
            )
            candidate_bytes = yaml.safe_dump(
                merged_candidate, allow_unicode=True, sort_keys=False
            ).encode("utf-8")
            candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
            manifest["candidate_sha256"] = candidate_hash
            source_used = "cumulative immutable candidate history"
            timestamp_result = subprocess.run(
                ["git", "show", "-s", "--format=%cI", "FETCH_HEAD"],
                check=True, capture_output=True, text=True, timeout=10,
            )
            raw_timestamp = timestamp_result.stdout.strip()
            parsed_timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
            beijing = parsed_timestamp.astimezone(timezone(timedelta(hours=8)))
            CANDIDATE_UPDATED_AT = beijing.strftime("%Y-%m-%d %H:%M:%S")
            CANDIDATE_FETCHED_AT = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
            print(f"候选版本更新时间（北京时间）：{CANDIDATE_UPDATED_AT}")
            print(f"本机获取时间（北京时间）：{CANDIDATE_FETCHED_AT}")
            print(f"累计批次：{len(candidate_commits)}；最新版本：{candidate_commit}")
        except (RuntimeError, ValueError, requests.RequestException, json.JSONDecodeError,
                subprocess.SubprocessError) as exc:
            raise SystemExit(f"Unable to collect pending candidate batches: {type(exc).__name__}: {exc}") from exc
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
    if args.limit:
        proxies = proxies[:args.limit]
        selected_names = {p["name"] for p in proxies}
        manifest_nodes = [n for n in manifest_nodes if n.get("name") in selected_names]
    # The receipt must be validated against the exact tested subset, not the
    # larger pre-limit candidate list.
    candidate = {"proxies": proxies}
    manifest["nodes"] = manifest_nodes
    candidate_bytes = yaml.safe_dump(
        candidate, allow_unicode=True, sort_keys=False
    ).encode("utf-8")
    candidate_hash = hashlib.sha256(candidate_bytes).hexdigest()
    manifest["candidate_sha256"] = candidate_hash
    print(f"累计批次合并后：{len(proxies)} 个唯一节点；SHA256 {candidate_hash[:16]}…")
    write_progress(progress_path, "running", 0, len(proxies), [], started, stage="正在启动 Mihomo")

    api = requests.Session()
    api.headers.update({"Authorization": f"Bearer {SECRET}"})
    receipt_results = []
    result_lock = threading.Lock()
    pause_lock = threading.Lock()
    pause_announced = [False]
    pause_path = progress_path.with_name("probe.pause")
    stop_path = progress_path.with_name("probe.stop")

    def wait_if_paused() -> bool:
        while pause_path.exists() and not stop_path.exists():
            with pause_lock:
                if not pause_announced[0]:
                    with result_lock:
                        write_progress(progress_path, "paused", len(receipt_results), len(proxies),
                                       receipt_results, started, stage="已暂停，等待继续")
                    pause_announced[0] = True
            time.sleep(0.5)
        with pause_lock:
            if pause_announced[0]:
                with result_lock:
                    write_progress(progress_path, "running", len(receipt_results), len(proxies),
                                   receipt_results, started, stage="继续检测节点")
                pause_announced[0] = False
        return not stop_path.exists()

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

            # Snapshot the live Mihomo API before probing. This is intentionally
            # written while the temporary Mihomo process is still running.
            diagnostic_path = Path(args.mihomo_diagnostic)
            candidate_names = [node["name"] for node in proxies]
            mihomo_diagnostic = {
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "api_port": args.api_port,
                "workers": args.workers,
                "candidate_count": len(candidate_names),
                "candidate_names": candidate_names,
                "status": "unavailable",
            }
            try:
                live_response = api.get(api_base + "/proxies", timeout=8)
                live_response.raise_for_status()
                live_proxies = live_response.json().get("proxies", {})
                if not isinstance(live_proxies, dict):
                    raise ValueError("Mihomo /proxies response has no valid proxies object")
                candidate_set = set(candidate_names)
                selectors = {}
                for worker_index in range(args.workers):
                    selector_name = f"PHONE-{worker_index:02d}"
                    selector_data = live_proxies.get(selector_name)
                    selector_data = selector_data if isinstance(selector_data, dict) else {}
                    members = selector_data.get("all", [])
                    members = members if isinstance(members, list) else []
                    member_set = set(item for item in members if isinstance(item, str))
                    missing = sorted(candidate_set - member_set)
                    selectors[selector_name] = {
                        "exists": bool(selector_data),
                        "type": selector_data.get("type"),
                        "now": selector_data.get("now"),
                        "member_count": len(members),
                        "candidate_member_count": len(candidate_set & member_set),
                        "missing_candidate_count": len(missing),
                        "missing_candidates": missing,
                    }
                missing_from_api = sorted(candidate_set - set(live_proxies))
                mihomo_diagnostic.update({
                    "status": "ok",
                    "api_proxy_count": len(live_proxies),
                    "api_proxy_names": sorted(live_proxies.keys()),
                    "missing_candidates_from_api": missing_from_api,
                    "missing_candidate_count_from_api": len(missing_from_api),
                    "selectors": selectors,
                })
            except Exception as diagnostic_error:
                mihomo_diagnostic["error"] = {
                    "type": type(diagnostic_error).__name__,
                    "message": str(diagnostic_error)[:500],
                }
            diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostic_temp = diagnostic_path.with_name(diagnostic_path.name + ".tmp")
            diagnostic_temp.write_text(
                json.dumps(mihomo_diagnostic, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            diagnostic_temp.replace(diagnostic_path)
            if mihomo_diagnostic["status"] == "ok":
                print(
                    f"Mihomo diagnostic saved: {diagnostic_path}; "
                    f"API proxies={mihomo_diagnostic['api_proxy_count']}; "
                    f"candidate names missing from API="
                    f"{mihomo_diagnostic['missing_candidate_count_from_api']}"
                )
                for selector_name, selector_data in mihomo_diagnostic["selectors"].items():
                    print(
                        f"  {selector_name}: exists={selector_data['exists']}, "
                        f"members={selector_data['member_count']}, "
                        f"candidate members={selector_data['candidate_member_count']}, "
                        f"missing={selector_data['missing_candidate_count']}"
                    )
            else:
                print(
                    f"Mihomo diagnostic saved with error: "
                    f"{mihomo_diagnostic.get('error', {}).get('message', 'unknown error')}"
                )

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
                        expected_status = 204 if service == "google" else 200
                        if response.status_code != expected_status:
                            raise RuntimeError(
                                f"{service} returned HTTP {response.status_code}; "
                                f"expected {expected_status}"
                            )

                        # A successful status from an unrelated redirect/interstitial
                        # must not count as reaching the requested service.
                        original_host = (urlparse(target_url).hostname or "").lower()
                        final_host = (urlparse(response.url).hostname or "").lower()
                        original_root = original_host[4:] if original_host.startswith("www.") else original_host
                        final_root = final_host[4:] if final_host.startswith("www.") else final_host
                        allowed_roots = {
                            "youtube": {"youtube.com", "youtube-nocookie.com"},
                            "telegram": {"telegram.org"},
                            "google": {"google.com"},
                            "chatgpt": {"chatgpt.com", "openai.com"},
                            "github": {"github.com"},
                        }.get(service, set())
                        # Custom test URLs remain usable: their own host is accepted.
                        host_ok = (
                            final_root == original_root
                            or final_root.endswith("." + original_root)
                            or any(final_root == root or final_root.endswith("." + root)
                                   for root in allowed_roots)
                        )
                        if not host_ok:
                            raise RuntimeError(
                                f"{service} redirected to unexpected host: {final_host or 'unknown'}"
                            )

                        content_type = response.headers.get("Content-Type", "").lower()
                        sample = b""
                        if service != "google":
                            sample = next(response.iter_content(chunk_size=1024), b"")
                            if not sample:
                                raise RuntimeError(f"{service} returned an empty response body")
                            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                                raise RuntimeError(
                                    f"{service} returned unexpected Content-Type: {content_type or 'missing'}"
                                )
                        status_code = response.status_code
                        final_url = response.url
                        redirect_count = len(response.history)
                    return {"status": "passed", "status_code": status_code,
                            "final_url": final_url, "redirect_count": redirect_count,
                            "content_type": content_type,
                            "sample_bytes": len(sample),
                            "elapsed_ms": round((time.perf_counter() - started) * 1000)}
                except Exception as exc:
                    return {"status": "failed", **exception_details(exc),
                            "elapsed_ms": round((time.perf_counter() - started) * 1000)}

            def probe_worker(worker_index: int, batch: list[dict]) -> None:
                selector = api_base + f"/proxies/PHONE-{worker_index:02d}"
                proxy_url = f"http://127.0.0.1:{args.port_base + worker_index}"
                worker_api = requests.Session()
                worker_api.headers.update({"Authorization": f"Bearer {SECRET}"})
                for node in batch:
                    if not wait_if_paused():
                        break
                    node_id = next((item.get("node_id") for item in manifest_nodes
                                    if item.get("name") == node["name"]), None)
                    record = {"node_id": node_id, "name": node["name"],
                              "status": "failed", "download_speed_bps": 0}
                    current_stage = "mihomo_select"
                    try:
                        selected = worker_api.put(selector, json={"name": node["name"]}, timeout=8)
                        selected.raise_for_status()
                        confirmed = worker_api.get(selector, timeout=8)
                        confirmed.raise_for_status()
                        if confirmed.json().get("now") != node["name"]:
                            raise RuntimeError("Mihomo selector did not switch to requested node")
                        current_stage = "application_checks"
                        # Five application checks are hard gates and run sequentially.
                        # Telegram Web verifies HTTPS reachability, not native MTProto login.
                        service_targets = {
                            "youtube": args.youtube_url,
                            "telegram": args.telegram_url,
                            "google": args.google_url,
                            "chatgpt": args.chatgpt_url,
                            "github": args.github_url,
                        }
                        record["application_checks"] = {}
                        # Run checks sequentially to isolate failures caused by per-node
                        # concurrent HTTPS handshakes. Node-level workers remain concurrent.
                        for service, target_url in service_targets.items():
                            check_result = run_service_check(
                                service, target_url, proxy_url, args.service_timeout
                            )
                            record["application_checks"][service] = check_result
                        passed_services = [
                            service for service, result in record["application_checks"].items()
                            if result["status"] == "passed"
                        ]
                        # All five HTTPS services must pass before download testing.
                        # Keep every result so failures remain diagnosable.
                        required_passes = len(service_targets)
                        service_failed = len(passed_services) != required_passes
                        record["application_gate"] = {
                            "required_passes": required_passes,
                            "passed_count": len(passed_services),
                            "passed_services": passed_services,
                            "status": "passed" if not service_failed else "failed",
                        }
                        if service_failed:
                            record["failure_stage"] = "application_checks"

                        if not service_failed:
                            current_stage = "download"
                            start = time.perf_counter()
                            received = 0
                            with requests.get(args.download_url,
                                              proxies={"http": proxy_url, "https": proxy_url},
                                              stream=True, timeout=(args.timeout, args.timeout),
                                              headers={"User-Agent": USER_AGENT,
                                                       "Accept-Encoding": "identity"}) as response:
                                response.raise_for_status()
                                if response.status_code != 200:
                                    raise RuntimeError(
                                        f"Download endpoint returned HTTP {response.status_code}; expected 200"
                                    )
                                content_length = response.headers.get("Content-Length")
                                record["download_content_length"] = (
                                    int(content_length) if content_length and content_length.isdigit() else None
                                )
                                record["expected_download_bytes"] = args.max_bytes
                                if (record["download_content_length"] is not None
                                        and record["download_content_length"] < args.max_bytes):
                                    raise RuntimeError(
                                        f"Download response is shorter than test size: "
                                        f"Content-Length={record['download_content_length']}, "
                                        f"expected={args.max_bytes}"
                                    )
                                for chunk in response.iter_content(chunk_size=64 * 1024):
                                    if not chunk:
                                        continue
                                    remaining = args.max_bytes - received
                                    received += min(len(chunk), remaining)
                                    if received >= args.max_bytes:
                                        break
                            record["downloaded_bytes"] = received
                            if received != args.max_bytes:
                                raise RuntimeError(
                                    f"Incomplete download: received {received} bytes, "
                                    f"expected {args.max_bytes}"
                                )
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
                        record.update(exception_details(exc))
                        record["failure_stage"] = current_stage
                    with result_lock:
                        receipt_results.append(record)
                        done = len(receipt_results)
                        write_progress(progress_path, "running", done, len(proxies),
                                       receipt_results, started, node["name"], record,
                                       stage="节点检测完成")
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
        finally:
            mihomo.terminate()
            try:
                mihomo.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mihomo.kill()

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_status = "interrupted" if stop_path.exists() else "completed"
    receipt = {
        "schema_version": 1,
        "status": run_status,
        "created_at": completed_at,
        "completed_at": completed_at,
        "candidate_sha256": candidate_hash,
        "candidate_commit": candidate_commit,
        "candidate_commits": candidate_commits,
        "candidate_batch_count": len(candidate_commits),
        "candidate_updated_at": CANDIDATE_UPDATED_AT,
        "candidate_fetched_at": CANDIDATE_FETCHED_AT,
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
        "max_download_bytes": args.max_bytes,
        "results": receipt_results,
    }
    Path(args.output).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
    output_path = Path(args.output)
    output_path.with_suffix(".candidate.yaml").write_bytes(candidate_bytes)
    output_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("批次快照已随回执保存。")
    passed = sum(r["status"] == "passed" for r in receipt_results)
    high = sum(r.get("status") == "passed" and r.get("speed_category") == "high" for r in receipt_results)
    normal = sum(r.get("status") == "passed" and r.get("speed_category") == "normal" for r in receipt_results)
    rejected = len(receipt_results) - high - normal
    print(f"Application gates + download: high={high}, normal={normal}, rejected={rejected}")
    print(f"Download probe: {passed}/{len(receipt_results)} passed; receipt: {args.output}")
    final_stage = "测速已停止，部分结果已保存" if run_status == "interrupted" else "测速完成"
    write_progress(progress_path, run_status, len(receipt_results), len(proxies),
                   receipt_results, started, stage=final_stage)



if __name__ == "__main__":
    main()
