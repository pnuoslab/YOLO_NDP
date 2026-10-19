#!/usr/bin/env python3
"""
Spec 03b-H section 5-A: numbers behind the section 6 verdict table, from the
pilot (~/ndp_results/pilot_03b/results_pilot.csv). Read only.

Everything is mean, 95% t-interval and CV over repeats; speed-ups are the
mean of per-repeat ratios (repeat r of one condition paired with repeat r
of the other, which ran in the same outer-loop pass) with a t-interval.

    usage: python3 pilot_analysis.py   -> ~/ndp_analysis/pilot_03b/tables.md + CSVs
"""
import os

import numpy as np
import pandas as pd
from scipy import stats

CSV = os.path.expanduser("~/ndp_results/pilot_03b/results_pilot.csv")
REF = os.path.expanduser("~/ndp_analysis/results_full.csv")       # 10/19, reference only
OUT = os.path.expanduser("~/ndp_analysis/pilot_03b")


def ci(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0:
        return dict(n=0, mean=np.nan, lo=np.nan, hi=np.nan, cv=np.nan)
    m = x.mean()
    if n < 2:
        return dict(n=n, mean=m, lo=np.nan, hi=np.nan, cv=np.nan)
    s = x.std(ddof=1)
    h = stats.t.ppf(0.975, n - 1) * s / np.sqrt(n)
    return dict(n=n, mean=m, lo=m - h, hi=m + h, cv=100 * s / m if m else np.nan)


def fmt(c, d=3):
    if c["n"] == 0:
        return "-"
    if np.isnan(c["lo"]):
        return f"{c['mean']:.{d}f}"
    return f"{c['mean']:.{d}f} [{c['lo']:.{d}f}, {c['hi']:.{d}f}] cv {c['cv']:.1f}%"


def md(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def main():
    os.makedirs(OUT, exist_ok=True)
    d = pd.read_csv(CSV)
    d = d[d.status == "ok"].copy()
    d["extra_opts"] = d.extra_opts.fillna("")
    d["cond"] = (d.exp + " " + d.input + " " + d.pipeline + "-" + d.kernel + " sr" + d.sample_rate.astype(str)
                 + d.extra_opts.str.replace("-post_thresh 0.25", "").str.strip().map(lambda s: f" ({s})" if s else ""))
    # derived
    d["tgt_inner_s"] = (d.tgt_ssd_read_ms / 1000 + d.tgt_pre_total + d.tgt_serialize_ms / 1000)
    d["c0_resid_s"] = d.target_pre - d.tgt_inner_s
    d["host_cpu_s"] = d.host_cpu_user_s + d.host_cpu_sys_s
    d["cpu_x_pct"] = 100 * (d.host_cpu_procstat_s - d.host_cpu_s) / d.host_cpu_s
    d["rss_x_pct"] = 100 * (d.host_vmhwm_mb - d.host_maxrss_mb) / d.host_vmhwm_mb
    d["dec_threads_obs"] = d.tgt_thr_peak - d.tgt_thr_base - 1
    d["nic_over_payload_pct"] = 100 * (d.host_nic_rx_bytes - d.bytes) / d.bytes
    d["nic_over_block_pct"] = np.where(d.pipeline == "BASE",
                                       100 * (d.host_nic_rx_bytes - d.host_read_bytes) / d.host_read_bytes, np.nan)
    d["host_cpu_bl_pre_s"] = d.host_cpu_pre_s
    T = []
    T.append(f"runs ok: {len(d)} / {len(pd.read_csv(CSV))}\n")

    # ---- per-condition summary ----
    cols = ["e2e", "stage_sum_s", "resid_pct", "bl_open_s", "target_pre", "io", "setup_s", "host_pre", "infer",
            "post_s", "eval_s", "cleanup_s", "bl_decode_s", "bl_copy_s", "bl_mat2img_s", "bl_letterbox_s",
            "host_cpu_s", "host_cpu_procstat_s", "cpu_x_pct", "host_cpu_pre_s", "host_cpu_infer_s",
            "host_cpu_post_s", "host_maxrss_mb", "host_vmhwm_mb", "rss_x_pct", "host_thr_peak",
            "bytes", "host_read_bytes", "tgt_bytes_read", "host_nic_rx_bytes", "nic_over_payload_pct",
            "nic_over_block_pct", "tgt_pre_total", "tgt_decode", "tgt_letterbox", "tgt_encode",
            "tgt_ssd_read_ms", "tgt_serialize_ms", "c0_resid_s", "tgt_cpu_storage_s", "tgt_cpu_user_s",
            "tgt_cpu_sys_s", "tgt_storage_mem_total_mb", "tgt_vmhwm_mb", "tgt_dec_threads_set",
            "tgt_thr_peak", "dec_threads_obs", "ap50", "ap5095"]
    rows = []
    for cond, g in d.groupby("cond", sort=False):
        r = dict(cond=cond)
        for c in cols:
            if c in g and g[c].notna().any():
                cc = ci(g[c])
                r[c] = cc["mean"]; r[c + "_lo"] = cc["lo"]; r[c + "_hi"] = cc["hi"]; r[c + "_cv"] = cc["cv"]
                r["n"] = cc["n"]
        rows.append(r)
    summ = pd.DataFrame(rows)
    summ.to_csv(os.path.join(OUT, "summary_by_condition.csv"), index=False)

    T.append("## A. e2e, stage sum, residual (mean [95% CI] cv)\n")
    rows = []
    for cond, g in d.groupby("cond", sort=False):
        rows.append(dict(cond=cond, n=len(g), e2e=fmt(ci(g.e2e), 2), stage_sum=fmt(ci(g.stage_sum_s), 2),
                         resid_pct=f"{g.resid_pct.mean():.2f} (max {g.resid_pct.max():.2f})"))
    T.append(md(pd.DataFrame(rows)))

    T.append("\n## B. stage breakdown, mean (cv %)\n")
    st = ["bl_open_s", "target_pre", "io", "setup_s", "host_pre", "infer", "post_s", "eval_s", "cleanup_s",
          "bl_decode_s", "bl_copy_s", "bl_mat2img_s", "bl_letterbox_s"]
    rows = []
    for cond, g in d.groupby("cond", sort=False):
        r = dict(cond=cond)
        for c in st:
            cc = ci(g[c])
            r[c] = f"{cc['mean']:.3f} ({cc['cv']:.1f})" if cc["n"] > 1 else f"{cc['mean']:.3f}"
        rows.append(r)
    T.append(md(pd.DataFrame(rows)))

    # ---- speed-ups (paired by rep) ----
    def paired(a, b, col="e2e"):
        x = a.set_index("rep")[col]; y = b.set_index("rep")[col]
        k = x.index.intersection(y.index)
        return ci((x[k] / y[k]).to_numpy())

    T.append("\n## C. speed-up vs Baseline (paired per repeat; e2e and stage sum)\n")
    rows = []
    for inp in ("res1080_h264", "res2160_h264"):
        base = d[(d.exp == "L1") & (d.input == inp) & (d.pipeline == "BASE")]
        for lab, sel in [("sws-q85", (d.exp == "L1") & (d.pipeline == "q85")),
                         ("sws-raw", (d.exp == "L1") & (d.pipeline == "raw")),
                         ("darknet-q85 (K1)", (d.exp == "K1") & (d.pipeline == "q85")),
                         ("darknet-q100 (K1)", (d.exp == "K1") & (d.pipeline == "q100")),
                         ("darknet-raw (K1)", (d.exp == "K1") & (d.pipeline == "raw"))]:
            c = d[sel & (d.input == inp)]
            if not len(c):
                continue
            e = paired(base, c, "e2e"); s = paired(base, c, "stage_sum_s")
            rows.append(dict(input=inp, cond=lab, n=e["n"], speedup_e2e=fmt(e, 3), speedup_stagesum=fmt(s, 3),
                             ci_excludes_1=bool(e["lo"] > 1 or e["hi"] < 1)))
    sp = pd.DataFrame(rows)
    sp.to_csv(os.path.join(OUT, "speedup.csv"), index=False)
    T.append(md(sp))

    # ---- cv_threads 1 (C1 vs L1 q85, reps 1-3) ----
    T.append("\n## D. -cv_threads 1 (C1) vs default (L1 q85), reps 1-3\n")
    rows = []
    for inp in ("res1080_h264", "res2160_h264"):
        a = d[(d.exp == "L1") & (d.input == inp) & (d.pipeline == "q85") & (d.rep <= 3)]
        b = d[(d.exp == "C1") & (d.input == inp)]
        for col in ("e2e", "host_pre", "infer", "host_cpu_s", "host_cpu_pre_s", "host_cpu_infer_s", "host_thr_peak"):
            rows.append(dict(input=inp, metric=col, default=fmt(ci(a[col]), 3), cv_threads_1=fmt(ci(b[col]), 3),
                             diff=f"{b[col].mean() - a[col].mean():+.3f}",
                             within_cv=bool(abs(b[col].mean() - a[col].mean()) <= 2 * a[col].std(ddof=1))))
    T.append(md(pd.DataFrame(rows)))

    # ---- decoder threads ----
    T.append("\n## E. decoder threads (target)\n")
    rows = []
    for cond, g in d[d.pipeline != "BASE"].groupby("cond", sort=False):
        if not g.input.str.contains("av1|hevc").any() and "dec_threads" not in cond:
            if not (g.exp.iloc[0] == "L1" and g.input.iloc[0] == "res2160_h264" and g.pipeline.iloc[0] == "q85"):
                continue
        rows.append(dict(cond=cond, n=len(g), dec=g.tgt_dec_name.iloc[0], req=int(g.tgt_dec_threads_req.iloc[0]),
                         set=int(g.tgt_dec_threads_set.iloc[0]), thr_peak=int(g.tgt_thr_peak.max()),
                         dec_threads_obs=int(g.dec_threads_obs.max()),
                         tgt_pre_total=fmt(ci(g.tgt_pre_total), 2), tgt_cpu_storage=fmt(ci(g.tgt_cpu_storage_s), 1),
                         e2e=fmt(ci(g.e2e), 2)))
    T.append(md(pd.DataFrame(rows)))
    ref = pd.read_csv(REF)
    r = ref[(ref.exp == "E5") & (ref.pipeline == "q85") & ref.input.str.contains("av1|hevc")].groupby("input")[
        ["target_pre", "e2e"]].agg(["mean", "std"]).round(2)
    T.append("\n10/19 reference (nvmf_tgt_measure, auto threads; e2e there includes 0.005 post-processing, so only target_pre is comparable):\n")
    T.append("```\n" + r.to_string() + "\n```")

    # ---- transfer ----
    T.append("\n## F. transfer volume: harness / block / NIC\n")
    rows = []
    for cond, g in d.groupby("cond", sort=False):
        rows.append(dict(cond=cond, harness_bytes=f"{g.bytes.mean():.0f}",
                         host_block_read=f"{g.host_read_bytes.mean():.0f}", tgt_bdev_read=f"{g.tgt_bytes_read.mean():.0f}",
                         nic_rx=f"{g.host_nic_rx_bytes.mean():.0f}", tgt_nic_tx=f"{g.tgt_nic_tx_bytes.mean():.0f}",
                         nic_over_harness=f"{g.nic_over_payload_pct.mean():.2f}%",
                         nic_eq_both_ends=bool((g.host_nic_rx_bytes == g.tgt_nic_tx_bytes).all())))
    T.append(md(pd.DataFrame(rows)))

    # ---- memory / CPU cross-checks ----
    T.append("\n## G. cross-checks: memory (ru_maxrss vs VmHWM), CPU (getrusage vs /proc), storage CPU/mem CV\n")
    rows = []
    for cond, g in d.groupby("cond", sort=False):
        rows.append(dict(cond=cond, maxrss=fmt(ci(g.host_maxrss_mb), 1), vmhwm=fmt(ci(g.host_vmhwm_mb), 1),
                         rss_diff_pct=f"{g.rss_x_pct.mean():.2f}", cpu_diff_pct=f"{g.cpu_x_pct.mean():+.3f}",
                         host_cpu=fmt(ci(g.host_cpu_s), 1), host_thr_peak=int(g.host_thr_peak.max()),
                         storage_cpu=fmt(ci(g.tgt_cpu_storage_s), 1) if g.tgt_cpu_storage_s.notna().any() else "-",
                         storage_mem=fmt(ci(g.tgt_storage_mem_total_mb), 1) if g.tgt_storage_mem_total_mb.notna().any() else "-"))
    T.append(md(pd.DataFrame(rows)))

    # ---- target-internal residual ----
    T.append("\n## H. 0xC0 round trip vs target-internal timers\n")
    rows = []
    for cond, g in d[d.pipeline != "BASE"].groupby("cond", sort=False):
        rows.append(dict(cond=cond, c0_roundtrip=fmt(ci(g.target_pre), 3), ssd_read=f"{g.tgt_ssd_read_ms.mean() / 1000:.3f}",
                         pre_total=f"{g.tgt_pre_total.mean():.3f}", serialize=f"{g.tgt_serialize_ms.mean() / 1000:.3f}",
                         resid_s=fmt(ci(g.c0_resid_s), 3),
                         resid_pct=f"{100 * g.c0_resid_s.mean() / g.target_pre.mean():.2f}%"))
    T.append(md(pd.DataFrame(rows)))

    # ---- K2 ----
    T.append("\n## I. K2 accuracy run vs 10/19\n")
    k = d[d.exp == "K2"][["pipeline", "ap50", "ap5095", "e2e", "post_s", "eval_s", "resid_pct"]]
    r = ref[(ref.exp == "E2") & (ref.input == "MOT17-04") & (ref.sample_rate == 1) & ref.pipeline.isin(["BASE", "q85"])
            & (ref.rep == 1)][["pipeline", "ap50", "ap5095"]]
    T.append(md(k.merge(r, on="pipeline", suffixes=("", "_1019"))))

    open(os.path.join(OUT, "tables.md"), "w").write("\n".join(T) + "\n")
    print("\n".join(T))


if __name__ == "__main__":
    main()
