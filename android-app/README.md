# AutoSubHub Android dashboard
Manual Android dashboard for the Termux phone probe.

## Setup
- Install Termux (version 0.109 or newer, for command-result callbacks) and this APK.
- Clone ccvvv/AutoSubHub to ~/AutoSubHub in Termux.
- Ensure Python dependencies, Mihomo and SSH upload are configured.
- Run chmod +x ~/AutoSubHub/app_control.sh.
- Set allow-external-apps = true in ~/.termux/termux.properties, then run termux-reload-settings.
- Grant the app the Termux Run Commands permission in Android app permissions.
- The dashboard includes an Update Workspace button (`git pull --ff-only`); it refuses to update if the local repository has uncommitted changes.

The home screen shows live progress, tested/available/failed counts, pass rate, latest node, speed and five application checks. Raw logs are hidden in a collapsible panel. Update workspace, start, stop, upload and candidate-check actions remain manual.

Default probe settings: 5 workers, 3-second application checks, 5-second download timeout, 200 KB/s minimum and 500 KB/s high-speed threshold. Telegram check means Telegram Web HTTPS reachability, not native MTProto login.
