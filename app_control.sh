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
    # Runtime output is local state, not source changes; preserve it and
    # allow workspace updates while still blocking actual source edits.
    # Ignore known probe/diagnostic artifacts, but do not blanket-ignore JSON files.
    DIRTY="$(git status --porcelain --untracked-files=all | grep -Ev '^\?\? (logs/|progress\.json$|receipt\.json$|diagnostic_[^/]*\.json$|single_node_[^/]*\.json$)' || true)"
    if [ -n "$DIRTY" ]; then
      say "更新已取消：检测到源码文件存在未提交修改，请先处理"
      printf '%s\n' "$DIRTY" | tee -a "$LOG"
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
    if [ -z "$SHA" ]; then say "检查失败：无法读取候选分支"; exit 1; fi
    if ! git fetch --quiet --depth=1 origin refs/heads/published-subscriptions 2>>"$LOG"; then
      say "候选版本 SHA: $SHA"
      say "更新时间获取失败：Git fetch 未成功"
      exit 1
    fi
    RAW_TIME="$(git show -s --format=%cI FETCH_HEAD 2>>"$LOG")"
    BEIJING_TIME="$(python -c 'from datetime import datetime,timedelta,timezone; import sys; d=datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")); print(d.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"))' "$RAW_TIME" 2>>"$LOG")"
    say "候选版本 SHA: $SHA"
    say "候选版本更新时间（北京时间）: $BEIJING_TIME"
    say "本机检查时间（北京时间）: $(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')"
    ;;
  probe)
    if pgrep -f '[t]ermux_probe.py' >/dev/null 2>&1; then say "测速任务已经在运行，本次取消"; exit 1; fi
    WORKERS="${2:-200}"
    if ! [[ "$WORKERS" =~ ^[0-9]+$ ]] || [ "$WORKERS" -lt 1 ] || [ "$WORKERS" -gt 300 ]; then
      say "测速启动失败：并发必须是 1 到 300 的整数"
      exit 2
    fi
    rm -f probe.pause probe.stop
    say "测速开始：并发$WORKERS，应用检测超时3秒，下载超时5秒"
    python -u termux_probe.py --workers "$WORKERS" --service-timeout 3 --timeout 5 --min-speed-kbps 200 --high-speed-kbps 500 --output receipt.json --progress progress.json 2>&1 | tee -a "$LOG"
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
  pause)
    if ! pgrep -f '[t]ermux_probe.py' >/dev/null 2>&1; then say "暂停失败：当前没有运行中的测速任务"; exit 1; fi
    touch probe.pause
    say "已请求暂停：当前节点检测结束后暂停后续节点"
    ;;
  resume)
    if [ ! -f probe.pause ]; then say "当前任务并未暂停"; exit 1; fi
    rm -f probe.pause
    say "已请求继续测速"
    ;;
  stop)
    if ! pgrep -f '[t]ermux_probe.py' >/dev/null 2>&1; then say "当前没有运行中的测速任务"; exit 1; fi
    rm -f probe.pause
    touch probe.stop
    say "已请求停止：当前节点检测结束后停止，并保存已完成结果"
    ;;
  progress)
    python - <<'PY'
import json, os, tempfile, subprocess
path = "progress.json"
running = subprocess.run(["pgrep", "-f", "[t]ermux_probe.py"], stdout=subprocess.DEVNULL).returncode == 0
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {"status": "idle", "done": 0, "total": 0}
data["process_running"] = running
if not running and data.get("status") in ("running", "preparing", "paused"):
    data["status"] = "interrupted"
    data["stage"] = "未检测到运行中的测速进程，已清理残留状态"
    try:
        fd, tmp = tempfile.mkstemp(prefix="progress.", suffix=".tmp", dir=".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass
print(json.dumps(data, ensure_ascii=False))
PY
    ;;
  logs) tail -n 100 "$LOG" 2>/dev/null || echo "暂无日志" ;;
  *) echo "Usage: app_control.sh {check|probe|pause|resume|stop|upload|progress|logs}"; exit 2 ;;
esac
