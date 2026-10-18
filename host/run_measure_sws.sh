#!/usr/bin/env bash
# Same three payload-format conditions as run_measure.sh, but with the
# swscale one-step kernel (colour conversion + downscale in a single
# sws_scale() call) instead of the darknet-matched kernel. Baseline is not
# re-run here: baseline never touches ndp_frame_to_letterbox_rgb(), so the
# resize kernel choice cannot affect it - its numbers from run_measure.sh
# already cover this comparison.
#
#   usage: ./run_measure_sws.sh <seq-dir> <video> [repeats]
#   e.g.   ./run_measure_sws.sh MOT17-04-DPM /mnt/ext4/MOT17_04.mp4 3
set -u

SEQ=${1:?usage: $0 <seq-dir> <video> [repeats]}
VIDEO=${2:?}
REPEATS=${3:-3}

DATA=mot.data
CFG=cfg/yolov7-tiny.cfg
WEIGHTS=yolov7-tiny.weights
LABELS=labels/${SEQ}
OUT=maplogs/${SEQ}/measure
TARGET_HOST=${TARGET_HOST:-}
TARGET_LOG=${TARGET_LOG:-/var/log/spdk_tgt.log}

[ -d "$LABELS" ] || { echo "[ERR] no labels: $LABELS"; exit 1; }
[ -f "$VIDEO" ]  || { echo "[ERR] no video: $VIDEO";  exit 1; }
mkdir -p "$OUT"

RAW=${OUT}/measure_raw_sws.log
: > "$RAW"

run() {
  local tag=$1 sr=$2; shift 2
  for r in $(seq 1 "$REPEATS"); do
    echo "=== ${tag} sr=${sr} run=${r} ==="
    local mark
    mark=$(date -Is)

    ./darknet detector video_map "$DATA" "$CFG" "$WEIGHTS" \
        -file "$VIDEO" -labels "$LABELS" \
        -sample_rate "$sr" -label_base 1 -points 101 -coco_map -dont_show \
        "$@" 2>&1 | tee -a "${OUT}/${tag}_sr${sr}_r${r}.log" \
      | grep -E "^\[MEASURE\]" \
      | sed "s|^|tag=${tag} sr=${sr} run=${r} |" | tee -a "$RAW"

    if [ -n "$TARGET_HOST" ]; then
      ssh "$TARGET_HOST" "awk -v since='$mark' '/NDP-MEASURE/' '$TARGET_LOG' | tail -1" \
        | sed "s|^|tag=${tag} sr=${sr} run=${r} |" | tee -a "$RAW"
    fi
    sleep 3
  done
}

for SR in 1 5; do
  # No -resize flag: sws is the target's default kernel.
  run ndp_sws_q85  "$SR" -ndp -jpeg_q 85  -chroma 420
  run ndp_sws_q100 "$SR" -ndp -jpeg_q 100 -chroma 420
  run ndp_sws_raw  "$SR" -ndp -jpeg_q -1
done

echo
echo "raw measurements: $RAW"
echo "If TARGET_HOST was not set, pull the target's [NDP-MEASURE] lines"
echo "for this run manually and tag them ndp_sws_q85/ndp_sws_q100/ndp_sws_raw"
echo "the same way the earlier darknet-kernel lines were tagged."
