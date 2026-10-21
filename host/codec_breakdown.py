#!/usr/bin/env python3
"""
Spec 03c2 section 4 (C): NDP stage breakdown per codec, with the same
columns as summary_04/fig_ndp_breakdown.csv (seconds; mean, sd, 95% t CI),
from the spec 04 results. -dec_threads 0 (auto) only:
  H.264  block A (res1080_h264, res2160_h264; 6 configurations, 5 repeats)
  HEVC, AV1  block C (sws-q85, sws-raw, darknet-q100; 3 repeats)
No new measurement. Writes only the new file
~/ndp_analysis/main04/summary_04/fig_ndp_breakdown_codec.csv; existing
outputs are left as they are.

    usage: python3 codec_breakdown.py
"""
import os

import numpy as np
import pandas as pd
from scipy import stats

R4 = os.path.expanduser("~/ndp_results/main04/results.csv")
OUT = os.path.expanduser("~/ndp_analysis/main04/summary_04/fig_ndp_breakdown_codec.csv")
STAGES = ["tgt_ssd_read_s", "tgt_decode", "tgt_letterbox", "tgt_encode", "tgt_pre_other_s", "tgt_pre_total",
          "tgt_serialize_s", "c0_other_s", "target_pre", "io", "setup_s", "host_pre", "infer", "post_s",
          "cleanup_s", "stage_sum_s", "e2e"]


def ci(x):
    x = np.asarray(x, float)
    m = x.mean(); s = x.std(ddof=1)
    h = stats.t.ppf(0.975, len(x) - 1) * s / np.sqrt(len(x))
    return m, s, m - h, m + h


def main():
    d = pd.read_csv(R4)
    d = d[(d.status == "ok") & (d.pipeline != "BASE")].copy()
    d["cfg"] = d.kernel + "-" + d.pipeline
    d["dt"] = d.extra_opts.fillna("").str.extract(r"-dec_threads (\d+)")[0].fillna("0").astype(int)
    d["codec"] = d.input.str.extract(r"_(h264|hevc|av1)$")[0]
    sel = (((d.exp == "M4A") & d.input.isin(["res1080_h264", "res2160_h264"]))
           | ((d.exp == "M4C") & (d.dt == 0)))
    d = d[sel].copy()
    d["tgt_ssd_read_s"] = d.tgt_ssd_read_ms / 1000
    d["tgt_serialize_s"] = d.tgt_serialize_ms / 1000
    d["tgt_pre_other_s"] = d.tgt_pre_total - d.tgt_decode - d.tgt_letterbox - d.tgt_encode
    d["c0_other_s"] = d.target_pre - d.tgt_ssd_read_s - d.tgt_pre_total - d.tgt_serialize_s
    rows = []
    for (codec, inp, cfg), g in d.groupby(["codec", "input", "cfg"], sort=False):
        r = dict(codec=codec, input=inp, cfg=cfg, block=g.exp.iloc[0], n=len(g),
                 dec=g.tgt_dec_name.iloc[0], dec_threads_set=int(g.tgt_dec_threads_set.iloc[0]),
                 dec_threads_obs=int((g.tgt_thr_peak - g.tgt_thr_base - 1).max()))
        for c in STAGES:
            m, s, lo, hi = ci(g[c])
            r[f"{c}_mean"] = round(m, 4); r[f"{c}_sd"] = round(s, 4)
            r[f"{c}_lo"] = round(lo, 4); r[f"{c}_hi"] = round(hi, 4)
        rows.append(r)
    o = pd.DataFrame(rows)
    co = {"h264": 0, "hevc": 1, "av1": 2}
    o = o.sort_values(["codec", "input", "cfg"], key=lambda s: s.map(co) if s.name == "codec" else s)
    o.to_csv(OUT, index=False)
    print(OUT, o.shape)
    q = o[o.cfg == "sws-q85"]
    print(q[["codec", "input", "n", "dec_threads_obs", "tgt_decode_mean", "tgt_letterbox_mean", "tgt_encode_mean",
             "tgt_pre_other_s_mean", "tgt_pre_total_mean"]].to_string(index=False))


if __name__ == "__main__":
    main()
