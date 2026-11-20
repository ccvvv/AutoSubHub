# Termux phone probe (initial implementation)

This script runs the download-speed stage on the phone's own network. GitHub-side parsing, TCP/UDP prefilters, protocol checks, Mihomo HTTP checks, and ChatGPT HTTPS certificate checks remain on GitHub.

## Install in Termux

```sh
pkg update -y
pkg install -y python curl
python -m pip install --upgrade pip
python -m pip install requests pyyaml
```

Install a Mihomo binary built for the phone's Android ABI and make it executable as `./mihomo` (or set `MIHOMO_BIN` to its path). Do not use the Linux amd64 binary from the GitHub Actions workflow on Android. Verify it with:

```sh
chmod +x ./mihomo
./mihomo -v
```

## Run a local test first

```sh
python termux_probe.py --workers 5
```

Defaults: 5 simultaneous proxy download tests, 5 MiB maximum downloaded per node, and a 500 KB/s minimum. The script writes `receipt.json`. It does not upload anything unless `--upload` is explicitly supplied.

## Upload receipt

Create a fine-grained GitHub token restricted to this repository with Contents read/write permission, create the `probe-receipts` branch, and set the token only in the Termux session:

```sh
export GITHUB_TOKEN='YOUR_TOKEN'
export GITHUB_REPOSITORY='ccvvv/AutoSubHub'
python termux_probe.py --workers 5 --upload
```

Never commit the token or put it in the app source. The initial GitHub finalization workflow currently reads `receipts/latest.json` from the `probe-receipts` branch. Protect the token and rotate it if it is exposed.

## Important limitations

- This is an initial test implementation, not a verified Android release. It requires a compatible Android Mihomo binary; the Linux GitHub Actions binary is not suitable.
- The Cloudflare 5 MiB download endpoint is configurable with `--download-url`; test it on the target domestic network before relying on results.
- Start with 5 workers. Higher concurrency (10/20/50) must be measured on the actual phone; 50 simultaneous downloads are not assumed stable.
- The receipt upload currently uses a repository token. A later Android app should store credentials securely and should not embed a long-lived token in the APK.
- Final publishing must not be enabled for routine use until receipt authentication, candidate version binding, and end-to-end tests have been completed.
