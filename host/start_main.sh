#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Start a block of the main measurement with one sudo prompt, detached so
# it survives the terminal tab closing.
#
#   usage: sudo ./start_main.sh E1 E5          (any run_all.sh step names)
#
# 1. remount_verify.sh - reattach the volume and check every input; the
#    measurement is never started if this fails.
# 2. run_all.sh with the given steps, under setsid + nohup, as root, with
#    its output in $RESULTS/run.log. This script then returns; progress is
#    followed through that log and results.csv.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

[ "$(id -u)" -eq 0 ] || { echo "[STOP] run with sudo" >&2; exit 1; }
[ $# -gt 0 ] || { echo "usage: sudo $0 <steps...>   e.g. E1 E5" >&2; exit 2; }

export RESULTS=${RESULTS:-/home/${SUDO_USER:-$USER}/ndp_results/main}
export TARGET_LOG=${TARGET_LOG:-/home/${SUDO_USER:-$USER}/nvmf_tgt_measure.log}
mkdir -p "$RESULTS"
LOG="$RESULTS/run_$(echo "$@" | tr ' ' '_')_$(date +%m%d_%H%M).log"

./remount_verify.sh 2>&1 | tee "$RESULTS/remount_$(date +%m%d_%H%M).log"

setsid nohup ./run_all.sh "$@" > "$LOG" 2>&1 < /dev/null &
pid=$!
sleep 3
if kill -0 "$pid" 2>/dev/null; then
  echo
  echo "started: run_all.sh $*  (pid $pid)"
  echo "log:     $LOG"
  echo "results: $RESULTS/results.csv"
  echo "It keeps running if this tab is closed."
else
  echo "[STOP] run_all.sh exited at once:" >&2
  tail -20 "$LOG" >&2
  exit 1
fi
