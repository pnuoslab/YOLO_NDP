#!/usr/bin/env python3
"""
Summarise the main measurement per condition (mean +- sd over repeats).

    usage: python3 analyze_main.py <results_full.csv> [E1 E5 ...]

Reads the CSV that rebuild_results.py writes. Latency is reported two ways,
because they mean different things and the paper has to pick one knowingly:

  stage sum  Baseline: host_pre + infer
             NDP:      target_pre + io + host_pre + infer
             (target_pre is the host-measured 0xC0 round trip.) This is the
             convention the published table 2 used.
  wall e2e   the measured wall clock of the frame loop. It also contains the
             harness's own per-frame box post-processing at the 0.005
             confidence floor, which is not part of either pipeline.

Speedup is Baseline / NDP on the same input and sampling rate, on stage sums.
"""
import csv
import statistics as st
import sys
from collections import defaultdict


def f(r, k):
    try:
        return float(r.get(k, "") or "nan")
    except ValueError:
        return float("nan")


def ms(xs):
    xs = [x for x in xs if x == x]
    if not xs:
        return float("nan"), float("nan")
    return st.mean(xs), (st.stdev(xs) if len(xs) > 1 else 0.0)


def fmt(m, s, nd=2):
    if m != m:
        return "-"
    return f"{m:.{nd}f} ± {s:.{nd}f}"


def stage_sum(r):
    if r["pipeline"] == "BASE":
        return f(r, "host_pre") + f(r, "infer")
    return f(r, "target_pre") + f(r, "io") + f(r, "host_pre") + f(r, "infer")


