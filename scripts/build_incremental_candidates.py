#!/usr/bin/env python3
"""Build only the newly discovered phone-probe queue from per-source snapshots."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build as builder
from scripts.source_cache import load_cache, empty_cache, conditional_headers, record_response, save_cache
from scripts.source_snapshots import (
    apply_source_response, reconcile_sources, node_fingerprint, url_fingerprint,
)

USER_AGENT = builder.USER_AGENT


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def atomic_json(path: Path, value: dict) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def atomic_yaml(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    builder.write_yaml(str(temporary), value)
    raw = temporary.read_bytes()
    temporary.replace(path)
    return raw


def configured_sources() -> list[str]:
    configured = os.getenv("SUBSCRIPTION_URLS", "").strip()
    if configured:
        urls = [item.strip() for item in configured.splitlines() if item.strip()]
        source_mode = "Actions secret"
    else:
        with open("subscriptions.txt", encoding="utf-8") as handle:
            urls = [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]
        source_mode = "subscriptions.txt"
    extra_path = Path("extra-subscriptions.txt")
    if extra_path.exists():
        with extra_path.open(encoding="utf-8") as handle:
            extra = [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]
        known = set(urls)
        for url in extra:
            if url not in known:
                urls.append(url)
                known.add(url)
        if extra:
            source_mode += " + extra-subscriptions.txt"
    resolved, seen = [], set()
    for url in urls:
        item = builder.resolve_source_url(url)
        if item not in seen:
            resolved.append(item)
            seen.add(item)
    if not resolved:
        raise RuntimeError("No subscription sources configured")
    print(f"Loaded {len(resolved)} sources from {source_mode}")
    return resolved


def load_snapshots(directory: Path) -> dict[str, dict]:
    result = {}
    if not directory.exists():
        return result
    for path in sorted(directory.glob("source-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        source_id = data.get("source_id")
        if not isinstance(source_id, str) or path.stem != source_id:
            raise RuntimeError(f"Invalid source snapshot file: {path}")
        result[source_id] = data
    return result


def fetch_one(index: int, url: str, cache: dict, previous: dict | None):
    source_id = f"source-{index:03d}"
    headers = {"User-Agent": USER_AGENT}
    # A validator is safe only when its matching full source snapshot exists.
    if previous is not None and previous.get("source_url_sha256") == url_fingerprint(url):
        headers.update(conditional_headers(cache, url))
    response = None
    for attempt in range(3):
        response = requests.get(url, headers=headers, timeout=25)
        if response.status_code == 304:
            snapshot, _ = apply_source_response(previous, source_id, url, 304, None)
            return source_id, snapshot, [], 304, dict(response.headers)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
                continue
        response.raise_for_status()
        break
    parsed, rejected = builder.parse_source(response.text)
    snapshot, _ = apply_source_response(
        previous, source_id, url, response.status_code, parsed, dict(response.headers)
    )
    return source_id, snapshot, parsed, response.status_code, dict(response.headers), rejected


def main() -> None:
    urls = configured_sources()
    snapshot_dir = Path("source-snapshots")
    old_snapshots = load_snapshots(snapshot_dir)
    cache_path = os.getenv("SOURCE_CACHE_PATH", "source-cache.json")
    try:
        cache = load_cache(cache_path)
    except ValueError as exc:
        print(f"Source validator cache invalid; fetching unconditionally: {exc}")
        cache = empty_cache()

    fetched: dict[str, dict] = {}
    source_status: dict[str, dict] = {}
    cache_changed = False
    failures = []

    def fetch_job(item):
        index, url = item
        source_id = f"source-{index:03d}"
        previous = old_snapshots.get(source_id)
        try:
            return ("ok", index, url, previous, fetch_one(index, url, cache, previous), None)
        except Exception as exc:
            return ("error", index, url, previous, None, exc)

    results = []
    workers = max(1, min(int(os.getenv("SOURCE_WORKERS", "10") or "10"), len(urls)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(fetch_job, item) for item in enumerate(urls, 1)]
        for future in as_completed(futures):
            results.append(future.result())

    for outcome, index, url, previous, result, error in sorted(results, key=lambda row: row[1]):
        source_id = f"source-{index:03d}"
        if outcome == "ok":
            source_id, snapshot, parsed, status, headers, *tail = result
            fetched[source_id] = snapshot
            rejected = tail[0] if tail else 0
            source_status[source_id] = {
                "status": status, "node_count": len(snapshot["nodes"]),
                "rejected_http": rejected, "configured_url_sha256": url_fingerprint(url),
            }
            if status == 200:
                cache_changed = record_response(cache, url, status, headers) or cache_changed
            print(f"{source_id}: HTTP {status}; snapshot nodes={len(snapshot['nodes'])}; rejected HTTP={rejected}")
            continue
        if previous is None:
            source_status[source_id] = {
                "status": "missing", "node_count": 0,
                "error": type(error).__name__,
                "source_url_sha256": url_fingerprint(url),
                "configured_url_sha256": url_fingerprint(url),
            }
            failures.append(source_id)
            print(f"{source_id}: initial fetch failed ({type(error).__name__}); no empty snapshot created")
            continue
        fetched[source_id] = previous
        source_status[source_id] = {
            "status": "error", "node_count": len(previous.get("nodes", [])),
            "error": type(error).__name__,
            "configured_url_sha256": url_fingerprint(url),
        }
        failures.append(source_id)
        print(f"{source_id}: fetch/parse failed; retained previous snapshot ({type(error).__name__})")

    if not fetched:
        raise RuntimeError("No usable source snapshots are available; refusing to replace the existing pool")

    # Remove stale source groups only when the configured source list itself changed.
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for old_id in set(old_snapshots) - set(fetched):
        (snapshot_dir / f"{old_id}.json").unlink(missing_ok=True)
    for source_id, snapshot in fetched.items():
        atomic_json(snapshot_dir / f"{source_id}.json", snapshot)
    source_manifest = {
        "schema_version": 1,
        "updated_at": now_iso(),
        "sources": [
            {
                "source_id": f"source-{index:03d}",
                "source_url_sha256": fetched.get(f"source-{index:03d}", {}).get("source_url_sha256"),
                "configured_url_sha256": source_status.get(f"source-{index:03d}", {}).get(
                    "configured_url_sha256",
                    source_status.get(f"source-{index:03d}", {}).get("source_url_sha256"),
                ),
                "fetched_at": fetched.get(f"source-{index:03d}", {}).get("fetched_at"),
                "checked_at": fetched.get(f"source-{index:03d}", {}).get("checked_at"),
                "last_status": source_status.get(f"source-{index:03d}", {}).get("status", "missing"),
                "node_count": source_status.get(f"source-{index:03d}", {}).get("node_count", 0),
                "error": source_status.get(f"source-{index:03d}", {}).get("error"),
            }
            for index in range(1, len(urls) + 1)
        ],
    }
    atomic_json(Path("source-manifest.json"), source_manifest)
    if cache_changed:
        save_cache(cache_path, cache)

    state_path = Path("candidate/probe-state.json")
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"schema_version": 1, "nodes": {}}

    # Existing final nodes are historical completed nodes during the one-time migration.
    final_ids = builder.load_final_node_fingerprints()
    state_nodes = state.setdefault("nodes", {})
    for fingerprint in final_ids:
        state_nodes.setdefault(fingerprint, {
            "status": "completed", "result": "published",
            "completed_at": now_iso(), "source": "legacy-final-migration",
        })

    pool, queue, state = reconcile_sources(fetched, state, now_iso())
    atomic_yaml(Path("candidate/pool.yaml"), {"proxies": pool["proxies"]})
    atomic_json(Path("candidate/pool-manifest.json"), {
        "schema_version": 1, "generated_at": pool["generated_at"], "nodes": pool["nodes"],
    })

    # Run the existing cloud-side safety prefilter only on never-tested queue entries.
    pending_by_fp = {item["fingerprint"]: item for item in queue["nodes"]}
    candidates = queue["proxies"]
    state_nodes = state["nodes"]
    invalid = []
    validated = []
    for proxy in candidates:
        fingerprint = node_fingerprint(proxy)
        if builder.valid_reality_node(proxy):
            validated.append(proxy)
        else:
            invalid.append(fingerprint)
    for fingerprint in invalid:
        state_nodes[fingerprint].update({"status": "precheck_failed", "reason": "invalid_reality", "completed_at": now_iso()})

    tested = []
    if validated:
        with tempfile.TemporaryDirectory(prefix="autohub-incremental-") as tmp:
            test_path = str(Path(tmp) / "test.yaml")
            builder.write_yaml(test_path, builder.test_config(
                builder.unique_names(validated), builder.PROBE_WORKERS
            ))
            mihomo_bin = os.getenv("MIHOMO_BIN", "./mihomo")
            check = __import__("subprocess").run(
                [mihomo_bin, "-t", "-f", test_path], capture_output=True, text=True, timeout=45
            )
            if check.returncode:
                rejected = builder.find_mihomo_rejected_proxies(validated, mihomo_bin)
                if not rejected:
                    raise RuntimeError("Mihomo rejected the incremental queue but no invalid node could be isolated")
                rejected_ids = {node_fingerprint(item) for item in rejected}
                for fingerprint in rejected_ids:
                    state_nodes[fingerprint].update({"status": "precheck_failed", "reason": "mihomo_config", "completed_at": now_iso()})
                validated = [item for item in validated if node_fingerprint(item) not in rejected_ids]
                if validated:
                    builder.write_yaml(test_path, builder.test_config(
                        builder.unique_names(validated), builder.PROBE_WORKERS
                    ))
                    retry = __import__("subprocess").run(
                        [mihomo_bin, "-t", "-f", test_path], capture_output=True, text=True, timeout=45
                    )
                    if retry.returncode:
                        raise RuntimeError("Mihomo still rejects the incremental queue after isolating invalid nodes")
            if validated:
                process = __import__("subprocess").Popen(
                    [mihomo_bin, "-d", tmp, "-f", test_path],
                    stdout=__import__("subprocess").DEVNULL,
                    stderr=__import__("subprocess").STDOUT,
                )
                try:
                    api = requests.Session()
                    api.headers.update({"Authorization": "Bearer autohub-local-test"})
                    for _ in range(40):
                        if process.poll() is not None:
                            raise RuntimeError("Mihomo exited before the incremental HTTP API became ready")
                        try:
                            if api.get(builder.CONTROLLER + "/version", timeout=1).ok:
                                break
                        except requests.RequestException:
                            time.sleep(0.5)
                    else:
                        raise RuntimeError("Mihomo API did not become ready")

                    def proxy_delay(node):
                        from urllib.parse import quote
                        endpoint = builder.CONTROLLER + "/proxies/" + quote(node["name"], safe="") + "/delay"
                        for target in (builder.LATENCY_URL, builder.LATENCY_FALLBACK_URL):
                            try:
                                response = api.get(endpoint, params={"url": target, "timeout": builder.HTTP_TIMEOUT_MS},
                                                   timeout=builder.HTTP_TIMEOUT_MS / 1000 + 5)
                                response.raise_for_status()
                                delay = response.json().get("delay")
                                if delay and int(delay) > 0:
                                    return int(delay), node
                            except Exception:
                                continue
                        return None, node

                    with ThreadPoolExecutor(max_workers=builder.HTTP_WORKERS) as executor:
                        futures = [executor.submit(proxy_delay, item) for item in validated]
                        for future in as_completed(futures):
                            delay, node = future.result()
                            if delay is None:
                                state_nodes[node_fingerprint(node)].update({
                                    "status": "precheck_failed", "reason": "http_latency",
                                    "completed_at": now_iso(),
                                })
                            else:
                                tested.append((delay, node))
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except __import__("subprocess").TimeoutExpired:
                        process.kill()

    tested.sort(key=lambda item: item[0])
    passed_nodes = [dict(node) for _, node in tested]
    passed_fingerprints = {node_fingerprint(node) for node in passed_nodes}
    queue_manifest_nodes = []
    for node in passed_nodes:
        fingerprint = node_fingerprint(node)
        meta = pending_by_fp[fingerprint]
        queue_manifest_nodes.append({
            "node_id": fingerprint[:20],
            "name": node["name"],
            "sources": meta["sources"],
            "github_delay_ms": next(delay for delay, item in tested if node_fingerprint(item) == fingerprint),
        })

    candidate_path = Path("candidates/candidates.yaml")
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_bytes = atomic_yaml(candidate_path, {"proxies": passed_nodes})
    candidate_manifest = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "nodes": queue_manifest_nodes,
    }
    atomic_json(Path("candidates/manifest.json"), candidate_manifest)
    atomic_json(state_path, state)

    os.makedirs("candidates", exist_ok=True)
    with open(os.getenv("GITHUB_OUTPUT", "/dev/null"), "a", encoding="utf-8") as output:
        output.write(f"has_candidates={'true' if passed_nodes else 'false'}\n")
        output.write("has_source_updates=true\n")
        output.write(f"has_cache_updates={'true' if cache_changed else 'false'}\n")

    print(
        f"Incremental build complete: source_groups={len(fetched)}, "
        f"source_failures_retained={len(failures)}, pool_nodes={len(pool['proxies'])}, "
        f"new_queue_nodes={len(queue['nodes'])}, cloud_passed={len(passed_nodes)}"
    )


if __name__ == "__main__":
    main()
