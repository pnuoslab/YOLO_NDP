#!/usr/bin/env python3
"""
Spec 03c: follow-up of the person-level analysis (03a). Offline only, on the
2026-10-19 MOT dumps; 03a's definitions are reused unchanged
(person_analysis.py: valid GT, conf >= 0.25, IoU >= 0.5 greedy 1:1,
processed = frame_idx % N == 0, reuse of the last processed frame).

  3-1  conditional loss by height / visibility bin, stratified by sequence,
       with 1 s block-bootstrap CIs
  3-2  failure modes A-E of "BASE only" boxes (and of "cmp only", mirrored)
  3-3  box shift of the two matched boxes (source px and network-input px)
  3-4  extra-missed people by BASE hit count, hypergeometric expectation,
       first-detection delay split into "within N-1 frames" / "beyond"
  3-5  matching ambiguity and greedy vs Hungarian
  3-6  sequence means, Wilson CIs for track proportions

Randomness: block bootstrap only, numpy default_rng(SEED + sequence index),
SEED = 0, B = 1000.

    usage: python3 person_followup.py   -> ~/ndp_analysis/person/out_03c/
           python3 person_followup.py --dumps ~/ndp_results/main04/dumps \
                                      --out ~/ndp_analysis/person/out_03c_04

--dumps: a directory holding the sr=1 dumps. 10/19 names
(E2_<seq>_<pipe>_sws_sr1_r1.csv, E3_<seq>_<pipe>_darknet_sr1_r1.csv) are
used if present, otherwise spec 04 block G names
(M4G_<seq>_<pipe>_<kernel>_sr1_r1.csv). Default: the 10/19 dumps.
Paths are the only thing the arguments change; every definition is as 03c.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment
from scipy.special import gammaln

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import person_analysis as pa  # noqa: E402

OUT = os.path.expanduser("~/ndp_analysis/person/out_03c")
SEED, B = 0, 1000
CMP = [("q85", "sws"), ("q100", "sws"), ("raw", "sws"), ("q85", "darknet"), ("q100", "darknet"), ("raw", "darknet")]
NAME = {("BASE", "sws"): "BASE"} | {c: f"{c[1]}-{c[0]}" for c in CMP}
H_BINS = [0, 50, 100, 200, np.inf]
H_LAB = ["<50", "50-100", "100-200", ">=200"]
V_BINS = [0.25, 0.5, 0.75, np.inf]
V_LAB = ["0.25-0.5", "0.5-0.75", ">=0.75"]
K_BINS = [1, 2, 5, 20, np.inf]
K_LAB = ["1", "2-4", "5-19", ">=20"]
MIN_BASE_HITS = 200


def log(*a):
    print(*a, flush=True)


# ── matching ───────────────────────────────────────────────────────────
def hungarian(det_xy, gt_xy):
    gm = np.full(len(gt_xy), -1, dtype=np.int64)
    if len(det_xy) == 0 or len(gt_xy) == 0:
        return gm
    m = pa.iou_matrix(det_xy, gt_xy)
    cost = np.where(m >= pa.IOU, 1.0 - m, 1e6)
    r, c = linear_sum_assignment(cost)
    ok = m[r, c] >= pa.IOU
    gm[c[ok]] = r[ok]
    return gm


def match(gt, frames_all, n, N, method):
    """Per GT box: matched detection's conf and box (NaN if none), with
    sampling N and reuse. frames_all: frame -> (xy, conf) of ALL dets; only
    conf >= 0.25 take part in matching."""
    hit = np.zeros(len(gt), bool)
    mconf = np.full(len(gt), np.nan)
    mbox = np.full((len(gt), 4), np.nan)
    by_f = gt.groupby("frame_idx").indices
    gxy_all = gt[["x1", "y1", "x2", "y2"]].to_numpy()
    empty = (np.zeros((0, 4)), np.zeros(0))
    for f, gi in by_f.items():
        p = f - (f % N)
        xy, cf = frames_all.get(p, empty)
        keep = cf >= pa.CONF
        dxy, dc = xy[keep], cf[keep]
        if method == "greedy":
            gm, _ = pa.greedy(dxy, dc, gxy_all[gi])
        else:
            gm = hungarian(dxy, gxy_all[gi])
        ok = gm >= 0
        hit[gi[ok]] = True
        mconf[gi[ok]] = dc[gm[ok]]
        mbox[gi[ok]] = dxy[gm[ok]]
    proc = gt.frame_idx.to_numpy() % N == 0
    return hit, proc, mconf, mbox


def frames_of(dump):
    out = {}
    for f, g in dump.groupby("frame_idx"):
        out[f] = (g[["x", "y", "x2", "y2"]].to_numpy(), g.conf.to_numpy())
    return out


# ── statistics helpers ─────────────────────────────────────────────────
def boot_weights(n, fps, rng):
    bl = int(round(fps))
    nb = int(np.ceil(n / bl))
    draws = rng.integers(0, nb, size=(B, nb))
    W = np.zeros((B, n), dtype=np.float32)
    for b in range(B):
        W[b] = np.repeat(np.bincount(draws[b], minlength=nb), bl)[:n]
    return W


def per_frame(values, frames, n):
    return np.bincount(frames, weights=values.astype(float), minlength=n)


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def pct(a, b):
    return 100.0 * a / b if b else np.nan


def ratio_ci(num_list, den_list):
    """Pooled ratio sum(num)/sum(den) and its bootstrap CI; each list item is
    (point, boot_vector) for one sequence."""
    pn = sum(x[0] for x in num_list); pd_ = sum(x[0] for x in den_list)
    bn = sum(x[1] for x in num_list); bd = sum(x[1] for x in den_list)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = 100 * bn / bd
    return 100 * pn / pd_ if pd_ else np.nan, np.nanpercentile(r, 2.5), np.nanpercentile(r, 97.5)


def log_comb(a, b):
    return gammaln(a + 1) - gammaln(b + 1) - gammaln(a - b + 1)


# ── main ───────────────────────────────────────────────────────────────
def dump_file(dumps_dir, seq, pipe, kern):
    if dumps_dir is None:
        return pa.dump_path(seq, pipe, kern, 1)
    old = os.path.join(dumps_dir, os.path.basename(pa.dump_path(seq, pipe, kern, 1)))
    if os.path.isfile(old):
        return old
    return os.path.join(dumps_dir, f"M4G_{seq}_{pipe}_{kern}_sr1_r1.csv")


def main():
    global OUT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dumps", default=None, help="directory of sr=1 dumps (default: the 10/19 dumps)")
    ap.add_argument("--out", default=OUT, help=f"output directory (default: {OUT})")
    args = ap.parse_args()
    OUT = os.path.expanduser(args.out)
    dumps_dir = os.path.expanduser(args.dumps) if args.dumps else None
    os.makedirs(OUT, exist_ok=True)
    used = []
    bins_rows, fm_rows, fmA_rows, shift_rows, pos_rows = [], [], [], [], []
    km_rows, delay_rows, stab_rows, amb_rows, gh_rows, gh_examples = [], [], [], [], [], []
    seqmean_rows = []
    pooled = {}   # (key) -> list of (point, boot) for pooled CIs

    for si, seq in enumerate(pa.SEQS):
        n, fps = pa.seq_info(seq)
        g = pa.SEQS[seq]
        scale = min(416.0 / g["vw"], 416.0 / g["vh"])     # letterbox scale (network px per source px)
        gt = pa.load_gt(seq)
        gt["hbin"] = pd.cut(gt.hpx, H_BINS, right=False, labels=H_LAB)
        gt["vbin"] = pd.cut(gt.visibility, V_BINS, right=False, labels=V_LAB)
        fr = gt.frame_idx.to_numpy()
        W = boot_weights(n, fps, np.random.default_rng(SEED + si))
        log(seq, "frames", n, "gt", len(gt))

        dumps = {}
        for c in [("BASE", "sws")] + CMP:
            f_ = dump_file(dumps_dir, seq, c[0], c[1])
            used.append(dict(seq=seq, cond=NAME[c], file_1019=os.path.basename(pa.dump_path(seq, c[0], c[1], 1)),
                             file_used=f_))
            dumps[c] = frames_of(pa.load_dump(f_))
        M = {}
        for c in [("BASE", "sws")] + CMP:
            for N in (pa.NS if c[1] == "sws" else [1]):
                for meth in ("greedy", "hungarian"):
                    M[(c, N, meth)] = match(gt, dumps[c], n, N, meth)
        log(seq, "matched")
        bh, _, bconf, bbox = M[(("BASE", "sws"), 1, "greedy")]
        tid = gt.track_id.to_numpy()

        # ---- 3-1 conditional loss by bin ----
        for c in CMP:
            ch, _, cconf, cbox = M[(c, 1, "greedy")]
            bo = bh & ~ch; co = ~bh & ch
            for by, col, labs in (("height", "hbin", H_LAB), ("visibility", "vbin", V_LAB)):
                for lab in labs:
                    sel = (gt[col] == lab).to_numpy()
                    vals = dict(n=sel, base_hit=bh & sel, cmp_hit=ch & sel, base_only=bo & sel, cmp_only=co & sel)
                    pf = {k: per_frame(v, fr, n) for k, v in vals.items()}
                    pt = {k: v.sum() for k, v in pf.items()}
                    bt = {k: W @ v for k, v in pf.items()}
                    for k in pf:
                        pooled.setdefault((NAME[c], by, lab, k), []).append((pt[k], bt[k]))
                    if pt["base_hit"] >= MIN_BASE_HITS:
                        cl, lo, hi = ratio_ci([(pt["base_only"], bt["base_only"])], [(pt["base_hit"], bt["base_hit"])])
                        cond_loss = f"{cl:.2f} [{lo:.2f}, {hi:.2f}]"
                        cg = pct(pt["cmp_only"], pt["cmp_hit"])
                        cond_gain = f"{cg:.2f}"
                    else:
                        cond_loss = cond_gain = "표본 부족"
                    bins_rows.append(dict(cond=NAME[c], seq=seq, by=by, bin=lab, gt=int(pt["n"]),
                                          base_recall=round(pct(pt["base_hit"], pt["n"]), 2),
                                          base_hits=int(pt["base_hit"]), base_only=int(pt["base_only"]),
                                          cmp_only=int(pt["cmp_only"]),
                                          cond_loss_pct=cond_loss, cond_gain_pct=cond_gain,
                                          net_loss_pct_03a=round(pct(pt["base_only"] - pt["cmp_only"], pt["n"]), 2)))

        # ---- 3-6 inputs: per-frame hits for recall / box-rate CIs (N=1) ----
        pf_n = per_frame(np.ones(len(gt)), fr, n)
        pooled.setdefault(("rec", "n"), []).append((pf_n.sum(), W @ pf_n))
        pf_b = per_frame(bh, fr, n)
        pooled.setdefault(("rec", "BASE"), []).append((pf_b.sum(), W @ pf_b))
        for c in CMP:
            ch = M[(c, 1, "greedy")][0]
            for k, v in (("hit", ch), ("base_only", bh & ~ch), ("cmp_only", ~bh & ch)):
                pfv = per_frame(v, fr, n)
                pooled.setdefault(("rec", NAME[c], k), []).append((pfv.sum(), W @ pfv))

        # ---- 3-2 failure modes ----
        def classify(miss_idx, other):
            """miss_idx: GT indices the condition missed; other: frames_all of
            that condition (whose detections we inspect)."""
            out = []
            gxy = gt[["x1", "y1", "x2", "y2"]].to_numpy()
            empty = (np.zeros((0, 4)), np.zeros(0))
            for i in miss_idx:
                xy, cf = other.get(fr[i], empty)
                if len(xy) == 0:
                    out.append(("E", np.nan)); continue
                iou = pa.iou_matrix(xy, gxy[i:i + 1])[:, 0]
                hi_c = cf >= pa.CONF
                if np.any((iou >= 0.5) & ~hi_c):
                    out.append(("A", cf[(iou >= 0.5) & ~hi_c].max()))
                elif np.any(hi_c & (iou >= 0.3) & (iou < 0.5)):
                    out.append(("B", np.nan))
                elif np.any(hi_c & (iou >= 0.5)):
                    out.append(("C", np.nan))
                elif np.any(~hi_c & (iou >= 0.3) & (iou < 0.5)):
                    out.append(("D", np.nan))
                else:
                    out.append(("E", np.nan))
            return out

        for c in CMP:
            ch, _, cconf, cbox = M[(c, 1, "greedy")]
            for side, miss, other, ref_conf in (("base_only", np.nonzero(bh & ~ch)[0], dumps[c], bconf),
                                                ("cmp_only", np.nonzero(~bh & ch)[0], dumps[("BASE", "sws")], cconf)):
                cls = classify(miss, other)
                kinds = np.array([k for k, _ in cls]) if cls else np.array([])
                for k in "ABCDE":
                    fm_rows.append(dict(cond=NAME[c], side=side, seq=seq, mode=k, count=int((kinds == k).sum()),
                                        total=len(kinds)))
                if side == "base_only":
                    a_sel = kinds == "A"
                    a_conf = np.array([v for k, v in cls if k == "A"])
                    fmA_rows.append(pd.DataFrame(dict(cond=NAME[c], seq=seq, cmp_conf=a_conf,
                                                      base_conf=ref_conf[miss[a_sel]])))

        # ---- 3-3 box shift (both hit) ----
        gcx = ((gt.x1 + gt.x2) / 2).to_numpy(); gcy = ((gt.y1 + gt.y2) / 2).to_numpy()
        third = np.digitize(gcx, [g["cw"] / 3, 2 * g["cw"] / 3])     # 0 left 1 centre 2 right
        half = (gcy >= g["ch"] / 2).astype(int)                        # 0 top 1 bottom
        for c in CMP:
            ch, _, cconf, cbox = M[(c, 1, "greedy")]
            both = bh & ch
            b, x = bbox[both], cbox[both]
            d = dict(dcx=((x[:, 0] + x[:, 2]) - (b[:, 0] + b[:, 2])) / 2,
                     dcy=((x[:, 1] + x[:, 3]) - (b[:, 1] + b[:, 3])) / 2,
                     dw=(x[:, 2] - x[:, 0]) - (b[:, 2] - b[:, 0]),
                     dh=(x[:, 3] - x[:, 1]) - (b[:, 3] - b[:, 1]))
            fb = fr[both]
            cnt = per_frame(np.ones(both.sum()), fb, n); bc = W @ cnt
            for k, v in d.items():
                s = per_frame(v, fb, n); bs = W @ s
                with np.errstate(invalid="ignore", divide="ignore"):
                    bm = bs / bc
                pooled.setdefault((NAME[c], "shift", k, "sum"), []).append((s.sum(), bs))
                pooled.setdefault((NAME[c], "shift", k, "cnt"), []).append((cnt.sum(), bc))
                m = v.mean(); lo, hi = np.nanpercentile(bm, [2.5, 97.5])
                shift_rows.append(dict(cond=NAME[c], seq=seq, metric=k, n=len(v), scale=round(scale, 4),
                                       mean_px=round(m, 3), ci_lo_px=round(lo, 3), ci_hi_px=round(hi, 3),
                                       sd_px=round(v.std(ddof=1), 3), median_px=round(np.median(v), 3),
                                       mean_net=round(m * scale, 3), ci_lo_net=round(lo * scale, 3),
                                       ci_hi_net=round(hi * scale, 3), sd_net=round(v.std(ddof=1) * scale, 3),
                                       systematic=bool((lo > 0 or hi < 0) and abs(m * scale) >= 0.25)))
            for t in range(3):
                for h in range(2):
                    s = (third[both] == t) & (half[both] == h)
                    pos_rows.append(dict(cond=NAME[c], seq=seq, x_third=["left", "centre", "right"][t],
                                         y_half=["top", "bottom"][h], n=int(s.sum()),
                                         dcx_mean_net=round(d["dcx"][s].mean() * scale, 3) if s.any() else np.nan,
                                         dcy_mean_net=round(d["dcy"][s].mean() * scale, 3) if s.any() else np.nan))

        # ---- 3-4 tracks: extra-missed by k, expectation, delay split ----
        g2 = gt.assign(bh=bh)
        k_hits = g2[g2.bh].groupby("track_id").size()
        base_first = g2[g2.bh].groupby("track_id").frame_idx.min()
        tracks = np.array(sorted(gt.track_id.unique()))
        app = gt.groupby("track_id").frame_idx.apply(np.array)
        conds = [(("BASE", "sws"), N) for N in (2, 5)] + [(c, N) for c in CMP if c[1] == "sws" for N in pa.NS] \
            + [(c, 1) for c in CMP if c[1] == "darknet"]
        for c, N in conds:
            h, proc, _, _ = M[(c, N, "greedy")]
            gg = gt.assign(h=h & proc)
            hit_p = gg[gg.h].groupby("track_id").size()
            first_p = gg[gg.h].groupby("track_id").frame_idx.min()
            for t in tracks:
                if t not in k_hits.index:
                    continue
                k = int(k_hits[t]); kb = K_LAB[np.digitize(k, K_BINS[1:-1])] if k < 20 else ">=20"
                if k == 1:
                    kb = "1"
                missed = int(hit_p.get(t, 0)) == 0
                fa = app[t]; a = len(fa); p = int((fa % N == 0).sum())
                expP = np.exp(log_comb(a - p, k) - log_comb(a, k)) if (N > 1 and k <= a - p) else 0.0
                fh = first_p.get(t, np.nan); bf = base_first[t]
                dl = fh - bf if fh == fh else np.nan
                km_rows.append(dict(cond=NAME[c], N=N, seq=seq, track_id=int(t), k=k, kbin=kb, extra_missed=missed,
                                    exp_missed_hyper=expP if c == ("BASE", "sws") else np.nan,
                                    delay_frames=dl, delay_s=dl / fps if dl == dl else np.nan,
                                    within_bound=(dl <= N - 1) if dl == dl else np.nan))
        # detection stability: BASE re-hit within the next N frames after its first hit
        bh_set = set(zip(tid[bh], fr[bh]))
        for t in base_first.index:
            bf = base_first[t]; k = int(k_hits[t])
            for N in (2, 5):
                again = any((t, bf + j) in bh_set for j in range(1, N + 1))
                stab_rows.append(dict(seq=seq, track_id=int(t), k=k, N=N, rehit_within_N=again))

        # ---- 3-5 ambiguity and greedy vs Hungarian ----
        gxy = gt[["x1", "y1", "x2", "y2"]].to_numpy()
        by_f = gt.groupby("frame_idx").indices
        for c in [("BASE", "sws")] + CMP:
            n_det = amb_det = 0; gt_amb = 0; pair_iou = []
            for f, gi in by_f.items():
                xy, cf = dumps[c].get(f, (np.zeros((0, 4)), np.zeros(0)))
                xy = xy[cf >= pa.CONF]
                if len(xy) == 0:
                    continue
                m = pa.iou_matrix(xy, gxy[gi])
                cand = m >= pa.IOU
                n_det += len(xy)
                multi = cand.sum(axis=1) >= 2
                amb_det += int(multi.sum())
                gt_amb += int((cand.sum(axis=0) >= 2).sum())
                for di in np.nonzero(multi)[0]:
                    js = np.nonzero(cand[di])[0]
                    gg_ = pa.iou_matrix(gxy[gi][js], gxy[gi][js])
                    pair_iou.extend(gg_[np.triu_indices(len(js), 1)].tolist())
            pi = np.array(pair_iou)
            amb_rows.append(dict(cond=NAME[c], seq=seq, dets=n_det, amb_dets=amb_det,
                                 amb_det_pct=round(pct(amb_det, n_det), 3), gt=len(gt), amb_gt=gt_amb,
                                 amb_gt_pct=round(pct(gt_amb, len(gt)), 3),
                                 gt_gt_iou_median=round(np.median(pi), 3) if len(pi) else np.nan,
                                 gt_gt_iou_p10=round(np.quantile(pi, 0.1), 3) if len(pi) else np.nan,
                                 gt_gt_iou_p90=round(np.quantile(pi, 0.9), 3) if len(pi) else np.nan))
        bhh = M[(("BASE", "sws"), 1, "hungarian")][0]
        for c, N in [(("BASE", "sws"), N) for N in pa.NS] + [(c, N) for c in CMP if c[1] == "sws" for N in pa.NS] \
                + [(c, 1) for c in CMP if c[1] == "darknet"]:
            r = dict(cond=NAME[c], N=N, seq=seq)
            for meth, bref in (("greedy", bh), ("hungarian", bhh)):
                h, proc, _, _ = M[(c, N, meth)]
                base_caught = set(tid[bref])
                caught = set(tid[h & proc])
                r[f"{meth}_extra_missed"] = len(base_caught - caught)
                r[f"{meth}_gt_proc"] = int(proc.sum()); r[f"{meth}_hit_proc"] = int((h & proc).sum())
                if N == 1 and c != ("BASE", "sws"):
                    r[f"{meth}_both"] = int((bref & h).sum()); r[f"{meth}_base_only"] = int((bref & ~h).sum())
                    r[f"{meth}_cmp_only"] = int((~bref & h).sum()); r[f"{meth}_neither"] = int((~bref & ~h).sum())
            gh_rows.append(r)
            # examples where the two methods disagree on a box
            # tracks whose extra-missed status differs between the two methods
            hgp = M[(c, N, "greedy")][0] & M[(c, N, "greedy")][1]
            hhp = M[(c, N, "hungarian")][0] & M[(c, N, "hungarian")][1]
            mg = set(tid[bh]) - set(tid[hgp]); mh = set(tid[bhh]) - set(tid[hhp])
            for t in sorted(mg ^ mh):
                gh_examples.append(dict(kind="track", cond=NAME[c], N=N, seq=seq, frame_idx=-1, track_id=int(t),
                                        greedy_hit=t not in mg, hungarian_hit=t not in mh))
            hg = M[(c, N, "greedy")][0]; hh = M[(c, N, "hungarian")][0]
            dif = np.nonzero(hg != hh)[0]
            for i in dif[:5]:
                gh_examples.append(dict(kind="box", cond=NAME[c], N=N, seq=seq, frame_idx=int(fr[i]), track_id=int(tid[i]),
                                        greedy_hit=bool(hg[i]), hungarian_hit=bool(hh[i])))
        log(seq, "done")

    # ── assemble ───────────────────────────────────────────────────────
    bins = pd.DataFrame(bins_rows)
    rows = []
    for (cond, by, lab, k) in [k for k in pooled if len(k) == 4 and k[3] == "n" and k[1] in ("height", "visibility")]:
        def P(x):
            return pooled[(cond, by, lab, x)]
        cl, lo, hi = ratio_ci(P("base_only"), P("base_hit"))
        cg, glo, ghi = ratio_ci(P("cmp_only"), P("cmp_hit"))
        ntot = sum(x[0] for x in P("n")); bht = sum(x[0] for x in P("base_hit"))
        rows.append(dict(cond=cond, seq="ALL", by=by, bin=lab, gt=int(ntot),
                         base_recall=round(pct(bht, ntot), 2), base_hits=int(bht),
                         base_only=int(sum(x[0] for x in P("base_only"))), cmp_only=int(sum(x[0] for x in P("cmp_only"))),
                         cond_loss_pct=f"{cl:.2f} [{lo:.2f}, {hi:.2f}]", cond_gain_pct=f"{cg:.2f} [{glo:.2f}, {ghi:.2f}]",
                         net_loss_pct_03a=round(pct(sum(x[0] for x in P("base_only")) - sum(x[0] for x in P("cmp_only")), ntot), 2)))
    bins = pd.concat([bins, pd.DataFrame(rows)], ignore_index=True)
    bins.to_csv(os.path.join(OUT, "bins_conditional.csv"), index=False)

    fm = pd.DataFrame(fm_rows)
    fma = fm.groupby(["cond", "side", "mode"])[["count"]].sum().reset_index()
    tot = fm.groupby(["cond", "side", "seq"]).total.first().groupby(["cond", "side"]).sum()
    fma["total"] = [tot[(a, b)] for a, b in zip(fma.cond, fma.side)]
    fma["pct"] = (100 * fma["count"] / fma.total).round(2)
    fma["seq"] = "ALL"
    fm["pct"] = (100 * fm["count"] / fm.total.replace(0, np.nan)).round(2)
    pd.concat([fm, fma], ignore_index=True).to_csv(os.path.join(OUT, "failure_modes.csv"), index=False)
    fmA = pd.concat(fmA_rows, ignore_index=True)
    fmA_s = fmA.groupby("cond").agg(n=("cmp_conf", "size"),
                                    cmp_conf_med=("cmp_conf", "median"),
                                    cmp_conf_p10=("cmp_conf", lambda s: s.quantile(0.1)),
                                    cmp_conf_p90=("cmp_conf", lambda s: s.quantile(0.9)),
                                    base_conf_med=("base_conf", "median"),
                                    base_conf_p10=("base_conf", lambda s: s.quantile(0.1)),
                                    base_conf_p90=("base_conf", lambda s: s.quantile(0.9))).round(3).reset_index()
    fmA_s.to_csv(os.path.join(OUT, "failure_modes_A_conf.csv"), index=False)

    sh = pd.DataFrame(shift_rows)
    rows = []
    for cond in sh.cond.unique():
        for k in ("dcx", "dcy", "dw", "dh"):
            s = pooled[(cond, "shift", k, "sum")]; cn = pooled[(cond, "shift", k, "cnt")]
            pt = sum(x[0] for x in s) / sum(x[0] for x in cn)
            bm = sum(x[1] for x in s) / sum(x[1] for x in cn)
            lo, hi = np.nanpercentile(bm, [2.5, 97.5])
            rows.append(dict(cond=cond, seq="ALL(px)", metric=k, n=int(sum(x[0] for x in cn)), mean_px=round(pt, 3),
                             ci_lo_px=round(lo, 3), ci_hi_px=round(hi, 3)))
    sh = pd.concat([sh, pd.DataFrame(rows)], ignore_index=True)
    sh.to_csv(os.path.join(OUT, "box_shift.csv"), index=False)
    pd.DataFrame(pos_rows).to_csv(os.path.join(OUT, "box_shift_by_position.csv"), index=False)

    km = pd.DataFrame(km_rows)
    kms = km.groupby(["cond", "N", "kbin"]).agg(tracks=("track_id", "size"), extra_missed=("extra_missed", "sum"),
                                                exp_missed_hyper=("exp_missed_hyper", "sum")).reset_index()
    kms["extra_missed_pct"] = (100 * kms.extra_missed / kms.tracks).round(2)
    kms.to_csv(os.path.join(OUT, "extra_missed_by_k.csv"), index=False)
    km.to_csv(os.path.join(OUT, "tracks_03c.csv"), index=False)
    rows = []
    for (cond, N), g_ in km[km.delay_frames.notna()].groupby(["cond", "N"]):
        for part, s in (("earlier", g_.delay_frames < 0),
                        ("within_N-1", (g_.delay_frames >= 0) & (g_.delay_frames <= N - 1)),
                        ("beyond", g_.delay_frames > N - 1)):
            d = g_[s].delay_s
            rows.append(dict(cond=cond, N=N, part=part, tracks=int(s.sum()), median_s=round(d.median(), 3) if len(d) else np.nan,
                             p90_s=round(d.quantile(0.9), 3) if len(d) else np.nan, max_s=round(d.max(), 3) if len(d) else np.nan))
    st = pd.DataFrame(stab_rows)
    st["kbin"] = pd.cut(st.k, K_BINS, right=False, labels=K_LAB)
    sts = st.groupby(["N", "kbin"], observed=False).agg(tracks=("track_id", "size"),
                                                        rehit_pct=("rehit_within_N", "mean")).reset_index()
    sts["rehit_pct"] = (100 * sts.rehit_pct).round(2)
    dl = pd.DataFrame(rows)
    dl.to_csv(os.path.join(OUT, "delay_split.csv"), index=False)
    sts.to_csv(os.path.join(OUT, "detection_stability.csv"), index=False)

    am = pd.DataFrame(amb_rows)
    am.to_csv(os.path.join(OUT, "matching_ambiguity.csv"), index=False)
    gh = pd.DataFrame(gh_rows)
    gh.to_csv(os.path.join(OUT, "greedy_vs_hungarian.csv"), index=False)
    pd.DataFrame(gh_examples).to_csv(os.path.join(OUT, "greedy_vs_hungarian_examples.csv"), index=False)

    # ---- 3-6 sequence means and Wilson ----
    km1 = km.copy()
    rows = []
    for (cond, N), g_ in km1.groupby(["cond", "N"]):
        per = g_.groupby("seq").extra_missed.mean() * 100
        k_, n_ = int(g_.extra_missed.sum()), len(g_)
        lo, hi = wilson(k_, n_)
        ghs = gh[(gh.cond == cond) & (gh.N == N)]
        rec_seq = (100 * ghs.greedy_hit_proc / ghs.greedy_gt_proc)
        r = dict(cond=cond, N=N, extra_missed=k_, base_caught=n_, extra_missed_pct_pooled=round(pct(k_, n_), 2),
                 wilson_lo=round(lo, 2), wilson_hi=round(hi, 2), extra_missed_pct_seqmean=round(per.mean(), 2),
                 recall_pct_pooled=round(pct(ghs.greedy_hit_proc.sum(), ghs.greedy_gt_proc.sum()), 2),
                 recall_pct_seqmean=round(rec_seq.mean(), 2))
        if N == 1 and cond != "BASE":
            for k in ("base_only", "cmp_only"):
                r[f"{k}_pct_pooled"] = round(pct(ghs[f"greedy_{k}"].sum(), ghs.greedy_gt_proc.sum()), 3)
                r[f"{k}_pct_seqmean"] = round((100 * ghs[f"greedy_{k}"] / ghs.greedy_gt_proc).mean(), 3)
        rows.append(r)
    sm = pd.DataFrame(rows)
    # recall difference vs BASE and box rates (N=1), pooled, block bootstrap
    nP = pooled[("rec", "n")]; bP = pooled[("rec", "BASE")]
    n_pt = sum(x[0] for x in nP); n_bt = sum(x[1] for x in nP)
    b_pt = sum(x[0] for x in bP); b_bt = sum(x[1] for x in bP)
    rows = []
    for c in CMP:
        h = pooled[("rec", NAME[c], "hit")]
        h_pt = sum(x[0] for x in h); h_bt = sum(x[1] for x in h)
        d_pt = 100 * (h_pt - b_pt) / n_pt; d_bt = 100 * (h_bt - b_bt) / n_bt
        r = dict(cond=NAME[c], recall_diff_pp=round(d_pt, 3),
                 recall_diff_lo=round(np.percentile(d_bt, 2.5), 3), recall_diff_hi=round(np.percentile(d_bt, 97.5), 3))
        for k in ("base_only", "cmp_only"):
            v = pooled[("rec", NAME[c], k)]
            pt = 100 * sum(x[0] for x in v) / n_pt; bt = 100 * sum(x[1] for x in v) / n_bt
            r[f"{k}_pct"] = round(pt, 3); r[f"{k}_lo"] = round(np.percentile(bt, 2.5), 3)
            r[f"{k}_hi"] = round(np.percentile(bt, 97.5), 3)
        rows.append(r)
    sm = sm.merge(pd.DataFrame(rows).assign(N=1), on=["cond", "N"], how="left")
    sm.to_csv(os.path.join(OUT, "seq_mean_summary.csv"), index=False)
    pd.DataFrame(used).to_csv(os.path.join(OUT, "inputs_used.csv"), index=False)
    log("done:", OUT)


if __name__ == "__main__":
    main()
