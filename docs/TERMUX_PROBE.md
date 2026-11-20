# Termux phone probe (initial implementation)

This script runs the download-speed stage on the phone's own network. GitHub-side parsing, TCP/UDP prefilters, protocol checks, Mihomo HTTP checks, and ChatGPT HTTPS certificate checks remain on GitHub.

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

## Run a local test

Run the probe on the phone. It only tests nodes and writes a local JSON receipt; it does not upload to GitHub:

```sh
python termux_probe.py --workers 5 --output receipt.json
```

Increase concurrency gradually after checking device stability. For example, test 10, then 20, then 50 workers; 50 is a target to test, not a guarantee that every phone can sustain it.

## Upload the saved receipt separately

Only after the probe has finished and `receipt.json` exists, upload it in a separate command. Create a fine-grained GitHub token restricted to this repository with **Contents: Read and write** permission, then set it in the current Termux session:

```sh
export GITHUB_TOKEN='YOUR_TOKEN'
export GITHUB_REPOSITORY='ccvvv/AutoSubHub'
python upload_receipt.py --input receipt.json
```

The uploader creates the `probe-receipts` branch from `main` if it does not exist, then writes `receipts/latest.json`. If upload fails, keep the local receipt and rerun only the upload command; do not repeat the speed test. The uploader validates the receipt and never deletes or changes the local file.

Never commit the token or put it in the app source. Protect the token and revoke it if it is exposed.

## Important limitations

- This is an initial test implementation, not a verified Android release. It requires a compatible Android Mihomo binary; the Linux GitHub Actions binary is not suitable.
- The Cloudflare 5 MiB download endpoint is configurable with `--download-url`; test it on the target domestic network before relying on results.
- Start with 5 workers. Higher concurrency (10/20/50) must be measured on the actual phone; 50 simultaneous downloads are not assumed stable.
- Receipt upload uses a repository token supplied through the Termux environment. Do not embed a long-lived token in an APK.
- Final publishing must not be enabled for routine use until receipt authentication, candidate version binding, and end-to-end tests have been completed.
