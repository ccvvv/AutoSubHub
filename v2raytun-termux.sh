#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

CACHE="${CACHE_DIR:-candidate-cache}"
CANDIDATE="$CACHE/candidate-local.yaml"
MAP="$CACHE/v2raytun-import.map.json"

usage() {
  cat <<'EOF'
AutoSubHub / V2RayTun Termux helper

  bash v2raytun-termux.sh prepare   Download cumulative pending nodes, convert, copy subscription, open V2RayTun
  bash v2raytun-termux.sh convert   Convert the existing downloaded candidate list
  bash v2raytun-termux.sh collect   Read clipboard export AFTER V2RayTun real-delay test + invalid-node deletion
  bash v2raytun-termux.sh upload    Upload the inspected V2RayTun receipt to its own GitHub branch
  bash v2raytun-termux.sh help

The helper does not tap V2RayTun buttons or claim tests succeeded.
EOF
}

case "${1:-help}" in
  prepare)
    python download_candidates.py --output "$CANDIDATE"
    python tools/v2raytun_bridge.py --candidate-file "$CANDIDATE" --output "$CACHE/v2raytun-import.txt" --copy
    am start -n com.v2raytun.android/.ui.activity.MainActivity
    echo
    echo "已打开 V2RayTun。请在应用中导入剪贴板订阅。"
    echo "导入后执行 Real delay all configurations，再执行 Delete invalid config (Test first)。"
    echo "完成后回到 Termux，运行：bash v2raytun-termux.sh collect"
    ;;
  convert)
    python tools/v2raytun_bridge.py --candidate-file "$CANDIDATE" --output "$CACHE/v2raytun-import.txt"
    ;;
  collect)
    python tools/collect_v2raytun_survivors.py --map "$MAP" --clipboard --confirm-invalid-removed --output "$CACHE/v2raytun-survivors.json"
    echo "请检查 $CACHE/v2raytun-survivors.json；未匹配节点不会被标记为失败。"
    ;;
  upload)
    python upload_v2raytun_receipt.py --input "$CACHE/v2raytun-survivors.json" --candidate "$CANDIDATE" --manifest "$CACHE/candidate-local.manifest.json" --map "$MAP"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
