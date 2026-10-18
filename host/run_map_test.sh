#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  run_map_test.sh v2 — MOT17 시퀀스 Baseline / NDP mAP 비교
#
#  사용:
#    ./run_map_test.sh MOT17-11-FRCNN            # sample_rate 기본 5
#    ./run_map_test.sh MOT17-09-FRCNN 5
#    SKIP_B=1 ./run_map_test.sh MOT17-02-FRCNN   # B 생략(느림)
#    REENCODE=1 ./run_map_test.sh ...            # mp4 강제 재생성
#
#  4개 조건
#    A  Baseline, 전 프레임        (절대 정확도 기준선)
#    B  NDP,      전 프레임        (A 와 1:1 대응 — 전처리 경로만 다름)
#    C  Baseline, 1/N 샘플         (D 와 1:1 대응)
#    D  NDP,      1/N 샘플         (제안 기법 전체)
#
#  전제: darknet 루트에서 실행, mot17_gt_to_yolo.py 동일 디렉터리,
#        /mnt/ext4 가 NVMe-oF 마운트 지점
# ══════════════════════════════════════════════════════════════════════
set -uo pipefail          # ★ -e 제거: 한 조건이 실패해도 나머지는 계속 진행

SEQ="${1:?usage: $0 <MOT17-XX-FRCNN> [sample_rate]}"
RATE="${2:-5}"

DARKNET=./darknet
CFG=cfg/yolov7-tiny.cfg
WEIGHTS=yolov7-tiny.weights
NVME_DEV=/dev/nvme1n1
NVME_MNT=/mnt/ext4

TAG="${SEQ%-FRCNN}"; TAG="${TAG#MOT17-}"
LABELS="labels/MOT17-${TAG}"
VIDEO="MOT17_${TAG}.mp4"
LOGDIR="maplogs/MOT17-${TAG}"
DATA=mot.data
SUMMARY="$LOGDIR/summary.tsv"

mkdir -p "$LOGDIR"

die () { echo "ERR: $*" >&2; exit 1; }

echo "════════ [0] 사전 점검 ════════"
[[ -f "$SEQ/gt/gt.txt"   ]] || die "$SEQ/gt/gt.txt 없음 (test split 은 GT 가 없음)"
[[ -f "$SEQ/seqinfo.ini" ]] || die "$SEQ/seqinfo.ini 없음"
[[ -f "$WEIGHTS"         ]] || die "$WEIGHTS 없음"
[[ -x "$DARKNET"         ]] || die "$DARKNET 없음/실행권한 없음"
[[ -d "$NVME_MNT"        ]] || die "$NVME_MNT 없음"
[[ -r "$NVME_DEV"        ]] || echo "  경고: $NVME_DEV 읽기 권한 없음 → sudo 로 실행할 것"
findmnt -n -o SOURCE,FSTYPE --target "$NVME_MNT" || true

FPS=$(sed -n 's/^frameRate=//p' "$SEQ/seqinfo.ini" | tr -d '\r')
LEN=$(sed -n 's/^seqLength=//p' "$SEQ/seqinfo.ini" | tr -d '\r')
IW=$( sed -n 's/^imWidth=//p'   "$SEQ/seqinfo.ini" | tr -d '\r')
IH=$( sed -n 's/^imHeight=//p'  "$SEQ/seqinfo.ini" | tr -d '\r')
echo "  seq=$SEQ  ${IW}x${IH}  ${LEN} frames @ ${FPS}fps  sample_rate=1/${RATE}"

if [[ ! -f "$DATA" ]]; then
  printf 'classes = 80\nnames = data/coco.names\n' > "$DATA"
  echo "  생성: $DATA"
fi

echo "════════ [1] GT → YOLO 라벨 ════════"
python3 mot17_gt_to_yolo.py --seq "$SEQ" --out "$LABELS" --emit-empty \
  2>&1 | tee "$LOGDIR/01_labels.log"
NLAB=$(ls "$LABELS" | wc -l)
echo "  라벨 파일 수: $NLAB  (기대: $LEN)"
[[ "$NLAB" == "$LEN" ]] || die "라벨 파일 수 불일치"

echo "════════ [2] H.264 인코딩 ════════"
if [[ -f "$VIDEO" && "${REENCODE:-0}" != "1" ]]; then
  echo "  이미 존재: $VIDEO (재사용)  — 재생성하려면 REENCODE=1"
else
  # MOT17 원본은 JPEG(yuvj420p, full-range)이므로 swscaler 가
  # 'deprecated pixel format' 경고를 냄. 색범위 태그를 명시해 억제한다.
  # (limited-range 변환은 A/B/C/D 모두에 동일 적용되므로 비교에 영향 없음)
  ffmpeg -hide_banner -loglevel error -y \
         -framerate "$FPS" -i "$SEQ/img1/%06d.jpg" \
         -c:v libx264 -crf 18 -pix_fmt yuv420p \
         -color_range tv -colorspace bt709 \
         -color_primaries bt709 -color_trc bt709 \
         -fps_mode passthrough -an "$VIDEO" || die "ffmpeg 실패"
fi

echo "════════ [3] 프레임 수 검증 ════════"
NB=$(ffprobe -v error -count_frames -select_streams v:0 \
     -show_entries stream=nb_read_frames -of csv=p=0 "$VIDEO" | tr -d ',\r')
