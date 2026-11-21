#!/data/data/com.termux/files/usr/bin/bash
set -u
set -o pipefail
cd "$HOME/AutoSubHub" || exit 1
mkdir -p logs
ACTION="${1:-}"
LOG="logs/app-control.log"
now() { date '+%Y-%m-%d %H:%M:%S'; }
say() { printf '[%s] %s\n' "$(now)" "$*" | tee -a "$LOG"; }

case "$ACTION" in
  check)
    say "检查候选分支 published-subscriptions"
    SHA="$(git ls-remote origin refs/heads/published-subscriptions 2>>"$LOG" | awk 'NR==1 {print $1}')"
    if [ -n "$SHA" ]; then
      say "候选版本 SHA: $SHA"
    else
      say "检查失败：无法读取候选分支，请检查网络和 Git SSH 配置"
      exit 1
    fi
    ;;
  probe)
    if pgrep -f '[t]ermux_probe.py' >/dev/null 2>&1; then
      say "测速任务已经在运行；为避免重复启动，本次取消"
      exit 1
    fi
    say "开始测速：workers=200 timeout=5"
    python termux_probe.py --workers 200 --timeout 5 --output receipt.json 2>&1 | tee -a "$LOG"
    code=${PIPESTATUS[0]}
    say "测速结束，退出码：$code"
    exit "$code"
    ;;
  upload)
    if [ ! -s receipt.json ]; then
      say "上传失败：receipt.json 不存在或为空"
      exit 1
    fi
    say "开始通过 SSH 上传已有回执（不会重新测速）"
    python upload_receipt.py --input receipt.json 2>&1 | tee -a "$LOG"
    code=${PIPESTATUS[0]}
    say "上传结束，退出码：$code"
    exit "$code"
    ;;
  stop)
    pkill -f '[t]ermux_probe.py' 2>/dev/null || true
    say "已发送停止测速信号"
    ;;
  logs)
    tail -n 100 "$LOG" 2>/dev/null || echo "暂无日志"
    ;;
  *)
    echo "Usage: app_control.sh {check|probe|upload|stop|logs}"
    exit 2
    ;;
esac
