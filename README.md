# AutoSubHub

AutoSubHub publishes two independent public subscription links. Both are intentionally public, including node credentials contained in the YAML.

## Public subscription links

- **Candidate subscription (phone pending test):** `https://raw.githubusercontent.com/ccvvv/AutoSubHub/published-subscriptions/candidate/subscription.yaml`
- **Final subscription (phone-tested nodes only):** `https://raw.githubusercontent.com/ccvvv/AutoSubHub/published-subscriptions/final/latest.yaml`

The candidate and final URLs are separate and must never be mixed. The final URL is not populated by the candidate workflow; it will be written only by the phone-receipt finalization workflow.

## Candidate pipeline

GitHub Actions fetches configured source subscriptions, parses supported Clash YAML and URI formats, excludes pure HTTP proxy entries, removes duplicates, and generates `candidates/candidates.yaml` plus a credential-free manifest. The candidate workflow publishes the YAML and manifest to the public `published-subscriptions` data branch under `candidate/`. A successful new candidate overwrites the previous candidate at the same stable URL. Pull-request validation does not publish candidate credentials.

## Phone probe and final pipeline

Android/Termux downloads the public candidate link, tests nodes from the domestic mobile network, and sends a receipt containing tested node IDs and results. GitHub must validate the receipt and build the final subscription exclusively from passed node IDs.

The final publisher will maintain three rolling files:
- `final/latest.yaml`
- `final/previous.yaml`
- `final/older.yaml`

Rotation occurs only after a non-empty final subscription is generated and structurally validated from a successful phone receipt. Failed or incomplete runs leave all three previous versions untouched.

## Security and privacy

The repository is public and the user explicitly chose public subscription links. Anyone who knows or discovers the links can read node server addresses and credentials, use the proxies, and redistribute them. Do not place personal or paid credentials in these sources unless that exposure is intended.

## Development status

- Candidate generation and public candidate publishing: workflow implemented on `feature/termux-probe-v1`; needs a successful Actions run to verify the public URL.
- Termux probe engine and authenticated receipt upload: not implemented yet.
- Phone-receipt-based final publisher and three-version rotation: not implemented yet.
- Legacy cloud-only final publishing is disabled to prevent it from publishing nodes that were not tested by the phone.
