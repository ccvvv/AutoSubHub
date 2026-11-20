# AutoSubHub

GitHub performs lightweight subscription parsing and deduplication. Android/Termux probe performs domestic-network reachability, TLS and download-speed tests. Only phone-tested results are eligible for the final subscription.

## Directory layout

- `source/`: optional source-list location (legacy root `subscriptions.txt` remains supported).
- `candidates/`: generated candidate YAML and manifest, delivered as a short-lived Actions artifact in this initial phase.
- `probe/`: Termux probe runtime and scripts (added in the next phase).
- `probe-results/`: phone test receipts (planned; pushed to a dedicated branch).
- `final/`: final subscription output (planned; remains separate from candidates).

## Privacy

Candidate YAML contains proxy credentials. Do not commit it to a public repository or publish it through public GitHub Pages. The current candidate workflow uploads it as a short-lived Actions artifact. Before the Android App downloads artifacts automatically, authenticated artifact retrieval with a narrowly scoped GitHub token must be implemented.

## Candidate pipeline

1. Fetch configured sources (repository secret `SUBSCRIPTION_URLS`, or legacy root `subscriptions.txt`).
2. Parse supported Clash YAML and URI formats.
3. Exclude pure HTTP proxy entries and malformed/unsupported entries.
4. Deduplicate nodes and create stable opaque node IDs in `candidates/manifest.json`.
5. Upload candidate YAML and manifest as a 3-day Actions artifact.

GitHub no longer runs strict TCP/handshake/ChatGPT certificate/download-speed rejection. Those decisions move to the Android phone's domestic-network probe.

## Existing final subscription

The currently published Pages subscription is not changed by this candidate-only workflow. A separate final publisher will be introduced after phone receipts are validated end to end.
