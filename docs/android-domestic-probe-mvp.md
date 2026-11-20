# Android domestic probe MVP

This is an additive, opt-in companion workflow. It does not change the existing Clash subscription build, its six-hour schedule, or its 500 KB/s GitHub-runner threshold.

## Device and responsibilities

- Device: Xiaomi Mi 6, stock MIUI, no root, mainland-China Wi-Fi.
- Proxy engine: sing-box Android (SFA) is a separate VPN app. It provides proxy protocol support, but does not expose a ready-made unattended batch-test-and-upload feature to a companion app.
- Companion app: planned Android 9+ controller. It will fetch a candidate list, ask the proxy engine to test one candidate at a time, run a GPT reachability check through the selected proxy, measure a bounded download sample, and submit sanitized results.
- GitHub Actions: accepts reports and stores a latest snapshot. The normal subscription build remains independent until domestic results have been validated in real use.

## MVP stages

1. Establish a stable candidate manifest with opaque node IDs and no credentials in the report.
2. Confirm on the Mi 6 that the chosen sing-box integration can start/stop a single proxy and route test requests through it. SFA itself should not be assumed to provide an automation API; embedding libbox in a dedicated app may be required.
3. Implement serial testing first (one active proxy at a time). Suggested initial limits: connect/GPT request timeout 15 seconds; download sample 256 KiB; speed threshold 500 KiB/s; one retry for transient errors. These are initial tuning values, not proven device limits.
4. Submit results using GitHub `repository_dispatch` event type `domestic-probe-results`.
5. After several successful real-device runs, decide whether domestic-pass results should influence published subscriptions. Do not automatically remove nodes based on a single Wi-Fi probe.

## Report contract

The app sends a JSON object as `client_payload`:

```json
{
  "device_id": "mi6-probe-01",
  "network": "mainland-wifi",
  "tested_at": "2026-11-20T12:00:00Z",
  "results": [
    {
      "node_id": "opaque-stable-id",
      "protocol": "shadowsocks",
      "status": "passed",
      "gpt_ok": true,
      "latency_ms": 420,
      "download_kbps": 780.5
    }
  ]
}
```

Allowed status values: `passed`, `failed`, `inconclusive`. Never include subscription URLs, server credentials, UUIDs, passwords, private keys, full proxy URIs, or GitHub tokens in a report. Node IDs must be opaque identifiers generated from the candidate manifest, not raw connection details.

## GitHub authentication

The phone will need a dedicated fine-grained GitHub token to call the repository-dispatch API. Restrict it to this repository and the minimum permissions required by GitHub for dispatching the event. Store it only in Android Keystore-backed encrypted storage; never put it in source code, logs, exported reports, or repository files. Revoke it immediately if the phone is lost. A token on a device is still a credential and can be extracted from a compromised device.

## Important limitations

- A mainland Wi-Fi test represents that Wi-Fi/ISP route, not all mainland mobile carriers.
- A successful TCP/UDP reachability check is not proof that a proxy protocol handshake works.
- GPT checks must be made through the candidate proxy, not directly.
- Certificate/TLS checks alone do not prove ChatGPT app compatibility.
- Android VPN service lifecycle and MIUI background restrictions can interrupt long runs. Keep the first version foreground-driven and serial.
- The existing GitHub Actions tests run from GitHub-hosted infrastructure and remain a separate measurement column.
