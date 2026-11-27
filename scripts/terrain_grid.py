"""Slope statistics per national grid cell from AWS Terrain Tiles (Terrarium PNG, global ~150 m DEM).

Same DEM and Horn slope as the Aleph dashboard (terrain.js), applied nation-wide:
  * z10 (~100 m/px in southern Canada) for pixels south of 60 N
  * z9  (~150 m/px at 60 N, finer further north) for pixels at or north of 60 N
so every pixel is <= ~150 m. Tiles already cached by the Aleph dashboard are copied, not re-fetched.

Output data/work/terrain.npz with per-cell arrays:
  n           DEM pixels in cell
  slope_mean  mean slope (deg)
  elev_mean   mean elevation (m)
  relief      max - min elevation (m)
  frac_gtT    share of pixels steeper than T deg, T in THRESH

Run:  python -I scripts/terrain_grid.py
"""
import concurrent.futures as cf
import io
import json
import math
import os
import pathlib
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grid  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEM = ROOT / "data" / "dem" / "terrarium"
ALEPH_CACHE = pathlib.Path(r"C:\Data_002\Satellogic_aleph\cache\terrain")
URL = "https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"
SPLIT_LAT = 60.0
THRESH = [10, 15, 20, 25, 30, 35]
EARTH_CIRC = 40075016.686


def lon2x(lon, z): return (lon + 180) / 360 * 2 ** z
def lat2y(lat, z):
    s = math.sin(math.radians(lat))
    return (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * 2 ** z
def y2lat(y, z):  # y may be fractional / array (global tile units)
    n = np.pi - 2 * np.pi * np.asarray(y, dtype=np.float64) / 2 ** z
    return np.degrees(np.arctan(np.sinh(n)))


def canada_mask(dilate=2):
    """Boolean grid (NY x NX) of cells touching Canada, from build_layers' land share, dilated a little."""
    from scipy.ndimage import binary_dilation
    land = np.load(ROOT / "data" / "work" / "layers.npz")["land"].reshape(grid.NY, grid.NX) > 0
    return binary_dilation(land, iterations=dilate)


def tile_list(mask):
    tiles = []
    for z, lat0, lat1 in [(10, grid.S, SPLIT_LAT), (9, SPLIT_LAT, grid.N)]:
        x0, x1 = int(lon2x(grid.W, z)), int(lon2x(grid.E, z))
        y0, y1 = int(lat2y(lat1, z)), int(lat2y(lat0, z))
        for x in range(x0, x1 + 1):
            w = x / 2 ** z * 360 - 180
            e = (x + 1) / 2 ** z * 360 - 180
            c0, c1 = max(0, int((w - grid.W) / grid.DLON)), min(grid.NX - 1, int((e - grid.W) / grid.DLON))
            for y in range(y0, y1 + 1):
                n_, s_ = min(float(y2lat(y, z)), lat1), max(float(y2lat(y + 1, z)), lat0)
                if n_ <= s_ or c1 < c0:
                    continue
                r0, r1 = max(0, int((grid.N - n_) / grid.DLAT)), min(grid.NY - 1, int((grid.N - s_) / grid.DLAT))
                if r1 >= r0 and mask[r0:r1 + 1, c0:c1 + 1].any():
                    tiles.append((z, x, y))
    return tiles


def fetch(t):
    import requests
    z, x, y = t
    f = DEM / str(z) / str(x) / f"{y}.png"
    if f.exists():
        return 0
    f.parent.mkdir(parents=True, exist_ok=True)
    src = ALEPH_CACHE / str(z) / str(x) / f"{y}.png"
    if src.exists():
        shutil.copyfile(src, f)
        return 0
    for attempt in range(5):
        try:
            r = requests.get(URL.format(z=z, x=x, y=y), timeout=60)
            if r.status_code == 404:
                f.write_bytes(b"")
                return 0
            r.raise_for_status()
            tmp = f.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(f)
            return len(r.content)
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    return -1


def process(t):
    """Per-cell partial aggregates for one DEM tile -> (cells, n, sum_slope, sum_elev, emax, emin, counts[T])."""
    from PIL import Image
    z, x, y = t
    f = DEM / str(z) / str(x) / f"{y}.png"
    if not f.exists() or f.stat().st_size == 0:
        return None
    a = np.asarray(Image.open(f).convert("RGB"), dtype=np.float32)
    elev = a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    h, w = elev.shape
    p = np.pad(elev, 1, mode="edge")
    z1, z2, z3 = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    z4, z6 = p[1:-1, :-2], p[1:-1, 2:]
    z7, z8, z9 = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    py = y + (np.arange(h) + 0.5) / h                      # global tile units (pixel centres)
    lat = y2lat(py, z)
    px_m = (EARTH_CIRC * np.cos(np.radians(lat)) / (256 * 2 ** z))[:, None]
    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8 * px_m)
    dzdy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8 * px_m)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    lon = (x + (np.arange(w) + 0.5) / w) / 2 ** z * 360 - 180
    LON, LAT = np.meshgrid(lon, lat)
    keep = (LAT < SPLIT_LAT) if z == 10 else (LAT >= SPLIT_LAT)
    idx = grid.cell_index(LON, LAT)
    keep &= idx >= 0
    if not keep.any():
        return None
    idx, s, e = idx[keep], slope[keep], elev[keep]
    cells, inv = np.unique(idx, return_inverse=True)
    k = len(cells)
    n = np.bincount(inv, minlength=k)
    ss = np.bincount(inv, weights=s, minlength=k)
    se = np.bincount(inv, weights=e, minlength=k)
    emax = np.full(k, -1e9); np.maximum.at(emax, inv, e)
    emin = np.full(k, 1e9); np.minimum.at(emin, inv, e)
    cnt = np.stack([np.bincount(inv, weights=(s > T), minlength=k) for T in THRESH])
    return cells, n, ss, se, emax, emin, cnt


