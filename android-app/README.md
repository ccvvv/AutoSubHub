# AutoSubHub Android control app

This is a manual-only Android front end for the existing Termux probe scripts. It does not contain node credentials, SSH keys, or GitHub tokens.

## Termux prerequisites

- Termux installed from a supported source, version 0.109 or newer for result callbacks.
- Repository cloned at `~/AutoSubHub`.
- Existing `termux_probe.py`, `upload_receipt.py`, Mihomo binary, Python dependencies, and SSH key already configured.
- Make the dispatcher executable:

```sh
cd ~/AutoSubHub
chmod +x app_control.sh
```

In Termux, edit `~/.termux/termux.properties` and set:

```properties
allow-external-apps = true
```

Then run `termux-reload-settings`. Install the APK, grant its **Run commands in Termux** permission in Android App Info, and keep Termux installed. The app sends explicit commands through Termux's documented RUN_COMMAND service.

## Manual controls

- Check candidate: reads the current `published-subscriptions` branch SHA.
- Start probe: runs the existing probe with 100 workers and 5-second timeout.
- Upload receipt: uploads the existing `receipt.json` over SSH; it does not start a probe.
- Stop: terminates the active probe process.
- Logs: shows the last 100 lines from the local dispatcher log.

No schedule or background polling is installed. The first build is a prototype and must be tested on the target phone before relying on it.
