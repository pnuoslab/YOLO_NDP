#!/usr/bin/env python3
"""
MOT17 gt/gt.txt -> darknet YOLO 라벨 (프레임당 1파일)

출력:  <out_dir>/000001.txt, 000002.txt, ...
형식:  <class_id> <cx> <cy> <w> <h>   (원본 해상도 기준 0.0~1.0 정규화)

class_id 는 COCO 80-index 기준 0 (person) 으로 고정한다.
(yolov7-tiny.cfg 가 80 클래스이므로 data/coco.names 를 그대로 쓴다)

사용 예:
  python3 mot17_gt_to_yolo.py --seq ~/darknet-for-ndp/MOT17-14-FRCNN \
                              --out labels/MOT17-14
"""
import argparse, configparser, os, sys
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", required=True,
                    help="MOT17-XX-FRCNN 시퀀스 디렉터리")
    ap.add_argument("--out", required=True, help="라벨 출력 디렉터리")
    ap.add_argument("--person-classes", default="1",
                    help="person 으로 간주할 MOT class id (쉼표 구분). "
                         "기본 1(pedestrian). 1,2,7 로 넓히면 "
                         "person-on-vehicle / static-person 도 포함")
    ap.add_argument("--min-vis", type=float, default=0.25,
                    help="최소 visibility (기본 0.25)")
    ap.add_argument("--emit-empty", action="store_true",
                    help="객체가 없는 프레임도 빈 .txt 로 생성 (권장)")
    ap.add_argument("--width", type=int, default=0,
                    help="정규화에 쓸 폭을 seqinfo.ini 값 대신 지정. "
                         "영상화할 때 패딩이 들어간 경우에 쓴다. darknet 은 "
                         "영상의 실제 폭으로 박스를 정규화하므로, 라벨도 "
                         "같은 폭으로 정규화해야 어긋나지 않는다 "
                         "(예: MOT20-03 은 1173 이 홀수라 yuv420p "
                         "H.264 로 인코딩되지 않아 1174 로 패딩한다)")
    ap.add_argument("--height", type=int, default=0,
                    help="정규화에 쓸 높이를 seqinfo.ini 값 대신 지정")
    args = ap.parse_args()

    gt_path = os.path.join(args.seq, "gt", "gt.txt")
    ini_path = os.path.join(args.seq, "seqinfo.ini")
    if not os.path.isfile(gt_path):
        sys.exit(f"[ERR] not found: {gt_path}  (test split 은 GT 가 없음)")

    # ── 해상도 / 프레임 수 ────────────────────────────────────────────
    if os.path.isfile(ini_path):
        cp = configparser.ConfigParser()
        cp.read(ini_path)
        s = cp["Sequence"]
        W, H = int(s["imWidth"]), int(s["imHeight"])
        n_frames = int(s["seqLength"])
        fps = float(s["frameRate"])
    else:
        sys.exit(f"[ERR] not found: {ini_path}")

    # 클리핑은 항상 원본 프레임 경계(seqinfo 값)로 한다. 패딩으로 늘어난
    # 영역은 검은 띠이고 거기에 객체가 있을 수 없으므로, 클리핑 경계를
    # 늘리면 프레임 밖 박스를 잘못 살려두게 된다.
    CLIP_W, CLIP_H = W, H
    if args.width:
        W = args.width
    if args.height:
        H = args.height

    keep = {int(x) for x in args.person_classes.split(",")}
    print(f"[INFO] {os.path.basename(args.seq)}  clip={CLIP_W}x{CLIP_H}  "
          f"normalise={W}x{H}  {n_frames} frames @ {fps}fps")
    print(f"[INFO] keep MOT classes {sorted(keep)}, visibility >= {args.min_vis}")

    # ── 파싱 ──────────────────────────────────────────────────────────
    per_frame = defaultdict(list)
    n_total = n_kept = 0
    clipped = 0

    with open(gt_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = line.split(",")
            if len(p) < 9:
                sys.exit("[ERR] gt.txt 열이 9개 미만임. det.txt 를 넘긴 것 아닌지 확인")
            frame = int(p[0])
            x, y, w, h = (float(p[2]), float(p[3]), float(p[4]), float(p[5]))
            flag = int(p[6])
            cls = int(p[7])
            vis = float(p[8])
            n_total += 1

            if flag != 1 or cls not in keep or vis < args.min_vis:
                continue

            # MOT 박스는 프레임 밖으로 나갈 수 있으므로 클리핑
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(CLIP_W), x + w), min(float(CLIP_H), y + h)
            if x1 <= x0 or y1 <= y0:
                continue
            if (x0, y0, x1, y1) != (x, y, x + w, y + h):
                clipped += 1

            cx = (x0 + x1) / 2.0 / W
            cy = (y0 + y1) / 2.0 / H
            bw = (x1 - x0) / W
            bh = (y1 - y0) / H
            per_frame[frame].append((cx, cy, bw, bh))
            n_kept += 1

    # ── 출력 ──────────────────────────────────────────────────────────
    os.makedirs(args.out, exist_ok=True)
    written = 0
    rng = range(1, n_frames + 1) if args.emit_empty else sorted(per_frame)
    for fr in rng:
        boxes = per_frame.get(fr, [])
        if not boxes and not args.emit_empty:
            continue
        with open(os.path.join(args.out, f"{fr:06d}.txt"), "w") as f:
            for cx, cy, bw, bh in boxes:
                f.write(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")
        written += 1

    empty = sum(1 for fr in rng if not per_frame.get(fr))
    print(f"[OK] {n_kept}/{n_total} boxes kept ({clipped} clipped), "
          f"{written} files written to {args.out}/ ({empty} empty)")
    print(f"[NEXT] ffmpeg -framerate {fps:g} -i {args.seq}/img1/%06d.jpg "
          f"-c:v libx264 -crf 18 -pix_fmt yuv420p -fps_mode passthrough "
          f"-an MOT17_xx.mp4")


if __name__ == "__main__":
    main()
