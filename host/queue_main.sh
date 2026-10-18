#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Queue a block of the main measurement to start as soon as the block that
# is running now finishes - one sudo prompt now, nothing later.
#
#   usage: sudo ./queue_main.sh E2 E3
#
# Unlike start_main.sh this does NOT remount the volume: the running block
# is using it, and an unmount underneath it would destroy that block. The
# volume was already verified when the running block started.
#
# The waiter detaches at once (setsid + nohup), so this returns
# immediately and the queue survives the terminal tab closing. Before
# starting, it re-checks that the volume is still mounted and not in
# shutdown, and that the previous block left no run_all.sh behind.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")"

[ "$(id -u)" -eq 0 ] || { echo "[STOP] run with sudo" >&2; exit 1; }
[ $# -gt 0 ] || { echo "usage: sudo $0 <steps...>   e.g. E2 E3" >&2; exit 2; }

export RESULTS=${RESULTS:-/home/${SUDO_USER:-$USER}/ndp_results/main}
export TARGET_LOG=${TARGET_LOG:-/home/${SUDO_USER:-$USER}/nvmf_tgt_measure.log}
MNT=${MNT:-/mnt/ext4}
TAG=$(echo "$@" | tr ' ' '_')
QLOG="$RESULTS/queue_${TAG}_$(date +%m%d_%H%M).log"

running=$(pgrep -af '^bash \./run_all\.sh' || true)
echo "currently running:"
echo "${running:-  (nothing)}"

setsid nohup bash -c '
  echo "[$(date -Is)] waiting for the running run_all.sh to finish"
  # Anchored on the real process (cmdline "bash ./run_all.sh ..."). An
  # unanchored "run_all.sh" also matches THIS waiter, whose own command
  # line contains the text, and it would wait forever.
  while pgrep -f "^bash \./run_all\.sh" >/dev/null; do sleep 30; done
  echo "[$(date -Is)] previous block finished"
  if ! findmnt "'"$MNT"'" >/dev/null || findmnt -no OPTIONS "'"$MNT"'" | grep -q shutdown; then
    echo "[$(date -Is)] [STOP] '"$MNT"' is not mounted or is in shutdown; not starting"
    exit 1
  fi
  RUNLOG="'"$RESULTS"'/run_'"$TAG"'_$(date +%m%d_%H%M).log"
  echo "[$(date -Is)] starting run_all.sh '"$*"' -> $RUNLOG"
  exec ./run_all.sh '"$*"' > "$RUNLOG" 2>&1 < /dev/null
' > "$QLOG" 2>&1 < /dev/null &

sleep 2
echo
echo "queued: run_all.sh $*  (starts when the running block ends)"
echo "queue log: $QLOG"
echo "It keeps waiting if this tab is closed."
