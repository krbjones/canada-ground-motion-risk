"""Seasonal-snow calendar per grid cell from the NOAA/NSIDC IMS daily snow and ice analysis (G02156, 4 km).

Why: C-band InSAR coherence collapses when wet or fresh snow covers the ground, so acquisitions taken while
a target is under SEASONAL snow add noise rather than signal. Permanent snow/ice (glaciers, ice caps) is
left alone - those cells keep every acquisition.

Per cell (sampled at the IMS pixel under its centre) and per calendar year:
  sf_start, sf_end   first / last day-of-year of the longest snow-free run, after a 15-day running majority
                     filter so single snowfalls or one-day gaps do not split the season (0/0 = no snow-free run)
  perm               1 = permanent snow/ice: no snow-free run of >= MIN_RUN days in any complete year, or a
                     mean of < PERM_DAYS snow-free days a year (ice caps whose bare ice briefly reads snow-free)
  sf_days            mean snow-free days per complete year

IMS codes: 1 open water, 2 snow-free land, 3 sea/lake ice, 4 snow-covered land, 0 outside NH.
Source: https://noaadata.apps.nsidc.org/NOAA/G02156/4km/  (no login needed)

Output data/work/snow.npz
Run:  python scripts/fetch_snow.py [first_year] [last_year]      (default 2024 .. current year)
"""
import concurrent.futures as cf
import datetime as dt
import gzip
import pathlib
import sys
import time

import numpy as np
import requests
from pyproj import Transformer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grid  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "snow"
BASE = "https://noaadata.apps.nsidc.org/NOAA/G02156/4km"
N = 6144
ORIGIN, RES = 12288000.0, 4000.0
SMOOTH = 15
MIN_RUN = 20
PERM_DAYS = 45
S = requests.Session()
S.headers["User-Agent"] = "large-area-risk-canada/0.1 (research)"


def ims_index():
    """Flat IMS pixel index (row counted from the TOP) under every grid-cell centre."""
    tr = Transformer.from_crs("EPSG:4326", "+proj=stere +lat_0=90 +lat_ts=60 +lon_0=-80 +datum=WGS84 +units=m", always_xy=True)
    lon, lat = grid.centres()
    x, y = tr.transform(lon, lat)
    col = np.floor((x + ORIGIN) / RES).astype(np.int64)
    row = np.floor((ORIGIN - y) / RES).astype(np.int64)
    return np.clip(row, 0, N - 1) * N + np.clip(col, 0, N - 1)


def fetch(day):
    f = RAW / str(day.year) / f"ims{day.year}{day.timetuple().tm_yday:03d}_00UTC_4km_v1.3.asc.gz"
    if f.exists() and f.stat().st_size > 0:
        return f
    f.parent.mkdir(parents=True, exist_ok=True)
    url = f"{BASE}/{day.year}/{f.name}"
    for attempt in range(4):
        try:
            r = S.get(url, timeout=120)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            tmp = f.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(f)
            return f
        except Exception:  # noqa: BLE001
            time.sleep(3 * (attempt + 1))
    return None


def read(f, idx):
    """IMS codes at the given flat (top-origin) pixel indices."""
    raw = gzip.decompress(f.read_bytes())
    start = raw.index(b"Dim Units:                         Pixels", raw.index(b"Dimension #       1")) + 1
    start = raw.index(b"\n", start) + 1
    body = np.frombuffer(raw[start:], dtype=np.uint8)
    body = body[(body >= 48) & (body <= 52)] - 48          # keep digit characters only
    if body.size != N * N:
        raise ValueError(f"{f.name}: {body.size} values")
    img = body.reshape(N, N)[::-1]                          # file starts at the lower-left corner
    return img.ravel()[idx]


def longest_run(free):
    """free: (days, cells) bool -> start, end (1-based day index) of the longest True run per cell; 0/0 if none."""
    d, n = free.shape
    best_len = np.zeros(n, np.int32); best_end = np.zeros(n, np.int32)
    cur = np.zeros(n, np.int32)
    for t in range(d):
        cur = np.where(free[t], cur + 1, 0)
        better = cur > best_len
        best_len = np.where(better, cur, best_len)
        best_end = np.where(better, t + 1, best_end)
    start = np.where(best_len > 0, best_end - best_len + 1, 0)
    return start.astype(np.uint16), best_end.astype(np.uint16), best_len


