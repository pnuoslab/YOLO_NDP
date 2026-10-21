#!/usr/bin/env python3
"""
Spec 05-H blocks M and R, from ~/ndp_results/main05/results.csv, with the
04 results (~/ndp_results/main04/results.csv) read for comparison only.

M  storage memory per condition (dma_readbuf, dma_blob, heap_inuse,
   storage_mem_alloc, and the old storage_mem_total), plus whether the time
   and CPU of each v3 run fall inside the 04 spread of the same condition.
R  Baseline -cv_threads 1 completed: sub-stage breakdown default vs cv1 per
   input, and the speed-up table with both sides at cv1 next to the 04
   both-default table. The NDP value used is recorded in ndp_source.
   Speed-up CIs here are for a ratio of two independent means (the two
   sides come from different blocks, so per-repeat pairing is not
   available everywhere): delta method, t with min(n)-1 degrees of freedom.

    usage: python3 analysis_05.py   -> ~/ndp_analysis/main05/ (tables_05.md, *.csv)
"""
import os

import numpy as np
import pandas as pd
from scipy import stats

R5 = os.path.expanduser("~/ndp_results/main05/results.csv")
R4 = os.path.expanduser("~/ndp_results/main04/results.csv")
OUT = os.path.expanduser("~/ndp_analysis/main05")
MOT = ["MOT17-02", "MOT17-04", "MOT17-09", "MOT20-02", "MOT20-03"]


def prep(d):
    d = d[d.status == "ok"].copy()
    d["extra_opts"] = d.extra_opts.fillna("")
    d["cfg"] = np.where(d.pipeline == "BASE", "BASE", d.kernel + "-" + d.pipeline)
    d["dt"] = d.extra_opts.str.extract(r"-dec_threads (\d+)")[0].fillna("0").astype(int)
    d["ct"] = d.extra_opts.str.extract(r"-cv_threads (\d+)")[0].fillna("-1").astype(int)
    d["host_cpu_s"] = d.host_cpu_user_s + d.host_cpu_sys_s
    return d


