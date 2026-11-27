"""BC Terrain Stability Mapping (TSM) -> per-cell calibration targets on the national grid.

Source: BC Data Catalogue, WHSE_TERRESTRIAL_ECOLOGY.STE_TER_STABILITY_POLYS_SVW (openmaps WFS,
Open Government Licence - British Columbia). ~413k polygons mapped by terrain geoscientists.

Per polygon we keep only centroid, area, slope-stability class and whether the mapped geomorphic
process includes mass movement (R = rapid mass movement, F = slow mass movement, BC Terrain
Classification System). Geometry is discarded after each tile.

Outputs
  data/raw/tsm/tsm_polygons.csv     lon, lat, area_m2, class, mass_movement (0/1)
  data/work/tsm_cells.npz           per grid cell: mapped area and area by class / mass movement (km2)

Run:  python scripts/fetch_tsm.py            (re-aggregates the saved CSV; add --refetch to download again)
"""
import concurrent.futures as cf
import csv
import pathlib
import re
import sys
import time

import numpy as np
import requests
import shapely
from shapely.geometry import shape

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grid  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
WFS = "https://openmaps.gov.bc.ca/geo/pub/wfs"
LAYER = "pub:WHSE_TERRESTRIAL_ECOLOGY.STE_TER_STABILITY_POLYS_SVW"
PROPS = "SLOPE_STABILITY_CLASS_W_ROADS,GEOMORPH_PROCESS_LBL,FEATURE_AREA_SQM,GEOMETRY"
PAGE = 10000
CLASSES = ["I", "II", "III", "IV", "IVR", "V"]
# Projects use three labelling schemes; map them onto the five-class Roman system.
#   numeric 1-5 (+R)         -> I-V (+R)
#   3-class S / P / PR / U    -> stable (III) / potentially unstable (IV, IVR) / unstable (V)
# '0', 'N', 'A', 'L', 'R', '3/4' and blanks are unrated or ambiguous and are left out.
NORMALISE = {"1": "I", "2": "II", "3": "III", "IIIR": "III", "4": "IV", "4R": "IVR", "3/4R": "IVR", "5": "V", "VR": "V",
             "S": "III", "P": "IV", "PR": "IVR", "U": "V"}
MASS_MOVEMENT = re.compile(r"[RF]")  # BC TCS geomorphological process codes: R rapid, F slow mass movement
S = requests.Session()
S.headers["User-Agent"] = "large-area-risk-canada/0.1 (research)"


def fetch(b):
    """All polygons in bbox b=(w,s,e,n); splits into quarters when the page limit is hit."""
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeName": LAYER, "count": PAGE,
              "sortBy": "OBJECTID", "outputFormat": "json", "srsName": "EPSG:4326", "propertyName": PROPS,
              "bbox": f"{b[0]},{b[1]},{b[2]},{b[3]},EPSG:4326"}
    for attempt in range(6):
        try:
            r = S.get(WFS, params=params, timeout=300)
            r.raise_for_status()
            d = r.json()
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 5:
                raise RuntimeError(f"{b}: {e}") from e
            time.sleep(5 * (attempt + 1))
    feats = d.get("features", [])
    if len(feats) >= PAGE:
        w, s, e, n = b
        mx, my = (w + e) / 2, (s + n) / 2
        out = []
        for q in [(w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)]:
            out += fetch(q)
        return out
    rows = []
    for f in feats:
        if not f.get("geometry"):
            continue
        g = shape(f["geometry"])
        c = shapely.point_on_surface(g)
        # bbox filter returns polygons that merely touch the tile: keep each polygon in exactly one tile
        if not (b[0] <= c.x < b[2] and b[1] <= c.y < b[3]):
            continue
        p = f["properties"]
        cls = (p.get("SLOPE_STABILITY_CLASS_W_ROADS") or "").strip().upper()
        mm = 1 if MASS_MOVEMENT.search(p.get("GEOMORPH_PROCESS_LBL") or "") else 0
        rows.append((round(c.x, 5), round(c.y, 5), float(p.get("FEATURE_AREA_SQM") or 0), cls, mm))
    return rows


def aggregate(rows):
    lon = np.array([r[0] for r in rows], dtype=float); lat = np.array([r[1] for r in rows], dtype=float)
    area = np.array([r[2] for r in rows], dtype=float) / 1e6
    raw = np.array([r[3] for r in rows]); mm = np.array([r[4] for r in rows], dtype=int)
    cls = np.array([NORMALISE.get(c, c) for c in raw])
    cell = grid.cell_index(lon, lat)
    ok = cell >= 0
    agg = {"mapped": np.bincount(cell[ok], weights=area[ok], minlength=grid.NCELL),
           "classed": np.bincount(cell[ok], weights=(area * np.isin(cls, CLASSES))[ok], minlength=grid.NCELL),
           "mass_mv": np.bincount(cell[ok], weights=(area * mm)[ok], minlength=grid.NCELL)}
    for c in CLASSES:
        agg[f"c_{c}"] = np.bincount(cell[ok], weights=(area * (cls == c))[ok], minlength=grid.NCELL)
    np.savez_compressed(ROOT / "data" / "work" / "tsm_cells.npz", **agg)
    print(f"{len(rows):,} polygons, {area.sum():,.0f} km2 mapped, {(agg['mapped'] > 0).sum():,} cells, "
          f"{np.isin(cls, CLASSES).sum():,} classed polygons {dict(zip(*np.unique(cls[np.isin(cls, CLASSES)], return_counts=True)))}; "
          f"mass-movement polygons {mm.sum():,}")


def main():
    out = ROOT / "data" / "raw" / "tsm"
    if (out / "tsm_polygons.csv").exists() and "--refetch" not in sys.argv:
        with open(out / "tsm_polygons.csv", encoding="utf-8") as f:
            next(f)
            rows = [(float(a), float(b), float(c), d, int(e)) for a, b, c, d, e in csv.reader(f)]
        return aggregate(rows)
    tiles = [(x, y, x + 1, y + 1) for x in range(-140, -113) for y in range(48, 61)]
    rows, t0 = [], time.time()
    with cf.ThreadPoolExecutor(4) as ex:
        for k, r in enumerate(ex.map(fetch, tiles)):
            rows += r
            if (k + 1) % 25 == 0:
                print(f"  {k + 1}/{len(tiles)} tiles  {len(rows):,} polygons  {time.time() - t0:.0f}s", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "tsm_polygons.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lon", "lat", "area_m2", "class", "mass_movement"])
        w.writerows(rows)
    aggregate(rows)


if __name__ == "__main__":
    main()
