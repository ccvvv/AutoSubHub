# Android domestic probe MVP

This is an additive, opt-in companion workflow. It does not change the existing Clash subscription build, its six-hour schedule, or its 500 KB/s GitHub-runner threshold.

## Device and responsibilities

- Device: Xiaomi Mi 6, stock MIUI, no root, mainland-China Wi-Fi.
- Proxy client/engine: use the Hiddify Android app (built on sing-box), not a separate SFA app. Hiddify already contains node selection, URL delay tests, and per-node upload/download tests.
- Probe implementation: add the domestic-probe feature inside a Hiddify fork. Do not build a second app that tries to control Hiddify from outside: its core-control gRPC channel is an internal app channel protected by app-private mTLS identity, and its Android VPN service is not exported.
- GitHub Actions: accepts reports and stores a latest snapshot. The normal subscription build remains independent until domestic results have been validated in real use.

## MVP stages

1. Establish a stable candidate manifest with opaque node IDs and no credentials in the report.
2. Fork Hiddify and add a probe screen/service inside its Flutter app, reusing its existing `ProxyRepository` and `HiddifyCoreService` rather than opening a public unauthenticated control port.
3. Implement serial testing first (one active proxy at a time): select one outbound, wait for the core to settle, make the GPT reachability request through Hiddify's proxy-only HTTP path, record delay, and run a bounded download test. Suggested initial limits: GPT request timeout 15 seconds; download sample 256 KiB; speed threshold 500 KiB/s; one retry for transient errors. These are initial tuning values, not proven device limits.
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