def main():
    today = dt.date.today()
    y0 = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    y1 = int(sys.argv[2]) if len(sys.argv) > 2 else today.year
    idx_all = ims_index()
    land = np.load(ROOT / "data" / "work" / "layers.npz")["land"] > 0
    cells = np.nonzero(land)[0]
    idx = idx_all[cells]

    days = [dt.date(y0, 1, 1) + dt.timedelta(k) for k in range((min(dt.date(y1, 12, 31), today) - dt.date(y0, 1, 1)).days + 1)]
    t0 = time.time()
    with cf.ThreadPoolExecutor(8) as ex:
        files = list(ex.map(fetch, days))
    print(f"{sum(f is not None for f in files)}/{len(days)} IMS days on disk ({time.time() - t0:.0f}s)", flush=True)

    # sanity check on one winter day: Greenland ice sheet must be snow, Labrador Sea open water or ice
    probe = next(f for d, f in zip(days, files) if f is not None and d.month == 2)
    tr = Transformer.from_crs("EPSG:4326", "+proj=stere +lat_0=90 +lat_ts=60 +lon_0=-80 +datum=WGS84 +units=m", always_xy=True)
    def px(lon, lat):
        x, y = tr.transform(lon, lat)
        return int(np.floor((ORIGIN - y) / RES)) * N + int(np.floor((x + ORIGIN) / RES))
    chk = read(probe, np.array([px(-40, 72), px(-55, 58), px(-75, 45.5)]))
    print(f"sanity {probe.name}: Greenland {chk[0]} (4), Labrador Sea {chk[1]} (1/3), Ottawa {chk[2]} (4 in Feb)", flush=True)
    if chk[0] != 4:
        raise SystemExit("IMS orientation check failed")

    # Coastal / lakeshore cells: if the IMS pixel under the centre is water (1) or sea/lake ice (3), its open
    # water would read as 'snow-free'. Move those cells to the nearest IMS land pixel within 3 px (12 km).
    full = read(probe, np.arange(N * N)).reshape(N, N)
    land_px = (full == 2) | (full == 4)
    moved = 0
    for k, flat in enumerate(idx):
        r, c = divmod(int(flat), N)
        if land_px[r, c]:
            continue
        best = None
        for rad in (1, 2, 3):
            rr, cc = np.mgrid[max(0, r - rad):min(N, r + rad + 1), max(0, c - rad):min(N, c + rad + 1)]
            ok = land_px[rr, cc]
            if ok.any():
                d = (rr - r) ** 2 + (cc - c) ** 2
                j = np.argmin(np.where(ok, d, 1 << 30))
                best = int(rr.ravel()[j]) * N + int(cc.ravel()[j])
                break
        if best is not None:
            idx[k] = best
            moved += 1
    print(f"moved {moved:,} coastal/lakeshore cells to the nearest IMS land pixel", flush=True)
    del full

    out = {}
    run_ok = []
    for year in range(y0, y1 + 1):
        ydays = [(d, f) for d, f in zip(days, files) if d.year == year]
        codes = np.full((len(ydays), len(cells)), 255, np.uint8)
        with cf.ThreadPoolExecutor(6) as ex:
            for k, v in enumerate(ex.map(lambda df: read(df[1], idx) if df[1] is not None else None, ydays)):
                if v is not None:
                    codes[k] = v
        # missing days: carry the previous day forward
        for k in range(1, len(codes)):
            miss = codes[k] == 255
            codes[k][miss] = codes[k - 1][miss]
        snow = (codes == 4) | (codes == 3)                    # snow-covered land, or lake ice under snow
        # running majority over SMOOTH days
        cs = np.cumsum(np.vstack([np.zeros((1, snow.shape[1]), np.int32), snow.astype(np.int32)]), axis=0)
        h = SMOOTH // 2
        lo = np.clip(np.arange(len(snow)) - h, 0, len(snow)); hi = np.clip(np.arange(len(snow)) + h + 1, 0, len(snow))
        frac = (cs[hi] - cs[lo]) / (hi - lo)[:, None]
        start, end, length = longest_run(frac < 0.5)
        complete = len(ydays) >= 360
        full_s = np.zeros(grid.NCELL, np.uint16); full_e = np.zeros(grid.NCELL, np.uint16)
        full_s[cells], full_e[cells] = start, end
        out[f"sf_start_{year}"], out[f"sf_end_{year}"] = full_s, full_e
        if complete:
            run_ok.append(length >= MIN_RUN)
            sfd = np.zeros(grid.NCELL, np.float32); sfd[cells] = (~snow).sum(0)
            out[f"sf_days_{year}"] = sfd
        print(f"{year}: {len(ydays)} days, median snow-free run {np.median(length):.0f} d, "
              f"cells with no >= {MIN_RUN}-day snow-free run {(length < MIN_RUN).mean():.1%}", flush=True)
    full_years = [y for y in range(y0, y1 + 1) if f"sf_days_{y}" in out]
    out["sf_days"] = np.mean([out[f"sf_days_{y}"] for y in full_years], axis=0) if full_years else np.zeros(grid.NCELL)
    # Permanent snow / ice: no snow-free run in ANY complete year, or fewer than PERM_DAYS snow-free days a year
    # on average (glaciers and ice caps whose bare ice shows briefly as 'snow-free' in warm summers).
    perm = np.zeros(grid.NCELL, np.uint8)
    if run_ok:
        perm[cells] = ((~np.any(run_ok, axis=0)) | (out["sf_days"][cells] < PERM_DAYS)).astype(np.uint8)
    out["perm"] = perm
    out["years"] = np.arange(y0, y1 + 1)
    np.savez_compressed(ROOT / "data" / "work" / "snow.npz", **out)
    print(f"permanent-snow cells: {perm.sum():,} of {len(cells):,} land cells; wrote data/work/snow.npz")


if __name__ == "__main__":
    main()
