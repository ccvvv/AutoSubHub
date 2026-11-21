# AutoSubHub Android dashboard
Manual Android dashboard for the Termux phone probe.

## Setup
- Install Termux and this APK.
- Clone ccvvv/AutoSubHub to ~/AutoSubHub in Termux.
- Ensure Python dependencies, Mihomo and SSH upload are configured.
- Run chmod +x ~/AutoSubHub/app_control.sh.
- Set allow-external-apps = true in ~/.termux/termux.properties, then run termux-reload-settings.
- Grant the app the Termux Run Commands permission.

The home screen shows live progress, tested/available/failed counts, pass rate, latest node, speed and five application checks. Raw logs are hidden in a collapsible panel. Start, stop, upload and candidate-check actions remain manual.

Default probe settings: 5 workers, 3-second application checks, 5-second download timeout, 200 KB/s minimum and 500 KB/s high-speed threshold. Telegram check means Telegram Web HTTPS reachability, not native MTProto login.
