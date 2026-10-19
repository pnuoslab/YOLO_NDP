#!/usr/bin/env python3
"""
Compare two directories of darknet_repro run-0 dumps (f%03d.rgb, 416x416
RGB24) frame by frame: how many frames differ, how many pixels per frame,
where (canvas columns / rows), and the max / mean absolute difference.

usage: cmp_rgb_dirs.py <label> <dirA> <dirB> [<label> <dirA> <dirB> ...]
"""
import glob
import os
import sys

N = 416


def cmp(a, b):
    files = sorted(glob.glob(os.path.join(a, 'f*.rgb')))
    diff_px = frames_diff = 0
    cols, rows = set(), []
    maxabs = sabs = nb = 0
    per_frame = []
    for f in files:
        x = open(f, 'rb').read()
        y = open(os.path.join(b, os.path.basename(f)), 'rb').read()
        fd = 0
        for p in range(N * N):
            e = [abs(x[3 * p + k] - y[3 * p + k]) for k in range(3)]
            if any(e):
                fd += 1
                cols.add(p % N)
                rows.append(p // N)
                maxabs = max(maxabs, max(e))
                for v in e:
                    if v:
                        sabs += v
                        nb += 1
        per_frame.append(fd)
        diff_px += fd
        frames_diff += fd > 0
    s = "frames=%d differing=%d px_total=%d" % (len(files), frames_diff, diff_px)
    if diff_px:
        nz = [v for v in per_frame if v]
        s += " px/frame=%d..%d (%.4f%% of canvas) cols=%d..%d rows=%d..%d max_abs=%d mean_abs=%.1f" % (
            min(nz), max(nz), 100.0 * diff_px / (len(files) * N * N),
            min(cols), max(cols), min(rows), max(rows), maxabs, sabs / nb)
    return s


args = sys.argv[1:]
for i in range(0, len(args), 3):
    print("%-40s %s" % (args[i], cmp(args[i + 1], args[i + 2])))
