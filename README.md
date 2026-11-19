# AutoSubHub

Automatically fetch, parse, TCP-check, deduplicate and publish a combined Clash-compatible subscription every hour using GitHub Actions and GitHub Pages.

## Important privacy warning

This repository and the generated Pages output are public. Anyone who knows the published URL can download the generated proxy configuration and use the included nodes. Only add subscription sources that you are allowed and willing to expose. Do not commit subscription URLs or credentials.

## Setup

1. Open **Settings → Secrets and variables → Actions**.
2. Add a repository secret named `SUBSCRIPTION_URLS`.
3. Put one subscription URL on each line in the secret. Multiple URLs are supported.
4. Open **Settings → Pages** and set **Build and deployment → Source** to **GitHub Actions**.
5. Open **Actions**, enable workflows if GitHub asks, and run **Auto build subscription** once using **Run workflow**.

Published file (after the first successful run):

`https://ccvvv.github.io/AutoSubHub/subscription.yaml`

## Schedule

Runs at minute 17 of every hour (UTC). GitHub scheduled workflows can be delayed during periods of high load. A manual run is also available.

## What it does

- Fetches subscription URLs from the `SUBSCRIPTION_URLS` Actions secret (one URL per line).
- Supports Clash YAML subscriptions and common URI formats: VMess, VLESS, Trojan, Shadowsocks and SOCKS.
- Performs concurrent TCP-connect latency checks to each node's server and port.
- Drops nodes whose TCP endpoint cannot be reached within the timeout.
- Deduplicates by protocol/server/port/credentials and sorts by measured TCP connect latency.
- Publishes a minimal Clash-compatible YAML file to GitHub Pages.

**Measurement limitation:** this is a TCP-connect check to the node endpoint, not a full proxy handshake, TLS validation, throughput test, or proof that the proxy can reach the internet. A reachable endpoint may still be unusable. Use the generated list as a first-pass filter.

## Configuration

Optional repository variables:

- `MAX_WORKERS` (default `80`)
- `CONNECT_TIMEOUT` (default `3` seconds)
- `MAX_NODES` (default `500`)

Do not put secret URLs in repository variables or committed files.

## License

MIT
