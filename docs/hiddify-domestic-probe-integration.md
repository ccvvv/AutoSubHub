# Hiddify-based domestic probe implementation

## Decision

Use a fork of the official Hiddify Android app and add the probe inside that app. Hiddify is already based on sing-box and has the proxy operations needed by the probe. A separate controller app is not the first choice because the Android core-control channel is internal to Hiddify and uses a client identity stored in Hiddify's private app directory.

Upstream source inspected:
- `lib/features/proxy/data/proxy_repository.dart`: `watchProxies`, `watchActiveProxies`, `selectProxy`, `urlTest`, `urlTestProxies`, `uploadTest`, `downloadTest`, and `clearSpeedTests`.
- `lib/hiddifycore/core_interface/core_interface_mobile.dart`: local gRPC on loopback ports 17078/17079; the mTLS client identity is loaded from Hiddify's private directory.
- `android/app/src/main/AndroidManifest.xml`: Hiddify's VPN service is `exported=false`.

## Probe workflow inside Hiddify

1. User opens **Domestic Probe** and explicitly starts a run.
2. Load the candidate profile already imported into Hiddify. Give each candidate a stable opaque `node_id`; never export its URI, UUID, password, private key, hostname, or raw subscription URL.
3. Enumerate the selectable outbound nodes from the existing proxy repository.
4. Test nodes serially in the first release:
   - select exactly one node with `selectProxy(groupTag, outboundTag)`;
   - wait for the selected outbound/core state to settle;
   - send a small HTTPS GET to `https://chatgpt.com/` using Hiddify's proxy-only HTTP path, with normal TLS certificate validation and a 15-second timeout;
   - record the final HTTP status, elapsed time, and whether the response proves ChatGPT reachability;
   - run a bounded per-node download test (initial sample target: 256 KiB) and read the result from Hiddify's proxy state;
   - proceed to the next node.
5. Mark a node `passed` only if the proxy selection succeeded, the GPT request reached an accepted ChatGPT response, and the download rate meets the configured threshold. Keep separate failure reasons; do not label an untested node as failed.
6. Show a summary and let the user submit it to AutoSubHub. Do not silently upload in the background in the MVP.

## Initial thresholds

- Concurrency: 1 (serial).
- GPT request timeout: 15 seconds.
- Download sample: 256 KiB.
- Initial minimum download rate: 500 KiB/s.
- Retry: one retry for timeout/transient network failure; do not retry permanent TLS or authentication errors.
- A result below the speed threshold is `failed` for the speed criterion, but preserve the raw measurement for later tuning.

All thresholds are configurable constants initially. Do not assume a single Wi-Fi test represents every mainland carrier.

## GPT reachability check

The request must travel through the currently selected Hiddify outbound, not the device's direct network. Use the app's existing proxy-aware HTTP client with proxy-only routing (the same general mechanism used by Hiddify's current-IP lookup). Do not use a plain Dart `HttpClient` that might bypass the selected proxy.

Treat HTTP success/redirect responses as reachable only after checking the final URL host and response behavior. A TLS certificate error, DNS failure, timeout, or connection reset is not a GPT pass. Record HTTP status and a compact failure code, never response bodies, cookies, or user data.

## GitHub upload

Use the existing AutoSubHub receiver:
- Event: `repository_dispatch`
- Event type: `domestic-probe-results`
- Payload: `client_payload` matching the schema documented in `docs/android-domestic-probe-mvp.md`.

Store a dedicated fine-grained GitHub token using Android Keystore-backed encrypted storage. Request only the minimum repository permission needed to dispatch the event. Never hardcode or log the token. Make upload user-initiated and provide a clear success/failure state.

## Security and platform constraints

- Do not expose Hiddify's gRPC service as an unauthenticated external API.
- Do not export the VPN service or share Hiddify's private mTLS identity with another app.
- Keep the probe UI and execution inside the Hiddify process so it can call the existing repository/service layer directly.
- Keep the first release foreground-driven; MIUI may suspend background work.
- Do not store full proxy configurations in probe history or GitHub reports.
- A report must contain only opaque node IDs, protocol family, status, GPT result, latency, download rate, timestamp, and non-sensitive failure code.

## Implementation sequence

1. Add a probe feature module and screen in the Hiddify fork; no core changes and no public control API yet.
2. Add a serial runner that calls the existing proxy repository methods.
3. Add a GPT probe adapter using Hiddify's proxy-aware HTTP client.
4. Add a result model and local run history with secrets excluded.
5. Add an explicit GitHub upload adapter for the existing AutoSubHub dispatch receiver.
6. Build a debug APK and test on the Mi 6: one known-good node, one known-bad node, then a small batch. Only then increase the batch size or change thresholds.

## Not included in this first implementation

- No 50/100/200-way concurrent tests.
- No automatic deletion or publication filtering based on one device report.
- No separate companion app controlling Hiddify.
- No changes to the existing six-hour GitHub subscription build.