def md(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def ratio_ci(a, b):
    """mean(a)/mean(b), delta-method 95% CI."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    ma, mb = a.mean(), b.mean()
    r = ma / mb
    va = a.var(ddof=1) / len(a) if len(a) > 1 else 0.0
    vb = b.var(ddof=1) / len(b) if len(b) > 1 else 0.0
    se = r * np.sqrt(va / ma ** 2 + vb / mb ** 2)
    dfree = max(min(len(a), len(b)) - 1, 1)
    h = stats.t.ppf(0.975, dfree) * se
    return r, r - h, r + h


def main():
    os.makedirs(OUT, exist_ok=True)
    d5 = prep(pd.read_csv(R5))
    d4 = prep(pd.read_csv(R4))
    T = [f"05 rows ok: {len(d5)} ({d5.groupby('exp').size().to_dict()})\n"]

    # ── M confirmation ────────────────────────────────────────────────
    c = d5[d5.exp == "M5CHK"].sort_values("run_id_req")
    T.append("## M confirmation sequence (res1080_h264, in order)\n")
    T.append(md(c[["run_id_req", "cfg", "e2e", "tgt_payload_mb", "tgt_dma_readbuf_mb", "tgt_dma_blob_mb",
                   "tgt_heap_inuse_mb", "tgt_storage_mem_alloc_mb", "tgt_storage_mem_total_mb"]]))

    # ── M main ────────────────────────────────────────────────────────
    T.append("\n## M main: storage memory (MB) and the time/CPU check against 04\n")
    rows = []
    for _, r in d5[d5.exp == "M5M"].iterrows():
        if r.input in MOT:
            ref_exp = "M4E"
        elif r.input.endswith("h264"):
            ref_exp = "M4A" if r.sample_rate == 1 else "M4B"
        else:
            ref_exp = "M4C"
        g = d4[(d4.exp == ref_exp) & (d4.input == r.input) & (d4.cfg == r.cfg) & (d4.sample_rate == r.sample_rate)
               & (d4.dt == 0) & (d4.ct == -1)]
        o = dict(input=r.input, cfg=r.cfg, sr=int(r.sample_rate),
                 dma_readbuf=round(r.tgt_dma_readbuf_mb, 1), dma_blob=round(r.tgt_dma_blob_mb, 1),
                 heap_inuse=round(r.tgt_heap_inuse_mb, 1), alloc=round(r.tgt_storage_mem_alloc_mb, 1),
                 old_total_05=round(r.tgt_storage_mem_total_mb, 1),
                 old_total_04_median=round(g.tgt_storage_mem_total_mb.median(), 1), ref=ref_exp, n04=len(g))
        for col, nm in (("e2e", "e2e"), ("tgt_pre_total", "pre"), ("tgt_cpu_storage_s", "scpu")):
            m, s = g[col].mean(), g[col].std(ddof=1)
            o[f"{nm}_05"] = round(r[col], 2)
            o[f"{nm}_04"] = round(m, 2)
            o[f"{nm}_diff_pct"] = round(100 * (r[col] / m - 1), 2)
            o[f"{nm}_04_cv"] = round(100 * s / m, 2)
            o[f"{nm}_within_2sd"] = bool(abs(r[col] - m) <= 2 * s)
        rows.append(o)
    mm = pd.DataFrame(rows)
    mm.to_csv(os.path.join(OUT, "blockM.csv"), index=False)
    T.append(md(mm[["input", "cfg", "sr", "dma_readbuf", "dma_blob", "heap_inuse", "alloc", "old_total_05",
                    "old_total_04_median"]]))
    T.append("\nTime / CPU vs 04 (diff %, 04 CV %, within 2 SD):\n")
    T.append(md(mm[["input", "cfg", "sr", "ref", "n04", "e2e_diff_pct", "e2e_04_cv", "e2e_within_2sd",
                    "pre_diff_pct", "pre_04_cv", "pre_within_2sd", "scpu_diff_pct", "scpu_04_cv", "scpu_within_2sd"]]))

    # ── R: Baseline breakdown default vs cv1 ──────────────────────────
    T.append("\n## R: Baseline sub-stages, default (04) vs -cv_threads 1 (04 D-ref + 05 R)\n")
    cols = ["e2e", "bl_open_s", "bl_decode_s", "bl_copy_s", "bl_mat2img_s", "bl_letterbox_s", "infer", "post_s",
            "cleanup_s", "host_cpu_s"]
    inputs = ["res1080_h264", "res1440_h264", "res2160_h264", "res1080_hevc", "res2160_hevc", "res1080_av1",
              "res2160_av1"] + MOT
    rows = []
    for inp in inputs:
        dexp = "M4E" if inp in MOT else ("M4A" if inp.endswith("h264") else "M4C")
        de = d4[(d4.exp == dexp) & (d4.input == inp) & (d4.cfg == "BASE") & (d4.ct == -1)]
        cv = pd.concat([d4[(d4.exp == "M4Dref") & (d4.input == inp)],
                        d5[(d5.exp == "M5R") & (d5.input == inp) & (d5.cfg == "BASE")]])
        for lab, g in (("default", de), ("cv1", cv)):
            r = dict(input=inp, setting=lab, n=len(g), reps=",".join(map(str, sorted(g.rep.tolist()))))
            for c_ in cols:
                r[c_] = round(g[c_].mean(), 3)
            r["e2e_cv_pct"] = round(100 * g.e2e.std(ddof=1) / g.e2e.mean(), 2) if len(g) > 1 else np.nan
            rows.append(r)
    rb = pd.DataFrame(rows)
    rb.to_csv(os.path.join(OUT, "blockR_baseline_breakdown.csv"), index=False)
    T.append(md(rb))

    # ── R: speed-up with both sides at cv1 ────────────────────────────
    T.append("\n## R: speed-up, both default (04) vs both -cv_threads 1\n")
    rows = []
    cfgs = ["sws-q85", "sws-q100", "sws-raw", "darknet-q85", "darknet-q100", "darknet-raw"]
    for inp in inputs:
        dexp = "M4E" if inp in MOT else ("M4A" if inp.endswith("h264") else "M4C")
        bdef = d4[(d4.exp == dexp) & (d4.input == inp) & (d4.cfg == "BASE") & (d4.ct == -1)].e2e
        bcv = pd.concat([d4[(d4.exp == "M4Dref") & (d4.input == inp)],
                         d5[(d5.exp == "M5R") & (d5.input == inp) & (d5.cfg == "BASE")]]).e2e
        for cfg in cfgs:
            ndef = d4[(d4.exp == dexp) & (d4.input == inp) & (d4.cfg == cfg) & (d4.dt == 0) & (d4.ct == -1)].e2e
            if not len(ndef):
                continue
            if inp in ("res1080_h264", "res2160_h264"):
                ncv = d4[(d4.exp == "M4D") & (d4.input == inp) & (d4.cfg == cfg)].e2e; src = "04 D (cv1)"
            elif inp == "res1440_h264":
                ncv = d5[(d5.exp == "M5R") & (d5.input == inp) & (d5.cfg == cfg)].e2e; src = "05 R (cv1)"
            else:
                ncv = ndef; src = f"04 {dexp[2:]} (default; NDP latency unaffected by cv1)"
            r0 = ratio_ci(bdef, ndef); r1 = ratio_ci(bcv, ncv)
            rows.append(dict(input=inp, cfg=cfg,
                             speedup_both_default=f"{r0[0]:.3f} [{r0[1]:.3f}, {r0[2]:.3f}]",
                             speedup_both_cv1=f"{r1[0]:.3f} [{r1[1]:.3f}, {r1[2]:.3f}]",
                             change_pct=f"{100 * (r1[0] / r0[0] - 1):+.1f}%",
                             base_cv1_n=len(bcv), ndp_cv1_n=len(ncv), ndp_source=src))
    sp = pd.DataFrame(rows)
    sp.to_csv(os.path.join(OUT, "blockR_speedup.csv"), index=False)
    T.append(md(sp))

    # ── R: 1440p NDP CPU ──────────────────────────────────────────────
    T.append("\n## R: 1440p NDP, default (04 A) vs cv1 (05 R)\n")
    rows = []
    for cfg in cfgs:
        a = d4[(d4.exp == "M4A") & (d4.input == "res1440_h264") & (d4.cfg == cfg)]
        x = d5[(d5.exp == "M5R") & (d5.input == "res1440_h264") & (d5.cfg == cfg)]
        rows.append(dict(cfg=cfg, e2e_default=round(a.e2e.mean(), 2), e2e_cv1=round(x.e2e.mean(), 2),
                         e2e_diff_pct=round(100 * (x.e2e.mean() / a.e2e.mean() - 1), 2),
                         e2e_default_cv=round(100 * a.e2e.std(ddof=1) / a.e2e.mean(), 2),
                         host_cpu_default=round(a.host_cpu_s.mean(), 1), host_cpu_cv1=round(x.host_cpu_s.mean(), 1)))
    T.append(md(pd.DataFrame(rows)))

    open(os.path.join(OUT, "tables_05.md"), "w").write("\n".join(T) + "\n")
    print("\n".join(T))


if __name__ == "__main__":
    main()
