#!/usr/bin/env python3
"""
Tables for result_03a_person.md from person_analysis.py's output.

    usage: python3 person_report.py      -> ~/ndp_analysis/person/out/tables.md
                                            and summary_*.csv

Every number in the report comes from here; nothing is typed by hand.
"""
import os

import numpy as np
import pandas as pd

OUT = os.path.expanduser("~/ndp_analysis/person/out")
SEQS = ["MOT17-02", "MOT17-04", "MOT17-09", "MOT20-02", "MOT20-03"]
SWS = ["BASE", "q85", "q100", "raw"]
LABEL = {"BASE": "BASE", "q85": "sws-q85", "q100": "sws-q100", "raw": "sws-raw"}


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def md(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def main():
    tr = pd.read_csv(os.path.join(OUT, "track_status.csv"))
    fl = pd.read_csv(os.path.join(OUT, "summary_frame_level.csv"))
    ag = pd.read_csv(os.path.join(OUT, "agreement_boxes.csv"))
    se = pd.read_csv(os.path.join(OUT, "summary_sensitivity.csv"))
    mp = pd.read_csv(os.path.join(OUT, "summary_map_reproduction.csv"))
    sc = pd.read_csv(os.path.join(OUT, "summary_sampling_consistency.csv"))
    T = []

    # 4-0
    T.append("## 4-0.1 mAP reproduction\n")
    T.append(f"dumps: {len(mp)}, max |Δ mAP@0.5| = {mp.d50.abs().max():.4f} pp, "
             f"max |Δ mAP@[.5:.95]| = {mp.d5095.abs().max():.4f} pp\n")
    T.append(md(mp.assign(sr=mp.sr.astype(str))))
    T.append("\n## 4-0.2 sampling consistency\n")
    T.append(md(sc))

    # 4-1
    T.append("\n## 4-1 missed people (tracks)\n")
    rows = []
    for seq in SEQS + ["ALL"]:
        for p in SWS:
            for N in (1, 2, 5):
                d = tr[(tr.pipeline == p) & (tr.N == N)]
                if seq != "ALL":
                    d = d[d.seq == seq]
                n = len(d)
                bc = int(d.base_caught.sum())
                c = d.status.value_counts()
                ex = int(d.extra_missed.sum())
                rows.append(dict(seq=seq, cond=LABEL[p], N=N, tracks=n,
                                 structural=f"{c.get('structural', 0)} ({pct(c.get('structural', 0), n)})",
                                 detection_miss=f"{c.get('detection_miss', 0)} ({pct(c.get('detection_miss', 0), n)})",
                                 caught=f"{c.get('caught', 0)} ({pct(c.get('caught', 0), n)})",
                                 base_caught=bc, extra_missed=f"{ex} ({pct(ex, bc)})"))
    t41 = pd.DataFrame(rows)
    t41.to_csv(os.path.join(OUT, "summary_4_1_missed.csv"), index=False)
    T.append(md(t41))

    # 4-2
    T.append("\n## 4-2 first-detection delay vs BASE N=1 (tracks caught in both)\n")
    rows = []
    for seq in SEQS + ["ALL"]:
        for p in SWS:
            for N in (1, 2, 5):
                if p == "BASE" and N == 1:
                    continue
                d = tr[(tr.pipeline == p) & (tr.N == N) & tr.delay_frames.notna()]
                if seq != "ALL":
                    d = d[d.seq == seq]
                if not len(d):
                    continue
                f = d.delay_frames
                s = d.delay_s
                rows.append(dict(seq=seq, cond=LABEL[p], N=N, tracks=len(d),
                                 median_f=f"{f.median():.0f}", p90_f=f"{f.quantile(0.9):.0f}",
                                 max_f=f"{f.max():.0f}", median_s=f"{s.median():.2f}",
                                 p90_s=f"{s.quantile(0.9):.2f}", max_s=f"{s.max():.2f}",
                                 over_1s=int((s > 1.0).sum()), negative=int((f < 0).sum())))
    t42 = pd.DataFrame(rows)
    t42.to_csv(os.path.join(OUT, "summary_4_2_delay.csv"), index=False)
    T.append(md(t42))

    # 4-3
    T.append("\n## 4-3 track coverage MT/PT/ML (all sequences)\n")
    def cls(c):
        return np.where(c >= 0.8, "MT", np.where(c >= 0.2, "PT", "ML"))
    rows = []
    for seq in SEQS + ["ALL"]:
        for p in SWS:
            for N in (1, 2, 5):
                d = tr[(tr.pipeline == p) & (tr.N == N)]
                if seq != "ALL":
                    d = d[d.seq == seq]
                a = d[d.cov_processed.notna()]
                ca = pd.Series(cls(a.cov_processed)).value_counts()
                cb = pd.Series(cls(d.cov_all_reuse)).value_counts()
                rows.append(dict(seq=seq, cond=LABEL[p], N=N,
                                 a_tracks=len(a), a_MT=pct(ca.get("MT", 0), len(a)),
                                 a_PT=pct(ca.get("PT", 0), len(a)), a_ML=pct(ca.get("ML", 0), len(a)),
                                 a_excluded=len(d) - len(a),
                                 b_tracks=len(d), b_MT=pct(cb.get("MT", 0), len(d)),
                                 b_PT=pct(cb.get("PT", 0), len(d)), b_ML=pct(cb.get("ML", 0), len(d))))
    t43 = pd.DataFrame(rows)
    t43.to_csv(os.path.join(OUT, "summary_4_3_coverage.csv"), index=False)
    T.append(md(t43))

    # 4-4
    T.append("\n## 4-4 frame-level recall / precision\n")
    rows = []
    for seq in SEQS + ["ALL"]:
        for p in SWS:
            for N in (1, 2, 5):
                d = fl[(fl.pipeline == p) & (fl.N == N)]
                if seq != "ALL":
                    d = d[d.seq == seq]
                s = d.sum(numeric_only=True)
                rows.append(dict(seq=seq, cond=LABEL[p], N=N,
                                 A_recall=pct(s.hit_processed, s.gt_processed),
                                 A_precision=pct(s.matched_processed, s.det_processed),
                                 B_recall=pct(s.hit_all, s.gt_all),
                                 B_precision=pct(s.matched_all, s.det_all)))
    t44 = pd.DataFrame(rows)
    t44.to_csv(os.path.join(OUT, "summary_4_4_frame.csv"), index=False)
    T.append(md(t44))

    # 4-5
    T.append("\n## 4-5 Baseline vs NDP agreement (sr=1, per GT box)\n")
    ag["cond"] = ag.kernel + "-" + ag.pipeline
    order = ["sws-q85", "sws-q100", "sws-raw", "darknet-q85", "darknet-q100", "darknet-raw"]
    rows = []
    for c in order:
        for seq in SEQS + ["ALL"]:
            d = ag[ag.cond == c]
            if seq != "ALL":
                d = d[d.seq == seq]
            n = len(d)
            v = d.cell.value_counts()
            rows.append(dict(cond=c, seq=seq, gt_boxes=n,
                             both=f"{v.get('both', 0)} ({pct(v.get('both', 0), n)})",
                             base_only=f"{v.get('base_only', 0)} ({pct(v.get('base_only', 0), n)})",
                             cmp_only=f"{v.get('cmp_only', 0)} ({pct(v.get('cmp_only', 0), n)})",
                             neither=f"{v.get('neither', 0)} ({pct(v.get('neither', 0), n)})"))
    t45 = pd.DataFrame(rows)
    t45.to_csv(os.path.join(OUT, "summary_4_5_cells.csv"), index=False)
    T.append(md(t45))

    T.append("\n### 4-5.2 IoU between BASE's and the condition's matched detection ('both' cells)\n")
    rows = []
    for c in order:
        d = ag[(ag.cond == c) & (ag.cell == "both")].iou_base_cmp
        rows.append(dict(cond=c, n=len(d), mean=f"{d.mean():.4f}", median=f"{d.median():.4f}",
                         p10=f"{d.quantile(0.1):.4f}", share_eq1=pct((d >= 0.9999).sum(), len(d))))
    t452 = pd.DataFrame(rows)
    t452.to_csv(os.path.join(OUT, "summary_4_5_iou.csv"), index=False)
    T.append(md(t452))

    T.append("\n### 4-5.3 base_only / cmp_only by GT height and visibility (all sequences)\n")
    hb = pd.cut(ag.h_px, [0, 50, 100, 200, np.inf], right=False, labels=["<50", "50-100", "100-200", ">=200"])
    vb = pd.cut(ag.visibility, [0.25, 0.5, 0.75, np.inf], right=False, labels=["0.25-0.5", "0.5-0.75", ">=0.75"])
    ag["hbin"], ag["vbin"] = hb, vb
    rows = []
    for c in order:
        d = ag[ag.cond == c]
        for kind, col in (("height", "hbin"), ("visibility", "vbin")):
            for b, g in d.groupby(col, observed=False):
                n = len(g)
                bo = int((g.cell == "base_only").sum())
                co = int((g.cell == "cmp_only").sum())
                rows.append(dict(cond=c, by=kind, bin=str(b), gt_boxes=n,
                                 base_only=f"{bo} ({pct(bo, n)})", cmp_only=f"{co} ({pct(co, n)})",
                                 net_loss=f"{pct(bo - co, n)}"))
    t453 = pd.DataFrame(rows)
    t453.to_csv(os.path.join(OUT, "summary_4_5_bins.csv"), index=False)
    T.append(md(t453))

    T.append("\n### 4-5.4 tracks with the largest hit loss vs BASE (top 10 per condition)\n")
    rows = []
    for c in order:
        d = ag[ag.cond == c]
        g = d.groupby(["seq", "track_id"]).agg(
            base_hits=("cell", lambda s: int(s.isin(["both", "base_only"]).sum())),
            cmp_hits=("cell", lambda s: int(s.isin(["both", "cmp_only"]).sum())),
            h_median_px=("h_px", "median")).reset_index()
        g["loss"] = g.base_hits - g.cmp_hits
        g = g.sort_values(["loss", "seq", "track_id"], ascending=[False, True, True]).head(10)
        for _, r in g.iterrows():
            rows.append(dict(cond=c, seq=r.seq, track_id=int(r.track_id), base_hits=int(r.base_hits),
                             cmp_hits=int(r.cmp_hits), loss=int(r.loss), h_median_px=f"{r.h_median_px:.0f}"))
    t454 = pd.DataFrame(rows)
    t454.to_csv(os.path.join(OUT, "summary_4_5_top_tracks.csv"), index=False)
    T.append(md(t454))

    # 4-6
    T.append("\n## 4-6 sensitivity: extra-missed tracks at other confidence floors (all sequences)\n")
    rows = []
    for p in SWS:
        for N in (1, 2, 5):
            r = dict(cond=LABEL[p], N=N)
            for conf in (0.1, 0.25, 0.5):
                d = se[(se.pipeline == p) & (se.N == N) & (se.conf == conf)]
                r[f"conf{conf}"] = f"{int(d.extra_missed.sum())}/{int(d.base_caught.sum())} ({pct(d.extra_missed.sum(), d.base_caught.sum())})"
            rows.append(r)
    t46 = pd.DataFrame(rows)
    t46.to_csv(os.path.join(OUT, "summary_4_6_sensitivity.csv"), index=False)
    T.append(md(t46))

    open(os.path.join(OUT, "tables.md"), "w").write("\n".join(T) + "\n")
    print("tables:", os.path.join(OUT, "tables.md"))


if __name__ == "__main__":
    main()
