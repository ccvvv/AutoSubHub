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
  update)
    say "开始更新工作区：git pull --ff-only"
    if [ -n "$(git status --porcelain)" ]; then
      say "更新已取消：工作区存在未提交修改，请先处理本地修改"
      git status --short | tee -a "$LOG"
      exit 2
    fi
    if ! git pull --ff-only origin "$(git branch --show-current)" 2>&1 | tee -a "$LOG"; then
      say "工作区更新失败：请检查网络、分支跟踪关系或远端冲突"
      exit 1
    fi
    say "工作区更新完成：$(git rev-parse --short HEAD)"
    ;;
  check)
    say "检查候选版本 published-subscriptions"
    SHA="$(git ls-remote origin refs/heads/published-subscriptions 2>>"$LOG" | awk 'NR==1 {print $1}')"
    if [ -n "$SHA" ]; then say "候选版本 SHA: $SHA"; else say "检查失败：无法读取候选分支"; exit 1; fi
    ;;
  probe)
    if pgrep -f '[t]ermux_probe.py' >/dev/null 2>&1; then say "测速任务已经在运行，本次取消"; exit 1; fi
    say "测速开始：并发5，应用检测超时3秒，下载超时5秒"
    python -u termux_probe.py --workers 5 --service-timeout 3 --timeout 5 --min-speed-kbps 200 --high-speed-kbps 500 --output receipt.json --progress progress.json 2>&1 | tee -a "$LOG"
    code=${PIPESTATUS[0]}
    if [ "$code" -ne 0 ] && [ -s progress.json ]; then sed -i 's/"status":"running"/"status":"failed"/; s/"status":"preparing"/"status":"failed"/' progress.json; fi
    say "测速结束，退出码：$code"; exit "$code"
    ;;
  upload)
    if [ ! -s receipt.json ]; then say "上传失败：receipt.json 不存在或为空"; exit 1; fi
    say "开始通过 SSH 上传回执（不会重新测速）"
    python upload_receipt.py --input receipt.json 2>&1 | tee -a "$LOG"
    code=${PIPESTATUS[0]}; say "上传结束，退出码：$code"; exit "$code"
    ;;
  stop)
    pkill -f '[t]ermux_probe.py' 2>/dev/null || true
    if [ -s progress.json ]; then sed -i 's/"status":"[^"]*"/"status":"interrupted"/' progress.json; fi
    say "已发送停止测速信号"
    ;;
  snapshot)
    python3 - <<'PY'
import json
from pathlib import Path
try:
    progress = json.loads(Path("progress.json").read_text(encoding="utf-8"))
except Exception:
    progress = {"status": "idle", "done": 0, "total": 0}
try:
    logs = "\\n".join(Path("logs/app-control.log").read_text(encoding="utf-8", errors="replace").splitlines()[-60:])
except Exception:
    logs = "暂无日志"
print(json.dumps({"progress": progress, "logs": logs}, ensure_ascii=False))
PY
    ;;
  progress) cat progress.json 2>/dev/null || echo '{"status":"idle","done":0,"total":0}' ;;
  logs) tail -n 100 "$LOG" 2>/dev/null || echo "暂无日志" ;;
  *) echo "Usage: app_control.sh {check|probe|upload|progress|logs|stop}"; exit 2 ;;
esac
