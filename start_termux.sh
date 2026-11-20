#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

REPO="${GITHUB_REPOSITORY:-ccvvv/AutoSubHub}"
BRANCH="fix/https-only-github-prefilter"
RELEASE="v1.19.34"
BASE_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
WORK_DIR="${HOME}/AutoSubHub"
CONFIG_DIR="${HOME}/.config/autosubhub"
TOKEN_FILE="${CONFIG_DIR}/github_token"

say() { printf '\n[AutoSubHub] %s\n' "$*"; }
die() { say "错误：$*"; exit 1; }

command -v pkg >/dev/null 2>&1 || die "请在 Termux 中运行此脚本。"
mkdir -p "$WORK_DIR" "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"

say "安装运行依赖……"
pkg update -y
pkg install -y python curl gzip
python -m pip install --upgrade pip
python -m pip install --upgrade requests pyyaml

ARCH="$(uname -m)"
case "$ARCH" in
  aarch64|arm64)
    ASSET="mihomo-android-arm64-v8-${RELEASE}.gz"
    ;;
  armv7l|armv8l)
    ASSET="mihomo-android-arm-v7-${RELEASE}.gz"
    ;;
  *)
    die "暂不支持当前架构：$ARCH。请勿下载不匹配的 Mihomo 二进制。"
    ;;
esac

say "下载 Android 对应架构的 Mihomo（$ARCH）……"
curl -fL --retry 3 --connect-timeout 15 \
  -o "$WORK_DIR/mihomo.gz" \
  "https://github.com/MetaCubeX/mihomo/releases/download/${RELEASE}/${ASSET}"
gzip -dc "$WORK_DIR/mihomo.gz" > "$WORK_DIR/mihomo"
chmod 700 "$WORK_DIR/mihomo"
"$WORK_DIR/mihomo" -v

say "下载手机测速程序……"
curl -fL --retry 3 --connect-timeout 15 \
  -o "$WORK_DIR/termux_probe.py" \
  "$BASE_URL/termux_probe.py"

if [ -n "${GITHUB_TOKEN:-}" ]; then
  TOKEN="$GITHUB_TOKEN"
elif [ -s "$TOKEN_FILE" ]; then
  TOKEN="$(cat "$TOKEN_FILE")"
else
  printf '\n请输入 GitHub Fine-grained Token（只需首次输入；输入内容不会显示）：\n'
  read -r -s TOKEN
  printf '\n'
  [ -n "$TOKEN" ] || die "没有输入 Token，无法自动回传测速结果。"
  umask 077
  printf '%s' "$TOKEN" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
  say "Token 已保存在本机私有配置目录（权限 600），不会写入仓库。"
fi

export GITHUB_TOKEN="$TOKEN"
export GITHUB_REPOSITORY="$REPO"
WORKERS="${PROBE_WORKERS:-5}"
say "开始国内手机测速：并发 $WORKERS；完成后自动回传 GitHub。"
cd "$WORK_DIR"
exec python termux_probe.py \
  --mihomo "$WORK_DIR/mihomo" \
  --workers "$WORKERS" \
  --upload
