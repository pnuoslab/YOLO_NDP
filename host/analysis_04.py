#!/usr/bin/env python3
"""
Spec 04-H section 6: block summaries (6-1), the per-decision comparison
tables (6-2) and the figure CSVs (6-3), from ~/ndp_results/main04/results.csv.
Read only. Accuracy (ΔmAP CI on block G dumps) and the person-level rerun are
in analysis_04_acc.py; if its output exists, the deployment table picks up
the ΔmAP column from it.

Conventions (as in pilot_analysis.py): mean, 95% t-interval, CV over
repeats. Speed-ups are the mean of per-repeat ratios, repeat r of the
condition against repeat r of its Baseline, with a t-interval.
  M4A, M4C, M4E      Baseline of the same block, input and repeat
  M4Cp, M4D, M4B     block A Baseline, same input and repeat
  M4Dref             block A Baseline (standard), same input and repeat

    usage: python3 analysis_04.py   -> ~/ndp_analysis/main04/ (tables.md, *.csv)
                                       ~/ndp_analysis/main04/summary_04/fig_*.csv
"""
import os
import re

import numpy as np
import pandas as pd
from scipy import stats

CSV = os.path.expanduser("~/ndp_results/main04/results.csv")
REF = os.path.expanduser("~/ndp_analysis/results_full.csv")          # 10/19
OUT = os.path.expanduser("~/ndp_analysis/main04")
FIG = os.path.join(OUT, "summary_04")
ACC = os.path.join(OUT, "acc", "delta_map_ci_G.csv")
CFGS = ["sws-q85", "sws-q100", "sws-raw", "darknet-q85", "darknet-q100", "darknet-raw"]
RES = ["res1080_h264", "res1440_h264", "res2160_h264"]


