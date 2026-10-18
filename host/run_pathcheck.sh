#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Section 6 path-check: does the I/O we think we are measuring actually
# reach the device, and does a page-cache hit really bypass it?
#
#   usage: sudo -E TARGET_HOST=oslab-server3 ./run_pathcheck.sh
#
# Run this with the target up in its NDP_IO_TRACE=1 build, after the user
# has connected, formatted and mounted the volume by hand. It only writes
# and reads one scratch file, which it deletes at the end; it does not
# touch the measurement inputs.
#
# What it establishes:
#   - a direct write of a known size shows up in the host block counters
#     and in the target's bdev_get_iostat, and the two agree
#   - after drop_caches, a read of that file shows up in both as well
#   - WITHOUT drop_caches the same read shows ~0 device traffic
# The third point is the one that matters for the paper: it is the
# evidence that the September Baseline runs, which never dropped the
# cache, were not reading from the device at all.
# ══════════════════════════════════════════════════════════════════════
set -u

NVME_DEV=${NVME_DEV:-/dev/nvme1n1}
NVME_MNT=${NVME_MNT:-/mnt/ext4}
BLOCK_DEV=${BLOCK_DEV:-nvme1n1}
TARGET_HOST=${TARGET_HOST:-}
TARGET_RPC=${TARGET_RPC:-/root/spdk/scripts/rpc.py}
SIZE_MB=${SIZE_MB:-512}
OUT=${OUT:-$HOME/ndp_results/pathcheck}
TESTFILE="$NVME_MNT/iotest.bin"

[ "$(id -u)" -eq 0 ] || { echo "[ERR] run as root (drop_caches, O_DIRECT dd)"; exit 1; }
mountpoint -q "$NVME_MNT" || { echo "[ERR] $NVME_MNT not mounted - the user prepares the volume by hand"; exit 1; }
[ -r "/sys/block/$BLOCK_DEV/stat" ] || { echo "[ERR] no /sys/block/$BLOCK_DEV/stat"; exit 1; }

mkdir -p "$OUT"

snap() {   # $1 = label
  cat "/sys/block/$BLOCK_DEV/stat" > "$OUT/$1.hstat"
  if [ -n "$TARGET_HOST" ]; then
    ssh -o ConnectTimeout=5 "$TARGET_HOST" "$TARGET_RPC bdev_get_iostat" \
      > "$OUT/$1.tgt" 2>/dev/null || : > "$OUT/$1.tgt"
  else
    : > "$OUT/$1.tgt"
  fi
}

echo "=== step 7 first: can we reach the target's RPC? ==="
if [ -z "$TARGET_HOST" ]; then
  echo "  TARGET_HOST not set - target columns will be empty."
else
  if ssh -o ConnectTimeout=5 "$TARGET_HOST" "$TARGET_RPC bdev_get_iostat" \
       > "$OUT/rpc_probe.json" 2>"$OUT/rpc_probe.err"; then
    echo "  OK: $(wc -c < "$OUT/rpc_probe.json") bytes of JSON"
    python3 -I -c "
import json,sys
d=json.load(open('$OUT/rpc_probe.json'))
b=d.get('bdevs',d) if isinstance(d,dict) else d
print('  bdevs:', ', '.join(str(e.get('name')) for e in b))
" 2>/dev/null || echo "  (could not parse - see $OUT/rpc_probe.json)"
  else
    echo "  FAILED. stderr:"; sed 's/^/    /' "$OUT/rpc_probe.err"
    echo "  Fix the ssh/rpc path before running the measurement; the"
    echo "  target iostat columns of results.csv depend on it."
  fi
fi

echo
echo "=== S0: baseline counters ==="
snap S0

echo "=== S1: direct write of ${SIZE_MB} MiB ==="
dd if=/dev/urandom of="$TESTFILE" bs=1M count="$SIZE_MB" oflag=direct \
   2>&1 | tail -1
sync
snap S1

echo "=== drop caches ==="
sync; echo 3 > /proc/sys/vm/drop_caches

