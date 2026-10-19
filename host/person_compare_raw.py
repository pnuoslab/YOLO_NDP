#!/usr/bin/env python3
"""
Baseline vs the structure-only NDP (darknet kernel, raw RGB24, no JPEG).

darknet-raw is the NDP configuration that performs the same pixel work as
the Baseline - decode, darknet-compatible letterbox resize, no lossy
compression - only on the storage node instead of the host. Comparing it
with BASE isolates what moving the work changes, with the resize kernel
and JPEG taken out of the picture.

Reuses person_analysis.py's definitions (spec 03a section 3) unchanged:
valid GT, conf >= 0.25 / IoU >= 0.5 greedy matching, sampling picked from
the sr=1 dump (validated identical to real sampled dumps), reuse of the
last processed frame's detections.

    usage: python3 person_compare_raw.py   -> ~/ndp_analysis/person/raw_vs_base/
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import person_analysis as pa  # noqa: E402

OUT = os.path.expanduser("~/ndp_analysis/person/raw_vs_base")
CONDS = {"BASE": ("BASE", "sws"), "darknet-raw": ("raw", "darknet")}


def main():
    os.makedirs(OUT, exist_ok=True)
    tracks, frames, boxes = [], [], []
    for seq in pa.SEQS:
        n, fps = pa.seq_info(seq)
        gt = pa.load_gt(seq)
        meta = gt.groupby("track_id").agg(app=("frame_idx", "size"), hmed=("hpx", "median"))
        hits = {}
        for name, (pipe, kern) in CONDS.items():
            dets = pa.frame_dets(pa.load_dump(pa.dump_path(seq, pipe, kern, 1)), pa.CONF)
            for N in pa.NS:
                hits[(name, N)] = pa.match_all(gt, dets, n, N)
        bh = hits[("BASE", 1)][0]
        base_first = gt[bh].groupby("track_id").frame_idx.min()
        base_hits = gt[bh].groupby("track_id").size()
        for name in CONDS:
            for N in pa.NS:
                h, proc, box, ndet, nmat = hits[(name, N)]
                g = gt.assign(hit=h, proc=proc)
                gp = g[g.proc]
                app_p = gp.groupby("track_id").size()
                hit_p = gp[gp.hit].groupby("track_id").size()
                first_p = gp[gp.hit].groupby("track_id").frame_idx.min()
                hit_all = g[g.hit].groupby("track_id").size()
                for tid, m in meta.iterrows():
                    ap, hp = int(app_p.get(tid, 0)), int(hit_p.get(tid, 0))
                    fh, bf = first_p.get(tid, np.nan), base_first.get(tid, np.nan)
                    tracks.append(dict(
                        seq=seq, cond=name, N=N, track_id=int(tid),
                        status="structural" if ap == 0 else ("detection_miss" if hp == 0 else "caught"),
                        base_caught=tid in base_first.index, extra_missed=(tid in base_first.index and hp == 0),
                        base_hits_sr1=int(base_hits.get(tid, 0)), app_frames=int(m.app), h_median_px=m.hmed,
                        cov_all_reuse=int(hit_all.get(tid, 0)) / m.app,
                        delay_s=((fh - bf) / fps) if (fh == fh and bf == bf) else np.nan))
                procf = np.arange(n) % N == 0
                frames.append(dict(seq=seq, cond=name, N=N,
                                   gt_p=int(proc.sum()), hit_p=int((h & proc).sum()),
                                   det_p=int(ndet[procf].sum()), match_p=int(nmat[procf].sum()),
                                   gt_all=len(gt), hit_all=int(h.sum())))
        # per GT box agreement at N=1
        ch, _, cbox, _, _ = hits[("darknet-raw", 1)]
        _, _, bbox, _, _ = hits[("BASE", 1)]
        both = bh & ch
        a, b = bbox[both], cbox[both]
        ix = np.clip(np.minimum(a[:, 2], b[:, 2]) - np.maximum(a[:, 0], b[:, 0]), 0, None)
        iy = np.clip(np.minimum(a[:, 3], b[:, 3]) - np.maximum(a[:, 1], b[:, 1]), 0, None)
        inter = ix * iy
        union = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]) + (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) - inter
        iou = inter / union
        # centre shift of the matched boxes, in pixels: is there a systematic offset?
        dcx = ((b[:, 0] + b[:, 2]) - (a[:, 0] + a[:, 2])) / 2
        dcy = ((b[:, 1] + b[:, 3]) - (a[:, 1] + a[:, 3])) / 2
        boxes.append(dict(seq=seq, gt=len(gt), both=int(both.sum()), base_only=int((bh & ~ch).sum()),
                          raw_only=int((~bh & ch).sum()), neither=int((~bh & ~ch).sum()),
                          iou_median=float(np.median(iou)), iou_p10=float(np.quantile(iou, 0.1)),
                          identical_boxes=int((iou > 0.9999).sum()),
                          dcx_mean=float(dcx.mean()), dcy_mean=float(dcy.mean())))
        print(seq, "done", flush=True)

    tr = pd.DataFrame(tracks)
    fr = pd.DataFrame(frames)
    bx = pd.DataFrame(boxes)
    tr.to_csv(os.path.join(OUT, "tracks.csv"), index=False)
    fr.to_csv(os.path.join(OUT, "frames.csv"), index=False)
    bx.to_csv(os.path.join(OUT, "boxes.csv"), index=False)

    print("\n== track status (all sequences) ==")
    for name in CONDS:
        for N in pa.NS:
            d = tr[(tr.cond == name) & (tr.N == N)]
            c = d.status.value_counts()
            ex = d[d.extra_missed]
            print(f"{name:11s} N={N}: tracks {len(d)} structural {c.get('structural', 0)} "
                  f"detmiss {c.get('detection_miss', 0)} caught {c.get('caught', 0)} | "
                  f"extra missed {len(ex)}/{int(d.base_caught.sum())} "
                  f"(BASE sr1 hits of those: median {ex.base_hits_sr1.median() if len(ex) else 0:.0f}) | "
                  f"delay>1s {int((d.delay_s > 1).sum())}, p90 {d.delay_s.quantile(0.9):.2f}s | "
                  f"MT {(d.cov_all_reuse >= 0.8).mean() * 100:.1f}% ML {(d.cov_all_reuse < 0.2).mean() * 100:.1f}%")
    print("\n== frame recall / precision (processed frames, all sequences) ==")
    for name in CONDS:
        for N in pa.NS:
            s = fr[(fr.cond == name) & (fr.N == N)].sum(numeric_only=True)
            print(f"{name:11s} N={N}: recall {100 * s.hit_p / s.gt_p:.2f}%  precision {100 * s.match_p / s.det_p:.2f}%  "
                  f"recall all-frames(reuse) {100 * s.hit_all / s.gt_all:.2f}%")
    print("\n== per GT box, BASE vs darknet-raw at N=1 ==")
    print(bx.to_string(index=False))
    t = bx.sum(numeric_only=True)
    print(f"ALL: gt {int(t["gt"])} both {int(t.both)} base_only {int(t.base_only)} ({100 * t.base_only / t["gt"]:.2f}%) "
          f"raw_only {int(t.raw_only)} ({100 * t.raw_only / t["gt"]:.2f}%)  identical boxes {int(t.identical_boxes)}/{int(t.both)}")


if __name__ == "__main__":
    main()