def main():
    path = sys.argv[1]
    want = (set(sys.argv[2:]) or {"E1", "E5"}) - {"E2", "E3"} or {"E1", "E5"}
    rows = [r for r in csv.DictReader(open(path))
            if r["exp"] in want and r["status"].startswith("ok") and r["frames"]]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["exp"], r["input"], r["sample_rate"], r["pipeline"])].append(r)

    order = {"BASE": 0, "q85": 1, "q100": 2, "raw": 3}
    keys = sorted(groups, key=lambda k: (k[0], k[1], int(k[2]), order[k[3]]))

    base_ss = {}
    for k in keys:
        if k[3] == "BASE":
            base_ss[k[:3]] = ms([stage_sum(r) for r in groups[k]])[0]

    print("## Latency (s, mean ± sd over repeats)\n")
    print("| exp | input | sr | pipeline | n | frames | stage sum | wall e2e | speedup (stage) | CV stage |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for k in keys:
        g = groups[k]
        m, s = ms([stage_sum(r) for r in g])
        we = ms([f(r, "e2e") for r in g])
        b = base_ss.get(k[:3])
        sp = f"{b / m:.2f}×" if b and m and k[3] != "BASE" else ""
        cv = f"{100 * s / m:.1f}%" if m else ""
        print(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {len(g)} | {g[0]['frames']} | "
              f"{fmt(m, s)} | {fmt(*we)} | {sp} | {cv} |")

    print("\n## Baseline preprocessing breakdown (s)\n")
    print("| input | sr | host_pre | decode | copy | mat2img | letterbox | mat2img / host_pre |")
    print("|---|---|---|---|---|---|---|---|")
    for k in keys:
        if k[3] != "BASE":
            continue
        g = groups[k]
        hp = ms([f(r, "host_pre") for r in g])[0]
        parts = [ms([f(r, c) for r in g])[0] for c in
                 ("bl_decode_s", "bl_copy_s", "bl_mat2img_s", "bl_letterbox_s")]
        print(f"| {k[1]} | {k[2]} | {hp:.2f} | " + " | ".join(f"{p:.2f}" for p in parts) +
              f" | {100 * parts[2] / hp:.1f}% |")

    print("\n## NDP: target side and transfer\n")
    print("| input | sr | pipeline | target_pre (host) | pre_total (tgt) | ssd_read ms | serialize ms "
          "| cpu_storage s | transfer MB | storage mem MB | host RSS MB |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for k in keys:
        if k[3] == "BASE":
            continue
        g = groups[k]
        cols = [ms([f(r, c) for r in g])[0] for c in
                ("target_pre", "tgt_pre_total", "tgt_ssd_read_ms", "tgt_serialize_ms",
                 "tgt_cpu_storage_s")]
        tb = ms([f(r, "bytes") / 1e6 for r in g])[0]
        mem = ms([f(r, "tgt_storage_mem_total_mb") for r in g])[0]
        rss = ms([f(r, "host_rss_mb") for r in g])[0]
        print(f"| {k[1]} | {k[2]} | {k[3]} | " + " | ".join(
            f"{c:.2f}" if i < 2 else (f"{c:.1f}" if i < 4 else f"{c:.1f}")
            for i, c in enumerate(cols)) + f" | {tb:.1f} | {mem:.1f} | {rss:.1f} |")

    print("\n## Baseline: transfer, memory, CPU\n")
    print("| input | sr | file MB | host block read MB | target bytes_read MB | host RSS MB | host CPU s (user+sys) |")
    print("|---|---|---|---|---|---|---|")
    for k in keys:
        if k[3] != "BASE":
            continue
        g = groups[k]
        vals = [ms([f(r, "bytes") / 1e6 for r in g])[0],
                ms([f(r, "host_read_bytes") / 1e6 for r in g])[0],
                ms([f(r, "tgt_bytes_read") / 1e6 for r in g])[0],
                ms([f(r, "host_rss_mb") for r in g])[0],
                ms([f(r, "host_cpu_user_s") + f(r, "host_cpu_sys_s") for r in g])[0]]
        print(f"| {k[1]} | {k[2]} | " + " | ".join(f"{v:.1f}" for v in vals) + " |")


def accuracy(path):
    """E2 (scene/accuracy, sws) and E3 (darknet-kernel ablation): mAP per
    condition. The detector is deterministic, so the three repeats must give
    the same mAP; the spread is printed to prove it rather than assumed."""
    rows = [r for r in csv.DictReader(open(path))
            if r["exp"] in ("E2", "E3") and r["status"].startswith("ok")]
    g = defaultdict(list)
    for r in rows:
        g[(r["input"], r["sample_rate"], r["pipeline"], r["kernel"])].append(r)
    seqs = sorted({k[0] for k in g})
    order = {"BASE": 0, "q85": 1, "q100": 2, "raw": 3}

    print("\n## Accuracy: mAP@0.5 / mAP@[.5:.95] (%), and delta vs Baseline (pp)\n")
    print("| seq | sr | pipeline | kernel | mAP50 | mAP50-95 | Δ50 | Δ50-95 | spread over repeats | stage sum s | speedup |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for sq in seqs:
        for sr in ("1", "2", "5"):
            base = g.get((sq, sr, "BASE", "sws"))
            if not base:
                continue
            b50 = ms([f(r, "ap50") for r in base])[0]
            b95 = ms([f(r, "ap5095") for r in base])[0]
            bss = ms([stage_sum(r) for r in base])[0]
            for (s2, r2, p, k), gr in sorted(
                    ((k, v) for k, v in g.items() if k[0] == sq and k[1] == sr),
                    key=lambda kv: (order[kv[0][2]], kv[0][3])):
                a50 = [f(r, "ap50") for r in gr]
                a95 = ms([f(r, "ap5095") for r in gr])[0]
                m50 = ms(a50)[0]
                spread = max(a50) - min(a50)
                ss = ms([stage_sum(r) for r in gr])[0]
                d50 = "" if p == "BASE" else f"{m50 - b50:+.2f}"
                d95 = "" if p == "BASE" else f"{a95 - b95:+.2f}"
                sp = "" if p == "BASE" else f"{bss / ss:.2f}×"
                print(f"| {sq} | {sr} | {p} | {k if p != 'BASE' else '-'} | {m50:.2f} | {a95:.2f} | "
                      f"{d50} | {d95} | {spread:.4f} | {ss:.2f} | {sp} |")


if __name__ == "__main__":
    main()
    if {"E2", "E3"} & set(sys.argv[2:]):
        accuracy(sys.argv[1])
