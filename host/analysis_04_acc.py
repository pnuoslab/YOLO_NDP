#!/usr/bin/env python3
"""
Spec 04-H sections 6-2 (ΔmAP CI update) and 6-4 (person-level rerun), on
the block G dumps (~/ndp_results/main04/dumps/M4G_*_sr1_r1.csv).

1. ΔmAP@0.5 vs Baseline with 1 s block-bootstrap 95% CIs - the method of
   map_bootstrap.py (spec 03b section 5-B), unchanged, after checking that
   the Python AP reproduces each dump's C value from results.csv.
2. The 03a core numbers - extra-missed people, the per-GT-box 2x2, IoU of
   the two matched boxes, frame recall - computed with person_analysis.py's
   definitions on the 2026-10-19 dumps and on the block G dumps, side by
   side. Sampling N = 2, 5 is picked from the sr=1 dumps as in 03a.

    usage: python3 analysis_04_acc.py   -> ~/ndp_analysis/main04/acc/
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import map_bootstrap as mb  # noqa: E402
import person_analysis as pa  # noqa: E402

D04 = os.path.expanduser("~/ndp_results/main04/dumps")
R04 = os.path.expanduser("~/ndp_results/main04/results.csv")
OUT = os.path.expanduser("~/ndp_analysis/main04/acc")
CONDS = [("BASE", "sws")] + [(p, k) for k in ("sws", "darknet") for p in ("q85", "q100", "raw")]


def path04(seq, pipe, kern):
    return os.path.join(D04, f"M4G_{seq}_{pipe}_{kern}_sr1_r1.csv")


def path1019(seq, pipe, kern):
    return pa.dump_path(seq, pipe, kern, 1)


def delta_map(res04):
    rng = np.random.default_rng(mb.SEED)
    rows, check, boots = [], [], {}
    for seq in pa.SEQS:
        n, fps = pa.seq_info(seq)
        labels = pa.load_labels(seq, n)
        ngt_f = np.array([len(labels[f]) for f in range(n)], dtype=float)
        bl = int(round(fps)); nb = int(np.ceil(n / bl))
        draws = rng.integers(0, nb, size=(mb.B, nb))
        W = np.zeros((mb.B, n))
        for b in range(mb.B):
            W[b] = np.repeat(np.bincount(draws[b], minlength=nb), bl)[:n]
        flags = {}
        for pipe, kern in CONDS:
            flags[(pipe, kern)] = mb.tp_flags(pa.load_dump(path04(seq, pipe, kern)), labels, n)
            pt = mb.ap_weighted(*flags[(pipe, kern)], np.ones(n), ngt_f)
            c = res04[(res04.exp == "M4G") & (res04.input == seq) & (res04.pipeline == pipe)
                      & (res04.kernel == kern) & (res04.rep == 1)].iloc[0].ap50
            check.append(dict(seq=seq, pipeline=pipe, kernel=kern, c_ap50=c, py_ap50=round(pt, 4), d=round(pt - c, 4)))
        base = np.array([mb.ap_weighted(*flags[("BASE", "sws")], W[b], ngt_f) for b in range(mb.B)])
        b0 = mb.ap_weighted(*flags[("BASE", "sws")], np.ones(n), ngt_f)
        for pipe, kern in CONDS[1:]:
            cb = np.array([mb.ap_weighted(*flags[(pipe, kern)], W[b], ngt_f) for b in range(mb.B)])
            c0 = mb.ap_weighted(*flags[(pipe, kern)], np.ones(n), ngt_f)
            dd = cb - base
            boots[(seq, pipe, kern)] = dd
            lo, hi = np.percentile(dd, [2.5, 97.5])
            rows.append(dict(seq=seq, cond=f"{kern}-{pipe}", base_ap50=round(b0, 3), cond_ap50=round(c0, 3),
                             d_ap50=round(c0 - b0, 3), ci_lo=round(lo, 3), ci_hi=round(hi, 3),
                             ci_excludes_0=bool(lo > 0 or hi < 0)))
        print("map", seq, flush=True)
    for pipe, kern in CONDS[1:]:
        dd = np.mean([boots[(s, pipe, kern)] for s in pa.SEQS], axis=0)
        pt = np.mean([r["d_ap50"] for r in rows if r["cond"] == f"{kern}-{pipe}"])
        lo, hi = np.percentile(dd, [2.5, 97.5])
        rows.append(dict(seq="mean5", cond=f"{kern}-{pipe}", base_ap50=np.nan, cond_ap50=np.nan, d_ap50=round(pt, 3),
                         ci_lo=round(lo, 3), ci_hi=round(hi, 3), ci_excludes_0=bool(lo > 0 or hi < 0)))
    return pd.DataFrame(rows), pd.DataFrame(check)


def person(pathfn, tag):
    """03a core numbers for one dump set."""
    out = []
    for seq in pa.SEQS:
        n, fps = pa.seq_info(seq)
        gt = pa.load_gt(seq)
        hits = {}
        for pipe, kern in CONDS:
            dets = pa.frame_dets(pa.load_dump(pathfn(seq, pipe, kern)), pa.CONF)
            for N in (pa.NS if kern == "sws" else [1]):
                hits[(pipe, kern, N)] = pa.match_all(gt, dets, n, N)
        bh, _, bbox, _, _ = hits[("BASE", "sws", 1)]
        base_caught = set(gt[bh].track_id)
        for (pipe, kern, N), (h, proc, box, ndet, nmat) in hits.items():
            caught = set(gt[h & proc].track_id)
            r = dict(set=tag, seq=seq, cond="BASE" if pipe == "BASE" else f"{kern}-{pipe}", N=N,
                     tracks=gt.track_id.nunique(), base_caught=len(base_caught),
                     extra_missed=len(base_caught - caught),
                     gt_proc=int(proc.sum()), hit_proc=int((h & proc).sum()),
                     det_proc=int(ndet[np.arange(n) % N == 0].sum()), match_proc=int(nmat[np.arange(n) % N == 0].sum()))
            if N == 1 and pipe != "BASE":
                both = bh & h
                a, b = bbox[both], box[both]
                ix = np.clip(np.minimum(a[:, 2], b[:, 2]) - np.maximum(a[:, 0], b[:, 0]), 0, None)
                iy = np.clip(np.minimum(a[:, 3], b[:, 3]) - np.maximum(a[:, 1], b[:, 1]), 0, None)
                inter = ix * iy
                uni = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]) + (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) - inter
                iou = inter / uni
                r.update(both=int(both.sum()), base_only=int((bh & ~h).sum()), cmp_only=int((~bh & h).sum()),
                         neither=int((~bh & ~h).sum()), iou_sum=float(iou.sum()), iou_n=int(len(iou)),
                         iou_median=float(np.median(iou)) if len(iou) else np.nan)
            out.append(r)
        print("person", tag, seq, flush=True)
    return pd.DataFrame(out)


def main():
    os.makedirs(OUT, exist_ok=True)
    res04 = pd.read_csv(R04)
    dm, ck = delta_map(res04)
    ck.to_csv(os.path.join(OUT, "check_vs_c_G.csv"), index=False)
    dm.to_csv(os.path.join(OUT, "delta_map_ci_G.csv"), index=False)
    print(f"max |py - C| = {ck.d.abs().max():.4f} pp over {len(ck)} dumps")
    print(dm.to_string(index=False))

    p = pd.concat([person(path1019, "1019"), person(path04, "04")], ignore_index=True)
    p.to_csv(os.path.join(OUT, "person_by_seq.csv"), index=False)
    agg = p.groupby(["set", "cond", "N"]).sum(numeric_only=True).reset_index()
    agg["extra_missed_pct"] = 100 * agg.extra_missed / agg.base_caught
    agg["recall_pct"] = 100 * agg.hit_proc / agg.gt_proc
    agg["precision_pct"] = 100 * agg.match_proc / agg.det_proc
    agg["base_only_pct"] = 100 * agg.base_only / agg.gt_proc
    agg["cmp_only_pct"] = 100 * agg.cmp_only / agg.gt_proc
    agg["iou_mean"] = agg.iou_sum / agg.iou_n
    agg.to_csv(os.path.join(OUT, "person_summary.csv"), index=False)
    cols = ["cond", "N", "extra_missed", "extra_missed_pct", "recall_pct", "precision_pct",
            "both", "base_only", "cmp_only", "base_only_pct", "cmp_only_pct", "iou_mean"]
    w = agg[agg.set == "1019"][cols].merge(agg[agg.set == "04"][cols], on=["cond", "N"], suffixes=("_1019", "_04"))
    w.to_csv(os.path.join(OUT, "person_1019_vs_04.csv"), index=False)
    print(w.round(3).to_string(index=False))
    # per-seq differences
    k = ["seq", "cond", "N"]
    m = p[p.set == "1019"].merge(p[p.set == "04"], on=k, suffixes=("_a", "_b"))
    diff = m[(m.extra_missed_a != m.extra_missed_b) | (m.hit_proc_a != m.hit_proc_b)][
        k + ["extra_missed_a", "extra_missed_b", "hit_proc_a", "hit_proc_b"]]
    diff.to_csv(os.path.join(OUT, "person_diff_by_seq.csv"), index=False)
    print("\nper-seq differences (1019 -> 04):")
    print(diff.to_string(index=False))


if __name__ == "__main__":
    main()
