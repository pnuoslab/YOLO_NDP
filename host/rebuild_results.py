#!/usr/bin/env python3
"""
Rebuild a complete results CSV from a run_all.sh results directory.

Why: collect_row.py fixes the CSV header from the FIRST row it writes and
silently drops any key a later row adds. The first row of a matrix is a
Baseline run, which has no target [NDP-MEASURE] line, so every target
field (pre_total, ssd_read_bytes, cpu_storage_s, storage_mem_total_mb, ...)
was dropped from results.csv for every later NDP row. Nothing was lost:
run_all.sh keeps each run's raw artefacts under logs/ and io/. This script
reassembles every row from those files, with the header taken as the union
of all keys across all rows.

The per-run parsing is collect_row.py's own (imported, not copied), so the
values are exactly the ones that script would have written. The only thing
taken from the existing results.csv is each run's status, which run_all.sh
decided at run time (exit code) and which is not stored anywhere else.

    usage: python3 rebuild_results.py <RESULTS dir> [out.csv]
           default out: <RESULTS dir>/results_full.csv
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect_row as cr  # noqa: E402

STEM = re.compile(r"^(E\d)_(.+)_(BASE|q85|q100|raw)_(sws|darknet)_sr(\d+)_r(\d+)$")
QUALITY = {"BASE": "-", "q85": "85", "q100": "100", "raw": "raw"}


def read_target_measure(path):
    """key=value pairs of the target's [NDP-MEASURE] line.

    The target prints it through SPDK's logger, so the tag sits mid-line:
    "[2026-10-19 06:46:..] ctrlr_bdev.c:1704:...: *NOTICE*: [NDP-MEASURE] run_id=..."
    collect_row.read_measure() only accepts lines that START with the tag,
    which is right for the host's own [MEASURE] line and wrong here - the
    second reason no target field reached results.csv. Only the text after
    the tag is parsed, so the timestamp and source location are not read
    as fields.
    """
    if not os.path.isfile(path):
        return {}
    found = ""
    with open(path, errors="replace") as f:
        for line in f:
            i = line.find("[NDP-MEASURE]")
            if i >= 0:
                found = line[i + len("[NDP-MEASURE]"):]
    return cr.parse_kv(found) if found else {}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(root, "results_full.csv")
    logs, io = os.path.join(root, "logs"), os.path.join(root, "io")

    old = {}
    old_csv = os.path.join(root, "results.csv")
    if os.path.isfile(old_csv):
        for r in csv.DictReader(open(old_csv, newline="")):
            old[r.get("run_id_req", "")] = r

    rows, skipped = [], []
    for name in sorted(os.listdir(logs)):
        if not name.endswith(".log"):
            continue
        stem = name[:-4]
        m = STEM.match(stem)
        if not m:
            skipped.append(stem)
            continue
        exp, inp, pipe, kern, sr, rep = m.groups()
        meas = cr.read_measure(os.path.join(logs, name), "[MEASURE]")
        rid = meas.get("run_id", "")
        prev = old.get(rid, {})

        row = {
            "exp": exp, "input": inp, "pipeline": pipe, "kernel": kern,
            "quality": QUALITY[pipe], "sample_rate": sr, "rep": rep,
            "run_id_req": rid or prev.get("run_id_req", ""),
            "dump": "1" if os.path.isfile(os.path.join(root, "dumps", stem + ".csv")) else "0",
            "status": prev.get("status", "ok" if meas else "fail_no_measure"),
        }
        for f in cr.MEASURE_FIELDS:
            row[f] = meas.get(f, "")
        p = lambda suf: os.path.join(io, stem + suf)
        row.update(cr.blockstat_delta(p(".hstat.before"), p(".hstat.after")))
        row.update(cr.iostat_delta(p(".tgt.before"), p(".tgt.after")))
        for k, v in read_target_measure(p(".tgtmeasure")).items():
            key = "tgt_" + k
            if key in cr.TGT_IO_FIELDS:
                key = "tgt_m_" + k
            row[key] = v
        # File order alone does not say which run came first; sort by run_id.
        row["_order"] = int(rid) if rid.isdigit() else 0
        rows.append(row)

    rows.sort(key=lambda r: r["_order"])
    fixed = cr.COND_FIELDS + cr.MEASURE_FIELDS + cr.HOST_IO_FIELDS + cr.TGT_IO_FIELDS
    extra = sorted({k for r in rows for k in r} - set(fixed) - {"_order"})
    cols = fixed + extra
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    ndp = [r for r in rows if r["pipeline"] != "BASE"]
    with_tgt = [r for r in ndp if r.get("tgt_run_id")]
    print(f"rows: {len(rows)}  columns: {len(cols)}  -> {out}")
    print(f"NDP rows: {len(ndp)}, with target [NDP-MEASURE]: {len(with_tgt)}")
    bad = [r["run_id_req"] for r in with_tgt if r["tgt_run_id"] != r["run_id_req"]]
    print("target run_id mismatches:", bad or "none")
    if skipped:
        print("log files not matching the run naming (skipped):", skipped)


if __name__ == "__main__":
    main()
