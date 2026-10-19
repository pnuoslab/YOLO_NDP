#!/usr/bin/env python3
"""
Person-level accuracy analysis (spec 03a), offline only.

Questions it answers:
  - With frame sampling, are people who appear only between processed
    frames actually missed?
  - Do Baseline and NDP detect the same people?

Inputs (read only):
  ~/ndp_results/main/dumps/E2_* / E3_*   -dump_dets output of the 2026-10-19
                                         main measurement (MOT sequences)
  ~/ndp_analysis/results_full.csv        the C code's own mAP per run
  labels/<seq>/%06d.txt                  YOLO labels the C mAP used
  gt_csv/gt_<seq>.csv                    per-person ground truth (track IDs)
  seqinfo.ini                            frame rate

Definitions follow spec 03a section 3 exactly:
  valid GT    the same filter that produced the YOLO labels the mAP used
              (mot17_gt_to_yolo.py: MOT class 1, considered == 1,
              visibility >= 0.25, box clipped to the frame, non-empty).
              The script checks that this reproduces the label files box
              for box before using it.
  hit         a valid GT box matched, within one frame, by a person
              detection with conf >= 0.25: detections in descending conf,
              each taking the not-yet-matched GT with the largest IoU,
              accepted if IoU >= 0.5 (greedy, 1:1).
  processed   frame_idx % N == 0. Sampling metrics come from the sr=1 dump
              picked at N-frame intervals; section 4-0 checks this against
              the real sr=2 / sr=5 dumps.
  reuse       an unprocessed frame takes the detections of the most recent
              processed frame, coordinates unchanged.

Output goes to ~/ndp_analysis/person/out/. Deterministic: no randomness,
every table sorted.

    usage: python3 person_analysis.py
"""
import configparser
import json
import os
import sys

import numpy as np
import pandas as pd

HOME = os.path.expanduser("~")
REPO = os.path.dirname(os.path.abspath(__file__))
DUMPS = os.path.join(HOME, "ndp_results/main/dumps")
RESULTS = os.path.join(HOME, "ndp_analysis/results_full.csv")
OUT = os.path.join(HOME, "ndp_analysis/person/out")
MOTRAW = os.path.join(HOME, "ndp_dataset/mot_raw")

# Video geometry is what the detector saw; clip geometry is the original
# frame the GT lives in. They differ only for MOT20-03, padded 1173 -> 1174
# at offset 0, so pixel coordinates are identical in both.
SEQS = {
    "MOT17-02": dict(ini="MOT17/train/MOT17-02-DPM", vw=1920, vh=1080, cw=1920, ch=1080),
    "MOT17-04": dict(ini="MOT17/train/MOT17-04-DPM", vw=1920, vh=1080, cw=1920, ch=1080),
    "MOT17-09": dict(ini="MOT17/train/MOT17-09-DPM", vw=1920, vh=1080, cw=1920, ch=1080),
    "MOT20-02": dict(ini="MOT20/train/MOT20-02", vw=1920, vh=1080, cw=1920, ch=1080),
    "MOT20-03": dict(ini="MOT20/train/MOT20-03", vw=1174, vh=880, cw=1173, ch=880),
}
PERSON = 0              # data/coco.names line 1
CONF = 0.25             # spec 3-2
IOU = 0.5               # spec 3-2 (>=)
MAP_FLOOR = 0.005       # detector.c: thresh used for the AP integration
MAP_POINTS = 101        # run_all.sh passes -points 101
SWS = ["BASE", "q85", "q100", "raw"]          # E2, kernel sws (BASE: host)
DARKNET = ["q85", "q100", "raw"]              # E3, kernel darknet
NS = [1, 2, 5]


def log(*a):
    print(*a, flush=True)


# ── inputs ──────────────────────────────────────────────────────────────
def seq_info(seq):
    cp = configparser.ConfigParser()
    cp.read(os.path.join(MOTRAW, SEQS[seq]["ini"], "seqinfo.ini"))
    s = cp["Sequence"]
    return int(s["seqLength"]), float(s["frameRate"])


