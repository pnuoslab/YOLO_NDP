#!/usr/bin/env python3
"""
ΔmAP@0.5 (NDP setting - Baseline) with block-bootstrap 95% CIs (spec 03b-H
section 5-B). Offline; reads the 2026-10-19 sr=1 MOT dumps.

Why frame weights are exact: in detector.c's scoring a detection's TP/FP
status depends only on its own frame - its candidate is the same-frame GT
with the largest IoU, and "already taken" can only be decided by another
detection of the same frame. Duplicating a frame in a resample therefore
duplicates its detections' TP/FP pattern and its GT count, with no
interaction across frames. So every detection's TP flag is computed once,
and a resample is a vector of frame multiplicities w_f: cumulative TP/FP
along the global confidence rank become cumsum(w * tp) / cumsum(w * fp),
recall divides by sum(w_f * n_gt_f). The one approximation: copies of a
frame sit at the same rank position, so precision is evaluated only after
all copies of each detection rather than between them. With w = 1 the
result equals person_analysis.c_map() exactly; main() checks that.

Resampling (spec): blocks of one second (round(fps) consecutive frames),
1,000 resamples, seed 0, drawn per sequence. The same resampled frames are
scored for Baseline and the setting, so the CI is for the paired
difference. "All 5" is the mean of the five sequence ΔmAPs, each resampled
independently.

    usage: python3 map_bootstrap.py   -> ~/ndp_analysis/map_ci/
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import person_analysis as pa  # noqa: E402

OUT = os.path.expanduser("~/ndp_analysis/map_ci")
B = 1000
SEED = 0
CONDS = [("BASE", "sws")] + [(p, k) for k in ("sws", "darknet") for p in ("q85", "q100", "raw")]
IOU_T = np.float32(0.5)


def tp_flags(dump, labels, n):
    """Per detection (rank order, conf >= 0.005): frame, TP flag, as in c_map."""
    d = dump[dump.conf >= pa.MAP_FLOOR].sort_values("rank")
    tp = np.zeros(len(d), dtype=bool)
    fr = d.frame_idx.to_numpy()
    xy = d[["x", "y", "x2", "y2"]].to_numpy()
    pos = np.arange(len(d))
    order = np.argsort(fr, kind="stable")
    bounds = np.searchsorted(fr[order], np.arange(n + 1))
    for f in range(n):
        ii = order[bounds[f]:bounds[f + 1]]          # this frame's dets, rank order
        gt = labels[f]
        if len(ii) == 0 or len(gt) == 0:
            continue
        m = pa.iou_matrix(xy[ii], gt)
        j = m.argmax(axis=1)
        v = m[np.arange(len(m)), j]
        taken = set()
        for k in range(len(ii)):
            if v[k] > IOU_T and j[k] not in taken:
                tp[pos[ii[k]]] = True
                taken.add(j[k])
    return fr, tp


def ap_weighted(fr, tp, w, ngt_f):
    """101-point AP (binary grid, like the -Ofast binary) with frame weights."""
    wd = w[fr]
    ctp = np.cumsum(wd * tp)
    cfp = np.cumsum(wd * ~tp)
    tot = ctp + cfp
    prec = np.where(tot > 0, ctp / np.maximum(tot, 1), 0.0)
    n_gt = float((w * ngt_f).sum())
    rec = ctp / n_gt
    suf = np.maximum.accumulate(prec[::-1])[::-1]
    step = 1.0 / (pa.MAP_POINTS - 1)
    r = np.arange(pa.MAP_POINTS) * 1.0 * step
    k = np.searchsorted(rec, r, side="left")
    ok = k < len(rec)
    vals = np.where(ok, suf[np.minimum(k, len(rec) - 1)], 0.0)
    return 100.0 * vals[vals > 0].sum() / pa.MAP_POINTS


def main():
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(SEED)
    res = pd.read_csv(pa.RESULTS)
    rows, check, boots = [], [], {}
    for seq in pa.SEQS:
        n, fps = pa.seq_info(seq)
        labels = pa.load_labels(seq, n)
        ngt_f = np.array([len(labels[f]) for f in range(n)], dtype=float)
        bl = int(round(fps))
        nb = int(np.ceil(n / bl))
        # block multiplicities -> frame weights, same draws for every condition
        draws = rng.integers(0, nb, size=(B, nb))
        W = np.zeros((B, n))
        for b in range(B):
            cnt = np.bincount(draws[b], minlength=nb)
            W[b] = np.repeat(cnt, bl)[:n]
        flags = {}
        for pipe, kern in CONDS:
            dump = pa.load_dump(pa.dump_path(seq, pipe, kern, 1))
            flags[(pipe, kern)] = tp_flags(dump, labels, n)
            point = ap_weighted(*flags[(pipe, kern)], np.ones(n), ngt_f)
            exp = "E3" if kern == "darknet" else "E2"
            c = res[(res.exp == exp) & (res.input == seq) & (res.pipeline == pipe) & (res.kernel == kern)
                    & (res.sample_rate == 1) & (res.rep == 1)].iloc[0].ap50
            check.append(dict(seq=seq, pipeline=pipe, kernel=kern, c_ap50=c, py_ap50=round(point, 4),
                              d=round(point - c, 4)))
        base = np.array([ap_weighted(*flags[("BASE", "sws")], W[b], ngt_f) for b in range(B)])
        b0 = ap_weighted(*flags[("BASE", "sws")], np.ones(n), ngt_f)
        for pipe, kern in CONDS[1:]:
            cmpb = np.array([ap_weighted(*flags[(pipe, kern)], W[b], ngt_f) for b in range(B)])
            c0 = ap_weighted(*flags[(pipe, kern)], np.ones(n), ngt_f)
            d = cmpb - base
            boots[(seq, pipe, kern)] = d
            lo, hi = np.percentile(d, [2.5, 97.5])
            rows.append(dict(seq=seq, cond=f"{kern}-{pipe}", base_ap50=round(b0, 3), cond_ap50=round(c0, 3),
                             d_ap50=round(c0 - b0, 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                             boot_sd=round(d.std(ddof=1), 3), ci_excludes_0=bool(lo > 0 or hi < 0)))
        print(seq, "done", flush=True)
    for pipe, kern in CONDS[1:]:
        d = np.mean([boots[(s, pipe, kern)] for s in pa.SEQS], axis=0)
        pt = np.mean([r["d_ap50"] for r in rows if r["cond"] == f"{kern}-{pipe}" and r["seq"] in pa.SEQS])
        lo, hi = np.percentile(d, [2.5, 97.5])
        rows.append(dict(seq="mean5", cond=f"{kern}-{pipe}", base_ap50=np.nan, cond_ap50=np.nan,
                         d_ap50=round(pt, 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                         boot_sd=round(d.std(ddof=1), 3), ci_excludes_0=bool(lo > 0 or hi < 0)))
    ck = pd.DataFrame(check)
    ck.to_csv(os.path.join(OUT, "check_vs_c.csv"), index=False)
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, "delta_map_ci.csv"), index=False)
    print(f"max |py - C| = {ck.d.abs().max():.4f} pp over {len(ck)} dumps")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
