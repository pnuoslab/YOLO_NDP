#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Full re-measurement of the paper's experiments with one build of the code.
#
#   usage: sudo -E ./run_all.sh [E1 E2 E3 E5 ...]      (no arg = all)
#          sudo -E ./run_all.sh --smoke                 (the 4 checks of
#                                                        spec 02 section 9)
#
# Needs root: every run drops the host page cache first.
# Set TARGET_HOST to have the target's iostat and [NDP-MEASURE] pulled over
# ssh; without it those columns stay empty and the run still proceeds.
#
# Experiments
#   E1  resolution   res{1080,1440,2160}_h264  BASE/q85/q100/raw (sws)  sr 1,2,5
#   E5  codec        res{1080,2160}_{hevc,av1} BASE/q85          (sws)  sr 1
#   E2  scene+acc    5 MOT sequences           BASE/q85/q100/raw (sws)  sr 1,2,5
#   E3  kernel abl.  5 MOT sequences           q85/q100/raw  (darknet)  sr 1
# 3 repeats each, repeats as the OUTERMOST loop so a drifting machine shows
# up as spread between repeats rather than as a trend inside one condition.
#
# Every input - Baseline included - is read from the NVMe-oF volume, so the
# two paths read the same bytes from the same device and the host block
# counters are comparable. NDP needs this anyway: 0xC0 carries file extents
# of that volume.
# ══════════════════════════════════════════════════════════════════════
set -u

DARKNET=${DARKNET:-./darknet}
DATA=${DATA:-mot.data}
CFG=${CFG:-cfg/yolov7-tiny.cfg}
WEIGHTS=${WEIGHTS:-yolov7-tiny.weights}
NVME_DEV=${NVME_DEV:-/dev/nvme1n1}
NVME_MNT=${NVME_MNT:-/mnt/ext4}
BLOCK_DEV=${BLOCK_DEV:-nvme1n1}
RESULTS=${RESULTS:-$HOME/ndp_results}
REPEATS=${REPEATS:-3}
SLEEP_BETWEEN=${SLEEP_BETWEEN:-3}

TARGET_HOST=${TARGET_HOST:-target-server3}   # ~/.ssh/config alias, <target-lan-ip>:<ssh-port>
# nvmf_tgt writes to stdout, which the target operator tees to a file in
# ~kimkitae. Point this at whatever the running build is tee'd to.
TARGET_LOG=${TARGET_LOG:-/home/${SUDO_USER:-$USER}/nvmf_tgt_iotest.log}
TARGET_RPC=${TARGET_RPC:-/home/${SUDO_USER:-$USER}/near_data_processing_for_video/spdk/scripts/rpc.py}
TARGET_BDEV=${TARGET_BDEV:-}

# MOT sequences, as <tag>:<video>:<labels>
MOT_SEQS=${MOT_SEQS:-"MOT17-02:MOT17-02.mp4:labels/MOT17-02 \
MOT17-04:MOT17-04.mp4:labels/MOT17-04 \
MOT17-09:MOT17-09.mp4:labels/MOT17-09 \
MOT20-02:MOT20-02.mp4:labels/MOT20-02 \
MOT20-03:MOT20-03.mp4:labels/MOT20-03"}

SMOKE=0
STEPS=()
for a in "$@"; do
  case "$a" in
    --smoke) SMOKE=1 ;;
    *)       STEPS+=("$a") ;;
  esac
