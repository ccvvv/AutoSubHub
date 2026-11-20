# AutoSubHub Android Domestic Probe (MVP)

## Current scope

- Android 9+ app scaffold (minSdk 28).
- Downloads the public AutoSubHub Clash subscription over HTTPS and reports HTTP status, size, and a rough YAML proxy-entry count.
- Submits an explicitly supplied, sanitized result array to the repository's repository_dispatch endpoint.
- Does **not** start a proxy, switch nodes, test GPT through a proxy, or measure download throughput yet. Do not label placeholder/inconclusive records as successful tests.

## Build

Requires JDK 17 and Android SDK Platform 35. From the repository root:

```sh
gradle --no-daemon :app:assembleDebug
```

APK output after a successful build: app/build/outputs/apk/debug/app-debug.apk

## GitHub token

Create a fine-grained token limited to this repository. Grant the repository-level Actions permission required by the dispatch endpoint. Never commit the token or paste it into source code. The app keeps it in the password field only for the current process and clears it after a successful HTTP 204 response.

## Report contract

The JSON editor accepts 1–50 result objects with:
- node_id: opaque identifier, 8–80 characters, letters/numbers/underscore/hyphen only
- protocol: shadowsocks, vmess, vless, trojan, hysteria2, tuic, or other
- status: passed, failed, or inconclusive
- gpt_ok: boolean
- latency_ms and download_kbps: number or null

Never include proxy URLs, server addresses, UUIDs, passwords, subscription URLs, or tokens in reports.

## Next engineering gate

Before implementing automated node tests, validate the exact sing-box integration on the Mi 6. SFA is a separate VPN app and must not be assumed to expose an automation API. The next implementation should use a supported embedded libbox integration or another documented control surface, then test one node at a time with explicit Android VPN consent.