def dump_path(seq, pipe, kernel, sr):
    exp = "E3" if kernel == "darknet" else "E2"
    return os.path.join(DUMPS, f"{exp}_{seq}_{pipe}_{kernel}_sr{sr}_r1.csv")


def load_dump(path):
    """Rows in file order. The file was written after detector.c's qsort by
    confidence, so its row order IS the C code's rank order (ties included);
    `rank` keeps it. Only the person class is kept."""
    d = pd.read_csv(path)
    d["rank"] = np.arange(len(d))
    d = d[d.class_id == PERSON].copy()
    d["x2"] = d.x + d.w
    d["y2"] = d.y + d.h
    return d


def load_labels(seq, n):
    """YOLO labels as the C code read them (normalised by the VIDEO size),
    turned back into video-pixel x1,y1,x2,y2."""
    g = SEQS[seq]
    out = {}
    for f in range(n):
        p = os.path.join(REPO, "labels", seq, f"{f + 1:06d}.txt")   # label_base 1
        rows = []
        with open(p) as fh:
            for line in fh:
                c, cx, cy, w, h = line.split()
                cx, cy, w, h = float(cx) * g["vw"], float(cy) * g["vh"], float(w) * g["vw"], float(h) * g["vh"]
                rows.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
        out[f] = np.array(rows, dtype=np.float64).reshape(-1, 4)
    return out


def load_gt(seq):
    """Valid GT (spec 3-1) with the same clipping the YOLO labels got."""
    g = SEQS[seq]
    d = pd.read_csv(os.path.join(REPO, "gt_csv", f"gt_{seq}.csv"))
    d = d[(d.considered == 1) & (d.visibility >= 0.25)].copy()
    d["x1"] = d.x.clip(lower=0)
    d["y1"] = d.y.clip(lower=0)
    d["x2"] = (d.x + d.w).clip(upper=g["cw"])
    d["y2"] = (d.y + d.h).clip(upper=g["ch"])
    d = d[(d.x2 > d.x1) & (d.y2 > d.y1)].copy()
    d["hpx"] = d.y2 - d.y1
    d = d.sort_values(["frame_idx", "track_id"]).reset_index(drop=True)
    d["gid"] = np.arange(len(d))
    return d


