#!/usr/bin/env python3
"""
Spec 03c2 section 2-3: figure 13 data and the 03c -> 04 change list, from the
person_followup.py outputs (out_03c = 10/19 dumps, out_03c_04 = 04 block G).

  fig13_person.csv           panel,cond,bin,n,value,lo,hi
      a          extra-missed % by BASE hit-count bin k (Wilson 95%)
      b_height   conditional loss % by GT height bin (block-bootstrap 95%)
      b_vis      conditional loss % by visibility bin
      c          failure-mode share % of the BASE-only boxes (no CI)
  changed_cells_03c_vs_04.csv every cell that differs, aggregated tables only
                             (tracks_03c.csv per-track rows are left out)

    usage: python3 make_fig13.py
"""
import os
import re

import numpy as np
import pandas as pd

P = os.path.expanduser("~/ndp_analysis/person")
A, B = os.path.join(P, "out_03c"), os.path.join(P, "out_03c_04")
TABLES = ["bins_conditional.csv", "failure_modes.csv", "failure_modes_A_conf.csv", "box_shift.csv",
          "box_shift_by_position.csv", "extra_missed_by_k.csv", "delay_split.csv", "matching_ambiguity.csv",
          "greedy_vs_hungarian.csv", "seq_mean_summary.csv"]


def wilson(k, n, z=1.96):
    if n == 0:
        return np.nan, np.nan
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, 100 * (c - h)), min(100.0, 100 * (c + h))


def parse_ci(s):
    m = re.match(r"([-\d.]+) \[([-\d.]+), ([-\d.]+)\]", str(s))
    return (float(m[1]), float(m[2]), float(m[3])) if m else (np.nan, np.nan, np.nan)


def main():
    rows = []
    k = pd.read_csv(os.path.join(B, "extra_missed_by_k.csv"))
    k["label"] = np.where(k.cond == "BASE", "sampling-only N=" + k.N.astype(str), k.cond + " N=" + k.N.astype(str))
    for _, r in k.iterrows():
        lo, hi = wilson(int(r.extra_missed), int(r.tracks))
        rows.append(dict(panel="a", cond=r.label, bin=f"k {r.kbin}", n=int(r.tracks), value=r.extra_missed_pct, lo=lo, hi=hi))
    b = pd.read_csv(os.path.join(B, "bins_conditional.csv"))
    b = b[b.seq == "ALL"]
    for _, r in b.iterrows():
        v, lo, hi = parse_ci(r.cond_loss_pct)
        rows.append(dict(panel="b_height" if r.by == "height" else "b_vis", cond=r.cond, bin=r.bin,
                         n=int(r.base_hits), value=v, lo=lo, hi=hi))
    f = pd.read_csv(os.path.join(B, "failure_modes.csv"))
    f = f[(f.seq == "ALL") & (f.side == "base_only")]
    for _, r in f.iterrows():
        rows.append(dict(panel="c", cond=r.cond, bin=r["mode"], n=int(r.total), value=r.pct, lo=np.nan, hi=np.nan))
    fig = pd.DataFrame(rows)
    fig["value"] = fig.value.round(3); fig["lo"] = fig.lo.round(3); fig["hi"] = fig.hi.round(3)
    fig.to_csv(os.path.join(B, "fig13_person.csv"), index=False)

    ch = []
    for t in TABLES:
        a = pd.read_csv(os.path.join(A, t)); bb = pd.read_csv(os.path.join(B, t))
        key = [c for c in ("cond", "side", "seq", "by", "bin", "mode", "metric", "N", "kbin", "part", "x_third", "y_half")
               if c in a.columns]
        for i in range(len(a)):
            for c in a.columns:
                if c in key:
                    continue
                x, y = a.iloc[i][c], bb.iloc[i][c]
                if (pd.isna(x) and pd.isna(y)) or x == y:
                    continue
                ch.append(dict(table=t, **{kk: a.iloc[i][kk] for kk in key}, column=c, v_03c=x, v_04=y))
    chd = pd.DataFrame(ch)
    chd.to_csv(os.path.join(B, "changed_cells_03c_vs_04.csv"), index=False)
    print("fig13 rows", len(fig), "| changed cells", len(chd), "| conds changed:",
          sorted(chd["cond"].dropna().unique()) if "cond" in chd else [])


if __name__ == "__main__":
    main()
