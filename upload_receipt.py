#!/usr/bin/env python3
"""Upload a completed local AutoSubHub phone-probe receipt to GitHub.

The script never runs a probe and never deletes or modifies the local receipt.
Set GITHUB_TOKEN in the Termux environment; never hard-code the token here.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

import requests

DEFAULT_REPO = os.getenv("GITHUB_REPOSITORY", "ccvvv/AutoSubHub")
DEFAULT_BRANCH = "probe-receipts"
DEFAULT_PATH = "receipts/latest.json"
API_ROOT = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
USER_AGENT = "AutoSubHub-Termux-Receipt-Uploader/1.0"


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


def request(session: requests.Session, method: str, url: str, **kwargs):
    response = session.request(method, url, timeout=30, **kwargs)
    if response.status_code >= 400:
        detail = response.text[:1200].replace("\n", " ")
        raise RuntimeError(f"GitHub API {method} {url} failed "
                           f"({response.status_code}): {detail}")
    return response


def ensure_branch(session: requests.Session, repo_api: str, branch: str) -> None:
    ref_url = f"{repo_api}/git/ref/heads/{quote(branch, safe='')}"
    check = session.get(ref_url, timeout=20)
    if check.status_code == 200:
        return
    if check.status_code != 404:
        check.raise_for_status()

    main_ref = request(session, "GET", f"{repo_api}/git/ref/heads/main").json()
    create_url = f"{repo_api}/git/refs"
    payload = {"ref": f"refs/heads/{branch}", "sha": main_ref["object"]["sha"]}
    created = session.post(create_url, json=payload, timeout=30)
    if created.status_code in (201, 422):
        # 422 can mean another upload created the branch concurrently.
        verify = session.get(ref_url, timeout=20)
        if verify.status_code == 200:
            return
    if created.status_code >= 400:
        detail = created.text[:1200].replace("\n", " ")
        raise RuntimeError(f"Could not create branch {branch} "
                           f"({created.status_code}): {detail}")


def upload(raw: bytes, session: requests.Session, repo_api: str,
           branch: str, path: str) -> str:
    encoded_path = quote(path, safe="/")
    contents_url = f"{repo_api}/contents/{encoded_path}"
    params = {"ref": branch}
    current = session.get(contents_url, params=params, timeout=20)
    payload = {
        "message": "Upload Android phone probe receipt",
        "branch": branch,
        "content": base64.b64encode(raw).decode("ascii"),
    }
    if current.status_code == 200:
        payload["sha"] = current.json()["sha"]
    elif current.status_code != 404:
        current.raise_for_status()

    result = request(session, "PUT", contents_url, json=payload).json()
    return result["commit"]["sha"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload an existing local receipt; does not run node tests."
    )
    parser.add_argument("--input", default="receipt.json",
                        help="Local receipt JSON (default: receipt.json)")
    parser.add_argument("--repo", default=DEFAULT_REPO,
                        help="GitHub owner/repository (default: ccvvv/AutoSubHub)")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--path", default=DEFAULT_PATH)
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        print("Error: set GITHUB_TOKEN before uploading.", file=sys.stderr)
        return 2
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        print("Error: --repo must be owner/repository.", file=sys.stderr)
        return 2
    if args.path.startswith("/") or ".." in Path(args.path).parts:
        print("Error: --path must be a safe repository-relative path.", file=sys.stderr)
        return 2

    try:
        raw = validate_receipt(Path(args.input))
        session = requests.Session()
        session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        })
        repo_api = f"{API_ROOT}/repos/{args.repo}"
        ensure_branch(session, repo_api, args.branch)
        commit = upload(raw, session, repo_api, args.branch, args.path)
        print(f"Upload successful: {args.repo}/{args.path}")
        print(f"Branch: {args.branch}")
        print(f"Commit: {commit}")
        print(f"Local receipt kept: {Path(args.input).resolve()}")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError,
            requests.RequestException, RuntimeError, KeyError) as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
