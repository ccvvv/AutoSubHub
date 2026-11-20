# Termux phone probe (initial implementation)

This script runs the download-speed stage on the phone's own network. GitHub performs format filtering and deduplication, then HTTPS-only Mihomo latency checks and ChatGPT HTTPS certificate checks. Standalone TCP/UDP and generic TLS/WebSocket handshake prefilters are disabled to avoid false negatives. Plain-HTTP captive-portal checks are disabled.

## Install in Termux

```sh
pkg update -y
pkg install -y python curl
python -m pip install --upgrade pip
python -m pip install requests pyyaml
```

First check the phone ABI:

```sh
uname -m
```

For `aarch64` (64-bit ARM, expected on most recent Android phones), install the official Android ARM64 Mihomo release:

```sh
pkg install -y gzip
curl -fL -o mihomo.gz https://github.com/MetaCubeX/mihomo/releases/download/v1.19.34/mihomo-android-arm64-v8-v1.19.34.gz
gzip -dc mihomo.gz > mihomo
chmod +x mihomo
./mihomo -v
```

If `uname -m` returns something other than `aarch64`, do not use that ARM64 file; choose the matching Android asset from the [official Mihomo releases](https://github.com/MetaCubeX/mihomo/releases). Do not use the Linux amd64 binary from the GitHub Actions workflow on Android.

## Run a local test first

```sh
python termux_probe.py --workers 5
```

Defaults: 5 simultaneous proxy download tests, 5 MiB maximum downloaded per node, and a 500 KB/s minimum. The script writes `receipt.json`. It does not upload anything unless `--upload` is explicitly supplied.

## Upload receipt

Create a fine-grained GitHub token restricted to this repository with Contents read/write permission, then set the token only in the Termux session. The script automatically creates `probe-receipts` from `main` on the first upload:

```sh
export GITHUB_TOKEN='YOUR_TOKEN'
export GITHUB_REPOSITORY='ccvvv/AutoSubHub'
python termux_probe.py --workers 5 --upload
```

Never commit the token or put it in the app source. The GitHub finalization workflow reads the exact uploaded receipt commit from `receipts/latest.json` on the `probe-receipts` branch. Protect the token and rotate it if it is exposed.

## Important limitations

- This is an initial test implementation, not a verified Android release. It requires a compatible Android Mihomo binary; the Linux GitHub Actions binary is not suitable.
- The Cloudflare 5 MiB download endpoint is configurable with `--download-url`; test it on the target domestic network before relying on results.
- Start with 5 workers. Higher concurrency (10/20/50) must be measured on the actual phone; 50 simultaneous downloads are not assumed stable.
- The receipt upload currently uses a repository token. A later Android app should store credentials securely and should not embed a long-lived token in the APK.
- Final publishing must not be enabled for routine use until receipt authentication, candidate version binding, and end-to-end tests have been completed.
