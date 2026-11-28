"""Helpers for merging immutable phone-probe candidate batches."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import OrderedDict
from pathlib import Path

import requests
import yaml

from scripts.node_identity import node_fingerprint


def canonical_fingerprint(proxy: dict) -> str:
    """Compatibility alias for the one canonical system-wide fingerprint."""
    return node_fingerprint(proxy)


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
            declared_id = manifest_by_name[name].get("node_id")
            declared_fingerprint = manifest_by_name[name].get("fingerprint")
            # Every published candidate manifest must carry the complete SHA-256 ID.
            if declared_id != fingerprint:
                raise ValueError(f"Candidate manifest identity mismatch at {commit}: {name}")
            if declared_fingerprint is not None and declared_fingerprint != fingerprint:
                raise ValueError(f"Candidate full fingerprint mismatch at {commit}: {name}")
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
            "fingerprint": fingerprint,
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
    # A commit that deletes candidate/subscription.yaml is present in ordinary
    # path history too, but it is not a downloadable candidate batch. Include
    # only commits that add or modify the immutable candidate snapshot.
    commits_text = subprocess.run(
        ["git", "log", "--diff-filter=AM", "FETCH_HEAD", "--format=%H",
         "--", "candidate/subscription.yaml"],
        check=True, capture_output=True, text=True, timeout=30,
    ).stdout
    history = [line.strip().lower() for line in commits_text.splitlines() if line.strip()]
    if not history:
        raise RuntimeError("No candidate subscription history found on published branch")
    tip = history[0]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        local_cursor = state.get("last_uploaded_candidate_commit", "")
    except (OSError, ValueError, TypeError):
        local_cursor = ""

    if local_cursor and local_cursor not in history:
        raise RuntimeError(
            "Saved phone queue cursor is not present in published history; "
            "refusing to skip candidate batches. Keep the state file and investigate."
        )

    # Always reconcile local state with the remote receipt. If GitHub accepted
    # an upload but Android stopped before saving its local cursor, the remote
    # receipt is authoritative and prevents the already-tested batches replaying.
    remote_cursor = ""
    receipt_url = (
        f"https://raw.githubusercontent.com/{repo}/published-subscriptions/receipts/latest.json"
    )
    try:
        previous_receipt = session.get(receipt_url, timeout=20)
        if previous_receipt.status_code == 200:
            previous_data = previous_receipt.json()
            previous_commit = previous_data.get("candidate_commit")
            if isinstance(previous_commit, str) and previous_commit:
                if previous_commit not in history:
                    raise RuntimeError(
                        "Remote receipt cursor is not present in published history; "
                        "refusing to skip candidate batches."
                    )
                remote_cursor = previous_commit
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        pass

    cursors = [item for item in (local_cursor, remote_cursor) if item]
    if cursors:
        # Git history is newest-first: the smaller index is the more advanced cursor.
        cursor = min(cursors, key=history.index)
        pending = history[:history.index(cursor)]
    else:
        # No earlier phone receipt exists: backfill a bounded recent window.
        pending = history[:max(1, bootstrap_limit)]
    if not pending:
        raise RuntimeError("No new candidate batches since the last successful receipt upload")

    ordered_commits = list(reversed(pending))
    snapshots = []
    for commit in ordered_commits:
        base = f"https://raw.githubusercontent.com/{repo}/{commit}/candidate/"
        mirrors = (
            ("GitHub Raw", base),
            ("GHFast", f"https://ghfast.top/{base}"),
            ("GH-Proxy", f"https://gh-proxy.com/{base}"),
        )
        errors = []
        verified = None
        for source, mirror in mirrors:
            try:
                candidate_response = session.get(mirror + "subscription.yaml", timeout=45)
                candidate_response.raise_for_status()
                manifest_response = session.get(mirror + "manifest.json", timeout=45)
                manifest_response.raise_for_status()
                candidate_bytes = candidate_response.content
                manifest = manifest_response.json()
                digest = hashlib.sha256(candidate_bytes).hexdigest()
                if manifest.get("candidate_sha256") != digest:
                    raise ValueError("candidate/manifest SHA256 mismatch")
                verified = (candidate_bytes, manifest)
                print(f"Candidate batch {commit[:10]} verified via {source}")
                break
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{source}: {type(exc).__name__}: {exc}")
        if verified is None:
            raise RuntimeError(
                f"Unable to download candidate batch {commit}: " + " | ".join(errors)
            )
        candidate_bytes, manifest = verified
        candidate = yaml.safe_load(candidate_bytes) or {}
        snapshots.append((commit, candidate, manifest))

    merged_candidate, merged_manifest, source_commits = merge_candidate_snapshots(snapshots)
    return merged_candidate, merged_manifest, source_commits, tip
