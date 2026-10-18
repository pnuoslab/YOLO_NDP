#!/usr/bin/env bash
# Latency / transfer / memory measurement across the payload-format conditions.
#
#   usage: ./run_measure.sh <seq-dir> <video> [repeats]
#   e.g.   ./run_measure.sh MOT17-04-DPM /mnt/ext4/MOT17_04.mp4 3
#
# Conditions: baseline, NDP q85, NDP q100, NDP raw RGB24 - each at
# sample_rate 1 and 5. Overlay images are never written: they add disk I/O
# inside the timed loop and would contaminate every latency number.
#
# The host [MEASURE] line is captured here. The target's [NDP-MEASURE] line is
# printed by the SPDK process on the storage node, so set TARGET_HOST to have
# this script pull it over ssh; otherwise collect it manually.
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

RAW=${OUT}/measure_raw.log
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

    if [ -n "$TARGET_HOST" ] && [ "$tag" != baseline ]; then
      ssh "$TARGET_HOST" "awk -v since='$mark' '/NDP-MEASURE/' '$TARGET_LOG' | tail -1" \
        | sed "s|^|tag=${tag} sr=${sr} run=${r} |" | tee -a "$RAW"
    fi

    # Let the target's page cache and the NIC settle between runs so the
    # first repeat is not systematically slower than the rest.
    sleep 3
  done
}

for SR in 1 5; do
  run baseline "$SR"
  run ndp_q85  "$SR" -ndp -jpeg_q 85  -chroma 420 -resize darknet
  run ndp_q100 "$SR" -ndp -jpeg_q 100 -chroma 420 -resize darknet
  run ndp_raw  "$SR" -ndp -jpeg_q -1 -resize darknet
done

echo
echo "raw measurements: $RAW"
