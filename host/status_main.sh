#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# One-look status of the main measurement. No sudo needed.
#
#   usage: ./status_main.sh
#
# Shows which block is running or queued, how many runs each experiment
# has finished out of its planned count, any failed rows, the last run,
# a rough time to finish, and whether the target and the volume are up.
# ══════════════════════════════════════════════════════════════════════
R=${RESULTS:-/home/${SUDO_USER:-$USER}/ndp_results/main}
TARGET_HOST=${TARGET_HOST:-target-server3}

echo "── processes ──"
run=$(pgrep -af '^bash (\./|/[^ ]*/)run_all\.sh' || true)
echo "  running: ${run:-none}"
if pgrep -f 'previous block finished' >/dev/null; then
  echo "  queued:  $(ls -t "$R"/queue_*.log 2>/dev/null | head -1 | sed 's/.*queue_//; s/_[0-9]*_[0-9]*\.log//; s/_/ /g') (waits for the running block)"
fi

python3 -I - "$R" "${run:-}" <<'PY'
import csv, os, sys, datetime as dt
root, running = sys.argv[1], sys.argv[2]
path = os.path.join(root, "results.csv")
rows = list(csv.DictReader(open(path))) if os.path.isfile(path) else []
plan = {"E1": 108, "E5": 24, "E2": 180, "E3": 45}
print("── progress ──")
done = {}
for r in rows:
    done[r["exp"]] = done.get(r["exp"], 0) + 1
for e in ["E1", "E5", "E2", "E3"]:
    n = done.get(e, 0)
    bar = "#" * int(20 * n / plan[e])
    print(f"  {e}: {n:3d}/{plan[e]:<3d} [{bar:<20s}]{'  done' if n >= plan[e] else ''}")
total, planned = len(rows), sum(plan.values())
print(f"  all: {total}/{planned}")

bad = [r for r in rows if not r["status"].startswith("ok")]
print(f"── failed rows: {len(bad)} ──")
for r in bad[-10:]:
    print(f"  {r['run_id_req']} {r['exp']} {r['input']} {r['pipeline']} sr={r['sample_rate']} rep={r['rep']} {r['status']}")

if rows:
    r = rows[-1]
    t = dt.datetime.fromtimestamp(os.path.getmtime(path))
    print(f"── last run ({t:%H:%M}) ──")
    print(f"  {r['exp']} {r['input']} {r['pipeline']} sr={r['sample_rate']} rep={r['rep']} "
          f"status={r['status']} e2e={r['e2e']}s")

# Rough ETA: average wall time per finished run of the running block,
# times the runs it still has. Per-run time varies a lot (4K Baseline is
# several times a 1080p NDP run), so this is a ballpark, not a promise.
if running and rows:
    steps = [s for s in running.split()[2:] if s in plan]
    left = sum(plan[s] - done.get(s, 0) for s in steps)
    logs = sorted((os.path.getmtime(os.path.join(root, "logs", f)), f)
                  for f in os.listdir(os.path.join(root, "logs"))
                  if f.split("_")[0] in steps)
    if len(logs) >= 2 and left > 0:
        per = (logs[-1][0] - logs[0][0]) / (len(logs) - 1)
        eta = dt.datetime.now() + dt.timedelta(seconds=per * left)
        queued = [e for e in plan if e not in steps and done.get(e, 0) < plan[e]]
        print(f"── estimate ──")
        print(f"  running block: ~{left} runs left, about {per/60:.1f} min each -> ends ~{eta:%H:%M}")
        if queued:
            print(f"  then queued {' '.join(queued)} starts by itself")
PY

echo "── target ──"
if t=$(ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET_HOST" \
     'pgrep -af "build/bin/nvmf_tgt" | grep -v -e sudo -e "bash -c" | head -1' 2>/dev/null) && [ -n "$t" ]; then
  echo "  $t"
else
  echo "  NOT RUNNING or unreachable"
fi
echo "── volume ──"
if findmnt -no OPTIONS /mnt/ext4 2>/dev/null | grep -q shutdown; then
  echo "  /mnt/ext4 is in SHUTDOWN state"
elif findmnt /mnt/ext4 >/dev/null 2>&1; then
  echo "  /mnt/ext4 mounted, nvme1 $(cat /sys/class/nvme/nvme1/state 2>/dev/null)"
else
  echo "  /mnt/ext4 NOT mounted"
fi