def iou_matrix(a, b):
    """IoU of every box in a (n,4) against every box in b (m,4), x1y1x2y2."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    ix1 = np.maximum(a[:, None, 0], b[None, :, 0])
    iy1 = np.maximum(a[:, None, 1], b[None, :, 1])
    ix2 = np.minimum(a[:, None, 2], b[None, :, 2])
    iy2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    bb = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = aa[:, None] + bb[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(union > 0, inter / union, 0.0)


# ── 4-0.1 mAP, reimplemented from detector.c vmap_compute_map() ─────────
def c_map(dump, labels, frames, iou_t, binary_grid=True):
    """mAP@iou_t exactly as detector.c computes it for one class:
      - a detection's candidate GT is the same-frame GT with the largest
        IoU (first one on ties: `cur > max_iou` from 0); none if all 0
      - in rank order, it is a TP if that IoU is > iou_t (strict) and the
        GT is not taken yet, else FP
      - AP = mean over 101 recall points of the max precision at
        recall >= point (precision > 0 required, starting from 0)

    binary_grid: the measurement binary is built with -Ofast, whose
    -freciprocal-math turns `point * 1.0 / (map_points - 1)` into
    `point * (1.0 / 100)`. 0.01 is not exact in binary, so some grid points
    come out one ulp above k/100, and at a rank whose recall is exactly
    k/100 the test `recall >= cur_recall` then fails. Established on
    2026-10-19 with a diagnostic build: the same C function gives the
    binary's value under -O2 -freciprocal-math and the exact value under
    -O2, and only the grid division matters (precision, recall and the
    final mean are unaffected). True reproduces the measured C values;
    False gives the mathematically exact AP.
    """
    fs = set(frames)
    d = dump[dump.frame_idx.isin(fs) & (dump.conf >= MAP_FLOOR)].sort_values("rank")
    n_gt = sum(len(labels[f]) for f in frames)
    best_gt = np.full(len(d), -1, dtype=np.int64)
    best_iou = np.zeros(len(d))
    offset, base = {}, 0
    for f in frames:
        offset[f] = base
        base += len(labels[f])
    pos = 0
    idx_of = {}
    for f, grp in d.groupby("frame_idx", sort=False):
        idx_of[f] = grp.index
    order = {ix: i for i, ix in enumerate(d.index)}
    for f, ix in idx_of.items():
        gt = labels[f]
        if len(gt) == 0:
            continue
        rows = d.loc[ix]
        m = iou_matrix(rows[["x", "y", "x2", "y2"]].to_numpy(), gt)
        j = m.argmax(axis=1)
        v = m[np.arange(len(m)), j]
        sel = v > 0
        ii = np.array([order[k] for k in ix])
        best_gt[ii[sel]] = offset[f] + j[sel]
        best_iou[ii[sel]] = v[sel]
    cand = (best_gt > -1) & (best_iou > iou_t)
    tp = np.zeros(len(d), dtype=bool)
    if cand.any():
        cidx = np.nonzero(cand)[0]
        _, first = np.unique(best_gt[cidx], return_index=True)
        tp[cidx[first]] = True
    ctp = np.cumsum(tp)
    cfp = np.cumsum(~tp)
    prec = np.where(ctp + cfp > 0, ctp / np.maximum(ctp + cfp, 1), 0.0)
    rec = ctp / n_gt if n_gt else np.zeros(len(d))
    # suffix max of precision; recall is non-decreasing along rank
    suf = np.maximum.accumulate(prec[::-1])[::-1] if len(prec) else prec
    ap = 0.0
    step = 1.0 / (MAP_POINTS - 1)
    for p in range(MAP_POINTS):
        r = p * 1.0 * step if binary_grid else p * 1.0 / (MAP_POINTS - 1)
        k = np.searchsorted(rec, r, side="left")
        ap += suf[k] if k < len(rec) and suf[k] > 0 else 0.0
    return 100.0 * ap / MAP_POINTS


# ── matching (spec 3-2) ─────────────────────────────────────────────────
def greedy(det_xy, det_conf, gt_xy):
    """Returns (gt_match_det, n_matched): for each GT the index of the
    matched detection or -1. Detections are taken in descending conf."""
    gm = np.full(len(gt_xy), -1, dtype=np.int64)
    if len(det_xy) == 0 or len(gt_xy) == 0:
        return gm, 0
    m = iou_matrix(det_xy, gt_xy)
    nmatch = 0
    for di in np.argsort(-det_conf, kind="stable"):
        row = np.where(gm < 0, m[di], -1.0)
        j = int(row.argmax())
        if row[j] >= IOU:
            gm[j] = di
            nmatch += 1
    return gm, nmatch


def frame_dets(dump, conf):
    d = dump[dump.conf >= conf]
    out = {}
    for f, g in d.groupby("frame_idx"):
        out[f] = (g[["x", "y", "x2", "y2"]].to_numpy(), g.conf.to_numpy())
    return out


def match_all(gt, dets, n_frames, N):
    """Hit table for every valid GT box under sampling N with reuse.
    Columns: gid, hit (bool), processed (bool), det box of the match."""
    empty = (np.zeros((0, 4)), np.zeros(0))
    hit = np.zeros(len(gt), dtype=bool)
    mbox = np.full((len(gt), 4), np.nan)
    ndet_used = np.zeros(n_frames, dtype=np.int64)
    nmatched = np.zeros(n_frames, dtype=np.int64)
    by_f = gt.groupby("frame_idx").indices
    for f in range(n_frames):
        p = f - (f % N)
        dxy, dc = dets.get(p, empty)
        ndet_used[f] = len(dxy)
        gi = by_f.get(f)
        if gi is None:
            continue
        gm, nm = greedy(dxy, dc, gt.loc[gi, ["x1", "y1", "x2", "y2"]].to_numpy())
        nmatched[f] = nm
        ok = gm >= 0
        hit[gi[ok]] = True
        mbox[gi[ok]] = dxy[gm[ok]]
    processed = (gt.frame_idx.to_numpy() % N) == 0
    return hit, processed, mbox, ndet_used, nmatched


# ── main ────────────────────────────────────────────────────────────────
def main():
    os.makedirs(OUT, exist_ok=True)
    res = pd.read_csv(RESULTS)
    report = {"validation": {}, "inputs": {}}

    seq_meta = {s: seq_info(s) for s in SEQS}
    report["inputs"]["seqinfo"] = {s: {"seqLength": n, "fps": fps} for s, (n, fps) in seq_meta.items()}

    # ---- 4-0.1 mAP reproduction (all 75 MOT dumps) ----
    log("4-0.1 mAP reproduction")
    rows = []
    for seq in SEQS:
        n, _ = seq_meta[seq]
        labels = load_labels(seq, n)
        conds = [(p, "sws", sr) for p in SWS for sr in NS] + [(p, "darknet", 1) for p in DARKNET]
        for pipe, kern, sr in conds:
            dump = load_dump(dump_path(seq, pipe, kern, sr))
            frames = list(range(0, n, sr))
            m50 = c_map(dump, labels, frames, np.float32(0.5))
            m50x = c_map(dump, labels, frames, np.float32(0.5), binary_grid=False)
            # detector.c: float t = 0.50f + 0.05f * i;  (float32 arithmetic)
            m95 = np.mean([c_map(dump, labels, frames,
                                 np.float32(0.50) + np.float32(0.05) * np.float32(i)) for i in range(10)])
            exp = "E3" if kern == "darknet" else "E2"
            r = res[(res.exp == exp) & (res.input == seq) & (res.pipeline == pipe)
                    & (res.kernel == kern) & (res.sample_rate == sr) & (res.rep == 1)].iloc[0]
            rows.append(dict(seq=seq, pipeline=pipe, kernel=kern, sr=sr,
                             c_ap50=r.ap50, py_ap50=round(m50, 4), d50=round(m50 - r.ap50, 4),
                             py_ap50_exact=round(m50x, 4), exact_minus_c=round(m50x - r.ap50, 4),
                             c_ap5095=r.ap5095, py_ap5095=round(m95, 4), d5095=round(m95 - r.ap5095, 4)))
            log(f"  {seq} {pipe:4s} {kern:7s} sr{sr}: C {r.ap50:.4f}  py {m50:.4f}  d {m50 - r.ap50:+.4f}")
    mp = pd.DataFrame(rows)
    mp.to_csv(os.path.join(OUT, "summary_map_reproduction.csv"), index=False)
    worst = mp.d50.abs().max()
    report["validation"]["map"] = dict(n=len(mp), max_abs_d50=float(worst),
                                       max_abs_d5095=float(mp.d5095.abs().max()),
                                       passed=bool(worst <= 0.05))
    if worst > 0.05:
        log(f"STOP: mAP reproduction off by {worst:.4f} pp (> 0.05). Not continuing.")
        json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1)
        sys.exit(1)

    # ---- 4-0.2 sampling consistency ----
    log("4-0.2 sampling consistency")
    rows = []
    for seq in SEQS:
        for pipe in SWS:
            d1 = pd.read_csv(dump_path(seq, pipe, "sws", 1), dtype=str)
            for sr in (2, 5):
                ds = pd.read_csv(dump_path(seq, pipe, "sws", sr), dtype=str)
                fr = sorted(set(ds.frame_idx.astype(int)))
                a = d1[d1.frame_idx.astype(int).isin(fr)]
                key = ["frame_idx", "class_id", "conf", "x", "y", "w", "h"]
                ca = a[key].value_counts()
                cb = ds[key].value_counts()
                same_rows = int(np.minimum(ca, cb.reindex(ca.index, fill_value=0)).sum())
                fa = {f: g for f, g in a.groupby(a.frame_idx.astype(int))}
                fb = {f: g for f, g in ds.groupby(ds.frame_idx.astype(int))}
                same_frames = sum(1 for f in fr if f in fa and f in fb
                                  and sorted(map(tuple, fa[f][key].values)) == sorted(map(tuple, fb[f][key].values))
                                  or (f not in fa and f not in fb))
                rows.append(dict(seq=seq, pipeline=pipe, sr=sr, frames=len(fr), identical_frames=same_frames,
                                 rows_sr1=len(a), rows_srN=len(ds), identical_rows=same_rows))
    sc = pd.DataFrame(rows)
    sc.to_csv(os.path.join(OUT, "summary_sampling_consistency.csv"), index=False)
    report["validation"]["sampling"] = dict(
        frames=int(sc.frames.sum()), identical_frames=int(sc.identical_frames.sum()),
        rows=int(sc.rows_srN.sum()), identical_rows=int(sc.identical_rows.sum()))
    log(f"  identical frames {sc.identical_frames.sum()}/{sc.frames.sum()}, rows {sc.identical_rows.sum()}/{sc.rows_srN.sum()}")

    # ---- matching under every condition ----
    log("matching")
    track_rows, frame_rows, agree_rows = [], [], []
    sens = []
    for seq in SEQS:
        n, fps = seq_meta[seq]
        gt = load_gt(seq)
        meta = gt.groupby("track_id").agg(app=("frame_idx", "size"), first=("frame_idx", "min"),
                                          last=("frame_idx", "max"), hmed=("hpx", "median"),
                                          vis=("visibility", "mean"))
        dumps1 = {(p, "sws"): load_dump(dump_path(seq, p, "sws", 1)) for p in SWS}
        dumps1.update({(p, "darknet"): load_dump(dump_path(seq, p, "darknet", 1)) for p in DARKNET})

        hits = {}
        for (pipe, kern), dump in dumps1.items():
            dets = frame_dets(dump, CONF)
            for N in (NS if kern == "sws" else [1]):
                hits[(pipe, kern, N)] = match_all(gt, dets, n, N)
            log(f"  {seq} {pipe} {kern} done")

        # 4-1 .. 4-4 (sws pipelines incl. BASE)
        bh, bp, _, _, _ = hits[("BASE", "sws", 1)]
        base_first = gt[bh].groupby("track_id").frame_idx.min()
        base_caught = set(base_first.index)
        for pipe in SWS:
            for N in NS:
                h, proc, _, ndet, nmat = hits[(pipe, "sws", N)]
                g = gt.assign(hit=h, proc=proc)
                gp = g[g.proc]
                app_p = gp.groupby("track_id").size()
                hit_p = gp[gp.hit].groupby("track_id").size()
                first_p = gp[gp.hit].groupby("track_id").frame_idx.min()
                hit_all = g[g.hit].groupby("track_id").size()
                for tid, mrow in meta.iterrows():
                    ap = int(app_p.get(tid, 0))
                    hp = int(hit_p.get(tid, 0))
                    status = "structural" if ap == 0 else ("detection_miss" if hp == 0 else "caught")
                    fh = first_p.get(tid, np.nan)
                    bf = base_first.get(tid, np.nan)
                    track_rows.append(dict(
                        seq=seq, pipeline=pipe, kernel="sws", N=N, track_id=int(tid), status=status,
                        base_caught=tid in base_caught, extra_missed=(tid in base_caught and hp == 0),
                        app_frames=int(mrow.app), app_seconds=mrow.app / fps,
                        first_frame=int(mrow["first"]), last_frame=int(mrow["last"]),
                        h_median_px=mrow.hmed, vis_mean=mrow.vis,
                        app_processed=ap, hit_processed=hp,
                        cov_processed=(hp / ap) if ap else np.nan,
                        hit_all_reuse=int(hit_all.get(tid, 0)),
                        cov_all_reuse=int(hit_all.get(tid, 0)) / mrow.app,
                        first_hit=fh, base_first_hit=bf,
                        delay_frames=(fh - bf) if (fh == fh and bf == bf) else np.nan,
                        delay_s=((fh - bf) / fps) if (fh == fh and bf == bf) else np.nan))
                procf = np.arange(n) % N == 0
                fr_gt_proc = int(proc.sum())
                frame_rows.append(dict(
                    seq=seq, pipeline=pipe, N=N,
                    gt_processed=fr_gt_proc, hit_processed=int((h & proc).sum()),
                    det_processed=int(ndet[procf].sum()), matched_processed=int(nmat[procf].sum()),
                    gt_all=len(gt), hit_all=int(h.sum()),
                    det_all=int(ndet.sum()), matched_all=int(nmat.sum())))

        # 4-5 agreement at N=1
        _, _, bbox, _, _ = hits[("BASE", "sws", 1)]
        for pipe, kern in [(p, "sws") for p in SWS[1:]] + [(p, "darknet") for p in DARKNET]:
            ch, _, cbox, _, _ = hits[(pipe, kern, 1)]
            both = bh & ch
            iou_bc = np.full(len(gt), np.nan)
            if both.any():
                a, b = bbox[both], cbox[both]
                ix1 = np.maximum(a[:, 0], b[:, 0]); iy1 = np.maximum(a[:, 1], b[:, 1])
                ix2 = np.minimum(a[:, 2], b[:, 2]); iy2 = np.minimum(a[:, 3], b[:, 3])
                inter = np.clip(ix2 - ix1, 0, None) * np.clip(iy2 - iy1, 0, None)
                ua = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]) + (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) - inter
                iou_bc[both] = inter / ua
            cell = np.where(bh & ch, "both", np.where(bh, "base_only", np.where(ch, "cmp_only", "neither")))
            agree_rows.append(pd.DataFrame(dict(
                seq=seq, pipeline=pipe, kernel=kern, frame_idx=gt.frame_idx, track_id=gt.track_id,
                h_px=gt.hpx.round(2), visibility=gt.visibility, cell=cell, iou_base_cmp=np.round(iou_bc, 4))))

        # 4-6 sensitivity: extra-missed at other confidence floors
        for T in (0.1, 0.25, 0.5):
            if T == 0.25:
                hb = {p: hits[(p, "sws", 1)][0] for p in SWS}
            else:
                hb = {}
                for p in SWS:
                    hb[p] = match_all(gt, frame_dets(dumps1[(p, "sws")], T), n, 1)[0]
            bc = set(gt[hb["BASE"]].track_id)
            for p in SWS:
                for N in NS:
                    proc = gt.frame_idx.to_numpy() % N == 0
                    caught = set(gt[hb[p] & proc].track_id)
                    extra = len(bc - caught)
                    sens.append(dict(seq=seq, conf=T, pipeline=p, N=N, base_caught=len(bc),
                                     extra_missed=extra))

    tr = pd.DataFrame(track_rows).sort_values(["seq", "pipeline", "N", "track_id"])
    tr.to_csv(os.path.join(OUT, "track_status.csv"), index=False)
    tr[tr.extra_missed].to_csv(os.path.join(OUT, "extra_missed_tracks.csv"), index=False)
    pd.DataFrame(frame_rows).to_csv(os.path.join(OUT, "summary_frame_level.csv"), index=False)
    ag = pd.concat(agree_rows, ignore_index=True)
    ag.to_csv(os.path.join(OUT, "agreement_boxes.csv"), index=False)
    pd.DataFrame(sens).to_csv(os.path.join(OUT, "summary_sensitivity.csv"), index=False)
    json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=1)
    log("done:", OUT)


if __name__ == "__main__":
    main()
