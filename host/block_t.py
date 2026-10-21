#!/usr/bin/env python3
"""
Spec 05-H block T: target decode-only times (FFmpeg CLI, cores 2-15,
-threads 0) against the 04 worker's pre_total for the same input.

    usage: python3 block_t.py   (reads ~/ndp_analysis/main05/blockT_*.log)
"""
import glob
import os
import re

import numpy as np
import pandas as pd

R5 = os.path.expanduser("~/ndp_analysis/main05")   # ~/ndp_results is root-owned
R4 = os.path.expanduser("~/ndp_results/main04/results.csv")


def main():
    rows = []
    for p in sorted(glob.glob(os.path.join(R5, "blockT_*.log"))):
        for line in open(p):
            m = re.match(r"rep=(\d+) input=(\S+) frame=(\d+) nlwp=(\S+) bench: utime=([\d.]+)s stime=([\d.]+)s rtime=([\d.]+)s", line)
            if m:
                rows.append(dict(rep=int(m[1]), input=m[2], frames=int(m[3]), nlwp=m[4], utime=float(m[5]),
                                 stime=float(m[6]), rtime=float(m[7])))
    t = pd.DataFrame(rows)
    t["cpu"] = t.utime + t.stime
    s = t.groupby("input").agg(n=("rep", "size"), frames=("frames", "first"),
                               rtime=("rtime", "mean"), rtime_sd=("rtime", "std"),
                               cpu=("cpu", "mean"), cpu_sd=("cpu", "std")).reset_index()
    s["rtime_cv"] = 100 * s.rtime_sd / s.rtime
    s["cpu_cv"] = 100 * s.cpu_sd / s.cpu
    d = pd.read_csv(R4)
    d["cfg"] = d.kernel + "-" + d.pipeline
    d["dt"] = d.extra_opts.fillna("").str.extract(r"-dec_threads (\d+)")[0].fillna("0").astype(int)
    out = []
    for _, r in s.iterrows():
        exp = "M4A" if r.input.endswith("h264") else "M4C"
        for cfg in ("sws-q85", "sws-raw", "darknet-q100"):
            g = d[(d.exp == exp) & (d.input == r.input) & (d.cfg == cfg) & (d.dt == 0)]
            pt = g.tgt_pre_total.mean()
            out.append(dict(input=r.input, decode_rtime_s=round(r.rtime, 3), decode_rtime_cv=round(r.rtime_cv, 2),
                            decode_cpu_s=round(r.cpu, 1), decode_cpu_cv=round(r.cpu_cv, 2),
                            cfg=cfg, n_04=len(g), pre_total_04=round(pt, 2),
                            storage_cpu_04=round(g.tgt_cpu_storage_s.mean(), 1),
                            decode_over_pre_total_pct=round(100 * r.rtime / pt, 1),
                            decode_cpu_over_storage_cpu_pct=round(100 * r.cpu / g.tgt_cpu_storage_s.mean(), 1)))
    o = pd.DataFrame(out)
    o.to_csv(os.path.join(R5, "blockT_summary.csv"), index=False)
    t.to_csv(os.path.join(R5, "blockT_runs.csv"), index=False)
    print(s.round(3).to_string(index=False))
    print(o.to_string(index=False))


if __name__ == "__main__":
    main()
