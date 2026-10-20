#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# Full re-measurement of the paper's experiments with one build of the code.
#
#   usage: sudo -E ./run_all.sh [E1 E2 E3 E5 ...]      (no arg = all)
#          sudo -E ./run_all.sh --smoke                 (the 4 checks of
#                                                        spec 02 section 9)
#          RESULTS=~/ndp_results/pilot_03b CSV_NAME=results_pilot.csv \
#            sudo -E ./run_all.sh P3 | L1 L2 C1 D1 D2 K1 K2   (spec 03b-H)
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
# Spec 03b-H pilot (section 3 smoke P3, section 4-1 L1..K2), see the pilot
# block at the end.
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
CSV_NAME=${CSV_NAME:-results.csv}
HOST_NIC=${HOST_NIC:-enp133s0f0}   # data link 192.168.100.0/30, NVMe/TCP only
TARGET_NIC=${TARGET_NIC:-ens1f0}
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
CSV="$RESULTS/$CSV_NAME"
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
  # Anchored at the start of the command line (relative or absolute launch
  # path): an unanchored pattern also matches the ssh command's own shell.
  TPAT='^(/[^ ]*/)?build/bin/nvmf_tgt'
  TARGET_BIN=$(tssh "pgrep -a -f '$TPAT' | awk '{print \$2}' | head -1" 2>/dev/null || true)
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
  TARGET_BIN=$(tssh "p=\$(pgrep -f '$TPAT' | head -1); readlink -f /proc/\$p/exe 2>/dev/null" 2>/dev/null || echo "$TARGET_BIN")
  TARGET_BIN_SHA=$(tssh "sha256sum '$TARGET_BIN' | cut -d' ' -f1" 2>/dev/null || true)
  # Allow-list of measurement builds, and an optional exact-hash pin
  # (EXPECT_TARGET_SHA) so a block can insist on one specific binary.
  ALLOWED_TARGETS=${ALLOWED_TARGETS:-"nvmf_tgt nvmf_tgt_measure nvmf_tgt_measure_v2 nvmf_tgt_measure_v2_fix"}
  case " $ALLOWED_TARGETS " in
    *" $(basename "$TARGET_BIN") "*) ;;
    *) if [ "$ALLOW_IOTRACE" != 1 ]; then
         echo "[ERR] target binary $(basename "$TARGET_BIN") is not in ALLOWED_TARGETS ($ALLOWED_TARGETS)"; exit 1
       fi ;;
  esac
  if [ -n "${EXPECT_TARGET_SHA:-}" ] && [ "$TARGET_BIN_SHA" != "$EXPECT_TARGET_SHA" ]; then
    echo "[ERR] target sha256 $TARGET_BIN_SHA != EXPECT_TARGET_SHA $EXPECT_TARGET_SHA"; exit 1
  fi
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
# A function so that a multi-block invocation (the M4 steps) can also leave
# one record per block, taken when the block starts.
write_env() {
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
} > "$1" 2>&1
echo "[env] $1"
}
write_env "$ENVTXT"

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

# Data-link byte counters, "rx tx" on one line. The target's are read over
# the management LAN, so the ssh itself never shows up in them.
host_nic() {
  echo "$(cat /sys/class/net/$HOST_NIC/statistics/rx_bytes 2>/dev/null) $(cat /sys/class/net/$HOST_NIC/statistics/tx_bytes 2>/dev/null)" > "$1"
}
tgt_nic() {
  : > "$1"
  [ -n "$TARGET_HOST" ] || return 0
  tssh "echo \$(cat /sys/class/net/$TARGET_NIC/statistics/rx_bytes) \$(cat /sys/class/net/$TARGET_NIC/statistics/tx_bytes)" > "$1" 2>/dev/null || : > "$1"
}

tgt_measure() {   # $1=outfile $2=run_id
  : > "$1"
  [ -n "$TARGET_HOST" ] || return 0
  tssh \
      "grep -F 'run_id=$2' '$TARGET_LOG' | grep -F '[NDP-MEASURE]' | tail -1" \
      > "$1" 2>/dev/null || : > "$1"
}

