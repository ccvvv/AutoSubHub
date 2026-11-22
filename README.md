# AutoSubHub

AutoSubHub publishes two independent public subscription links. Both are intentionally public, including node credentials contained in the YAML.

## Public subscription links

- **Candidate subscription (phone pending test):** `https://raw.githubusercontent.com/ccvvv/AutoSubHub/published-subscriptions/candidate/subscription.yaml`
- **Final subscription (phone-tested nodes only):** `https://raw.githubusercontent.com/ccvvv/AutoSubHub/published-subscriptions/final/latest.yaml`

The candidate and final URLs are separate and must never be mixed. The final URL is not populated by the candidate workflow; it will be written only by the phone-receipt finalization workflow.

## Candidate pipeline

GitHub Actions fetches configured source subscriptions, parses supported Clash YAML and URI formats, excludes pure HTTP proxy entries, removes duplicates, and generates candidates after GitHub-side TCP/UDP prefiltering, protocol handshake checks, Mihomo HTTP proxy tests, and HTTPS certificate validation against ChatGPT. Download throughput is deliberately not tested on GitHub; that stage belongs to the Android/Termux phone.

The candidate workflow publishes only the pending-test YAML and credential-free manifest under `candidate/`. Candidate nodes are not included in lifecycle statistics. Lifecycle tracking runs only in the phone-receipt finalization workflow, after a non-empty phone-tested subscription has been built and copied to `final/latest.yaml`. It records only nodes present in that final published subscription. The fingerprint is based on the proxy configuration excluding the display name, so suffixes such as `[高速]` and `[普通]` do not reset a node's first-observed timestamp. Because tracking starts when deployed, `first_seen_at` means first observed in a final publication, not a reconstructed historical publication time.

## Phone probe and final pipeline

Android/Termux downloads the public candidate link, tests nodes from the phone's network through isolated Mihomo listeners, and writes a receipt containing tested node IDs and measured download speeds. An initial `termux_probe.py` script and setup guide are available in `docs/TERMUX_PROBE.md`. Receipt upload uses a user-supplied repository token; the token must never be committed or embedded in an APK. The complete phone-to-GitHub flow has not yet been tested.

The final publisher is designed to maintain three rolling files:
- `final/latest.yaml`
- `final/previous.yaml`
- `final/older.yaml`

Rotation should occur only after a non-empty final subscription is generated and structurally validated from a successful phone receipt. Failed or incomplete runs must leave all three previous versions untouched.

## Security and privacy

The repository and the published data branch are public. Anyone who knows or discovers the subscription URLs can read node server addresses and credentials, use the proxies, and redistribute them. Do not place personal or paid credentials in these sources unless that exposure is intended. The phone receipt upload must be authenticated before the final publisher is enabled for routine use.

## Development status

- Candidate generation and public candidate publishing: implemented on `feature/termux-probe-v1`; the PR validation run succeeded, but the public publishing step was skipped because it was a pull-request run.
- Termux probe engine and token-based receipt uploader: initial implementation committed; Android binary compatibility and end-to-end upload are not yet tested.
- Phone-receipt validator and three-version final publisher: initial implementation exists on the development branch; end-to-end validation remains outstanding.
- Legacy cloud-only final publishing is disabled to prevent it from publishing nodes that were not tested by the phone.
