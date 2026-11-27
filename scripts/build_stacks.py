"""Group harvested Sentinel-1 IW SLC products into InSAR stacks -> public/data/stacks.json.

ESA slices each datatake differently, so product boundaries move between repeat passes. Stacks are
therefore defined on a FIXED along-track grid:

  stack  = (orbit direction, relative orbit, frame k)
  frame k = seconds-since-ANX window [k*F, (k+1)*F),  F = 9 bursts x 2.758273 s = 24.82 s (~170 km)

A pass (platform + absolute orbit) belongs to a stack when its products cover >= MIN_COVER of the
window; the products touching the window are the SLCs that must be downloaded for that date (2 when
the window straddles a slice boundary). The frame footprint is the matching along-track portion of the
products' footprints (linear interpolation between their start and end edges).

Run:  python scripts/build_stacks.py
"""
import collections
import datetime as dt
import json
import pathlib
import sys

import numpy as np
import shapely
from shapely.geometry import Polygon

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from terrain_grid import canada_mask  # noqa: E402
import grid  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
BURST_S = 2.758273
FRAME_S = 9 * BURST_S
MIN_COVER = 0.5


def parse_time(s):
    return dt.datetime.strptime(s, "%Y%m%dT%H%M%S")


def edges(ring, direction):
    """(start_a, start_b, end_a, end_b) corners: start edge = first imaged line."""
    c = [tuple(p) for p in ring[:-1]] if ring[0] == ring[-1] else [tuple(p) for p in ring]
    if len(c) != 4:
        c = list(Polygon(ring).minimum_rotated_rectangle.exterior.coords)[:4]
    c = np.array(c)
    pairings = [((0, 1), (3, 2)), ((1, 2), (0, 3))]  # (edge, opposite edge with matching corner order)
    best = max(pairings, key=lambda pr: abs(c[list(pr[0])][:, 1].mean() - c[list(pr[1])][:, 1].mean()))
    e1, e2 = c[list(best[0])], c[list(best[1])]
    lat1, lat2 = e1[:, 1].mean(), e2[:, 1].mean()
    start, end = (e1, e2) if (lat1 < lat2) == (direction == "A") else (e2, e1)
    return start[0], start[1], end[0], end[1]


def sub_footprint(ring, direction, f0, f1):
    sa, sb, ea, eb = edges(ring, direction)
    a0, b0 = sa + (ea - sa) * f0, sb + (eb - sb) * f0
    a1, b1 = sa + (ea - sa) * f1, sb + (eb - sb) * f1
    return Polygon([a0, b0, b1, a1])


def main():
    rows = [json.loads(l) for l in open(ROOT / "data" / "stac" / "s1_iw_slc.ndjson")]
    meta = json.loads((ROOT / "data" / "stac" / "harvest_meta.json").read_text())
    day0 = dt.date.fromisoformat(meta["start"])
    for r in rows:
        p = r["id"].split("_")
        t0, t1 = parse_time(p[5]), parse_time(p[6])
        r["a0"] = r["anx"]
        r["a1"] = r["anx"] + (t1 - t0).total_seconds()
        r["day"] = (t0.date() - day0).days

    # pass = all products of one platform on one absolute orbit, per stack window
    passes = collections.defaultdict(list)  # (dir, ro, k, pf, ao) -> [product idx]
    for i, r in enumerate(rows):
        if r["a0"] is None:
            continue
        for k in range(int(r["a0"] // FRAME_S), int(r["a1"] // FRAME_S) + 1):
            w0, w1 = k * FRAME_S, (k + 1) * FRAME_S
            if min(r["a1"], w1) - max(r["a0"], w0) > 1.0:
                passes[(r["dir"], r["ro"], k, r["pf"], r["ao"])].append(i)

    m = canada_mask(dilate=0).ravel()
    clon, clat = grid.centres()
    clon, clat = clon[m], clat[m]

    def in_canada(g):
        b = g.bounds
        sel = (clon >= b[0]) & (clon <= b[2]) & (clat >= b[1]) & (clat <= b[3])
        return sel.any() and shapely.contains_xy(g, clon[sel], clat[sel]).any()
    stacks = collections.defaultdict(lambda: {"acq": [], "geoms": []})
    for (d, ro, k, pf, ao), idx in passes.items():
        w0, w1 = k * FRAME_S, (k + 1) * FRAME_S
        iv = sorted((max(rows[i]["a0"], w0), min(rows[i]["a1"], w1)) for i in idx)
        cov, cur_s, cur_e = 0.0, iv[0][0], iv[0][1]
        for s, e in iv[1:]:
            if s > cur_e:
                cov += cur_e - cur_s
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        cov = (cov + cur_e - cur_s) / FRAME_S
        if cov < MIN_COVER:
            continue
        st = stacks[(d, ro, k)]
        st["acq"].append([min(rows[i]["day"] for i in idx), pf, round(cov, 2), sorted(idx)])
        if len(st["geoms"]) < 6 and cov > 0.9:  # a few well-covering passes define the footprint
            parts = []
            for i in idx:
                r = rows[i]
                f0 = max(0.0, (w0 - r["a0"]) / (r["a1"] - r["a0"]))
                f1 = min(1.0, (w1 - r["a0"]) / (r["a1"] - r["a0"]))
                parts.append(sub_footprint(r["r"], d, f0, f1))
            st["geoms"].append(shapely.union_all(parts).buffer(0.001))

    out_stacks, used = [], set()
    for (d, ro, k), st in sorted(stacks.items()):
        if not st["geoms"]:  # only partial passes: fall back to whatever we have
            continue
        g = shapely.convex_hull(shapely.union_all(st["geoms"]))
        if not in_canada(g):
            continue
        st["acq"].sort()
        for a in st["acq"]:
            used.update(a[3])
        ring = [[round(x, 3), round(y, 3)] for x, y in g.simplify(0.01).exterior.coords]
        out_stacks.append({
            "id": f"{d}{ro:03d}-F{k:03d}", "dir": d, "ro": ro, "k": k,
            "anx": [round(k * FRAME_S, 1), round((k + 1) * FRAME_S, 1)],
            "c": [round(g.centroid.x, 2), round(g.centroid.y, 2)],
            "r": ring,
            "acq": st["acq"],  # [day offset, platform, coverage, [product idx]]
        })

    # compact product table (only products used by a Canadian stack); remap indices
    keep = sorted(used)
    remap = {old: new for new, old in enumerate(keep)}
    for s in out_stacks:
        for a in s["acq"]:
            a[3] = [remap[i] for i in a[3]]
    products = [[rows[i]["id"], rows[i]["day"], rows[i]["gb"]] for i in keep]

    out = {"start": meta["start"], "end": meta["end"], "harvested": meta["harvested"], "source": meta["source"],
           "frame_s": FRAME_S, "min_cover": MIN_COVER, "products": products, "stacks": out_stacks}
    dest = ROOT / "public" / "data" / "stacks.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, separators=(",", ":")))
    n_acq = sum(len(s["acq"]) for s in out_stacks)
    print(f"{len(out_stacks)} stacks ({sum(s['dir'] == 'A' for s in out_stacks)} asc / {sum(s['dir'] == 'D' for s in out_stacks)} desc), "
          f"{n_acq} stack-acquisitions, {len(products)} products, {sum(p[2] for p in products) / 1000:.1f} TB; "
          f"{dest.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
