#!/usr/bin/env python3
"""Match V2RayTun's post-cleanup clipboard export to AutoSubHub node IDs.

IMPORTANT: only use after the user has run V2RayTun's real-delay test and
manually invoked its "Delete invalid config (Test first)" action.
Unmatched nodes are reported as unresolved, never as failed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit


def read_clipboard() -> str:
    import shutil
    command = shutil.which("termux-clipboard-get")
    if not command:
        raise RuntimeError("termux-clipboard-get missing; install Termux:API and pkg install termux-api")
    result = subprocess.run([command], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def decode_lines(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []
    # V2RayTun may export plain share links or a Base64 subscription.
    if "://" not in text and re.fullmatch(r"[A-Za-z0-9+/=_-]+", text):
        padded = text + "=" * (-len(text) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
            if "://" in decoded:
                text = decoded
        except (ValueError, UnicodeDecodeError):
            pass
    return [line.strip() for line in text.splitlines() if "://" in line.strip()]


def tag(uri: str) -> str:
    try:
        return unquote(urlsplit(uri).fragment).strip()
    except ValueError:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Match V2RayTun's surviving exported configs to AutoSubHub node IDs")
    ap.add_argument("--map", default="candidate-cache/v2raytun-import.map.json")
    ap.add_argument("--clipboard", action="store_true", help="read Termux clipboard")
    ap.add_argument("--input", help="read exported text from a file instead of clipboard")
    ap.add_argument("--output", default="candidate-cache/v2raytun-survivors.json")
    ap.add_argument("--confirm-invalid-removed", action="store_true",
                    help="confirm V2RayTun real-delay test and invalid-config deletion were completed")
    args = ap.parse_args()

    if not args.confirm_invalid_removed:
        ap.error("refusing to label survivors passed without --confirm-invalid-removed")
    if bool(args.clipboard) == bool(args.input):
        ap.error("choose exactly one of --clipboard or --input")

    mapping_path = Path(args.map).expanduser()
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    raw = read_clipboard() if args.clipboard else Path(args.input).expanduser().read_text(encoding="utf-8")
    exported = decode_lines(raw)
    if not exported:
        raise SystemExit("No share links found in V2RayTun export; no receipt written")

    exact = {}
    names = {}
    for item in mapping.get("nodes", []):
        uri = item.get("uri")
        if uri:
            exact.setdefault(uri, []).append(item)
        names.setdefault(item.get("name", ""), []).append(item)

    # Older map files store the URI line index, so use the source links file
    # for exact matching when present.
    links_path = mapping_path.with_name(mapping_path.name.replace(".map.json", ".links.txt"))
    source_links = links_path.read_text(encoding="utf-8").splitlines() if links_path.exists() else []
    for item in mapping.get("nodes", []):
        line = item.get("line")
        if isinstance(line, int) and 1 <= line <= len(source_links):
            exact.setdefault(source_links[line - 1].strip(), []).append(item)

    results, unresolved, seen = [], [], set()
    for uri in exported:
        matches = exact.get(uri, [])
        match_method = "exact_uri"
        if not matches:
            matches = names.get(tag(uri), [])
            match_method = "unique_name_tag"
        unique = {item.get("node_id"): item for item in matches if item.get("node_id")}
        if len(unique) != 1:
            unresolved.append({"uri": uri, "reason": "no unique node_id match"})
            continue
        item = next(iter(unique.values()))
        node_id = item["node_id"]
        if node_id in seen:
            unresolved.append({"uri": uri, "reason": "duplicate exported node"})
            continue
        seen.add(node_id)
        results.append({
            "node_id": node_id,
            "name": item.get("name", ""),
            "status": "passed",
            "engine": "v2raytun_user_confirmed_real_delay_and_cleanup",
            "match_method": match_method,
        })

    report = {
        "schema_version": 1,
        "engine": "v2raytun",
        "status": "completed_with_unresolved" if unresolved else "completed",
        "candidate_sha256": mapping.get("candidate_sha256"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "confirmation": {
            "real_delay_test_completed": True,
            "invalid_configs_deleted_in_v2raytun": True,
            "confirmed_by_user": True,
        },
        "exported_link_count": len(exported),
        "matched_survivor_count": len(results),
        "unresolved_count": len(unresolved),
        "results": results,
        "unresolved": unresolved,
        "note": "Only exported survivors are marked passed. Missing/unmatched candidate nodes are not marked failed.",
    }
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已匹配保留节点：{len(results)}")
    print(f"未能匹配：{len(unresolved)}（不会判为失败）")
    print(f"结果文件：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