echo "=== S2: read with a cold cache ==="
dd if="$TESTFILE" of=/dev/null bs=1M 2>&1 | tail -1
snap S2

echo "=== S3: read again, cache NOT dropped ==="
dd if="$TESTFILE" of=/dev/null bs=1M 2>&1 | tail -1
snap S3

echo
python3 -I - "$OUT" "$SIZE_MB" <<'PY'
import json, os, sys

out, size_mb = sys.argv[1], int(sys.argv[2])
nominal = size_mb * 1024 * 1024

def hstat(tag):
    p = os.path.join(out, tag + ".hstat")
    if not os.path.isfile(p):
        return None
    f = open(p).read().split()
    if len(f) < 10:
        return None
    f = [int(x) for x in f]
    # read_ios read_merges read_sectors read_ticks write_ios write_merges
    # write_sectors write_ticks in_flight io_ticks ...
    return {"rd": f[2] * 512, "wr": f[6] * 512, "ticks": f[9]}

def tgt(tag):
    p = os.path.join(out, tag + ".tgt")
    if not os.path.isfile(p) or os.path.getsize(p) == 0:
        return None
    try:
        d = json.load(open(p))
    except Exception:
        return None
    bl = d.get("bdevs", d) if isinstance(d, dict) else d
    if not isinstance(bl, list):
        return None
    acc = {"rd": 0, "wr": 0}
    for e in bl:
        acc["rd"] += int(e.get("bytes_read", 0))
        acc["wr"] += int(e.get("bytes_written", 0))
    return acc

def human(n):
    return "n/a" if n is None else f"{n:,}"

steps = [("S0->S1", "S0", "S1", "direct write"),
         ("S1->S2", "S1", "S2", "read, cold cache"),
         ("S2->S3", "S2", "S3", "read, warm cache")]

print(f"nominal transfer per step: {nominal:,} bytes ({size_mb} MiB)")
print()
hdr = ("step", "what", "host_read", "host_write", "io_ticks_ms",
       "tgt_read", "tgt_write")
print("{:<8} {:<18} {:>14} {:>14} {:>12} {:>14} {:>14}".format(*hdr))
for label, a, b, what in steps:
    ha, hb = hstat(a), hstat(b)
    ta, tb = tgt(a), tgt(b)
    hr = hw = ht = tr = tw = None
    if ha and hb:
        hr, hw, ht = hb["rd"] - ha["rd"], hb["wr"] - ha["wr"], hb["ticks"] - ha["ticks"]
    if ta and tb:
        tr, tw = tb["rd"] - ta["rd"], tb["wr"] - ta["wr"]
    print("{:<8} {:<18} {:>14} {:>14} {:>12} {:>14} {:>14}".format(
        label, what, human(hr), human(hw), human(ht), human(tr), human(tw)))

print()
rd_cold = hstat("S2")["rd"] - hstat("S1")["rd"] if hstat("S1") and hstat("S2") else None
rd_warm = hstat("S3")["rd"] - hstat("S2")["rd"] if hstat("S2") and hstat("S3") else None
if rd_cold is not None and rd_warm is not None:
    print("--- the point of this test ---")
    print(f"cold-cache read reached the device: {rd_cold:,} bytes "
          f"({rd_cold / nominal * 100:.1f}% of nominal)")
    print(f"warm-cache read reached the device: {rd_warm:,} bytes "
          f"({rd_warm / nominal * 100:.1f}% of nominal)")
    if rd_warm < nominal * 0.05:
        print("PASS: a page-cache hit does essentially no device I/O, so a run")
        print("      that does not drop the cache is not measuring device reads.")
    else:
        print("UNEXPECTED: the warm read still hit the device. Check whether")
        print("      the file is larger than free memory, or the cache was")
        print("      dropped by something else between the two reads.")
PY

echo
echo "=== cleanup ==="
rm -f "$TESTFILE"
sync
echo "removed $TESTFILE"
echo "snapshots: $OUT"