done
do_step() { [ ${#STEPS[@]} -eq 0 ] && return 0
            for s in "${STEPS[@]}"; do [ "$s" = "$1" ] && return 0; done
            return 1; }

# ALLOW_NO_NVME=1 exists to exercise this script's plumbing (argument
# building, row assembly, dump paths) on a host with no NVMe-oF volume and
# no target. It skips the root requirement and the page-cache drop, so the
# latency numbers it produces are NOT measurement grade - every row it
# writes is stamped status=ok_nodrop so such rows can never be mistaken for
# real ones. Never use it for the actual measurement.
ALLOW_NO_NVME=${ALLOW_NO_NVME:-0}
OK_STATUS=ok
if [ "$ALLOW_NO_NVME" = 1 ]; then
  OK_STATUS=ok_nodrop
  echo "[WARN] ALLOW_NO_NVME=1: no page-cache drop, no mount check."
  echo "[WARN] Rows are stamped status=ok_nodrop and are not measurement grade."
fi

[ -x "$DARKNET" ] || { echo "[ERR] not built: $DARKNET"; exit 1; }
[ "$ALLOW_NO_NVME" = 1 ] || [ "$(id -u)" -eq 0 ] || {
  echo "[ERR] run as root: every run drops the page cache"; exit 1; }
[ "$ALLOW_NO_NVME" = 1 ] || mountpoint -q "$NVME_MNT" || {
  echo "[ERR] $NVME_MNT is not mounted."
  echo "      The user prepares the volume by hand:"
  echo "        sudo nvme connect -t tcp -n nqn.2016-06.io.spdk:cnode1 -a 192.168.100.2 -s 4420"
  echo "        sudo mkfs.ext4 $NVME_DEV        # WIPES the volume"
  echo "        sudo mount $NVME_DEV $NVME_MNT"
  echo "      then copies the inputs in and runs 'sync'."
  exit 1
}

mkdir -p "$RESULTS"/{logs,dumps,io}
CSV="$RESULTS/results.csv"
RUN_BASE=$(( ($(date +%s) % 100000) * 10000 ))
SEQNO=0

# ssh to the target. Two things matter for an unattended multi-hour run:
#  - BatchMode=yes: with no usable key, plain ssh stops and asks for a
#    password, which would freeze the matrix at its first run. BatchMode
#    makes it fail at once instead, so the run continues with empty target
#    columns rather than hanging.
#  - It runs as the invoking user, not root. The script itself runs under
#    sudo, and ssh takes its keys from the passwd home directory - /root -
#    not from $HOME, so as root it would not find the user's key.
SSH_AS=${SSH_AS:-${SUDO_USER:-}}
tssh() {
  local -a cmd=(ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET_HOST" "$@")
  if [ "$(id -u)" -eq 0 ] && [ -n "$SSH_AS" ] && [ "$SSH_AS" != root ]; then
    sudo -u "$SSH_AS" "${cmd[@]}"
  else
    "${cmd[@]}"
  fi
}

# ── target build guard ────────────────────────────────────────────────
# The target ships several nvmf_tgt variants side by side, and the tracing
# one (nvmf_tgt_iotrace, NDP_IO_TRACE=1) adds per-I/O bookkeeping to the
# very preprocessing path being timed. It was left running once already
# after a restart was believed to have happened, so the check is
# mechanical: whatever binary is actually running is looked up and an
# iotrace build stops the script. ALLOW_IOTRACE=1 overrides, for the
# section 6 path check only.
ALLOW_IOTRACE=${ALLOW_IOTRACE:-0}
TARGET_BIN=""
if [ -n "$TARGET_HOST" ]; then
  TARGET_BIN=$(tssh "pgrep -a -f 'build/bin/nvmf_tgt' | grep -v -e sudo -e pgrep | awk '{print \$2}' | head -1" 2>/dev/null || true)
  if [ -z "$TARGET_BIN" ]; then
    echo "[ERR] no nvmf_tgt process found on $TARGET_HOST"; exit 1
  fi
  echo "[target] running: $TARGET_BIN"
  case "$TARGET_BIN" in
    *iotrace*)
      if [ "$ALLOW_IOTRACE" != 1 ]; then
        echo "[ERR] the target is running the I/O-tracing build ($TARGET_BIN)."
        echo "      Restart it with the measurement build (nvmf_tgt_measure or"
        echo "      nvmf_tgt) before measuring; ALLOW_IOTRACE=1 overrides."
        exit 1
      fi
      echo "[WARN] ALLOW_IOTRACE=1: measuring against a tracing build" ;;
  esac
  # pgrep shows the path as typed at launch, which may be relative (it was:
  # "build/bin/nvmf_tgt_measure", so sha256sum from $HOME found nothing and
  # env.txt recorded "?"). /proc/<pid>/exe is the absolute path of what is
  # actually running.
  TARGET_BIN=$(tssh "p=\$(pgrep -f 'build/bin/nvmf_tgt' | head -1); readlink -f /proc/\$p/exe 2>/dev/null" 2>/dev/null || echo "$TARGET_BIN")
  TARGET_BIN_SHA=$(tssh "sha256sum '$TARGET_BIN' | cut -d' ' -f1" 2>/dev/null || true)
  if ! tssh "test -r '$TARGET_LOG'" 2>/dev/null; then
    echo "[ERR] TARGET_LOG=$TARGET_LOG is not readable on $TARGET_HOST;"
    echo "      [NDP-MEASURE] lines could not be paired. Set TARGET_LOG to the"
    echo "      file the running nvmf_tgt is tee'd to."
    exit 1
  fi
fi

# ── environment record ────────────────────────────────────────────────
# One file per invocation. Two blocks share a RESULTS directory (E1+E5,
# then E2+E3), and a single env.txt let the second block overwrite the
# first block's record of the commit and target binary it ran with.
ENVTXT="$RESULTS/env_$(echo "${STEPS[@]:-all}" | tr ' ' '_')$([ "$SMOKE" = 1 ] && echo _smoke)_$(date +%m%d_%H%M).txt"
{
  echo "date: $(date -Is)"
  echo "host: $(hostname)  kernel: $(uname -r)"
  echo "host commit: $(git -C "$(dirname "$0")" log -1 --format='%H %ci %s' 2>/dev/null)"
  # Tracked files only: untracked data directories (labels/, maplogs/, ...)
  # are always present and would make a clean tree look modified.
  echo "host dirty:  $(git -C "$(dirname "$0")" status --porcelain --untracked-files=no 2>/dev/null | wc -l) modified tracked files"
  echo "binary: $DARKNET  mtime=$(stat -c %y "$DARKNET")  sha256=$(sha256sum "$DARKNET" | cut -d' ' -f1)"
  echo "weights: $WEIGHTS sha256=$(sha256sum "$WEIGHTS" | cut -d' ' -f1)"
  echo "opencv: $(pkg-config --modversion opencv4 2>/dev/null)"
  echo "ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"
  echo "run_base: $RUN_BASE"
  echo
  echo "── cpu governor ──"
  for c in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
    [ -r "$c" ] && cat "$c"
  done | sort | uniq -c
  echo
  echo "── nvidia-smi clocks ──"
  nvidia-smi -q -d CLOCK 2>/dev/null | sed -n '1,40p'
  echo
  echo "── nvme-oF ──"
  nvme list-subsys 2>/dev/null
  mount | grep -F "$NVME_MNT"
  echo
  if [ -n "$TARGET_HOST" ]; then
    echo "── target ($TARGET_HOST) ──"
    echo "binary: $TARGET_BIN  sha256=${TARGET_BIN_SHA:-?}"
    echo "log:    $TARGET_LOG"
    tssh \
      "uname -r; $TARGET_RPC spdk_get_version 2>/dev/null | head -20; \
       git -C /home/${SUDO_USER:-$USER}/near_data_processing_for_video/spdk log -1 --format='%H %ci %s' 2>/dev/null" 2>&1
  else
    echo "── target: TARGET_HOST not set, target columns will be empty ──"
  fi
} > "$ENVTXT" 2>&1
echo "[env] $ENVTXT"

# ── helpers ───────────────────────────────────────────────────────────
drop_caches() {
  [ "$ALLOW_NO_NVME" = 1 ] && { sync; return 0; }
  sync; echo 3 > /proc/sys/vm/drop_caches
}

host_stat() { cat "/sys/block/$BLOCK_DEV/stat" > "$1" 2>/dev/null || : > "$1"; }

tgt_iostat() {
  : > "$1"
  [ -n "$TARGET_HOST" ] || return 0
  tssh \
      "$TARGET_RPC bdev_get_iostat" > "$1" 2>/dev/null || : > "$1"
}

tgt_measure() {   # $1=outfile $2=run_id
  : > "$1"
  [ -n "$TARGET_HOST" ] || return 0
  tssh \
      "grep -F 'run_id=$2' '$TARGET_LOG' | grep -F '[NDP-MEASURE]' | tail -1" \
      > "$1" 2>/dev/null || : > "$1"
}

# one_run <exp> <input_tag> <video> <labels|-> <pipeline> <kernel> <quality> <sr> <rep> <dump 0|1>
one_run() {
  local exp=$1 tag=$2 video=$3 labels=$4 pipe=$5 kern=$6 qual=$7 sr=$8 rep=$9 dump=${10}

  SEQNO=$((SEQNO + 1))
  local rid=$((RUN_BASE + SEQNO))
  local stem="${exp}_${tag}_${pipe}_${kern}_sr${sr}_r${rep}"
  local log="$RESULTS/logs/${stem}.log"
  local sb="$RESULTS/io/${stem}.hstat.before" sa="$RESULTS/io/${stem}.hstat.after"
  local tb="$RESULTS/io/${stem}.tgt.before"   ta="$RESULTS/io/${stem}.tgt.after"
  local tm="$RESULTS/io/${stem}.tgtmeasure"

  local -a opts=(-file "$video" -sample_rate "$sr" -label_base 1
                 -points 101 -coco_map -dont_show -run_id "$rid")
  [ "$labels" != "-" ] && opts+=(-labels "$labels")

  case "$pipe" in
    BASE) ;;
    q85)  opts+=(-ndp -dev "$NVME_DEV" -jpeg_q 85  -chroma 420 -resize "$kern") ;;
    q100) opts+=(-ndp -dev "$NVME_DEV" -jpeg_q 100 -chroma 420 -resize "$kern") ;;
    raw)  opts+=(-ndp -dev "$NVME_DEV" -jpeg_q -1              -resize "$kern") ;;
    *)    echo "[ERR] unknown pipeline $pipe"; return 1 ;;
  esac
  # -jpeg_q -1 selects the uncompressed RGB24 payload (NDP_JPEG_Q_RAW=101 in
  # detector.c); -chroma does not apply there and is left off.

  if [ "$dump" = 1 ]; then
    opts+=(-dump_dets "$RESULTS/dumps/${stem}.csv")
  fi

  echo "=== [$SEQNO] $stem run_id=$rid dump=$dump ==="

  drop_caches
  host_stat "$sb"
  tgt_iostat "$tb"

  local status=$OK_STATUS
  if ! "$DARKNET" detector video_map "$DATA" "$CFG" "$WEIGHTS" \
        "${opts[@]}" > "$log" 2>&1; then
    status=fail_exit
  fi

  host_stat "$sa"
  tgt_iostat "$ta"
  tgt_measure "$tm" "$rid"

  python3 -I collect_row.py --csv "$CSV" \
      --exp "$exp" --input "$tag" --pipeline "$pipe" --kernel "$kern" \
      --quality "$qual" --sample_rate "$sr" --rep "$rep" \
      --run_id_req "$rid" --dump "$dump" --status "$status" \
      --log "$log" --tgt-measure "$tm" \
      --host-stat-before "$sb" --host-stat-after "$sa" \
      --tgt-iostat-before "$tb" --tgt-iostat-after "$ta" \
      --tgt-bdev "$TARGET_BDEV"

  sleep "$SLEEP_BETWEEN"
}

