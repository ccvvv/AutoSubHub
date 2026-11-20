# Domestic probe receipt contract

This document defines the first-version contract between the Android probe and AutoSubHub. It deliberately has no task-download or remote-wakeup endpoint.

## Candidate download

The probe downloads both files from the same published Pages batch:

- `https://ccvvv.github.io/AutoSubHub/candidates.yaml`
- `https://ccvvv.github.io/AutoSubHub/candidate-manifest.json`

The manifest contains:

- `schema_version`: currently `1`
- `generated_at`: candidate batch revision; return this exact string as `candidate_revision`
- `candidate_count`
- `nodes[]`: stable `id` and display `name`, plus protocol/server/port metadata

The candidate profile's proxy names carry the stable ID as `probe_<24-hex-id>__<display-name>`. The probe must not modify or regenerate these IDs.

## Receipt request

Send a GitHub `repository_dispatch` event:

- Method: `POST`
- URL: `https://api.github.com/repos/ccvvv/AutoSubHub/dispatches`
- Required headers:
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer <token>`
  - `X-GitHub-Api-Version: 2022-11-28`
  - `Content-Type: application/json`

The token must be supplied by the user at runtime and stored using Android Keystore-backed secure storage. Never embed it in source code, candidate YAML, or a receipt. Use a fine-grained token limited to this repository with the permission needed to create repository dispatch events (Contents: write).

Example body:

```json
{
  "event_type": "domestic-probe-results",
  "client_payload": {
    "candidate_revision": "2026-11-20T10:00:00Z",
    "device_id": "mi6-probe-01",
    "network": "mainland-wifi",
    "results": [
      {
        "node_id": "0123456789abcdef01234567",
        "status": "passed",
        "speed_kbps": 1250.5,
        "latency_ms": 180
      },
      {
        "node_id": "abcdef0123456789abcdef01",
        "status": "failed",
        "speed_kbps": 0,
        "latency_ms": null
      }
    ]
  }
}
```

A successful dispatch request returns HTTP `204`. This only means GitHub accepted the event; it does not mean the receipt was validated or the final subscription was deployed.

## Field rules

- `candidate_revision`: exact `generated_at` value from the downloaded manifest.
- `device_id`: fixed to `mi6-probe-01` for this private first deployment.
- `network`: fixed to `mainland-wifi`.
- `node_id`: exactly one ID from the downloaded manifest; do not send proxy names, server credentials, or full proxy objects.
- `status`: `passed`, `failed`, or `inconclusive`.
- `speed_kbps`: measured download speed in decimal kilobits per second; a `passed` result must include a positive value.
- `latency_ms`: measured delay in milliseconds, or `null` when unavailable.

Send only nodes that were actually tested. Do not mark cached speed data as a fresh pass.

## GitHub-side behavior

The receiver checks the candidate revision, exact candidate ID set, duplicate/unknown IDs, device/network identifiers, status values, and numeric measurements. It archives a sanitized latest receipt in `data/domestic-probe/latest.json`.

Only nodes marked `passed` are retained in the final subscription. The exact final YAML is checked by the pinned Mihomo core before Pages deployment. If no node passes, the previous final subscription is not replaced.

The receiver is available only after this pull request is merged into the repository's default branch and a candidate batch has been published. No task polling or remote wake-up is implemented.
