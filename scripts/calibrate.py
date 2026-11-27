"""Calibrate the failure-susceptibility index against BC Terrain Stability Mapping (TSM).

Sample   BC grid cells where >= MIN_COVER of the land carries a TSM slope-stability class.
Target   cell is 'unstable' when >= UNSTABLE_SHARE of its classed area is class IV, IVR or V
         ("potentially unstable", "... after road building", "unstable").
Index    r = steepShare(T) x (1 - w + w*min(1, relief/1500)) x (1 + amp * burnedShare(window))
         - the dashboard's susceptibility formula without the saturation cap.
Fit      1. T, w, window, amp: grid search maximising spatially cross-validated AUC
            (folds = 1-degree blocks, so neighbouring cells never sit on both sides of a split)
         2. P(unstable) = logistic(a + b*ln(r + eps)), fitted on out-of-fold predictions' data
Report   CV AUC for the heuristic defaults, the calibrated index and an unconstrained logistic
         regression on all terrain features (upper bound), a reliability table, and an
         independent check against mapped mass-movement processes (R/F codes).

Output   public/data/calibration.json, data/work/calibration_cells.npz

Run:  python scripts/calibrate.py
"""
import itertools
import json
import pathlib
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grid  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "data" / "work"
BC = 59
MIN_COVER = 0.2
UNSTABLE_SHARE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25
MM_SHARE = 0.05
EPS = 1e-3
DEFAULT = {"T": 25, "w": 0.4, "win": 10, "amp": 1.0, "fsat": 0.30}


def load():
    t = np.load(WORK / "terrain.npz")
    l = np.load(WORK / "layers.npz")
    s = np.load(WORK / "tsm_cells.npz")
    land_km2 = l["land"] * grid.cell_area_km2()
    unst = s["c_IV"] + s["c_IVR"] + s["c_V"]
    with np.errstate(invalid="ignore", divide="ignore"):
        cover = np.where(land_km2 > 0, s["classed"] / land_km2, 0)
        share = np.where(s["classed"] > 0, unst / s["classed"], 0)
        mm = np.where(s["mapped"] > 0, s["mass_mv"] / s["mapped"], 0)
    sel = (l["prov"] == BC) & (cover >= MIN_COVER) & (t["n"] > 0)
    th = list(t["thresh"])
    feats = {f"f{T}": np.nan_to_num(t["frac"][i]) for i, T in enumerate(th)}
    feats.update(relief=np.nan_to_num(t["relief"]), slope=np.nan_to_num(t["slope_mean"]), elev=np.nan_to_num(t["elev_mean"]),
                 burn5=l["burn5"], burn10=l["burn10"], burn20=l["burn20"])
    lon, lat = grid.centres()
    block = (np.floor(lon) * 1000 + np.floor(lat)).astype(np.int64)
    rng = np.random.default_rng(7)
    ub = np.unique(block[sel])
    fold_of = dict(zip(ub, rng.permutation(len(ub)) % 5))
    fold = np.array([fold_of.get(b, -1) for b in block])
    return sel, share, mm, cover, s, feats, th, fold


def index(feats, T, w, win, amp):
    return feats[f"f{T}"] * (1 - w + w * np.minimum(1, feats["relief"] / 1500)) * (1 + amp * feats[f"burn{win}"])


def heuristic(feats, p):
    return np.minimum(1, index(feats, p["T"], p["w"], p["win"], p["amp"]) / p["fsat"])


def cv_auc(x, y, fold):
    """Mean AUC across spatial folds (a pure ranking, no fitting needed for an index)."""
    out = [roc_auc_score(y[fold == k], x[fold == k]) for k in range(5) if len(np.unique(y[fold == k])) == 2]
    return float(np.mean(out))


def fit_logit(x, y):
    m = LogisticRegression(C=1e6, max_iter=1000).fit(np.log(x + EPS)[:, None], y)
    return float(m.intercept_[0]), float(m.coef_[0, 0])


