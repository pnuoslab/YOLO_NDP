#!/usr/bin/env bash
# Performance-only measurement for section 4.2 (4K footage). This footage has
# no ground truth, so -labels is omitted entirely and detector.c runs in
# performance-only mode: the pipeline is exercised and [MEASURE] is printed,
# but no annotations are read and no mAP is computed (ap50/ap5095 come back
# as -1 to mark "not measured"). The fields that matter here are
# e2e / io / host_pre / target_pre / bytes / host_rss_mb / peak_mb.
#
#   usage: ./run_measure_4k.sh <video.mp4> [repeats]
#   e.g.   ./run_measure_4k.sh /mnt/ext4/testd_15s.mp4 3
#
# Re-run once per clip length (15s/30s/1min/10min) by pointing at each file
# in turn; each run writes to its own maplogs/4k_<clipname>/ directory so
# nothing gets overwritten.
set -u

VIDEO=${1:?usage: $0 <video.mp4> [repeats]}
REPEATS=${2:-3}

DATA=mot.data
CFG=cfg/yolov7-tiny.cfg
WEIGHTS=yolov7-tiny.weights
TAG=$(basename "$VIDEO" .mp4)
OUT=maplogs/4k_${TAG}
TARGET_HOST=${TARGET_HOST:-}
TARGET_LOG=${TARGET_LOG:-/var/log/spdk_tgt.log}

[ -f "$VIDEO" ] || { echo "[ERR] no video: $VIDEO"; exit 1; }
[ -x ./darknet ] || { echo "[ERR] ./darknet not built"; exit 1; }
mkdir -p "$OUT"

# ---- clip info ----------------------------------------------------------
NFRAMES=$(ffprobe -v error -count_frames -select_streams v:0 \
            -show_entries stream=nb_read_frames -of csv=p=0 "$VIDEO" | tr -d '[:space:]')
RES=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=width,height -of csv=p=0 "$VIDEO")
echo "[INFO] ${VIDEO}: ${RES}, ${NFRAMES} frames"
echo "[INFO] performance-only run: no -labels, no mAP computed"

RAW=${OUT}/measure_4k_raw.log
: > "$RAW"

run() {
  local tag=$1 sr=$2; shift 2
  for r in $(seq 1 "$REPEATS"); do
    echo "=== ${tag} sr=${sr} run=${r} ==="
    local mark
    mark=$(date -Is)

    ./darknet detector video_map "$DATA" "$CFG" "$WEIGHTS" \
        -file "$VIDEO" \
        -sample_rate "$sr" -label_base 1 -points 101 -coco_map -dont_show \
        "$@" 2>&1 | tee -a "${OUT}/${tag}_sr${sr}_r${r}.log" \
      | grep -E "^\[MEASURE\]" \
      | sed "s|^|tag=${tag} sr=${sr} run=${r} |" | tee -a "$RAW"

    if [ -n "$TARGET_HOST" ] && [ "$tag" != baseline ]; then
      ssh "$TARGET_HOST" "awk -v since='$mark' '/NDP-MEASURE/' '$TARGET_LOG' | tail -1" \
        | sed "s|^|tag=${tag} sr=${sr} run=${r} |" | tee -a "$RAW"
    fi
    sleep 3
  done
}

# sws kernel (the deployed configuration; no -resize flag needed since sws
# is the default), q85 (v1.3's original operating point for the 4K set).
# NDP without sampling and with 1/5 sampling mirror the original three-way
# Baseline / NDP(w/o sampling) / NDP(w/ sampling) comparison.
run baseline 1
run ndp_q85  1 -ndp -jpeg_q 85 -chroma 420
run ndp_q85  5 -ndp -jpeg_q 85 -chroma 420

echo
echo "raw measurements: $RAW"
echo "If TARGET_HOST was not set, pull the target's [NDP-MEASURE] lines"
echo "for this run manually and tag them the same way as before."