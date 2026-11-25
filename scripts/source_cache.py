#!/usr/bin/env python3
"""Private, URL-hash keyed HTTP validators for subscription source caching.

Only ETag and Last-Modified response metadata are persisted. Source URLs,
credentials, and response bodies are never written to this cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Mapping

SCHEMA_VERSION = 1


def source_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def empty_cache() -> dict:
    return {"schema_version": SCHEMA_VERSION, "sources": {}}


def load_cache(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return empty_cache()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict)
                or value.get("schema_version") != SCHEMA_VERSION
                or not isinstance(value.get("sources"), dict)):
            raise ValueError("Unsupported source cache schema")
        sources = value["sources"]
        for key, entry in sources.items():
            if (not isinstance(key, str) or len(key) != 64
                    or not isinstance(entry, dict)
                    or any(name not in {"etag", "last_modified"} for name in entry)
                    or any(not isinstance(item, str) for item in entry.values())):
                raise ValueError("Malformed source cache entry")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Cannot read source cache safely") from exc


def conditional_headers(cache: Mapping, url: str) -> dict[str, str]:
    entry = cache.get("sources", {}).get(source_key(url), {})
    headers: dict[str, str] = {}
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    if entry.get("last_modified"):
        headers["If-Modified-Since"] = entry["last_modified"]
    return headers


def record_response(cache: dict, url: str, status_code: int,
                    response_headers: Mapping[str, str]) -> bool:
    """Record validators for a successful 200 only; return whether cache changed."""
    if status_code != 200:
        return False
    key = source_key(url)
    entry = {}
    etag = response_headers.get("ETag") or response_headers.get("etag")
    modified = response_headers.get("Last-Modified") or response_headers.get("last-modified")
    if etag:
        entry["etag"] = str(etag)
    if modified:
        entry["last_modified"] = str(modified)
    sources = cache.setdefault("sources", {})
    previous = sources.get(key)
    if entry:
        sources[key] = entry
    else:
        sources.pop(key, None)
    return previous != (entry if entry else None)


def save_cache(path: str | Path, cache: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=2) + "\\n"
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
