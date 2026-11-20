# AutoSubHub

AutoSubHub publishes two independent public subscription links. Both are intentionally public, including node credentials contained in the YAML.

## Public subscription links

- **Candidate subscription (phone pending test):** `https://raw.githubusercontent.com/ccvvv/AutoSubHub/published-subscriptions/candidate/subscription.yaml`
- **Final subscription (phone-tested nodes only):** `https://raw.githubusercontent.com/ccvvv/AutoSubHub/published-subscriptions/final/latest.yaml`

The candidate and final URLs are separate and must never be mixed. The final URL is not populated by the candidate workflow; it will be written only by the phone-receipt finalization workflow.

## Candidate pipeline

GitHub Actions fetches configured source subscriptions, parses supported Clash YAML and URI formats, excludes pure HTTP proxy entries, removes duplicates, and generates candidates after GitHub-side TCP/UDP prefiltering, protocol handshake checks, Mihomo HTTP proxy tests, and HTTPS certificate validation against ChatGPT. Download throughput is deliberately not tested on GitHub; that stage belongs to the Android/Termux phone.

The candidate workflow publishes the YAML and credential-free manifest to the public `published-subscriptions` data branch under `candidate/`. A successful new candidate overwrites the previous candidate at the same stable URL. Pull-request validation does not publish candidate credentials.

## Phone probe and final pipeline

Android/Termux is intended to download the public candidate link, test nodes from the domestic mobile network, and send a receipt containing tested node IDs and results. The initial GitHub receipt validator and publisher workflow are present on the development branch, but the complete phone-to-GitHub flow has not yet been tested. Receipt authentication and the Termux uploader still need to be implemented before treating final publishing as production-ready.

The final publisher is designed to maintain three rolling files:
- `final/latest.yaml`
- `final/previous.yaml`
- `final/older.yaml`

Rotation should occur only after a non-empty final subscription is generated and structurally validated from a successful phone receipt. Failed or incomplete runs must leave all three previous versions untouched.

## Security and privacy

The repository and the published data branch are public. Anyone who knows or discovers the subscription URLs can read node server addresses and credentials, use the proxies, and redistribute them. Do not place personal or paid credentials in these sources unless that exposure is intended. The phone receipt upload must be authenticated before the final publisher is enabled for routine use.

## Development status

- Candidate generation and public candidate publishing: implemented on `feature/termux-probe-v1`; the PR validation run succeeded, but the public publishing step was skipped because it was a pull-request run.
- Termux probe engine and authenticated receipt upload: not implemented yet.
- Phone-receipt validator and three-version final publisher: initial implementation exists on the development branch; end-to-end validation remains outstanding.
- Legacy cloud-only final publishing is disabled to prevent it from publishing nodes that were not tested by the phone.
