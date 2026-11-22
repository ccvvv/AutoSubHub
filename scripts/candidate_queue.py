"""Helpers for merging immutable phone-probe candidate batches."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import OrderedDict
from pathlib import Path

import requests
import yaml


def canonical_fingerprint(proxy: dict) -> str:
    """Identify a proxy by its configuration, excluding its display name."""
    config = {key: value for key, value in proxy.items() if key != "name"}
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def merge_candidate_snapshots(snapshots: list[tuple[str, dict, dict]]) -> tuple[dict, dict, list[str]]:
    """Merge (commit, candidate, manifest) snapshots; retain first name and all provenance."""
    merged = OrderedDict()
    commits = []
    for commit, candidate, manifest in snapshots:
        if commit not in commits:
            commits.append(commit)
        proxies = candidate.get("proxies")
        nodes = manifest.get("nodes")
        if not isinstance(proxies, list) or not isinstance(nodes, list):
            raise ValueError(f"Invalid candidate snapshot at {commit}")
        by_name = {item.get("name"): item for item in proxies
                   if isinstance(item, dict) and isinstance(item.get("name"), str)}
        if len(by_name) != len(proxies):
            raise ValueError(f"Invalid or duplicate proxy names at {commit}")
        manifest_by_name = {item.get("name"): item for item in nodes
                            if isinstance(item, dict) and isinstance(item.get("name"), str)}
        if len(manifest_by_name) != len(nodes) or set(manifest_by_name) != set(by_name):
            raise ValueError(f"Manifest does not match candidate at {commit}")
        for name, proxy in by_name.items():
            fingerprint = canonical_fingerprint(proxy)
            entry = merged.setdefault(fingerprint, {
                "proxy": dict(proxy), "source_commits": [], "source_names": [],
            })
            if commit not in entry["source_commits"]:
                entry["source_commits"].append(commit)
            if name not in entry["source_names"]:
                entry["source_names"].append(name)

    proxies = []
    nodes = []
    used_names = set()
    for fingerprint, entry in merged.items():
        proxy = dict(entry["proxy"])
        original_name = proxy["name"]
        if original_name in used_names:
            proxy["name"] = f"{original_name} [{fingerprint[:6]}]"
        used_names.add(proxy["name"])
        proxies.append(proxy)
        nodes.append({
            "node_id": fingerprint,
            "name": proxy["name"],
            "source_commits": entry["source_commits"],
            "source_names": entry["source_names"],
        })
    return {"proxies": proxies}, {"nodes": nodes}, commits


def collect_pending_candidate_batches(remote: str, repo: str, branch: str,
                                      session: requests.Session, state_path: Path,
                                      bootstrap_limit: int = 20) -> tuple[dict, dict, list[str], str]:
    """Fetch every candidate revision newer than the last uploaded phone receipt."""
    ref = f"refs/heads/{branch}"
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip() == "true"
    fetch_args = ["git", "fetch", "--quiet"]
    fetch_args += ["--unshallow"] if shallow else []
    fetch_args += [remote, ref]
    subprocess.run(fetch_args, check=True, capture_output=True, text=True, timeout=180)
    commits_text = subprocess.run(
        ["git", "log", "FETCH_HEAD", "--format=%H", "--", "candidate/subscription.yaml"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout
    history = [line.strip().lower() for line in commits_text.splitlines() if line.strip()]
    if not history:
        raise RuntimeError("No candidate subscription history found on published branch")
    tip = history[0]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        cursor = state.get("last_uploaded_candidate_commit", "")
    except (OSError, ValueError, TypeError):
        cursor = ""

    if cursor:
        if cursor not in history:
            raise RuntimeError(
                "Saved phone queue cursor is not present in published history; "
                "refusing to skip candidate batches. Keep the state file and investigate."
            )
        pending = history[:history.index(cursor)]
    else:
        # First run after installing the queue: backfill a bounded recent window.
        pending = history[:max(1, bootstrap_limit)]
    if not pending:
        raise RuntimeError("No new candidate batches since the last successful receipt upload")

    ordered_commits = list(reversed(pending))
    snapshots = []
    for commit in ordered_commits:
        base = f"https://raw.githubusercontent.com/{repo}/{commit}/candidate/"
        candidate_response = session.get(base + "subscription.yaml", timeout=45)
        candidate_response.raise_for_status()
        manifest_response = session.get(base + "manifest.json", timeout=45)
        manifest_response.raise_for_status()
        candidate_bytes = candidate_response.content
        manifest = manifest_response.json()
        digest = hashlib.sha256(candidate_bytes).hexdigest()
        if manifest.get("candidate_sha256") != digest:
            raise ValueError(f"Candidate/manifest SHA256 mismatch at {commit}")
        candidate = yaml.safe_load(candidate_bytes) or {}
        snapshots.append((commit, candidate, manifest))

    merged_candidate, merged_manifest, source_commits = merge_candidate_snapshots(snapshots)
    return merged_candidate, merged_manifest, source_commits, tip
