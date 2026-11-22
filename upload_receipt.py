#!/usr/bin/env python3
"""Push a completed local AutoSubHub phone-probe receipt to GitHub.

Uses GITHUB_TOKEN over HTTPS when supplied; otherwise falls back to SSH.
The script never runs a probe and never deletes or modifies the local receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import base64
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


DEFAULT_REPO = "ccvvv/AutoSubHub"
DEFAULT_BRANCH = "probe-receipts"
DEFAULT_PATH = "receipts/latest.json"
GIT_AUTH_ENV: dict[str, str] = {}


def validate_receipt(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError(f"Receipt file not found: {path}")
    raw = path.read_bytes()
    if not raw:
        raise ValueError("Receipt file is empty")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Receipt root must be a JSON object")
    if data.get("schema_version") != 1 or data.get("status") != "completed":
        raise ValueError("Receipt must have schema_version=1 and status=completed")
    digest = data.get("candidate_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Receipt candidate_sha256 is missing or invalid")
    commit = data.get("candidate_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Receipt candidate_commit is missing or invalid; update termux_probe.py and run a new probe")
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("Receipt results must be a non-empty list")
    ids = []
    for item in results:
        if not isinstance(item, dict):
            raise ValueError("Every result must be a JSON object")
        node_id = item.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("A result is missing node_id")
        if item.get("status") not in ("passed", "failed"):
            raise ValueError("A result has an invalid status")
        ids.append(node_id)
    if len(ids) != len(set(ids)):
        raise ValueError("Receipt contains duplicate node_id values")
    return raw


def git(*args: str, cwd: Path | None = None, check: bool = True):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, **GIT_AUTH_ENV},
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result


def save_queue_cursor(commits: list[str], receipt_commit: str) -> None:
    """Advance the local batch cursor after the receipt bundle is present on GitHub."""
    from datetime import datetime, timezone
    state_path = Path.home() / ".autosubhub_probe_state.json"
    state = {
        "last_uploaded_candidate_commit": commits[-1],
        "last_receipt_commit": receipt_commit,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_state = state_path.with_name(state_path.name + ".tmp")
    temporary_state.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    temporary_state.replace(state_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload an existing local receipt to GitHub; does not run node tests."
    )
    parser.add_argument("--input", default="receipt.json",
                        help="Local receipt JSON (default: receipt.json)")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help="GitHub owner/repository (default: ccvvv/AutoSubHub)")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--path", default=DEFAULT_PATH)
    args = parser.parse_args()

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        print("Error: --repo must be owner/repository.", file=sys.stderr)
        return 2
    if (not re.fullmatch(r"[A-Za-z0-9._/-]+", args.branch)
            or args.branch.startswith("-")
            or any(part in ("", ".", "..") for part in args.branch.split("/"))):
        print("Error: invalid branch name.", file=sys.stderr)
        return 2
    repo_path = PurePosixPath(args.path)
    if (repo_path.is_absolute() or not repo_path.parts
            or any(part in (".", "..") for part in repo_path.parts)):
        print("Error: --path must be a safe repository-relative path.", file=sys.stderr)
        return 2

    try:
        receipt_path = Path(args.input).expanduser()
        raw = validate_receipt(receipt_path)
        receipt_data = json.loads(raw.decode("utf-8"))
        candidate_path = receipt_path.with_suffix(".candidate.yaml")
        manifest_path = receipt_path.with_suffix(".manifest.json")
        if not candidate_path.is_file() or not manifest_path.is_file():
            raise ValueError("Candidate snapshot sidecars are missing; keep them with the receipt.")
        candidate_bytes = candidate_path.read_bytes()
        manifest_bytes = manifest_path.read_bytes()
        candidate_digest = hashlib.sha256(candidate_bytes).hexdigest()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if receipt_data.get("candidate_sha256") != candidate_digest:
            raise ValueError("Receipt/candidate snapshot SHA256 mismatch")
        if manifest.get("candidate_sha256") != candidate_digest:
            raise ValueError("Manifest/candidate snapshot SHA256 mismatch")
        commits = receipt_data.get("candidate_commits")
        if (not isinstance(commits, list) or not commits
                or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{40}", item)
                       for item in commits)):
            raise ValueError("Receipt candidate_commits list is missing or invalid")
        if receipt_data.get("candidate_commit") != commits[-1]:
            raise ValueError("Receipt candidate_commit does not match the newest queued batch")
        owner, name = args.repo.split("/", 1)
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if token:
            encoded = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
            GIT_AUTH_ENV.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
            })
            remote = f"https://github.com/{owner}/{name}.git"
            print("GitHub authentication: GITHUB_TOKEN over HTTPS")
        else:
            remote = f"git@github.com:{owner}/{name}.git"
            print("GitHub authentication: SSH key (GITHUB_TOKEN not set)")

        with tempfile.TemporaryDirectory(prefix="autosubhub-receipt-") as temp:
            work = Path(temp) / "repo"
            branch_ref = f"refs/heads/{args.branch}"
            exists = git("ls-remote", "--exit-code", "--heads", remote,
                         branch_ref, check=False)
            if exists.returncode == 0 and exists.stdout.strip():
                git("clone", "--depth", "1", "--branch", args.branch,
                    remote, str(work))
            elif exists.returncode == 2:
                git("clone", "--depth", "1", "--branch", "main",
                    remote, str(work))
                git("checkout", "-b", args.branch, cwd=work)
            else:
                raise RuntimeError((exists.stderr or exists.stdout).strip()
                                   or "Unable to check remote branch")

            destination = work.joinpath(*repo_path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            candidate_destination = work / "receipts" / "candidate.yaml"
            manifest_destination = work / "receipts" / "manifest.json"
            candidate_destination.write_bytes(candidate_bytes)
            manifest_destination.write_bytes(manifest_bytes)
            git("add", "--", str(repo_path), "receipts/candidate.yaml",
                "receipts/manifest.json", cwd=work)
            changed = git("diff", "--cached", "--quiet", cwd=work, check=False)
            if changed.returncode == 0:
                existing_commit = git("rev-parse", "HEAD", cwd=work).stdout.strip()
                save_queue_cursor(commits, existing_commit)
                print(f"Already uploaded: {args.repo}/{args.path}")
                print("Local candidate queue cursor synchronized.")
                print(f"Local receipt kept: {receipt_path.resolve()}")
                return 0
            if changed.returncode != 1:
                raise RuntimeError("Unable to inspect staged receipt changes")

            git("-c", "user.name=AutoSubHub Termux",
                "-c", "user.email=ccvvv@users.noreply.github.com",
                "commit", "-m", "Upload Android phone probe receipt", cwd=work)
            git("push", "origin", args.branch, cwd=work)
            commit = git("rev-parse", "HEAD", cwd=work).stdout.strip()

        # Advance only after the complete receipt bundle has been accepted by GitHub.
        save_queue_cursor(commits, commit)

        print(f"Upload successful: {args.repo}/{args.path}")
        print(f"Branch: {args.branch}")
        print(f"Commit: {commit}")
        print(f"Local receipt kept: {receipt_path.resolve()}")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
            RuntimeError) as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