def ci(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n == 0:
        return dict(n=0, mean=np.nan, lo=np.nan, hi=np.nan, cv=np.nan, sd=np.nan)
    m = x.mean()
    if n < 2:
        return dict(n=n, mean=m, lo=np.nan, hi=np.nan, cv=np.nan, sd=np.nan)
    s = x.std(ddof=1)
    h = stats.t.ppf(0.975, n - 1) * s / np.sqrt(n)
    return dict(n=n, mean=m, lo=m - h, hi=m + h, cv=100 * s / m if m else np.nan, sd=s)


def f(c, d=2, cv=True):
    if c["n"] == 0 or np.isnan(c["mean"]):
        return "-"
    if np.isnan(c["lo"]):
        return f"{c['mean']:.{d}f}"
    s = f"{c['mean']:.{d}f} [{c['lo']:.{d}f}, {c['hi']:.{d}f}]"
    return s + (f" cv {c['cv']:.1f}%" if cv else "")


def md(df):
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(out)


def load():
    d = pd.read_csv(CSV)
    d = d[d.status == "ok"].copy()
    d["extra_opts"] = d.extra_opts.fillna("")
    d["cfg"] = np.where(d.pipeline == "BASE", "BASE", d.kernel + "-" + d.pipeline)
    d["dt"] = d.extra_opts.str.extract(r"-dec_threads (\d+)")[0].fillna("0").astype(int)
    d["ct"] = d.extra_opts.str.extract(r"-cv_threads (\d+)")[0].fillna("-1").astype(int)
    d["codec"] = d.input.str.extract(r"_(h264|hevc|av1)$")[0].fillna("h264")
    d["host_cpu_s"] = d.host_cpu_user_s + d.host_cpu_sys_s
    d["dec_obs"] = d.tgt_thr_peak - d.tgt_thr_base - 1
    d["nic_mb"] = d.host_nic_rx_bytes / 1e6
    d["payload_mb"] = d.bytes / 1e6
    d["meta_mb"] = np.where(d.pipeline == "BASE", 0.0, d.host_read_bytes / 1e6)
    d["nic_over_pct"] = np.where(d.pipeline == "BASE",
                                 100 * (d.host_nic_rx_bytes / d.host_read_bytes - 1),
                                 100 * (d.host_nic_rx_bytes / (d.bytes + d.host_read_bytes) - 1))
    return d


def base_of(d, exp, inp, rep):
    ref_exp = exp if exp in ("M4A", "M4C", "M4E") else "M4A"
    b = d[(d.exp == ref_exp) & (d.input == inp) & (d.cfg == "BASE") & (d.ct == -1) & (d.rep == rep)]
    return b.iloc[0] if len(b) else None


def speedup(d, g, col="e2e"):
    r = []
    for _, row in g.iterrows():
        b = base_of(d, row.exp, row.input, row.rep)
        if b is not None:
            r.append(b[col] / row[col])
    return ci(r)


def main():
    os.makedirs(FIG, exist_ok=True)
    d = load()
    T = [f"rows ok: {len(d)}\n"]

    # ── 6-1 block summaries ─────────────────────────────────────────────
    T.append("## 6-1. Block summaries (e2e mean [95% CI] cv; speed-up vs Baseline, paired per repeat)\n")
    allrows = []
    for exp in ["M4A", "M4C", "M4Cp", "M4D", "M4Dref", "M4E", "M4B"]:
        rows = []
        for key, g in d[d.exp == exp].groupby(["input", "cfg", "sample_rate", "dt", "ct"], sort=False):
            inp, cfg, sr, dt, ct = key
            e = ci(g.e2e); s = ci(g.stage_sum_s)
            sp_e = speedup(d, g, "e2e") if (cfg != "BASE" or exp == "M4Dref") else ci([])
            sp_s = speedup(d, g, "stage_sum_s") if (cfg != "BASE" or exp == "M4Dref") else ci([])
            r = dict(block=exp, input=inp, cfg=cfg, sr=sr, dec_threads=dt, cv_threads=ct, n=e["n"],
                     e2e=f(e), stage_sum=f(s, cv=False),
                     resid_pct=f"{g.resid_pct.mean():.2f} (max {g.resid_pct.max():.2f})",
                     speedup_e2e=f(sp_e, 3, cv=False), speedup_stagesum=f(sp_s, 3, cv=False))
            rows.append(r)
            allrows.append(dict(r, e2e_mean=e["mean"], e2e_lo=e["lo"], e2e_hi=e["hi"], e2e_cv=e["cv"],
                                stage_mean=s["mean"], sp_mean=sp_e["mean"], sp_lo=sp_e["lo"], sp_hi=sp_e["hi"],
                                sps_mean=sp_s["mean"]))
        T.append(f"\n### {exp}\n")
        T.append(md(pd.DataFrame(rows).drop(columns=["block"])))
    g = d[d.exp == "M4G"]
    T.append("\n### M4G (accuracy runs; e2e includes the 0.005 post-processing, not a latency figure)\n")
    T.append(md(g.groupby(["input", "cfg"], sort=False).agg(n=("rep", "size"), e2e=("e2e", "mean"),
                                                             ap50=("ap50", "mean"), ap5095=("ap5095", "mean"))
                .round(4).reset_index()))
    blk = pd.DataFrame(allrows)
    blk.to_csv(os.path.join(OUT, "block_summary.csv"), index=False)

    # ── 6-2 (1) deployment table ────────────────────────────────────────
    T.append("\n## 6-2 (1). Deployment configurations (block A sr=1 defaults; host CPU also from block D)\n")
    acc = pd.read_csv(ACC) if os.path.isfile(ACC) else None
    rows = []
    for inp in RES:
        A = d[(d.exp == "M4A") & (d.input == inp)]
        D = d[(d.exp == "M4D") & (d.input == inp)]
        for cfg in ["BASE"] + CFGS:
            a = A[A.cfg == cfg]
            dd = D[D.cfg == cfg]
            r = dict(input=inp, cfg=cfg, e2e=f(ci(a.e2e), 2, cv=False),
                     speedup_e2e=f(speedup(d, a), 2, cv=False) if cfg != "BASE" else "1",
                     speedup_stagesum=f(speedup(d, a, "stage_sum_s"), 2, cv=False) if cfg != "BASE" else "1",
                     payload_MB=f"{a.payload_mb.mean():.1f}", nic_MB=f"{a.nic_mb.mean():.1f}",
                     meta_open_MB=f"{a.meta_mb.mean():.2f}",
                     host_vmhwm_MB=f"{a.host_vmhwm_mb.mean():.0f}",
                     storage_mem_MB_median=f"{a.tgt_storage_mem_total_mb.median():.0f}" if cfg != "BASE" else "-",
                     host_cpu_s_default=f"{a.host_cpu_s.mean():.1f}",
                     host_cpu_s_cv1=f"{dd.host_cpu_s.mean():.1f}" if len(dd) else "-",
                     storage_cpu_s=f"{a.tgt_cpu_storage_s.mean():.1f}" if cfg != "BASE" else "-")
            if acc is not None and cfg != "BASE":
                m = acc[(acc.seq == "mean5") & (acc.cond == cfg)]
                r["dmAP50_mean5"] = (f"{m.d_ap50.iloc[0]:+.2f} [{m.ci_lo.iloc[0]:.2f}, {m.ci_hi.iloc[0]:.2f}]"
                                     if len(m) else "-")
            rows.append(r)
    dep = pd.DataFrame(rows)
    dep.to_csv(os.path.join(OUT, "decision_deployment.csv"), index=False)
    T.append(md(dep))

    # ── 6-2 (2) decoder threads ─────────────────────────────────────────
    T.append("\n## 6-2 (2). Decoder threads (H.264 auto = block A reps 1-3, H.264 14 = block C', HEVC/AV1 = block C)\n")
    dec = d[((d.exp == "M4A") & (d.rep <= 3) & d.input.isin(["res1080_h264", "res2160_h264"])
             & d.cfg.isin(["sws-q85", "sws-raw", "darknet-q100"]))
            | (d.exp == "M4Cp") | ((d.exp == "M4C") & (d.cfg != "BASE"))]
    rows = []
    for (inp, cfg), g in dec.groupby(["input", "cfg"], sort=False):
        auto = g[g.dt == 0].set_index("rep")
        for dt, h in g.groupby("dt"):
            h = h.set_index("rep")
            k = auto.index.intersection(h.index)
            rp = ci((h.loc[k, "tgt_pre_total"] / auto.loc[k, "tgt_pre_total"]).to_numpy()) if dt else ci([1.0])
            re_ = ci((h.loc[k, "e2e"] / auto.loc[k, "e2e"]).to_numpy()) if dt else ci([1.0])
            rows.append(dict(input=inp, cfg=cfg, dec_threads=dt, dec=h.tgt_dec_name.iloc[0],
                             threads_set=int(h.tgt_dec_threads_set.iloc[0]), threads_obs=int(h.dec_obs.max()),
                             pre_total=f(ci(h.tgt_pre_total), 2), storage_cpu=f(ci(h.tgt_cpu_storage_s), 1, cv=False),
                             e2e=f(ci(h.e2e), 2, cv=False), pre_vs_auto=f(rp, 3, cv=False),
                             e2e_vs_auto=f(re_, 3, cv=False)))
    dect = pd.DataFrame(rows).sort_values(["input", "cfg", "dec_threads"])
    dect.to_csv(os.path.join(OUT, "decision_decoder_threads.csv"), index=False)
    T.append(md(dect))

    # ── 6-2 (3) OpenCV threads ──────────────────────────────────────────
    T.append("\n## 6-2 (3). -cv_threads 1: block D (NDP) and D-ref (Baseline) vs block A reps 1-3\n")
    rows = []
    for inp in ["res1080_h264", "res2160_h264"]:
        for cfg in ["BASE"] + CFGS:
            a = d[(d.exp == "M4A") & (d.input == inp) & (d.cfg == cfg) & (d.rep <= 3)].set_index("rep")
            x = d[(d.exp == ("M4Dref" if cfg == "BASE" else "M4D")) & (d.input == inp) & (d.cfg == cfg)].set_index("rep")
            k = a.index.intersection(x.index)
            de = ci((x.loc[k, "e2e"] - a.loc[k, "e2e"]).to_numpy())
            ca = ci(a.e2e)
            rows.append(dict(input=inp, cfg=cfg, e2e_default=f(ca, 2), e2e_cv1=f(ci(x.e2e), 2),
                             diff_s=f(de, 2, cv=False), diff_pct=f"{100 * de['mean'] / ca['mean']:+.2f}%",
                             default_cv_pct=f"{ca['cv']:.2f}%",
                             diff_ci_excludes_0=bool(de["lo"] > 0 or de["hi"] < 0),
                             host_cpu_default=f"{a.host_cpu_s.mean():.1f}", host_cpu_cv1=f"{x.host_cpu_s.mean():.1f}",
                             cpu_pre=f"{a.host_cpu_pre_s.mean():.1f} -> {x.host_cpu_pre_s.mean():.1f}",
                             cpu_infer=f"{a.host_cpu_infer_s.mean():.1f} -> {x.host_cpu_infer_s.mean():.1f}",
                             host_pre=f"{a.host_pre.mean():.2f} -> {x.host_pre.mean():.2f}",
                             threads=f"{int(a.host_thr_peak.max())} -> {int(x.host_thr_peak.max())}"))
    cvt = pd.DataFrame(rows)
    cvt.to_csv(os.path.join(OUT, "decision_cv_threads.csv"), index=False)
    T.append(md(cvt))
    b = d[(d.exp.isin(["M4A", "M4Dref"])) & (d.cfg == "BASE") & d.input.isin(["res1080_h264", "res2160_h264"]) & (d.rep <= 3)]
    T.append("\nBaseline sub-stages, default vs -cv_threads 1 (mean s):\n")
    T.append(md(b.groupby(["input", "ct"])[["bl_open_s", "bl_decode_s", "bl_copy_s", "bl_mat2img_s", "bl_letterbox_s",
                                             "infer", "post_s", "cleanup_s"]].mean().round(3).reset_index()))

    # ── 6-2 (4) storage memory ──────────────────────────────────────────
    T.append("\n## 6-2 (4). Storage memory (tgt_storage_mem_total_mb), block A sr=1, every repeat\n")
    rows = []
    for inp in RES:
        for cfg in CFGS:
            g = d[(d.exp == "M4A") & (d.input == inp) & (d.cfg == cfg)].sort_values("rep")
            v = g.tgt_storage_mem_total_mb.to_numpy()
            rows.append(dict(input=inp, cfg=cfg, mode="raw" if cfg.endswith("raw") else "JPEG",
                             values=" ".join(f"{x:.0f}" for x in v), median=f"{np.median(v):.0f}",
                             max=f"{v.max():.0f}", cv=f"{100 * v.std(ddof=1) / v.mean():.1f}%",
                             tgt_vmhwm_median=f"{g.tgt_vmhwm_mb.median():.0f}",
                             heap_peak_delta_median=f"{g.tgt_heap_peak_delta_mb.median():.0f}"))
    smem = pd.DataFrame(rows)
    smem.to_csv(os.path.join(OUT, "decision_storage_mem.csv"), index=False)
    T.append(md(smem))

    # ── 6-2 (5) MOT20-03 darknet determinism ────────────────────────────
    T.append("\n## 6-2 (5). MOT20-03 darknet: block G repeats (v2_fix) vs 2026-10-19 (before the fix)\n")
    ref = pd.read_csv(REF)
    rows = []
    for p in ["q85", "q100", "raw"]:
        g = d[(d.exp == "M4G") & (d.input == "MOT20-03") & (d.cfg == f"darknet-{p}")].sort_values("rep")
        r1019 = ref[(ref.exp == "E3") & (ref.input == "MOT20-03") & (ref.pipeline == p)].sort_values("rep")
        rows.append(dict(cfg=f"darknet-{p}", ap50_04=" / ".join(f"{x:.4f}" for x in g.ap50),
                         identical_04=bool(g.ap50.nunique() == 1 and g.ap5095.nunique() == 1),
                         ap50_1019=" / ".join(f"{x:.4f}" for x in r1019.ap50),
                         spread_1019=f"{r1019.ap50.max() - r1019.ap50.min():.4f}",
                         d_04_minus_1019_mean=f"{g.ap50.mean() - r1019.ap50.mean():+.4f}"))
    T.append(md(pd.DataFrame(rows)))
    rows = []
    for (inp, cfg), g in d[d.exp == "M4G"].groupby(["input", "cfg"], sort=False):
        r = g[g.rep == 1].iloc[0]
        if cfg == "BASE":
            q = ref[(ref.exp == "E2") & (ref.input == inp) & (ref.pipeline == "BASE") & (ref.sample_rate == 1) & (ref.rep == 1)]
        else:
            k, p = cfg.split("-")
            q = ref[(ref.exp == ("E3" if k == "darknet" else "E2")) & (ref.input == inp) & (ref.pipeline == p)
                    & (ref.kernel == k) & (ref.sample_rate == 1) & (ref.rep == 1)]
        rows.append(dict(input=inp, cfg=cfg, ap50_04=f"{r.ap50:.4f}", ap50_1019=f"{q.ap50.iloc[0]:.4f}",
                         same=bool(abs(r.ap50 - q.ap50.iloc[0]) < 1e-4)))
    gm = pd.DataFrame(rows)
    gm.to_csv(os.path.join(OUT, "g_map_vs_1019.csv"), index=False)
    T.append(f"\nAll block G mAP@0.5 vs 2026-10-19 rep 1: {int(gm.same.sum())}/{len(gm)} identical to 4 decimals; "
             f"different: {', '.join(gm[~gm.same].input + ' ' + gm[~gm.same].cfg) or 'none'}\n")

    # ── 10/19 comparison, same-definition stages only ───────────────────
    T.append("\n## 10/19 vs 04, same-definition stages only (block A sr=1 vs E1 sr=1, sws)\n")
    rows = []
    for inp in RES:
        for p in ["BASE", "q85", "q100", "raw"]:
            a = d[(d.exp == "M4A") & (d.input == inp) & (d.pipeline == p) & (d.kernel == "sws")]
            o = ref[(ref.exp == "E1") & (ref.input == inp) & (ref.pipeline == p) & (ref.sample_rate == 1)]
            rows.append(dict(input=inp, pipeline=p,
                             target_pre=f"{o.target_pre.mean():.2f} -> {a.target_pre.mean():.2f}" if p != "BASE" else "-",
                             infer=f"{o.infer.mean():.2f} -> {a.infer.mean():.2f}",
                             infer_pct=f"{100 * (a.infer.mean() / o.infer.mean() - 1):+.1f}%",
                             target_pre_pct=f"{100 * (a.target_pre.mean() / o.target_pre.mean() - 1):+.1f}%" if p != "BASE" else "-"))
    T.append(md(pd.DataFrame(rows)))

    # ── 6-3 figure data ─────────────────────────────────────────────────
    A = d[d.exp == "M4A"]
    rows = []
    for (inp, cfg), g in A.groupby(["input", "cfg"], sort=False):
        e, s = ci(g.e2e), ci(g.stage_sum_s)
        sp = speedup(d, g) if cfg != "BASE" else ci([1.0])
        rows.append(dict(input=inp, cfg=cfg, n=e["n"], e2e_mean=e["mean"], e2e_lo=e["lo"], e2e_hi=e["hi"],
                         stagesum_mean=s["mean"], stagesum_lo=s["lo"], stagesum_hi=s["hi"],
                         speedup_mean=sp["mean"], speedup_lo=sp["lo"], speedup_hi=sp["hi"]))
    pd.DataFrame(rows).to_csv(os.path.join(FIG, "fig_latency_resolution.csv"), index=False)
    bcols = ["bl_open_s", "bl_decode_s", "bl_copy_s", "bl_mat2img_s", "bl_letterbox_s", "infer", "post_s", "cleanup_s"]
    A[A.cfg == "BASE"].groupby("input")[bcols].agg(["mean", "std"]).to_csv(os.path.join(FIG, "fig_baseline_breakdown.csv"))
    # NDP breakdown, one flat header, seconds. The target's stages sit inside
    # its 0xC0 round trip (target_pre): ssd_read + pre_total + serialize +
    # untimed rest (c0_other_s). Inside pre_total, tgt_decode is only the
    # wait in avcodec_receive_frame (decoding runs on decoder threads in
    # parallel), so decode work is not separable here - see spec 05 block T.
    nd = A[A.cfg != "BASE"].copy()
    nd["tgt_ssd_read_s"] = nd.tgt_ssd_read_ms / 1000
    nd["tgt_serialize_s"] = nd.tgt_serialize_ms / 1000
    nd["tgt_pre_other_s"] = nd.tgt_pre_total - nd.tgt_decode - nd.tgt_letterbox - nd.tgt_encode
    nd["c0_other_s"] = nd.target_pre - nd.tgt_ssd_read_s - nd.tgt_pre_total - nd.tgt_serialize_s
    stages = [("tgt_ssd_read_s", "target: SSD read"), ("tgt_decode", "target: decode wait (receive_frame)"),
              ("tgt_letterbox", "target: resize + letterbox"), ("tgt_encode", "target: JPEG encode (0 for raw)"),
              ("tgt_pre_other_s", "target: rest of pre_total"), ("tgt_pre_total", "target: pre_total (sum of the four above)"),
              ("tgt_serialize_s", "target: serialize result"), ("c0_other_s", "target: untimed rest of the 0xC0 round trip"),
              ("target_pre", "host: 0xC0 round trip"), ("io", "host: 0xC2 transfer"), ("setup_s", "host: setup"),
              ("host_pre", "host: JPEG decode / raw unpack"), ("infer", "host: inference"), ("post_s", "host: post-processing"),
              ("cleanup_s", "host: cleanup"), ("stage_sum_s", "host: sum of stages"), ("e2e", "host: e2e")]
    rows = []
    for (inp, cfg), g in nd.groupby(["input", "cfg"], sort=False):
        r = dict(input=inp, cfg=cfg, n=len(g))
        for col, _ in stages:
            c_ = ci(g[col])
            r[f"{col}_mean"] = round(c_["mean"], 4); r[f"{col}_sd"] = round(c_["sd"], 4)
            r[f"{col}_lo"] = round(c_["lo"], 4); r[f"{col}_hi"] = round(c_["hi"], 4)
        rows.append(r)
    order = {k: i for i, k in enumerate(RES)}
    fb = pd.DataFrame(rows)
    fb = fb.sort_values(["input", "cfg"], key=lambda s_: s_.map(order) if s_.name == "input" else s_.map({c: i for i, c in enumerate(CFGS)}))
    fb.to_csv(os.path.join(FIG, "fig_ndp_breakdown.csv"), index=False)
    pd.DataFrame(stages, columns=["column", "meaning"]).to_csv(os.path.join(FIG, "fig_ndp_breakdown_columns.csv"), index=False)
    dect.to_csv(os.path.join(FIG, "fig_codec.csv"), index=False)
    cb = d[(d.exp == "M4C") & (d.cfg == "BASE")].groupby("input").e2e.agg(["mean", "std"])
    cb.to_csv(os.path.join(FIG, "fig_codec_baseline.csv"))
    smp = d[((d.exp == "M4A") & (d.cfg != "BASE")) | (d.exp == "M4B")]
    rows = []
    for (inp, cfg, sr), g in smp.groupby(["input", "cfg", "sample_rate"]):
        e = ci(g.e2e)
        rows.append(dict(input=inp, cfg=cfg, sr=sr, n=e["n"], e2e_mean=e["mean"], e2e_lo=e["lo"], e2e_hi=e["hi"],
                         payload_mb=g.payload_mb.mean(), nic_mb=g.nic_mb.mean(),
                         storage_cpu_s=g.tgt_cpu_storage_s.mean(), host_cpu_s=g.host_cpu_s.mean()))
    for inp, g in A[A.cfg == "BASE"].groupby("input"):
        e = ci(g.e2e)
        rows.append(dict(input=inp, cfg="BASE", sr=1, n=e["n"], e2e_mean=e["mean"], e2e_lo=e["lo"], e2e_hi=e["hi"],
                         payload_mb=g.payload_mb.mean(), nic_mb=g.nic_mb.mean(), storage_cpu_s=np.nan,
                         host_cpu_s=g.host_cpu_s.mean()))
    pd.DataFrame(rows).to_csv(os.path.join(FIG, "fig_sampling.csv"), index=False)
    res_cols = ["payload_mb", "host_read_bytes", "tgt_bytes_read", "nic_mb", "nic_over_pct", "host_vmhwm_mb",
                "host_maxrss_mb", "tgt_storage_mem_total_mb", "tgt_vmhwm_mb", "host_cpu_s", "host_cpu_pre_s",
                "host_cpu_infer_s", "tgt_cpu_storage_s"]
    d[d.exp.isin(["M4A", "M4D", "M4Dref"])].groupby(["exp", "input", "cfg"])[res_cols].agg(["mean", "median", "std"]) \
        .to_csv(os.path.join(FIG, "fig_resources.csv"))
    if acc is not None:
        t = pd.DataFrame(rows := [])
        out = []
        for inp in RES:
            for cfg in CFGS:
                g = A[(A.input == inp) & (A.cfg == cfg)]
                sp = speedup(d, g)
                m = acc[(acc.seq == "mean5") & (acc.cond == cfg)]
                out.append(dict(input=inp, cfg=cfg, speedup_mean=sp["mean"], speedup_lo=sp["lo"], speedup_hi=sp["hi"],
                                dmap50_mean5=m.d_ap50.iloc[0], dmap50_lo=m.ci_lo.iloc[0], dmap50_hi=m.ci_hi.iloc[0]))
        pd.DataFrame(out).to_csv(os.path.join(FIG, "fig_tradeoff.csv"), index=False)

    open(os.path.join(OUT, "tables.md"), "w").write("\n".join(T) + "\n")
    print("\n".join(T))


if __name__ == "__main__":
    main()
