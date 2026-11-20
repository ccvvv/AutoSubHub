# Termux phone probe (initial implementation)

This script runs the download-speed stage on the phone's own network. GitHub performs format filtering and deduplication, then HTTPS-only Mihomo latency checks and ChatGPT HTTPS certificate checks. Standalone TCP/UDP and generic TLS/WebSocket handshake prefilters are disabled to avoid false negatives. Plain-HTTP captive-portal checks are disabled.

## One-command setup and run

In Termux, run this single command. It installs Python dependencies, downloads the Android Mihomo binary matching the phone architecture, downloads the probe script, asks for the GitHub token once, then runs the domestic-network speed test and uploads the receipt automatically.

```sh
curl -fsSL https://raw.githubusercontent.com/ccvvv/AutoSubHub/fix/https-only-github-prefilter/start_termux.sh -o start_termux.sh && bash start_termux.sh
```

The token is saved locally under `~/.config/autosubhub/github_token` with file permissions 600; it is not committed to GitHub. To change the token, replace that local file or export `GITHUB_TOKEN` before running the script again.

Defaults: 5 concurrent download tests, 5 MiB maximum per node, and 500 KB/s minimum. To change concurrency, set `PROBE_WORKERS` before running the script, for example `PROBE_WORKERS=10 bash start_termux.sh`. Increase concurrency gradually; 50 workers must first be validated on the actual phone and network.

## Important limitations

- This is an initial test implementation, not a verified Android release. It requires a compatible Android Mihomo binary; the Linux GitHub Actions binary is not suitable.
- The Cloudflare 5 MiB download endpoint is configurable with `--download-url`; test it on the target domestic network before relying on results.
- Start with 5 workers. Higher concurrency (10/20/50) must be measured on the actual phone; 50 simultaneous downloads are not assumed stable.
- The receipt upload currently uses a repository token. A later Android app should store credentials securely and should not embed a long-lived token in the APK.
- Final publishing must not be enabled for routine use until receipt authentication, candidate version binding, and end-to-end tests have been completed.