# A missing input is reported once and skipped, so one absent file does not
# abort a multi-hour matrix.
have() {
  if [ -f "$1" ]; then return 0; fi
  echo "[SKIP] missing input: $1"
  return 1
}

# ── smoke test (spec 02 section 9) ────────────────────────────────────
if [ "$SMOKE" = 1 ]; then
  echo "### smoke test: 5 runs (spec 02 section 9, plus a dump-off/on pair) ###"
  V="$NVME_MNT/res1080_h264.mp4"
  # Runs 1 and 1b are the same condition with the dump off and on, which is
  # how spec 02 section 8-1 asks the dump's effect on latency to be checked.
  have "$V" && one_run E1 res1080_h264 "$V" - BASE sws -    1 1 0
  have "$V" && one_run E1 res1080_h264 "$V" - BASE sws -    1 2 1
  have "$V" && one_run E1 res1080_h264 "$V" - q85  sws 85   5 1 1
  V="$NVME_MNT/MOT17-04.mp4"
  have "$V" && one_run E2 MOT17-04 "$V" labels/MOT17-04 raw sws raw 1 1 1
  V="$NVME_MNT/res1080_av1.mp4"
  have "$V" && one_run E5 res1080_av1 "$V" - q85 sws 85 1 1 0
  echo "### smoke done: $CSV ###"
  exit 0
