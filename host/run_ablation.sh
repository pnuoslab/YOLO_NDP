#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  run_ablation.sh — NDP 전처리 경로의 정확도 차이 원인 분해
#
#  사용:  sudo ./run_ablation.sh MOT17-11-FRCNN [sample_rate]
#
#  전제:  run_map_test.sh 를 먼저 돌려 mp4/labels 가 준비되어 있을 것
#         (C_baseline_sampled 결과가 비교 기준이 됨)
#
#  조건
#    D0  scaler=BILINEAR(기본)  jpeg_q=85   ← 기존 제안 기법
#    D1  scaler=POINT           jpeg_q=85   ← 리사이즈 커널 기여 분리
#    D2  scaler=BILINEAR        jpeg_q=100  ← JPEG 손실 기여 분리
#    D3  scaler=POINT           jpeg_q=100  ← 둘 다 제거 (baseline 근사)
# ══════════════════════════════════════════════════════════════════════
set -uo pipefail

SEQ="${1:?usage: $0 <MOT17-XX-FRCNN> [sample_rate]}"
RATE="${2:-5}"

DARKNET=./darknet
CFG=cfg/yolov7-tiny.cfg
WEIGHTS=yolov7-tiny.weights
NVME_DEV=/dev/nvme1n1
NVME_MNT=/mnt/ext4
DATA=mot.data

TAG="${SEQ%-FRCNN}"; TAG="${TAG#MOT17-}"
LABELS="labels/MOT17-${TAG}"
VIDEO="MOT17_${TAG}.mp4"
NVME_VIDEO="$NVME_MNT/$VIDEO"
LOGDIR="maplogs/MOT17-${TAG}/ablation"
SUMMARY="$LOGDIR/summary.tsv"

mkdir -p "$LOGDIR"
[[ -f "$NVME_VIDEO" ]] || { echo "ERR: $NVME_VIDEO 없음. run_map_test.sh 먼저 실행"; exit 1; }
[[ -d "$LABELS"     ]] || { echo "ERR: $LABELS 없음"; exit 1; }

run_ab () {   # $1=이름 $2=scaler $3=jpeg_q
  local name="$1" sc="$2" jq="$3"
  local log="$LOGDIR/${name}.log"
  echo
  echo "════════ $name  (scaler=$sc, jpeg_q=$jq) ════════"
  $DARKNET detector video_map "$DATA" "$CFG" "$WEIGHTS" \
      -ndp -dev "$NVME_DEV" -file "$NVME_VIDEO" -labels "$LABELS" \
      -sample_rate "$RATE" -scaler "$sc" -jpeg_q "$jq" \
      -thresh 0.25 -points 101 -iou_thresh 0.5 -coco_map -dont_show \
      > "$log" 2>&1
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "  !! 실패 (rc=$rc)"; tail -15 "$log" | sed 's/^/     /'; return 1
  fi
  grep -E "VERIFY|mAP@|after class filtering|name = person|precision =" "$log" \
    | sed 's/^/  /'
}

run_ab "D0_bilinear_q85"  0 0      # 기본값 (0 → BILINEAR / 85)
run_ab "D1_point_q85"     1 85
run_ab "D2_bilinear_q100" 0 100
run_ab "D3_point_q100"    1 100

echo
echo "════════ 요약 ════════"
{
  printf "case\tdets\tAP50\tAP50_95\tTP\tFP\tprec\trecall\tavgIoU\n"
  # 비교 기준으로 C(baseline, 동일 샘플 프레임)를 첫 줄에 넣는다
  for pair in "C_baseline_sampled:../C_baseline_sampled.log" \
              "D0_bilinear_q85:D0_bilinear_q85.log" \
              "D1_point_q85:D1_point_q85.log" \
              "D2_bilinear_q100:D2_bilinear_q100.log" \
              "D3_point_q100:D3_point_q100.log"; do
    name="${pair%%:*}"; f="$LOGDIR/${pair#*:}"
    [[ -f "$f" ]] || continue
    dets=$(  grep -oP 'detections [0-9]+ -> \K[0-9]+' "$f" | tail -1)
    ap50=$(  grep -oP 'mAP@0\.50 = \K[0-9.]+'         "$f" | tail -1)
    ap9=$(   grep -oP 'mAP@\[\.50:\.95\] = \K[0-9.]+' "$f" | tail -1)
    tp=$(    grep -oP 'TP = \K[0-9]+'                 "$f" | tail -1)
    fp=$(    grep -oP 'FP = \K[0-9]+'                 "$f" | tail -1)
    prec=$(  grep -oP 'precision = \K[0-9.]+'         "$f" | tail -1)
    rec=$(   grep -oP 'recall = \K[0-9.]+'            "$f" | tail -1)
    iou=$(   grep -oP 'average IoU = \K[0-9.]+'       "$f" | tail -1)
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "$name" "${dets:--}" "${ap50:--}" "${ap9:--}" "${tp:--}" \
      "${fp:--}" "${prec:--}" "${rec:--}" "${iou:--}"
  done
} | tee "$SUMMARY" | column -t -s $'\t'

echo
echo "해석 가이드"
echo "  D1 이 C 쪽으로 이동 → 검출 수 감소와 IoU 상승의 원인은 리사이즈 커널"
echo "  D2 가 D0 과 거의 동일 → JPEG q85 의 정확도 기여는 무시 가능"
echo "  D3 가 C 에 가장 근접   → 두 요인으로 차이 전부가 설명됨"
echo
echo "로그: $LOGDIR/    요약: $SUMMARY"
