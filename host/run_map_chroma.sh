#!/usr/bin/env bash
# Four-way accuracy comparison for one sequence:
#   baseline / NDP q100 4:2:0 / NDP q100 4:4:4 / NDP raw RGB24
#
# Each run's stdout+stderr, per-detection CSV and (optionally) overlay frames
# land under maplogs/<seq>/<cond>/, so a condition can be re-examined later
# without having to guess which options produced it.
#
#   usage: ./run_map_chroma.sh <seq-dir> <video> [sample_rate] [conditions...]
#   e.g.   ./run_map_chroma.sh MOT17-04-DPM /mnt/ext4/MOT17_04-DPM.mp4 1
#          ./run_map_chroma.sh MOT17-04-DPM /mnt/ext4/MOT17_04-DPM.mp4 5 raw
#
#   NO_IMG=1   skip -save_img (four conditions at sample_rate 1 write
#              several thousand overlay JPEGs)
set -u

SEQ=${1:?usage: $0 <seq-dir> <video> [sample_rate] [conditions...]}
VIDEO=${2:?}
SR=${3:-1}
shift 3 2>/dev/null || shift $#
CONDS=("$@")
[ ${#CONDS[@]} -eq 0 ] && CONDS=(base q100_420 q100_444 raw)

DATA=mot.data
CFG=cfg/yolov7-tiny.cfg
WEIGHTS=yolov7-tiny.weights
LABELS=labels/${SEQ}
ROOT=maplogs/${SEQ}
NO_IMG=${NO_IMG:-0}

[ -d "$LABELS" ] || { echo "[ERR] no labels: $LABELS"; exit 1; }
[ -f "$VIDEO"  ] || { echo "[ERR] no video: $VIDEO";  exit 1; }
[ -x ./darknet ] || { echo "[ERR] ./darknet not built"; exit 1; }

VIDINFO=$(ffprobe -v error -count_frames -select_streams v:0 \
            -show_entries stream=width,height,nb_read_frames \
            -of csv=p=0 "$VIDEO")
NFRAMES=${VIDINFO##*,}
echo "[INFO] ${SEQ}  video=${VIDINFO}  sample_rate=1/${SR}"

run() {
  local cond=$1; shift
  local out=${ROOT}/${cond}
  mkdir -p "$out"

  local imgopt=()
  if [ "$NO_IMG" = "0" ]; then mkdir -p "${out}/img"; imgopt=(-save_img "${out}/img"); fi

  echo
  echo "=== ${cond} ==="
  {
    echo "# cmd: ./darknet detector video_map $DATA $CFG $WEIGHTS -file $VIDEO -labels $LABELS -sample_rate $SR $*"
    echo "# host: $(hostname)  date: $(date -Is)"
    echo "# video: ${VIDINFO}"
    echo
  } > "${out}/run.log"

  ./darknet detector video_map "$DATA" "$CFG" "$WEIGHTS" \
      -file "$VIDEO" -labels "$LABELS" \
      -sample_rate "$SR" -label_base 1 -points 101 -coco_map -dont_show \
      "${imgopt[@]}" -save_log "${out}/det.csv" \
      "$@" 2>&1 | tee -a "${out}/run.log"

  # Lines that decide whether the run is comparable at all, plus the results.
  grep -E "\[MAP\]\[VERIFY\]|\[NDP\]|mAP@|average IoU|conf_thresh|unique GT" \
       "${out}/run.log" > "${out}/summary.txt" 2>/dev/null || true
}

for c in "${CONDS[@]}"; do
  case "$c" in
    base|baseline) run "baseline_sr${SR}" ;;
    q100_420|420)  run "ndp_q100_420_sr${SR}" -ndp -jpeg_q 100 -chroma 420 ;;
    q100_444|444)  run "ndp_q100_444_sr${SR}" -ndp -jpeg_q 100 -chroma 444 ;;
    raw)
      # Raw ships 519168 B per frame and the target holds both the per-frame
      # buffers and the assembled output at once, so peak target memory is
      # roughly twice the transfer size. Warn rather than fail: the operator
      # knows the hugepage budget on the storage node, this script does not.
      if [ "$NFRAMES" -gt 0 ] 2>/dev/null; then
        MB=$(( NFRAMES / SR * 519168 / 1048576 ))
        echo "[WARN] raw mode: ~${MB} MB transfer, ~$((MB*2)) MB peak on target"
      fi
      run "ndp_raw_sr${SR}" -ndp -jpeg_q -1 ;;
    *) echo "[ERR] unknown condition: $c"; exit 1 ;;
  esac
done

echo
echo "=== summary ==="
printf "%-28s %8s %10s %6s %6s %8s\n" cond AP50 "mAP50:95" prec recall "avgIoU"
for d in ${ROOT}/*_sr${SR}; do
  [ -f "${d}/summary.txt" ] || continue
  ap=$(grep -oP 'mAP@0\.50 = \K[0-9.]+'      "${d}/summary.txt" | tail -1)
  mp=$(grep -oP 'mAP@\[\.50:\.95\] = \K[0-9.]+' "${d}/summary.txt" | tail -1)
  pr=$(grep -oP 'precision = \K[0-9.]+'      "${d}/summary.txt" | tail -1)
  rc=$(grep -oP 'recall = \K[0-9.]+'         "${d}/summary.txt" | tail -1)
  iu=$(grep -oP 'average IoU = \K[0-9.]+'    "${d}/summary.txt" | tail -1)
  printf "%-28s %8s %10s %6s %6s %8s\n" "$(basename "$d")" \
         "${ap:--}" "${mp:--}" "${pr:--}" "${rc:--}" "${iu:--}"
done
