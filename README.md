# AutoSubHub

GitHub keeps the existing published final subscription workflow separate from the lightweight candidate pipeline. Android/Termux performs domestic-network reachability, TLS and download-speed tests. Only phone-tested results are eligible for future final releases.

## Directory layout

- `source/`: optional source-list location (legacy root `subscriptions.txt` remains supported).
- `candidates/`: generated candidate YAML and manifest, delivered as a short-lived Actions artifact.
- `probe/`: Termux probe runtime and scripts (next implementation phase).
- `probe-results/`: phone test receipts (planned; pushed to a dedicated branch).
- `final/`: final subscription outputs and three-version history (planned; separate from candidates).

## Workflows

- `.github/workflows/build.yml` keeps the existing GitHub Pages final-subscription publishing workflow and calls `build-final.py`.
- `.github/workflows/build-candidates.yml` independently parses and deduplicates source subscriptions, then uploads the candidate artifact.
- `build.py` is the lightweight candidate generator. `build-final.py` preserves the existing cloud-side final builder while the phone-probe pipeline is developed.

## Candidate retention

Candidate YAML contains proxy credentials and is never committed to the public repository or published through public Pages. The candidate workflow generates and uploads the new candidate first, then removes older production artifacts, leaving one latest artifact named `autohub-candidates-latest`. Pull-request validation uses a separate temporary artifact name and cannot replace the production candidate. If generation or upload fails, the previous production artifact remains. GitHub Actions artifacts have a 90-day retention limit; the six-hour schedule normally refreshes the latest artifact well before expiry.

Before the Android client downloads artifacts automatically, authenticated artifact retrieval with a narrowly scoped GitHub token must be implemented.

## Final subscription retention

The agreed target is to keep three successful final versions: newest, previous, and two-versions-old. Rotation must happen only after a new final subscription is generated and validated successfully. A failed build or failed phone receipt must leave all three existing versions untouched. This three-version publisher is not implemented yet.

## Candidate pipeline

1. Fetch configured sources (repository secret `SUBSCRIPTION_URLS`, or legacy root `subscriptions.txt`).
2. Parse supported Clash YAML and URI formats.
3. Exclude pure HTTP proxy entries and malformed/unsupported entries.
4. Deduplicate nodes and create stable opaque node IDs in `candidates/manifest.json`.
5. Upload the candidate YAML and manifest as the latest access-controlled Actions artifact.

GitHub candidate generation does not perform strict TCP/handshake/ChatGPT certificate/download-speed rejection. Those decisions move to the Android phone's domestic-network probe.
