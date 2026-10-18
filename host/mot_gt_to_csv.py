#!/usr/bin/env python3
"""
MOT gt/gt.txt -> per-person ground-truth CSV (track IDs preserved)

This is the companion to mot17_gt_to_yolo.py. That script produces the
per-frame YOLO label files darknet needs for its own mAP pass; this one
produces a single CSV for the offline analysis, where the track ID matters
(per-person recall, and relative accuracy against the Baseline run).

Output: <out>  with header
    frame_idx,track_id,x,y,w,h,visibility,considered

  frame_idx   0-based video frame number. MOT gt.txt counts frames from 1,
              so this is (gt frame - 1). That makes it the SAME basis as
              the frame_idx written by darknet's -dump_dets, which is
              image_index * sample_rate and also 0-based. Getting this
              wrong shifts every box by one frame with no other symptom.
  track_id    MOT identity, carried through unchanged.
  x,y,w,h     source-resolution pixels, top-left corner and size, exactly
              as gt.txt stores them. NOT clipped to the frame: MOT boxes
              may extend past the edge, and -dump_dets does not clip
              either (darknet's correct_yolo_boxes leaves boxes
              unclipped), so leaving both sides unclipped keeps the two
              files comparable. Clip in the analysis if needed.
  visibility  gt.txt column 9, recorded as-is.
  considered  gt.txt column 7, recorded as-is.

Only MOT class 1 (pedestrian) rows are kept, matching the YOLO labels.
visibility and considered are recorded but NOT filtered: the analysis
applies the same visibility >= 0.25 threshold the YOLO labels use, and
keeping the raw values lets that choice be revisited without regenerating.

usage:
  python3 mot_gt_to_csv.py --seq ~/ndp_dataset/MOT17/train/MOT17-04-DPM \\
                           --out gt_MOT17-04.csv
"""
import argparse
import configparser
import os
import sys
from collections import defaultdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", required=True, help="MOT sequence directory")
    ap.add_argument("--out", required=True, help="output CSV path")
    ap.add_argument("--person-classes", default="1",
                    help="MOT class ids to keep (comma separated). "
                         "Default 1 (pedestrian), same as the YOLO labels.")
    args = ap.parse_args()

    gt_path = os.path.join(args.seq, "gt", "gt.txt")
    ini_path = os.path.join(args.seq, "seqinfo.ini")
    if not os.path.isfile(gt_path):
        sys.exit(f"[ERR] not found: {gt_path}  (the test split has no GT)")
    if not os.path.isfile(ini_path):
        sys.exit(f"[ERR] not found: {ini_path}")

    cp = configparser.ConfigParser()
    cp.read(ini_path)
    s = cp["Sequence"]
    W, H = int(s["imWidth"]), int(s["imHeight"])
    n_frames = int(s["seqLength"])
    fps = float(s["frameRate"])

    keep = {int(x) for x in args.person_classes.split(",")}
    print(f"[INFO] {os.path.basename(args.seq)}  {W}x{H}  "
          f"{n_frames} frames @ {fps:g}fps")

    rows = []
    n_total = 0
    tracks = set()
    per_frame = defaultdict(int)
    # A box reaching outside the frame is normal in MOT and is kept; it is
    # counted only so the report can say how often it happens.
    outside = 0
    vis_ge_025 = 0

    with open(gt_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            p = line.split(",")
            if len(p) < 9:
                sys.exit("[ERR] gt.txt has fewer than 9 columns - is this "
                         "det.txt rather than gt.txt?")
            frame = int(p[0])
            track_id = int(p[1])
            x, y, w, h = (float(p[2]), float(p[3]), float(p[4]), float(p[5]))
            considered = int(p[6])
            cls = int(p[7])
            vis = float(p[8])
            n_total += 1

            if cls not in keep:
                continue

            if x < 0 or y < 0 or x + w > W or y + h > H:
                outside += 1
            if vis >= 0.25 and considered == 1:
                vis_ge_025 += 1

            rows.append((frame - 1, track_id, x, y, w, h, vis, considered))
            tracks.add(track_id)
            per_frame[frame - 1] += 1

    rows.sort(key=lambda r: (r[0], r[1]))

    with open(args.out, "w") as f:
        f.write("frame_idx,track_id,x,y,w,h,visibility,considered\n")
        for (fi, tid, x, y, w, h, vis, con) in rows:
            f.write(f"{fi},{tid},{x:.2f},{y:.2f},{w:.2f},{h:.2f},"
                    f"{vis:.6f},{con}\n")

    fi_min = min(per_frame) if per_frame else -1
    fi_max = max(per_frame) if per_frame else -1
    print(f"[OK] {len(rows)}/{n_total} rows kept (MOT class "
          f"{sorted(keep)}), {len(tracks)} unique track IDs")
    print(f"     frame_idx {fi_min}..{fi_max} over {len(per_frame)} frames "
          f"(seqLength {n_frames})")
    print(f"     {vis_ge_025} rows pass visibility>=0.25 and considered==1 "
          f"(the YOLO-label condition); {outside} boxes reach outside the "
          f"frame and were kept unclipped")
    print(f"     written: {args.out}")

    if fi_max >= n_frames:
        print(f"[WARN] frame_idx {fi_max} >= seqLength {n_frames}: the "
              f"0-based conversion may be wrong for this sequence")


if __name__ == "__main__":
    main()
