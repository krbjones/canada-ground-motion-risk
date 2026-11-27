"""Pack terrain.npz + layers.npz into public/data/grid.bin (typed-array layers) + grid.json (index).

value = raw * scale  (distances in km, lengths in km, fractions 0-1, slope in degrees)
Cells with < 1 % land are zeroed (distance layers set to the max code) so the file gzips well.

Run:  python scripts/pack_grid.py
"""
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grid  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / "data" / "work"
PUB = ROOT / "public" / "data"

# name, dtype, scale, description
SPEC = [
    ("land", "u1", 1 / 255, "share of cell inside Canada"),
    ("prov", "u1", 1, "StatCan province/territory code"),
    ("elev", "i2", 1, "mean elevation, m"),
    ("slope", "u1", 0.25, "mean slope, deg"),
    ("relief", "u2", 1, "max - min elevation, m"),
    ("f10", "u1", 1 / 255, "share of DEM pixels > 10 deg"),
    ("f15", "u1", 1 / 255, "share > 15 deg"),
    ("f20", "u1", 1 / 255, "share > 20 deg"),
    ("f25", "u1", 1 / 255, "share > 25 deg"),
    ("f30", "u1", 1 / 255, "share > 30 deg"),
    ("f35", "u1", 1 / 255, "share > 35 deg"),
    ("burn_last", "u2", 1, "most recent burn year (0 none)"),
    ("burn5", "u1", 1 / 255, "share burned 2021-2025"),
    ("burn10", "u1", 1 / 255, "share burned 2016-2025"),
    ("burn20", "u1", 1 / 255, "share burned 2006-2025"),
    ("hw_len", "u2", 0.01, "highway length in cell, km"),
    ("hw_d", "u2", 0.1, "centre to nearest highway, km"),
    ("hw_cls", "u1", 1, "best highway class in cell (10 TCH, 11 NHS, 12 major, 13 secondary)"),
    ("rl_len", "u2", 0.01, "rail track length in cell, km"),
    ("rl_d", "u2", 0.1, "centre to nearest rail track, km"),
    ("pl_d", "u2", 0.1, "centre to nearest populated place, km"),
    ("pc_d", "u2", 0.1, "centre to nearest population centre (0 inside), km"),
    ("pc_cls", "u1", 1, "class of that population centre (1 small, 2 medium, 3 large)"),
    ("tsm_cover", "u1", 1 / 255, "BC TSM: share of cell land with a stability class"),
    ("tsm_unst", "u1", 1 / 255, "BC TSM: share of classed terrain in class IV/IVR/V"),
]


def main():
    t = np.load(WORK / "terrain.npz")
    l = np.load(WORK / "layers.npz")
    thresh = list(t["thresh"])
    src = {
        "land": l["land"], "prov": l["prov"], "elev": t["elev_mean"], "slope": t["slope_mean"], "relief": t["relief"],
        "burn_last": l["burn_last"], "burn5": l["burn5"], "burn10": l["burn10"], "burn20": l["burn20"],
        "hw_len": l["hw_len"], "hw_d": l["hw_d"], "hw_cls": l["hw_cls"], "rl_len": l["rl_len"], "rl_d": l["rl_d"],
        "pl_d": l["pl_d"], "pc_d": l["pc_d"], "pc_cls": l["pc_cls"],
    }
    for i, T in enumerate(thresh):
        src[f"f{T}"] = t["frac"][i]
    cal = WORK / "calibration_cells.npz"
    if cal.exists():
        c = np.load(cal)
        src["tsm_cover"], src["tsm_unst"] = np.clip(c["tsm_cover"], 0, 1), np.where(c["tsm_cover"] > 0, c["tsm_share"], 0)
    else:
        src["tsm_cover"] = src["tsm_unst"] = np.zeros(grid.NCELL)
    spec = list(SPEC)
    snow_years = []
    sn = WORK / "snow.npz"
    if sn.exists():  # seasonal-snow calendar from scripts/fetch_snow.py
        z = np.load(sn)
        snow_years = [int(y) for y in z["years"]]
        src["snow_perm"], src["sf_days"] = z["perm"], z["sf_days"]
        spec += [("snow_perm", "u1", 1, "IMS: permanent snow/ice (no snow-free run in any complete year)"),
                 ("sf_days", "u2", 1, "IMS: mean snow-free days per complete year")]
        for y in snow_years:
            src[f"sfs_{y}"], src[f"sfe_{y}"] = z[f"sf_start_{y}"], z[f"sf_end_{y}"]
            spec += [(f"sfs_{y}", "u2", 1, f"IMS: first day-of-year of the {y} snow-free season (0 none)"),
                     (f"sfe_{y}", "u2", 1, f"IMS: last day-of-year of the {y} snow-free season")]
    land = src["land"] >= 0.01
    blob, index, off = bytearray(), [], 0
    for name, dt, scale, desc in spec:
        v = np.nan_to_num(np.asarray(src[name], dtype=np.float64), nan=0.0) / scale
        info = np.iinfo(np.dtype(dt))
        v = np.clip(np.round(v), info.min, info.max)
        if name.endswith("_d"):
            v[~land] = info.max
        else:
            v[~land] = 0
        arr = v.astype(dt)
        pad = (-off) % 4
        blob += b"\0" * pad
        off += pad
        index.append({"name": name, "type": {"u1": "Uint8", "i2": "Int16", "u2": "Uint16"}[dt], "offset": off,
                      "scale": scale, "desc": desc})
        blob += arr.tobytes()
        off += arr.nbytes
    PUB.mkdir(parents=True, exist_ok=True)
    (PUB / "grid.bin").write_bytes(bytes(blob))
    prov = json.loads((WORK / "provinces.json").read_text(encoding="utf-8"))
    (PUB / "grid.json").write_text(json.dumps({
        "w": grid.W, "s": grid.S, "e": grid.E, "n": grid.N, "dlon": grid.DLON, "dlat": grid.DLAT, "nx": grid.NX, "ny": grid.NY,
        "thresholds": [int(x) for x in thresh], "layers": index, "snow_years": snow_years, "provinces": prov, "land_cells": int(land.sum()),
        "sources": {
            "dem": "AWS Terrain Tiles (Terrarium) z10 south of 60N / z9 north of 60N",
            "highways": "Statistics Canada Road Network File 2023, classes 10-13",
            "rail": "NRCan National Railway Network (NRWN), operational track excl. yards",
            "places": "NRCan Canadian Geographical Names - populated places + Indian reserves",
            "popctr": "Statistics Canada 2021 population centres",
            "fire": "CWFIS National Burned Area Composite, most recent burn 1972-2025 (250 m)",
            "calibration": "BC Terrain Stability Mapping (TSM), BC Data Catalogue",
            "snow": "NOAA/NSIDC IMS daily Northern Hemisphere snow and ice analysis, 4 km (G02156)",
        }}, indent=1), encoding="utf-8")
    print(f"grid.bin {len(blob) / 1e6:.1f} MB, {land.sum():,} land cells")


if __name__ == "__main__":
    main()
