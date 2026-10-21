#!/usr/bin/env python3
"""
Spec 03c2 section 3 (S): per-sequence 2x2 against BASE and the GT height
distribution, on the spec 04 block G dumps. Definitions are 03a's
(person_analysis.py: valid GT, conf >= 0.25, IoU >= 0.5 greedy 1:1, N = 1).

S-1  per sequence x configuration: GT boxes, BASE hits, BASE only, cmp only,
     recall change (pp), precision change (pp), failure-mode A share of the
     BASE-only boxes (from person_followup.py's failure_modes.csv)
S-2  per sequence: GT height bins (<50, 50-100, 100-200, >=200 px) and
     median, visibility >= 0.75 share - over all valid GT boxes and over the
     boxes BASE hits

    usage: python3 seq_2x2.py [--dumps DIR] [--out DIR] [--fm FAILURE_MODES_CSV]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import person_analysis as pa  # noqa: E402

CMP = [("q85", "sws"), ("q100", "sws"), ("raw", "sws"), ("q85", "darknet"), ("q100", "darknet"), ("raw", "darknet")]
H_BINS = [0, 50, 100, 200, np.inf]
H_LAB = ["<50", "50-100", "100-200", ">=200"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", default="~/ndp_results/main04/dumps")
    ap.add_argument("--out", default="~/ndp_analysis/person/out_03c_04")
    ap.add_argument("--fm", default="~/ndp_analysis/person/out_03c_04/failure_modes.csv")
    a = ap.parse_args()
    D, OUT = os.path.expanduser(a.dumps), os.path.expanduser(a.out)
    os.makedirs(OUT, exist_ok=True)
    fm = pd.read_csv(os.path.expanduser(a.fm)) if os.path.isfile(os.path.expanduser(a.fm)) else None

    s1, s2 = [], []
    for seq in pa.SEQS:
        n, _ = pa.seq_info(seq)
        gt = pa.load_gt(seq)
        res = {}
        for pipe, kern in [("BASE", "sws")] + CMP:
            dump = pa.load_dump(os.path.join(D, f"M4G_{seq}_{pipe}_{kern}_sr1_r1.csv"))
            h, proc, _, ndet, nmat = pa.match_all(gt, pa.frame_dets(dump, pa.CONF), n, 1)
            res[(pipe, kern)] = (h, int(ndet.sum()), int(nmat.sum()))
        bh, bdet, bmat = res[("BASE", "sws")]
        for pipe, kern in CMP:
            ch, cdet, cmat = res[(pipe, kern)]
            cond = f"{kern}-{pipe}"
            a_pct = np.nan
            if fm is not None:
                r = fm[(fm.cond == cond) & (fm.side == "base_only") & (fm.seq == seq) & (fm["mode"] == "A")]
                a_pct = float(r.pct.iloc[0]) if len(r) else np.nan
            s1.append(dict(seq=seq, cond=cond, gt=len(gt), base_hits=int(bh.sum()), cmp_hits=int(ch.sum()),
                           base_only=int((bh & ~ch).sum()), cmp_only=int((~bh & ch).sum()),
                           recall_base_pct=round(100 * bh.mean(), 3), recall_cmp_pct=round(100 * ch.mean(), 3),
                           recall_change_pp=round(100 * (ch.mean() - bh.mean()), 3),
                           precision_base_pct=round(100 * bmat / bdet, 3), precision_cmp_pct=round(100 * cmat / cdet, 3),
                           precision_change_pp=round(100 * (cmat / cdet - bmat / bdet), 3),
                           dets_base=bdet, dets_cmp=cdet, failure_A_pct_of_base_only=a_pct))
        for lab, sel in (("all valid GT", np.ones(len(gt), bool)), ("BASE-hit GT", bh)):
            g = gt[sel]
            hb = pd.cut(g.hpx, H_BINS, right=False, labels=H_LAB).value_counts(normalize=True)
            r = dict(seq=seq, over=lab, boxes=len(g), h_median_px=round(g.hpx.median(), 1),
                     vis_ge_075_pct=round(100 * (g.visibility >= 0.75).mean(), 2))
            for b in H_LAB:
                r[f"h_{b}_pct"] = round(100 * hb.get(b, 0.0), 2)
            s2.append(r)
        print(seq, "done", flush=True)
    t1 = pd.DataFrame(s1); t2 = pd.DataFrame(s2)
    t1.to_csv(os.path.join(OUT, "seq_2x2.csv"), index=False)
    t2.to_csv(os.path.join(OUT, "seq_height_dist.csv"), index=False)
    print(t1.to_string(index=False)); print(t2.to_string(index=False))


if __name__ == "__main__":
    main()
