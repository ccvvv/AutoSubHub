"""Independent streaming-platform availability checks for one selected Mihomo node.

These are lightweight HTTP availability probes, not full video-playback tests.
"""
from __future__ import annotations

from concurrent.futures import Executor
from typing import Any

import requests

USER_AGENT = "AutoSubHub-Streaming-Check/1.0"

# Keep this list limited to media/streaming platforms. Each probe is deliberately
# independent from the original five application checks.
PLATFORMS = {
    "netflix": "https://www.netflix.com/title/80018499",
    "youtube_premium": "https://www.youtube.com/premium",
    "disney_plus": "https://www.disneyplus.com/",
    "bilibili": "https://api.bilibili.com/x/web-interface/nav",
    "spotify": "https://open.spotify.com/",
    "tiktok": "https://www.tiktok.com/",
}


def _check_one(name: str, url: str, proxy_url: str, timeout: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "error",
        "confidence": "low",
        "detail": "未能完成检测",
    }
    headers = {"User-Agent": USER_AGENT}
    if name == "bilibili":
        headers["Referer"] = "https://www.bilibili.com/"
    try:
        response = requests.get(
            url,
            proxies={"http": proxy_url, "https": proxy_url},
            timeout=(timeout, timeout),
            headers=headers,
        )
        code = response.status_code
        result["http_status"] = code

        if name == "netflix":
            if code in (200, 301, 302):
                result.update(status="yes", confidence="medium",
                              detail="Netflix 页面可访问；不代表所有内容均可播放")
            elif code == 403:
                result.update(status="no", confidence="high", detail="HTTP 403")
            else:
                result.update(status="error", detail=f"HTTP {code}")
        elif name == "youtube_premium":
            if code == 200 and "premium" in response.text[:200000].lower():
                result.update(status="yes", confidence="medium",
                              detail="Premium 页面可访问且包含相关标记")
            elif code == 200:
                result.update(status="partial", confidence="low",
                              detail="页面可访问，但未发现 Premium 标记")
            elif code == 403:
                result.update(status="no", confidence="medium", detail="HTTP 403")
            else:
                result.update(status="error", detail=f"HTTP {code}")
        elif name == "disney_plus":
            body = response.text[:200000].lower()
            if code in (301, 302):
                result.update(status="yes", confidence="low",
                              detail="服务发生重定向；地区解锁判断置信度较低")
            elif code == 200 and "disney" in body:
                result.update(status="yes", confidence="medium",
                              detail="Disney+ 页面可访问")
            elif code == 403:
                result.update(status="no", confidence="medium", detail="HTTP 403")
            elif code == 200:
                result.update(status="partial", confidence="low",
                              detail="HTTP 200，但页面未出现 Disney 标记")
            else:
                result.update(status="error", detail=f"HTTP {code}")
        elif name == "bilibili":
            if code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                if isinstance(payload, dict) and payload.get("code") == 0:
                    result.update(status="yes", confidence="medium",
                                  detail="Bilibili API 返回 code=0")
                else:
                    result.update(status="no", confidence="medium",
                                  detail="Bilibili API 未返回 code=0")
            elif code == 403:
                result.update(status="no", confidence="medium", detail="HTTP 403")
            else:
                result.update(status="error", detail=f"HTTP {code}")
        elif name in ("spotify", "tiktok"):
            if code == 200:
                result.update(status="yes", confidence="medium",
                              detail="服务页面可访问；不代表实际播放验证")
            elif code == 403:
                result.update(status="no", confidence="medium", detail="HTTP 403")
            else:
                result.update(status="error", detail=f"HTTP {code}")
    except requests.RequestException as exc:
        result.update(status="error", detail=f"{type(exc).__name__}: {str(exc)[:160]}")
    except Exception as exc:  # Keep one platform failure isolated from other platforms.
        result.update(status="error", detail=f"{type(exc).__name__}: {str(exc)[:160]}")
    return result


def summarize_streaming(checks: dict[str, dict[str, Any]]) -> str:
    """At least one explicit yes passes; partial/error/no never count as yes."""
    return "passed" if any(
        isinstance(item, dict) and item.get("status") == "yes"
        for item in checks.values()
    ) else "failed"


def check_streaming_services(
    proxy_url: str, timeout: int = 2, executor: Executor | None = None
) -> tuple[dict, str]:
    """Check through the selected node proxy, using a bounded shared executor when supplied."""
    if executor is None:
        checks = {
            name: _check_one(name, url, proxy_url, timeout)
            for name, url in PLATFORMS.items()
        }
    else:
        futures = {
            name: executor.submit(_check_one, name, url, proxy_url, timeout)
            for name, url in PLATFORMS.items()
        }
        checks = {name: future.result() for name, future in futures.items()}
    return checks, summarize_streaming(checks)