fi

# ── full matrix ───────────────────────────────────────────────────────
for rep in $(seq 1 "$REPEATS"); do
  # The detection dump is deterministic, so it is only needed once; taking
  # it on repeat 1 also lets repeats 2-3 serve as the dump-free control for
  # the latency numbers. Baseline is dumped too, not just NDP: the offline
  # analysis measures NDP accuracy RELATIVE to Baseline, so the Baseline
  # dump is the reference every NDP dump is read against - and on the 4K
  # clips, which have no ground truth at all, it is the only reference there
  # is.
  dump=$([ "$rep" = 1 ] && echo 1 || echo 0)

  if do_step E1; then
    for r in 1080 1440 2160; do
      V="$NVME_MNT/res${r}_h264.mp4"
      have "$V" || continue
      for sr in 1 2 5; do
        one_run E1 "res${r}_h264" "$V" - BASE sws -    "$sr" "$rep" "$dump"
        one_run E1 "res${r}_h264" "$V" - q85  sws 85   "$sr" "$rep" "$dump"
        one_run E1 "res${r}_h264" "$V" - q100 sws 100  "$sr" "$rep" "$dump"
        one_run E1 "res${r}_h264" "$V" - raw  sws raw  "$sr" "$rep" "$dump"
      done
    done
  fi

  if do_step E5; then
    for f in res1080_hevc res2160_hevc res1080_av1 res2160_av1; do
      V="$NVME_MNT/${f}.mp4"
      have "$V" || continue
      one_run E5 "$f" "$V" - BASE sws -  1 "$rep" "$dump"
      one_run E5 "$f" "$V" - q85  sws 85 1 "$rep" "$dump"
    done
  fi

  if do_step E2; then
    for spec in $MOT_SEQS; do
      IFS=: read -r tag vid lab <<< "$spec"
      V="$NVME_MNT/$vid"
      have "$V" || continue
      for sr in 1 2 5; do
        one_run E2 "$tag" "$V" "$lab" BASE sws -    "$sr" "$rep" "$dump"
        one_run E2 "$tag" "$V" "$lab" q85  sws 85   "$sr" "$rep" "$dump"
        one_run E2 "$tag" "$V" "$lab" q100 sws 100  "$sr" "$rep" "$dump"
        one_run E2 "$tag" "$V" "$lab" raw  sws raw  "$sr" "$rep" "$dump"
      done
    done
  fi

  if do_step E3; then
    for spec in $MOT_SEQS; do
      IFS=: read -r tag vid lab <<< "$spec"
      V="$NVME_MNT/$vid"
      have "$V" || continue
      one_run E3 "$tag" "$V" "$lab" q85  darknet 85  1 "$rep" "$dump"
      one_run E3 "$tag" "$V" "$lab" q100 darknet 100 1 "$rep" "$dump"
      one_run E3 "$tag" "$V" "$lab" raw  darknet raw 1 "$rep" "$dump"
    done
  fi
done

echo
echo "runs: $SEQNO"
echo "results: $CSV"
echo "env:     $ENVTXT"