echo "  ffprobe nb_read_frames = $NB   (seqLength = $LEN)"
[[ "$NB" == "$LEN" ]] || die "프레임 수 불일치 → GT 정렬이 깨짐"
echo "  NDP 기대 샘플 수: 1/1=$NB, 1/$RATE=$(( (NB - 1) / RATE + 1 ))"

echo "════════ [4] NVMe-oF 볼륨으로 복사 ════════"
cp -f "$VIDEO" "$NVME_MNT/" || die "복사 실패"
sync; sleep 1
NVME_VIDEO="$NVME_MNT/$VIDEO"
echo "  $NVME_VIDEO"
filefrag "$NVME_VIDEO" 2>/dev/null || true

# ══════════════════════════════════════════════════════════════════════
run_case () {   # $1=라벨  $2...=darknet 인자
  local name="$1"; shift
  local log="$LOGDIR/${name}.log"
  echo
  echo "════════ $name ════════"
  # shellcheck disable=SC2068
  $DARKNET detector video_map "$DATA" "$CFG" "$WEIGHTS" $@ \
      -thresh 0.25 -points 101 -iou_thresh 0.5 -coco_map -dont_show \
      > "$log" 2>&1
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "  !! 실패 (rc=$rc). 로그 끝부분:"
    tail -20 "$log" | sed 's/^/     /'
    return 1
  fi
  grep -E "VERIFY|mAP@|eval frames|after class filtering|name = person|precision =" \
       "$log" | sed 's/^/  /'
  return 0
}

run_case "A_baseline_full" \
    -file "$VIDEO" -labels "$LABELS" -sample_rate 1

if [[ "${SKIP_B:-0}" != "1" ]]; then
  # 전 프레임 NDP. 결과 버퍼가 커지고 전처리도 N배 걸린다.
  run_case "B_ndp_full" \
      -ndp -dev "$NVME_DEV" -file "$NVME_VIDEO" -labels "$LABELS" \
      -sample_rate 1
else
  echo; echo "  (B 생략: SKIP_B=1)"
fi

run_case "C_baseline_sampled" \
    -file "$VIDEO" -labels "$LABELS" -sample_rate "$RATE"

run_case "D_ndp_sampled" \
    -ndp -dev "$NVME_DEV" -file "$NVME_VIDEO" -labels "$LABELS" \
    -sample_rate "$RATE"

# ══════════════════════════════════════════════════════════════════════
echo
echo "════════ 요약 ════════"
{
  printf "case\tframes\tGT\tdets\tAP50\tAP50_95\tTP\tFP\tprec\trecall\tavgIoU\n"
  for name in A_baseline_full B_ndp_full C_baseline_sampled D_ndp_sampled; do
    f="$LOGDIR/${name}.log"
    [[ -f "$f" ]] || continue
    frames=$(grep -oP 'eval frames = \K[0-9]+'         "$f" | tail -1)
    gt=$(    grep -oP 'unique GT boxes = \K[0-9]+'     "$f" | tail -1)
    dets=$(  grep -oP 'detections [0-9]+ -> \K[0-9]+'  "$f" | tail -1)
    ap50=$(  grep -oP 'mAP@0\.50 = \K[0-9.]+'          "$f" | tail -1)
    ap5095=$(grep -oP 'mAP@\[\.50:\.95\] = \K[0-9.]+'  "$f" | tail -1)
    tp=$(    grep -oP 'TP = \K[0-9]+'                  "$f" | tail -1)
    fp=$(    grep -oP 'FP = \K[0-9]+'                  "$f" | tail -1)
    prec=$(  grep -oP 'precision = \K[0-9.]+'          "$f" | tail -1)
    rec=$(   grep -oP 'recall = \K[0-9.]+'             "$f" | tail -1)
    iou=$(   grep -oP 'average IoU = \K[0-9.]+'        "$f" | tail -1)
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
           "$name" "${frames:--}" "${gt:--}" "${dets:--}" "${ap50:--}" \
           "${ap5095:--}" "${tp:--}" "${fp:--}" "${prec:--}" "${rec:--}" "${iou:--}"
  done
} | tee "$SUMMARY" | column -t -s $'\t'

echo
echo "── 정합성 체크 ──"
getgt () { grep -oP 'unique GT boxes = \K[0-9]+' "$1" 2>/dev/null | tail -1; }
gtA=$(getgt "$LOGDIR/A_baseline_full.log")
gtB=$(getgt "$LOGDIR/B_ndp_full.log")
gtC=$(getgt "$LOGDIR/C_baseline_sampled.log")
gtD=$(getgt "$LOGDIR/D_ndp_sampled.log")
if [[ -n "$gtA" && "$gtA" == "$gtB" ]]; then
  echo "  OK  A/B 동일 GT ($gtA)  → 전 프레임 쌍 비교 유효"
else
  echo "  !!  A/B GT 불일치: A=${gtA:--} B=${gtB:--}"
fi
if [[ -n "$gtC" && "$gtC" == "$gtD" ]]; then
  echo "  OK  C/D 동일 GT ($gtC)  → 샘플 프레임 쌍 비교 유효"
else
  echo "  !!  C/D GT 불일치: C=${gtC:--} D=${gtD:--}"
fi

echo
echo "로그: $LOGDIR/    요약: $SUMMARY"