#!/bin/bash
# Live checks for spec 02-T that need a running nvmf_tgt.
#   §3-1 verification 1 : which threads exist and how much CPU each uses
#   §3-4 item 1         : VmRSS / VmHWM / HugetlbPages of the target
# Read-only: starts nothing, changes nothing.
#
# Usage:  bash target_probe.sh [seconds]      (default 10)

SECS="${1:-10}"
PID=$(pgrep -f '[n]vmf_tgt' | head -1)

if [ -z "$PID" ]; then
    echo "ERROR: nvmf_tgt is not running. Start it first, then re-run this script."
    exit 1
fi

CLK=$(getconf CLK_TCK)
echo "nvmf_tgt PID: $PID   sample window: ${SECS}s   CLK_TCK=$CLK"
echo

echo "=== /proc/$PID/status (memory) ==="
grep -E '^(VmRSS|VmHWM|VmSize|HugetlbPages|Threads):' /proc/$PID/status
echo

echo "=== threads and their CPU over ${SECS}s ==="
declare -A T0
for t in /proc/$PID/task/*; do
    tid=${t##*/}
    read -r _ _ rest < <(sed 's/.*) //' $t/stat 2>/dev/null)
    set -- $rest
    # after "comm) ", field 12 = utime, 13 = stime (state is field 1)
    T0[$tid]=$(( ${12:-0} + ${13:-0} ))
done

sleep "$SECS"

printf "%-8s %-18s %10s %10s\n" TID NAME CPU_s "%%ofwall"
total=0
for t in /proc/$PID/task/*; do
    tid=${t##*/}
    [ -r "$t/stat" ] || continue
    name=$(cat $t/comm 2>/dev/null)
    read -r _ _ rest < <(sed 's/.*) //' $t/stat 2>/dev/null)
    set -- $rest
    now=$(( ${12:-0} + ${13:-0} ))
    d=$(( now - ${T0[$tid]:-$now} ))
    cpu=$(awk -v d="$d" -v c="$CLK" 'BEGIN{printf "%.3f", d/c}')
    pct=$(awk -v cp="$cpu" -v s="$SECS" 'BEGIN{printf "%.1f", 100*cp/s}')
    total=$(awk -v t="$total" -v c="$cpu" 'BEGIN{print t+c}')
    printf "%-8s %-18s %10s %10s\n" "$tid" "$name" "$cpu" "$pct"
done
echo "---"
echo "sum of all thread CPU over ${SECS}s: ${total}s"
echo
echo "Interpretation: any thread other than reactor_* showing non-trivial CPU"
echo "would contaminate cpu_storage_s, since that figure subtracts only the"
echo "reactor's own CPU from the process total."
