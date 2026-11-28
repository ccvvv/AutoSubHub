# Termux phone probe (initial implementation)

This script runs five application HTTPS gates and the download-speed stage on the phone's own network. Every request is routed through the selected node in the local Mihomo instance. A failure at any application gate rejects that node before download testing.

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

## Download first, then test locally with VPN off

Download and merge all pending candidate batches while the phone can reach GitHub or an available mirror. The command saves a local YAML, manifest, and metadata file and verifies their matching SHA256 values:

```sh
python download_candidates.py --output candidate-cache/candidate-local.yaml
```

The phone now verifies both the candidate-file SHA256 and each node's complete 64-character SHA256 fingerprint against its actual proxy configuration. The same full fingerprint is used by the phone manifest, receipt, cloud pool manifest, probe state, receipt validator and failed-node pruning. Display names are never used as node identity. Historical immutable candidate batches with 20-character IDs are recomputed from their proxy configuration while being merged; all newly generated manifests and receipts use full fingerprints.

After the download succeeds, turn **off the Android system VPN**. Then run the local test. This mode reads only the saved candidate YAML/manifest/metadata and does not fetch candidate files, call GitHub, or update the candidate queue:

```sh
python termux_probe.py --candidate-file candidate-cache/candidate-local.yaml --workers 5 --service-timeout 3 --min-speed-kbps 200 --high-speed-kbps 500 --output receipt.json
```

The node checks still use the temporary local Mihomo listeners to test each candidate node. “VPN off” means the phone's existing system VPN is off; Mihomo is launched locally by the probe to route each individual test through the node being measured.

In the Android control panel, use **下载候选列表** first, turn off the system VPN, and then tap **本地列表测速**. The original online-fetch probe command remains available for compatibility.

## Run a local test

Run the probe on the phone from inside the cloned AutoSubHub repository. It reads candidate batch history, combines every batch newer than the last successfully uploaded receipt, and deduplicates nodes by proxy configuration. On the first run after this feature is installed, it resumes from the candidate commit recorded in the last uploaded phone receipt, so already-tested batches are not repeated. Only if no earlier phone receipt exists does it backfill up to the latest 20 candidate batches. It writes a receipt and two matching snapshot sidecars locally; it does not upload to GitHub:

```sh
python termux_probe.py --candidate-file candidate-cache/candidate-local.yaml --workers 5 --service-timeout 3 --min-speed-kbps 200 --high-speed-kbps 500 --output receipt.json
```

Increase concurrency gradually after checking device stability. For example, test 10, then 20, then 50 workers; 50 is a target to test, not a guarantee that every phone can sustain it.

## Upload the saved receipt separately

Only after the probe has finished and `receipt.json`, `receipt.candidate.yaml`, and `receipt.manifest.json` exist, upload the complete bundle in a separate command. The uploader always uses the GitHub SSH key configured in Termux, through GitHub's SSH-over-port-443 endpoint (useful when port 22 is blocked). Any existing `GITHUB_TOKEN` environment variable is ignored:

```sh
python upload_receipt.py --input receipt.json
```

The uploader writes `receipts/latest.json` plus the exact merged `receipts/candidate.yaml` and `receipts/manifest.json` to the existing `published-subscriptions` data branch. It no longer creates a separate receipt branch. Only after the complete bundle is pushed does it advance `~/.autosubhub_probe_state.json` to the newest included candidate commit. If upload fails, keep all three local files and rerun only the upload command; do not repeat the speed test. The uploader validates the receipt and snapshot hashes and never deletes or changes the local files.

Never commit the token or put it in the app source. Protect the token and revoke it if it is exposed.

## Application gates and speed categories

- YouTube, Telegram Web, Google, ChatGPT, and GitHub are tested sequentially through the selected node. All five must pass; any failed check rejects the node and skips its download test. Different nodes are tested concurrently according to the configured worker count.
- Application checks require the expected HTTP status (Google `generate_204`: 204; the other four web pages: 200), a final URL on the expected service domain (or the configured custom URL's own domain), and a non-empty HTML response sample for the four page-based checks. The receipt records final URL, redirects, content type, sample size, and elapsed time.
- Telegram currently checks Telegram Web HTTPS reachability; it does not perform a native Telegram MTProto login or message test.
- Each application check uses a 3-second connect/read timeout by default. Slow or blocked endpoints can therefore reject a node.
- Download speed is counted only after the full configured test size is received (5 MiB by default). A non-200 response, a declared Content-Length shorter than the test size, or an early/incomplete response rejects the download stage instead of calculating a passing speed from partial data. The receipt records expected and received byte counts; the final publisher independently verifies that they match.
- Nodes below 200 KB/s are rejected. 200–499 KB/s nodes are tagged [普通]; 500 KB/s and above are tagged [高速].
- The final output remains one subscription, with 高速节点 and 普通节点 selectable proxy groups and matching node-name tags.

## Important limitations

- This is an initial test implementation, not a verified Android release. It requires a compatible Android Mihomo binary; the Linux GitHub Actions binary is not suitable.
- The Cloudflare 5 MiB download endpoint is configurable with `--download-url`; test it on the target domestic network before relying on results.
- Start with 5 workers. Higher concurrency (10/20/50) must be measured on the actual phone; 50 simultaneous downloads are not assumed stable.
- Receipt upload uses a repository token supplied through the Termux environment. Do not embed a long-lived token in an APK.
- Final publishing must not be enabled for routine use until receipt authentication, candidate version binding, and end-to-end tests have been completed.