def main():
    out = ROOT / "data" / "work" / "terrain.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    tiles = tile_list(canada_mask())
    print(f"{len(tiles)} DEM tiles needed (z10 S of {SPLIT_LAT}N: {sum(t[0] == 10 for t in tiles)}, z9 N: {sum(t[0] == 9 for t in tiles)})", flush=True)

    t0, done, nbytes, fails = time.time(), 0, 0, 0
    with cf.ThreadPoolExecutor(32) as ex:
        for b in ex.map(fetch, tiles):
            done += 1
            nbytes += max(b, 0)
            fails += b < 0
            if done % 2000 == 0:
                print(f"  fetched {done}/{len(tiles)}  {nbytes / 1e6:.0f} MB new  {time.time() - t0:.0f}s", flush=True)
    print(f"fetch done: {nbytes / 1e6:.0f} MB downloaded, {fails} failures", flush=True)

    n = np.zeros(grid.NCELL); ss = np.zeros(grid.NCELL); se = np.zeros(grid.NCELL)
    emax = np.full(grid.NCELL, -1e9); emin = np.full(grid.NCELL, 1e9)
    cnt = np.zeros((len(THRESH), grid.NCELL))
    t0 = time.time()
    with cf.ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1)) as ex:
        for i, r in enumerate(ex.map(process, tiles, chunksize=32)):
            if r is not None:
                c, a, b, d, mx, mn, k = r
                n[c] += a; ss[c] += b; se[c] += d
                emax[c] = np.maximum(emax[c], mx); emin[c] = np.minimum(emin[c], mn)
                cnt[:, c] += k
            if (i + 1) % 2000 == 0:
                print(f"  processed {i + 1}/{len(tiles)}  {time.time() - t0:.0f}s", flush=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        np.savez_compressed(out, n=n.astype(np.uint32),
                            slope_mean=np.where(n > 0, ss / n, np.nan).astype(np.float32),
                            elev_mean=np.where(n > 0, se / n, np.nan).astype(np.float32),
                            relief=np.where(n > 0, emax - emin, np.nan).astype(np.float32),
                            frac=np.where(n > 0, cnt / n, np.nan).astype(np.float32),
                            thresh=np.array(THRESH))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
