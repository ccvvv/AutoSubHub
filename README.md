# AutoSubHub

Automatically aggregates public Clash-compatible subscription sources and runs a two-stage filter: GitHub Actions performs coarse checks, then a mainland-China Android Hiddify probe verifies candidates before GitHub Pages publishes the final subscription.

## Important privacy warning

This repository and the generated Pages output are public. Anyone who knows the published URL can download the generated proxy configuration and use the included nodes. Only add subscription sources that you are allowed and willing to expose. Do not commit private subscription URLs or credentials.

## Setup

1. Open **Settings → Secrets and variables → Actions**.
2. Optionally add a repository secret named `SUBSCRIPTION_URLS`, with one source URL per line. If absent, `subscriptions.txt` is used.
3. Open **Settings → Pages** and choose **GitHub Actions** as the build source if it is not already configured. The workflow also asks the Pages action to enablement-check/enable Pages; repository administration permission may still be required.
4. Open **Actions**, enable workflows if prompted, and run **Auto build subscription** manually once.

Final subscription (updated only after a valid Mi 6 domestic-probe receipt):

`https://ccvvv.github.io/AutoSubHub/subscription.yaml`

## Build pipeline

1. Fetch and parse subscription sources (Clash YAML and common VMess, VLESS, Trojan, Shadowsocks and SOCKS URI formats).
2. Deduplicate nodes and perform a concurrent TCP-connect prefilter. UDP-based protocols (Hysteria, Hysteria2, TUIC and WireGuard) bypass this TCP-only check so they are not incorrectly discarded.
3. Run a FreeNodePanel-style protocol handshake prefilter for TCP transports: DNS resolution, TCP connection, TLS handshake (certificate verification is disabled for reachability testing), and WebSocket/HTTPUpgrade `101` validation where applicable. Reality and UDP protocols are deliberately deferred to Mihomo to avoid false negatives from generic TLS/TCP probes.
4. Start the pinned Mihomo core in the GitHub Actions runner.
5. Ask Mihomo to test each proxy against `LATENCY_URL`; nodes that cannot make a real HTTP request through the proxy are excluded.
6. Sample download throughput for the fastest `DOWNLOAD_TEST_NODES` candidates. Download speed is informational; a node remains eligible if the speed-test endpoint is unavailable but its proxy latency test passed.
7. Validate YAML structure, proxy-group references, and the exact output with Mihomo's config checker.
8. The scheduled GitHub build writes at most 50 coarse-screened candidates to `data/domestic-probe/candidates.yaml` and their stable IDs to `candidate-manifest.json`; it does not replace the final Pages subscription. The Mi 6 downloads that candidate profile, performs domestic tests, and sends a sanitized receipt. The receipt workflow publishes only nodes marked `passed`; if no node passes, the previous Pages subscription remains untouched.

## Domestic verification

- Candidate profile: `https://ccvvv.github.io/AutoSubHub/candidates.yaml`.
- Candidate manifest: `https://ccvvv.github.io/AutoSubHub/candidate-manifest.json`.
- Candidate node names carry stable `probe_<24-hex-id>__` identifiers. The Mi 6 reports those IDs only; it does not upload proxy URIs or credentials.
- The final Pages subscription is updated only after the receipt workflow matches the report revision and IDs against the latest candidate batch.

## Measurement notes

- TCP connect latency is only a cheap first filter.
- Mihomo delay is a real HTTP request through the proxy, but it measures the GitHub Actions runner's network route, not the user's phone or local ISP.
- Download speeds are measured from GitHub Actions and can differ substantially from Karing/FlClash results on Android.
- Download tests are limited to a small candidate sample to avoid wasting runner time and bandwidth.
- The workflow uses the Mihomo Linux AMD64 release binary pinned to v1.19.34.

## Optional repository variables

- `MAX_WORKERS`: TCP prefilter concurrency (default 80).
- `CONNECT_TIMEOUT`: TCP/TLS/WS connection timeout in seconds (default 3; handshake stage uses at least 4 seconds).\n- `HANDSHAKE_WORKERS`: concurrent protocol-handshake checks (default 60).
- `MAX_NODES`: maximum candidates entering TCP prefilter (default 500).
- `HTTP_WORKERS`: concurrent Mihomo proxy delay requests (default 40).
- `HTTP_TEST_TIMEOUT`: HTTP delay test timeout in milliseconds (default 8000).
- `DOWNLOAD_TEST_NODES`: number of top latency nodes to sample for download speed (default 25).
- `DOWNLOAD_BYTES`: target download sample size in bytes (default 1048576).

## Schedule

Runs every 6 hours at minute 17 (UTC). GitHub scheduled workflows may be delayed during high load. A manual run is also available.

## License

MIT