def main():
    sel, share, mm, cover, tsm, feats, th, fold = load()
    y = (share[sel] >= UNSTABLE_SHARE).astype(int)
    ymm = (mm[sel] >= MM_SHARE).astype(int)
    F = {k: v[sel] for k, v in feats.items()}
    fsel = fold[sel]
    print(f"{sel.sum():,} BC cells with >= {MIN_COVER:.0%} TSM cover; unstable (>= {UNSTABLE_SHARE:.0%} IV/IVR/V) "
          f"base rate {y.mean():.1%}; mass-movement (>= {MM_SHARE:.0%}) base rate {ymm.mean():.1%}")
    print("unstable-share quantiles", np.round(np.quantile(share[sel], [.1, .25, .5, .75, .9]), 3))

    # 1. heuristic defaults (as shipped)
    auc_default = cv_auc(heuristic(F, DEFAULT), y, fsel)

    # 2. grid search the ranking parameters
    best = None
    for T, w, win, amp in itertools.product(th, [0, 0.2, 0.4, 0.6, 0.8, 1.0], [5, 10, 20], [0, 0.5, 1, 2, 3]):
        a = cv_auc(index(F, T, w, win, amp), y, fsel)
        if best is None or a > best[0]:
            best = (a, T, w, win, amp)
    auc_cal, T, w, win, amp = best
    r = index(F, T, w, win, amp)
    print(f"default heuristic CV AUC {auc_default:.3f}  ->  best index T={T} w={w} win={win} amp={amp}: CV AUC {auc_cal:.3f}")

    # 3. logistic link, fitted out-of-fold for honest reliability, then on all data for the dashboard
    oof = np.zeros(len(y))
    for k in range(5):
        a_, b_ = fit_logit(r[fsel != k], y[fsel != k])
        oof[fsel == k] = 1 / (1 + np.exp(-(a_ + b_ * np.log(r[fsel == k] + EPS))))
    a, b = fit_logit(r, y)
    brier = float(np.mean((oof - y) ** 2))
    bins = np.array([0, .1, .25, .5, .75, 1.0001])
    rel = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (oof >= lo) & (oof < hi)
        if m.sum():
            rel.append({"bin": [float(lo), float(min(hi, 1))], "n": int(m.sum()), "pred": round(float(oof[m].mean()), 3),
                        "obs": round(float(y[m].mean()), 3)})
    print(f"logit a={a:.3f} b={b:.3f}; out-of-fold Brier {brier:.3f} (climatology {y.mean() * (1 - y.mean()):.3f})")
    for row in rel:
        print("   ", row)

    # 4. upper bound: unconstrained logistic regression on all terrain + burn features (CV)
    X = np.column_stack([F[k] for k in sorted(F)])
    Xs = (X - X.mean(0)) / (X.std(0) + 1e-9)
    ub = np.zeros(len(y))
    for k in range(5):
        m = LogisticRegression(max_iter=2000).fit(Xs[fsel != k], y[fsel != k])
        ub[fsel == k] = m.predict_proba(Xs[fsel == k])[:, 1]
    auc_full = cv_auc(ub, y, fsel)
    # 5. independent check against mapped mass-movement processes
    auc_mm_default = cv_auc(heuristic(F, DEFAULT), ymm, fsel)
    auc_mm_cal = cv_auc(r, ymm, fsel)
    print(f"full logistic (upper bound) CV AUC {auc_full:.3f}; mass-movement check AUC default {auc_mm_default:.3f} / calibrated {auc_mm_cal:.3f}")

    # class shares for the dashboard: probability bands vs observed unstable share
    p_all = 1 / (1 + np.exp(-(a + b * np.log(r + EPS))))
    out = {
        "source": "BC Terrain Stability Mapping (WHSE_TERRESTRIAL_ECOLOGY.STE_TER_STABILITY_POLYS_SVW), BC Data Catalogue",
        "target": f"cell has >= {UNSTABLE_SHARE:.0%} of its TSM-classed terrain in class IV, IVR or V",
        "sample": {"cells": int(sel.sum()), "min_cover": MIN_COVER, "base_rate": round(float(y.mean()), 3),
                   "folds": "5 spatial folds of 1-degree blocks"},
        "params": {"T": int(T), "w": w, "win": int(win), "amp": amp},
        "logit": {"a": round(a, 4), "b": round(b, 4), "eps": EPS},
        "metrics": {"auc_default": round(auc_default, 3), "auc_calibrated": round(auc_cal, 3), "auc_full_logistic": round(auc_full, 3),
                    "brier_oof": round(brier, 4), "brier_climatology": round(float(y.mean() * (1 - y.mean())), 4),
                    "auc_mass_movement_default": round(auc_mm_default, 3), "auc_mass_movement_calibrated": round(auc_mm_cal, 3)},
        "reliability": rel,
        "default": DEFAULT,
    }
    (ROOT / "public" / "data" / "calibration.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    np.savez_compressed(WORK / "calibration_cells.npz", tsm_share=share.astype(np.float32), tsm_cover=cover.astype(np.float32),
                        tsm_mm=mm.astype(np.float32))
    print("wrote public/data/calibration.json; calibrated P>=0.5 share of BC sample:", round(float((p_all >= 0.5).mean()), 3))


if __name__ == "__main__":
    main()
