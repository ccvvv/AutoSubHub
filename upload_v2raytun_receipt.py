#!/usr/bin/env python3
"""Upload a V2RayTun survivor receipt without advancing the Mihomo queue cursor."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "ccvvv/AutoSubHub"
BRANCH = "v2raytun-receipts"


def git(*args: str, cwd: Path | None = None, env=None, check=True):
    p = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                       text=True, capture_output=True, env=env)
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip() or "git command failed")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="Upload V2RayTun result separately from Mihomo probe receipts")
    ap.add_argument("--input", default="candidate-cache/v2raytun-survivors.json")
    ap.add_argument("--candidate", default="candidate-cache/candidate-local.yaml")
    ap.add_argument("--manifest", default="candidate-cache/candidate-local.manifest.json")
    ap.add_argument("--map", default="candidate-cache/v2raytun-import.map.json")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--branch", default=BRANCH)
    args = ap.parse_args()

    try:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
            raise ValueError("repo must be owner/name")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", args.branch) or ".." in args.branch.split("/"):
            raise ValueError("invalid branch")
        receipt_path = Path(args.input).expanduser()
        candidate_path = Path(args.candidate).expanduser()
        manifest_path = Path(args.manifest).expanduser()
        map_path = Path(args.map).expanduser()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        candidate_bytes = candidate_path.read_bytes()
        manifest_bytes = manifest_path.read_bytes()
        map_bytes = map_path.read_bytes()
        map_data = json.loads(map_bytes.decode("utf-8"))
        digest = hashlib.sha256(candidate_bytes).hexdigest()
        if receipt.get("schema_version") != 1 or receipt.get("engine") != "v2raytun":
            raise ValueError("not a V2RayTun receipt")
        if receipt.get("status") not in ("completed", "completed_with_unresolved"):
            raise ValueError("receipt is not completed")
        if receipt.get("candidate_sha256") != digest:
            raise ValueError("receipt/candidate SHA256 mismatch")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if manifest.get("candidate_sha256") != digest:
            raise ValueError("manifest/candidate SHA256 mismatch")
        if map_data.get("candidate_sha256") != digest:
            raise ValueError("node map/candidate SHA256 mismatch")
        if receipt.get("confirmation", {}).get("confirmed_by_user") is not True:
            raise ValueError("missing user confirmation")
        results = receipt.get("results")
        if not isinstance(results, list):
            raise ValueError("receipt results must be a list")
        token = os.getenv("GITHUB_TOKEN", "").strip()
        owner, name = args.repo.split("/", 1)
        auth_env = os.environ.copy()
        if token:
            encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            auth_env.update({
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
            })
            remote = f"https://github.com/{owner}/{name}.git"
            print("GitHub authentication: GITHUB_TOKEN over HTTPS")
        else:
            remote = f"git@github.com:{owner}/{name}.git"
            print("GitHub authentication: SSH key (GITHUB_TOKEN not set)")

        with tempfile.TemporaryDirectory(prefix="autosubhub-v2raytun-") as tmp:
            work = Path(tmp) / "repo"
            exists = git("ls-remote", "--exit-code", "--heads", remote,
                         f"refs/heads/{args.branch}", env=auth_env, check=False)
            if exists.returncode == 0 and exists.stdout.strip():
                git("clone", "--depth", "1", "--branch", args.branch, remote, str(work), env=auth_env)
            elif exists.returncode == 2:
                git("clone", "--depth", "1", "--branch", "main", remote, str(work), env=auth_env)
                git("checkout", "-b", args.branch, cwd=work, env=auth_env)
            else:
                raise RuntimeError((exists.stderr or exists.stdout).strip() or "unable to inspect remote branch")

            folder = work / "receipts" / "v2raytun"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "latest.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            (folder / "candidate.yaml").write_bytes(candidate_bytes)
            (folder / "manifest.json").write_bytes(manifest_bytes)
            (folder / "node-map.json").write_bytes(map_bytes)
            git("add", "--", "receipts/v2raytun/latest.json",
                "receipts/v2raytun/candidate.yaml",
                "receipts/v2raytun/manifest.json",
                "receipts/v2raytun/node-map.json", cwd=work, env=auth_env)
            diff = git("diff", "--cached", "--quiet", cwd=work, env=auth_env, check=False)
            if diff.returncode == 0:
                print("No new V2RayTun receipt changes to upload.")
                return 0
            if diff.returncode != 1:
                raise RuntimeError("unable to inspect staged changes")
            git("-c", "user.name=AutoSubHub V2RayTun",
                "-c", "user.email=ccvvv@users.noreply.github.com",
                "commit", "-m", "Upload V2RayTun real-delay survivor receipt",
                cwd=work, env=auth_env)
            git("push", "origin", args.branch, cwd=work, env=auth_env)

        print(f"V2RayTun receipt uploaded: {args.repo}/{args.branch}/receipts/v2raytun/latest.json")
        print(f"Matched survivors: {len(results)}; unresolved: {receipt.get('unresolved_count', 0)}")
        print("Mihomo candidate queue cursor was not changed.")
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        print(f"V2RayTun upload failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