# one_run <exp> <input_tag> <video> <labels|-> <pipeline> <kernel> <quality> <sr> <rep> <dump 0|1> [extra opts] [stem suffix]
one_run() {
  local exp=$1 tag=$2 video=$3 labels=$4 pipe=$5 kern=$6 qual=$7 sr=$8 rep=$9 dump=${10}
  local extra=${11:-} suffix=${12:-}

  SEQNO=$((SEQNO + 1))
  local rid=$((RUN_BASE + SEQNO))
  local stem="${exp}_${tag}_${pipe}_${kern}_sr${sr}${suffix}_r${rep}"
  local log="$RESULTS/logs/${stem}.log"
  local sb="$RESULTS/io/${stem}.hstat.before" sa="$RESULTS/io/${stem}.hstat.after"
  local tb="$RESULTS/io/${stem}.tgt.before"   ta="$RESULTS/io/${stem}.tgt.after"
  local tm="$RESULTS/io/${stem}.tgtmeasure"
  local nb="$RESULTS/io/${stem}.nic.before"   na="$RESULTS/io/${stem}.nic.after"
  local tnb="$RESULTS/io/${stem}.tnic.before" tna="$RESULTS/io/${stem}.tnic.after"

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
  # shellcheck disable=SC2206
  [ -n "$extra" ] && opts+=($extra)

  echo "=== [$SEQNO] $stem run_id=$rid dump=$dump ==="

  echo "[cmd] $DARKNET detector video_map $DATA $CFG $WEIGHTS ${opts[*]}"

  drop_caches
  host_stat "$sb"
  tgt_iostat "$tb"
  tgt_nic "$tnb"
  host_nic "$nb"

  # The detector runs in the background so a watcher can sample its thread
  # count once a second (/proc/<pid>/status Threads); the peak goes into the
  # row. The watcher only reads one procfs file per second.
  local status=$OK_STATUS thr_peak=0 t
  "$DARKNET" detector video_map "$DATA" "$CFG" "$WEIGHTS" \
        "${opts[@]}" > "$log" 2>&1 &
  local dpid=$!
  while kill -0 "$dpid" 2>/dev/null; do
    t=$(awk '/^Threads:/{print $2}' "/proc/$dpid/status" 2>/dev/null)
    [ -n "$t" ] && [ "$t" -gt "$thr_peak" ] && thr_peak=$t
    sleep 1
  done
  wait "$dpid" || status=fail_exit

  host_nic "$na"
  tgt_nic "$tna"
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
      --tgt-bdev "$TARGET_BDEV" \
      --nic-before "$nb" --nic-after "$na" \
      --tgt-nic-before "$tnb" --tgt-nic-after "$tna" \
      --host-thr-peak "$thr_peak" --extra-opts "$extra"

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

# ── spec 03b-H: section 3 smoke (P3) and section 4-1 pilot ───────────
# Perf runs pass -post_thresh 0.25 explicitly (also the default) so the
# command line in the log says what the post-processing did. Repeats are
# the outermost loop; conditions with 3 repeats run in repeats 1-3, K2
# (one accuracy run per pipeline) in repeat 1.
PILOT_STEPS="P3 L1 L2 C1 D1 D1b D2 K1 K2"
is_pilot=0
for s in "${STEPS[@]:-}"; do case " $PILOT_STEPS " in *" $s "*) is_pilot=1 ;; esac; done
if [ "$is_pilot" = 1 ]; then
  PT="-post_thresh 0.25"
  M=$NVME_MNT
  if do_step P3; then
    one_run P3 res1080_h264 "$M/res1080_h264.mp4" - BASE sws -  1 1 0 "$PT"
    one_run P3 res1080_h264 "$M/res1080_h264.mp4" - q85  sws 85 1 1 0 "$PT"
  fi
  for rep in 1 2 3 4 5; do
    if do_step L1; then
      for r in 1080 2160; do
        V="$M/res${r}_h264.mp4"
        one_run L1 "res${r}_h264" "$V" - BASE sws -   1 "$rep" 0 "$PT"
        one_run L1 "res${r}_h264" "$V" - q85  sws 85  1 "$rep" 0 "$PT"
        one_run L1 "res${r}_h264" "$V" - raw  sws raw 1 "$rep" 0 "$PT"
      done
    fi
    [ "$rep" -le 3 ] || continue
    if do_step L2; then
      one_run L2 res2160_h264 "$M/res2160_h264.mp4" - q85 sws 85 5 "$rep" 0 "$PT"
    fi
    if do_step C1; then
      for r in 1080 2160; do
        one_run C1 "res${r}_h264" "$M/res${r}_h264.mp4" - q85 sws 85 1 "$rep" 0 "$PT -cv_threads 1" _ct1
      done
    fi
    if do_step D1; then
      for r in 1080 2160; do
        one_run D1 "res${r}_av1" "$M/res${r}_av1.mp4" - q85 sws 85 1 "$rep" 0 "$PT -dec_threads 14" _dt14
      done
    fi
    # D1b (added; not in spec 03b-H): libdav1d turns cdw14=14 into 25 threads
    # but 6 into 12, the largest that fits the target's 14 cores
    # (result_03b_target.md section 1-3).
    if do_step D1b; then
      for r in 1080 2160; do
        one_run D1b "res${r}_av1" "$M/res${r}_av1.mp4" - q85 sws 85 1 "$rep" 0 "$PT -dec_threads 6" _dt6
      done
    fi
    if do_step D2; then
      for f in res2160_h264 res2160_hevc; do
        one_run D2 "$f" "$M/$f.mp4" - q85 sws 85 1 "$rep" 0 "$PT -dec_threads 14" _dt14
      done
    fi
    if do_step K1; then
      for q in q85:85 q100:100 raw:raw; do
        one_run K1 res2160_h264 "$M/res2160_h264.mp4" - "${q%%:*}" darknet "${q##*:}" 1 "$rep" 0 "$PT"
      done
    fi
    if [ "$rep" = 1 ] && do_step K2; then
      one_run K2 MOT17-04 "$M/MOT17-04.mp4" labels/MOT17-04 BASE sws -  1 1 1
      one_run K2 MOT17-04 "$M/MOT17-04.mp4" labels/MOT17-04 q85  sws 85 1 1 1
    fi
  done
  echo
  echo "runs: $SEQNO"
  echo "results: $CSV"
  echo "env:     $ENVTXT"
  exit 0
