#!/usr/bin/env python3
"""
Turn one run's artefacts into one results.csv row.

run_all.sh calls this once per run. Keeping the CSV assembly here rather
than in awk means the column order is defined in exactly one place and a
missing field becomes an empty cell instead of a silently shifted row.

Inputs are given as flags; everything that can be absent (a failed run, a
target that was not reachable) is written as an empty cell with status
saying what happened.

The [MEASURE] line is parsed as key=value pairs, so adding a field to
detector.c only needs that field appended to MEASURE_FIELDS below.
"""
import argparse
import csv
import json
import os
import re
import sys

# Order matters: this is the CSV column order after the condition columns.
MEASURE_FIELDS = [
    "mode", "q", "chroma", "resize", "frames",
    "e2e", "target_pre", "io", "host_pre", "infer",
    "bytes", "frame_min", "frame_avg", "frame_max",
    "host_rss_mb", "host_blob_mb", "ap50", "ap5095",
    "run_id",
    "bl_decode_s", "bl_copy_s", "bl_mat2img_s", "bl_letterbox_s",
    "host_cpu_user_s", "host_cpu_sys_s", "host_cpu_pre_s", "host_cpu_infer_s",
]

COND_FIELDS = ["exp", "input", "pipeline", "kernel", "quality",
               "sample_rate", "rep", "run_id_req", "dump", "status"]

HOST_IO_FIELDS = ["host_read_bytes", "host_write_bytes", "host_io_ticks_ms"]

TGT_IO_FIELDS = ["tgt_bytes_read", "tgt_bytes_written",
                 "tgt_num_read_ops", "tgt_num_write_ops"]

# Whatever the target prints in its own [NDP-MEASURE] line. Unknown keys are
# collected into tgt_extra rather than dropped, so a target-side field added
# later still reaches the CSV without a host-side change.
TGT_MEASURE_PREFIX = "tgt_"


def parse_kv(line):
    """key=value pairs from a [MEASURE]-style line."""
    out = {}
    for m in re.finditer(r"(\w+)=([^\s]+)", line):
        out[m.group(1)] = m.group(2)
    return out


def read_measure(path, tag):
    """key=value pairs that follow the last occurrence of `tag`.

    The tag may sit mid-line: the target prints [NDP-MEASURE] through SPDK's
    logger, behind a timestamp and source location. An earlier version only
    accepted lines that START with the tag, which is right for the host's
    [MEASURE] line and silently dropped every target line. Only the text
    after the tag is parsed, so the logger prefix is never read as fields.
    """
    if not path or not os.path.isfile(path):
        return {}
    found = ""
    with open(path, errors="replace") as f:
        for line in f:
            i = line.find(tag)
            if i >= 0:
                found = line[i + len(tag):].strip()
    return parse_kv(found) if found else {}


def blockstat_delta(before, after):
    """/sys/block/<dev>/stat deltas: read bytes, write bytes, io_ticks ms.

    Field order is the kernel's: read_ios read_merges read_sectors
    read_ticks write_ios write_merges write_sectors write_ticks in_flight
    io_ticks ... Sectors are always 512 B here regardless of the device's
    logical block size.
    """
    def fields(p):
        if not p or not os.path.isfile(p):
            return None
        with open(p) as f:
            parts = f.read().split()
        return [int(x) for x in parts] if len(parts) >= 10 else None

    b, a = fields(before), fields(after)
    if not b or not a:
        return {}
    return {
        "host_read_bytes": (a[2] - b[2]) * 512,
        "host_write_bytes": (a[6] - b[6]) * 512,
        "host_io_ticks_ms": a[9] - b[9],
    }


def iostat_delta(before, after, bdev=None):
    """SPDK bdev_get_iostat deltas.

    Sums every bdev unless a name is given. The JSON shape is
    {"tick_rate":..,"bdevs":[{"name":..,"bytes_read":..,...}]}; older
    builds put the list at the top level, so both are accepted.
    """
    def load(p):
        if not p or not os.path.isfile(p) or os.path.getsize(p) == 0:
            return None
        try:
            with open(p) as f:
                d = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(d, dict):
            d = d.get("bdevs", d.get("result", {}).get("bdevs", []))
        if not isinstance(d, list):
            return None
        acc = {}
        for e in d:
            if not isinstance(e, dict):
                continue
            if bdev and e.get("name") != bdev:
                continue
            for k in ("bytes_read", "bytes_written",
                      "num_read_ops", "num_write_ops"):
                if k in e:
                    acc[k] = acc.get(k, 0) + int(e[k])
        return acc or None

    b, a = load(before), load(after)
    if not b or not a:
        return {}
    return {"tgt_" + k: a[k] - b[k] for k in a if k in b}


