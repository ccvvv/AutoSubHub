# AutoSubHub

Automatically aggregates public Clash-compatible subscription sources, filters unreachable nodes, tests real proxy connectivity with the Mihomo core, samples download speed, validates the final configuration, and publishes it with GitHub Actions + GitHub Pages.

## Important privacy warning

This repository and the generated Pages output are public. Anyone who knows the published URL can download the generated proxy configuration and use the included nodes. Only add subscription sources that you are allowed and willing to expose. Do not commit private subscription URLs or credentials.

## Setup

1. Open **Settings → Secrets and variables → Actions**.
2. Optionally add a repository secret named `SUBSCRIPTION_URLS`, with one source URL per line. If absent, `subscriptions.txt` is used.
3. Open **Settings → Pages** and choose **GitHub Actions** as the build source if it is not already configured. The workflow also asks the Pages action to enablement-check/enable Pages; repository administration permission may still be required.
4. Open **Actions**, enable workflows if prompted, and run **Auto build subscription** manually once.

Published subscription (after a successful deployment):

`https://ccvvv.github.io/AutoSubHub/subscription.yaml`

## Build pipeline

1. Fetch and parse subscription sources (Clash YAML and common VMess, VLESS, Trojan, Shadowsocks and SOCKS URI formats).
2. Deduplicate nodes and perform a concurrent TCP-connect prefilter.
3. Start the pinned Mihomo core in the GitHub Actions runner.
4. Ask Mihomo to test each proxy against `LATENCY_URL`; nodes that cannot make a real HTTP request through the proxy are excluded.
5. Sample download throughput for the fastest `DOWNLOAD_TEST_NODES` candidates. Download speed is informational; a node remains eligible if the speed-test endpoint is unavailable but its proxy latency test passed.
6. Validate YAML structure, proxy-group references, and the exact output with Mihomo's config checker.
7. Only after all build steps succeed does the Pages deployment run. A failed build does not deploy a replacement artifact, leaving the previous published Pages version untouched.

## Measurement notes

- TCP connect latency is only a cheap first filter.
- Mihomo delay is a real HTTP request through the proxy, but it measures the GitHub Actions runner's network route, not the user's phone or local ISP.
- Download speeds are measured from GitHub Actions and can differ substantially from Karing/FlClash results on Android.
- Download tests are limited to a small candidate sample to avoid wasting runner time and bandwidth.
- The workflow uses the Mihomo Linux AMD64 release binary pinned to v1.19.34.

## Optional repository variables

- `MAX_WORKERS`: TCP prefilter concurrency (default 80).
- `CONNECT_TIMEOUT`: TCP timeout in seconds (default 3).
- `MAX_NODES`: maximum candidates entering TCP prefilter (default 500).
- `HTTP_WORKERS`: concurrent Mihomo proxy delay requests (default 40).
- `HTTP_TEST_TIMEOUT`: HTTP delay test timeout in milliseconds (default 8000).
- `DOWNLOAD_TEST_NODES`: number of top latency nodes to sample for download speed (default 25).
- `DOWNLOAD_BYTES`: target download sample size in bytes (default 1048576).

## Schedule

Runs at minute 17 of every hour (UTC). GitHub scheduled workflows may be delayed during high load. A manual run is also available.

## License

MIT
