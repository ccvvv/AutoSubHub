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
## Two-phone GitHub task loop (personal deployment)

The first deployment is intentionally hard-wired to the owner's repositories; it is not a public multi-tenant feature.

- Daily phone: reads the published subscription and the latest sanitized domestic-probe snapshot from this repository.
- GitHub: is the durable task mailbox and result store. It does not initiate a direct connection to the Android device.
- Xiaomi Mi 6: runs the Hiddify-based probe app, checks for a pending task, tests the requested candidate manifest, and uploads a sanitized result report.

### Task delivery model

Use a single active task for the MVP to avoid duplicate execution and conflicting writes:

1. A manual GitHub Actions workflow_dispatch creates a task with a unique task_id, candidate-manifest revision, creation time, and expiry.
2. The phone polls the task manifest when the probe app is open. Later, a foreground service may poll while the screen is off.
3. The phone records the task ID locally before starting, so process death does not silently restart a completed task.
4. The phone posts the result through repository_dispatch using the existing domestic-probe-results event.
5. The ingest workflow validates the task ID and report, stores the report, and marks that task complete. Expired or already-completed tasks are ignored.
6. The daily phone reads the updated snapshot; it never receives credentials or raw proxy URIs in the report.

Do not treat a successful GitHub API response as proof that the Actions workflow finished. The phone UI should distinguish “event accepted” from “result stored”. A task must have an expiry and a stable ID so a stale task cannot run forever.

### Polling and reliability policy

- MVP: user opens the probe screen and taps “Check GitHub task”; no hidden background polling is required.
- Next: foreground service with a persistent notification, conservative polling interval (for example, 5 minutes), network constraints, and exponential backoff after errors.
- Avoid frequent polling: GitHub API rate limits and Android background execution limits make second-by-second polling inappropriate.
- Persist task state locally: pending, running, uploading, completed, failed, or expired.
- On restart, resume only when the task is still valid; never upload the same report twice without an idempotency key (task_id).
- Keep the first implementation serial and foreground-driven. Only enable screen-off execution after real-device validation.

### ADB / MIUI assistance (no root)

ADB can improve the chance that Hiddify survives Doze/background restrictions, but cannot guarantee that MIUI will never kill it. The current Hiddify Android application ID is `app.hiddify.com`. Run these commands from a computer with USB debugging enabled and the phone authorized:

```sh
adb shell dumpsys deviceidle whitelist +app.hiddify.com
adb shell cmd appops set app.hiddify.com RUN_IN_BACKGROUND allow
adb shell cmd appops set app.hiddify.com RUN_ANY_IN_BACKGROUND allow
```

These commands are best-effort and may vary by Android/MIUI version. They do not bypass MIUI's own Autostart and battery-management controls. Configure those in MIUI Settings as well, and keep the app's foreground service notification visible during an active probe. Do not disable Doze globally or use undocumented force-stop workarounds.

The app should provide a diagnostics page showing battery-optimization exemption, notification permission, foreground-service state, VPN/core state, last successful GitHub poll, pending task ID, and last upload outcome. ADB cannot grant every vendor-specific MIUI permission; the UI must tell the user when a manual Settings action is required.

### Security and scope

- Pin the app to the owner's repository in this first release; do not build a generic repository picker yet.
- Use a narrowly scoped user-owned GitHub credential, stored using Android Keystore-backed encrypted storage. Never commit it, include it in task JSON, or write it to logs.
- Task manifests should contain only task metadata and opaque candidate IDs or a safe manifest reference. Never put UUIDs, passwords, private keys, raw proxy URIs, or private subscription URLs in a public task file.
- The report remains sanitized and user-owned. Do not automatically modify the published subscription based on one phone's test result in the MVP.