def main():
    ap = argparse.ArgumentParser()
    for f in COND_FIELDS:
        ap.add_argument("--" + f, default="")
    ap.add_argument("--log", default="", help="host stdout log of the run")
    ap.add_argument("--tgt-measure", default="",
                    help="file holding the target's [NDP-MEASURE] line")
    ap.add_argument("--host-stat-before", default="")
    ap.add_argument("--host-stat-after", default="")
    ap.add_argument("--tgt-iostat-before", default="")
    ap.add_argument("--tgt-iostat-after", default="")
    ap.add_argument("--tgt-bdev", default="")
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    row = {f: getattr(args, f) for f in COND_FIELDS}

    meas = read_measure(args.log, "[MEASURE]")
    for f in MEASURE_FIELDS:
        row[f] = meas.get(f, "")

    row.update(blockstat_delta(args.host_stat_before, args.host_stat_after))
    row.update(iostat_delta(args.tgt_iostat_before, args.tgt_iostat_after,
                            args.tgt_bdev or None))

    tgt = read_measure(args.tgt_measure, "[NDP-MEASURE]")
    extra = {}
    for k, v in tgt.items():
        key = TGT_MEASURE_PREFIX + k
        if key in TGT_IO_FIELDS:
            # Do not let the target's own line overwrite the iostat delta.
            key = TGT_MEASURE_PREFIX + "m_" + k
        extra[key] = v

    # A run that produced no [MEASURE] line did not complete, whatever the
    # caller thought the exit status was.
    if not meas and str(row["status"]).startswith("ok"):
        row["status"] = "fail_no_measure"

    # The host's own run_id must come back unchanged; if it does not, the
    # row cannot be paired with a target row and the pairing is unsafe.
    if meas.get("run_id") and row["run_id_req"] and \
            meas["run_id"] != row["run_id_req"]:
        row["status"] = "fail_runid_mismatch"
    if tgt.get("run_id") and row["run_id_req"] and \
            tgt["run_id"] != row["run_id_req"]:
        row["status"] = "fail_tgt_runid_mismatch"

    row.update(extra)
    fixed = COND_FIELDS + MEASURE_FIELDS + HOST_IO_FIELDS + TGT_IO_FIELDS

    # The header is the union of every row's keys. An earlier version froze
    # it at the first row - a Baseline run, with no target line - and then
    # dropped every target field of every later NDP row. Now, when a row
    # brings a key the file does not have, the whole file is rewritten with
    # the wider header (a few hundred rows; the cost is nothing next to a
    # run). Written to a temp file and renamed, so a crash cannot leave a
    # half-written results.csv.
    old_rows, old_cols = [], []
    if os.path.isfile(args.csv) and os.path.getsize(args.csv) > 0:
        with open(args.csv, newline="") as f:
            rd = csv.DictReader(f)
            old_cols = list(rd.fieldnames or [])
            old_rows = list(rd)
    new_keys = [k for k in sorted(row) if k not in old_cols and k not in fixed]
    if old_cols and not new_keys and all(k in old_cols for k in fixed):
        with open(args.csv, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=old_cols, extrasaction="ignore").writerow(row)
    else:
        extra_cols = sorted({k for r in old_rows + [row] for k in r} - set(fixed))
        cols = fixed + extra_cols
        tmp = args.csv + ".tmp"
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in old_rows + [row]:
                w.writerow(r)
        os.replace(tmp, args.csv)

    print(f"[row] {row['exp']} {row['input']} {row['pipeline']} "
          f"sr={row['sample_rate']} rep={row['rep']} "
          f"run_id={row['run_id_req']} status={row['status']} "
          f"e2e={row.get('e2e','')} ap50={row.get('ap50','')}")
    return 0 if str(row["status"]).startswith("ok") else 0


if __name__ == "__main__":
    sys.exit(main())