fi

# ── spec 04-H: main measurement 2 (M4 steps) ─────────────────────────
# Blocks run in the order given on the command line (spec order:
# M4A M4C M4Cp M4D M4Dref M4E M4B M4G); inside a block the repeat is the
# outermost loop. Each block leaves its own env_<block>_<time>.txt.
# Performance runs: no labels, no dump, -post_thresh 0.25 -warmup 3;
# -dec_threads / -cv_threads only where the block says so.
M4_STEPS="M4A M4C M4Cp M4D M4Dref M4E M4B M4G"
is_m4=0
for s in "${STEPS[@]:-}"; do case " $M4_STEPS " in *" $s "*) is_m4=1 ;; esac; done
if [ "$is_m4" = 1 ]; then
  PT="-post_thresh 0.25 -warmup 3"
  M=$NVME_MNT
  NDP6="q85:sws:85 q100:sws:100 raw:sws:raw q85:darknet:85 q100:darknet:100 raw:darknet:raw"
  # m4_ndp <exp> <tag> <video> <labels|-> <sr> <rep> <dump> <extra> <suffix> <pipe:kern:qual...>
  m4_ndp() {
    local exp=$1 tag=$2 v=$3 lab=$4 sr=$5 rep=$6 dump=$7 extra=$8 suf=$9; shift 9
    local c
    for c in "$@"; do
      IFS=: read -r p k q <<< "$c"
      one_run "$exp" "$tag" "$v" "$lab" "$p" "$k" "$q" "$sr" "$rep" "$dump" "$extra" "$suf"
    done
  }
  block_start() { echo "##### block $1 start $(date -Is) #####"; write_env "$RESULTS/env_$1_$(date +%m%d_%H%M).txt"; }
  block_end()   { echo "##### block $1 end $(date -Is) #####"; }

  for step in "${STEPS[@]}"; do
    case "$step" in
    M4A)
      block_start M4A
      for rep in 1 2 3 4 5; do
        for r in 1080 1440 2160; do
          V="$M/res${r}_h264.mp4"
          one_run M4A "res${r}_h264" "$V" - BASE sws - 1 "$rep" 0 "$PT"
          m4_ndp M4A "res${r}_h264" "$V" - 1 "$rep" 0 "$PT" "" $NDP6
        done
      done
      block_end M4A ;;
    M4C)
      block_start M4C
      for rep in 1 2 3; do
        for f in res1080_hevc res2160_hevc res1080_av1 res2160_av1; do
          V="$M/$f.mp4"
          one_run M4C "$f" "$V" - BASE sws - 1 "$rep" 0 "$PT"
          case "$f" in *av1) dts="0 6 14" ;; *) dts="0 14" ;; esac
          for dt in $dts; do
            m4_ndp M4C "$f" "$V" - 1 "$rep" 0 "$PT -dec_threads $dt" "_dt$dt" \
              q85:sws:85 raw:sws:raw q100:darknet:100
          done
        done
      done
      block_end M4C ;;
    M4Cp)
      block_start M4Cp
      for rep in 1 2 3; do
        for r in 1080 2160; do
          m4_ndp M4Cp "res${r}_h264" "$M/res${r}_h264.mp4" - 1 "$rep" 0 "$PT -dec_threads 14" _dt14 \
            q85:sws:85 raw:sws:raw q100:darknet:100
        done
      done
      block_end M4Cp ;;
    M4D)
      block_start M4D
      for rep in 1 2 3; do
        for r in 1080 2160; do
          m4_ndp M4D "res${r}_h264" "$M/res${r}_h264.mp4" - 1 "$rep" 0 "$PT -cv_threads 1" _ct1 $NDP6
        done
      done
      block_end M4D ;;
    M4Dref)
      block_start M4Dref
      for rep in 1 2 3; do
        for r in 1080 2160; do
          one_run M4Dref "res${r}_h264" "$M/res${r}_h264.mp4" - BASE sws - 1 "$rep" 0 "$PT -cv_threads 1" _ct1
        done
      done
      block_end M4Dref ;;
    M4E)
      block_start M4E
      for rep in 1 2 3; do
        for sp in $MOT_SEQS; do
          IFS=: read -r tag vid lab <<< "$sp"
          one_run M4E "$tag" "$M/$vid" - BASE sws - 1 "$rep" 0 "$PT"
          m4_ndp M4E "$tag" "$M/$vid" - 1 "$rep" 0 "$PT" "" $NDP6
        done
      done
      block_end M4E ;;
    M4B)
      block_start M4B
      for rep in 1 2 3; do
        for r in 1080 1440 2160; do
          for sr in 2 5; do
            m4_ndp M4B "res${r}_h264" "$M/res${r}_h264.mp4" - "$sr" "$rep" 0 "$PT" "" $NDP6
          done
        done
      done
      block_end M4B ;;
    M4G)
      # Accuracy: labels + dump, -warmup 3 (post-processing at 0.005 is
      # forced by the labels). MOT20-03 darknet x3 to check determinism.
      block_start M4G
      for sp in $MOT_SEQS; do
        IFS=: read -r tag vid lab <<< "$sp"
        one_run M4G "$tag" "$M/$vid" "$lab" BASE sws - 1 1 1 "-warmup 3"
        m4_ndp M4G "$tag" "$M/$vid" "$lab" 1 1 1 "-warmup 3" "" $NDP6
      done
      for rep in 2 3; do
        sp=$(echo $MOT_SEQS | tr ' ' '\n' | grep '^MOT20-03:')
        IFS=: read -r tag vid lab <<< "$sp"
        m4_ndp M4G "$tag" "$M/$vid" "$lab" 1 "$rep" 1 "-warmup 3" "" \
          q85:darknet:85 q100:darknet:100 raw:darknet:raw
      done
      block_end M4G ;;
    M4SMOKE)
      ;;
    *) echo "[ERR] unknown M4 step $step" ;;
    esac
  done
  echo
  echo "runs: $SEQNO"
  echo "results: $CSV"
  exit 0
fi

# spec 04-H section 5-2 smoke: kept apart from the M4 steps so its rows are
# never mixed into the main measurement (run it with its own RESULTS).
if [ "${STEPS[0]:-}" = "M4SMOKE" ]; then
  PT="-post_thresh 0.25 -warmup 3"
  M=$NVME_MNT
  V="$M/res1080_h264.mp4"
  one_run S4 res1080_h264 "$V" - BASE sws -   1 1 0 "$PT"
  one_run S4 res1080_h264 "$V" - q85  sws 85  1 1 0 "$PT"
  one_run S4 res1080_h264 "$V" - raw  darknet raw 1 1 0 "$PT"
  one_run S4 res1080_av1 "$M/res1080_av1.mp4" - q85 sws 85 1 1 0 "$PT -dec_threads 6" _dt6
  one_run S4 res1080_h264 "$V" - BASE sws -   1 1 0 "$PT -cv_threads 1" _ct1
  echo
  echo "runs: $SEQNO"
  echo "results: $CSV"
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
