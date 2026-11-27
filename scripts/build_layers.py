"""Exposure and fire layers on the national grid -> data/work/layers.npz (+ places list for the UI).

  land      share of each cell inside Canada (StatCan 2021 provinces, 10x super-sampled)
  prov      province / territory code of the cell centre
  hw_len    highway length in cell, km (RNF 2023 classes 10-13);   hw_d  centre -> nearest highway, km
  hw_cls    best (lowest) highway class in cell (10 TCH, 11 NHS, 12 major, 13 secondary; 0 none)
  rl_len    rail track length in cell, km (NRWN);                   rl_d  centre -> nearest track, km
  pl_d      centre -> nearest CGN populated place, km
  pc_d      centre -> nearest StatCan 2021 population centre (polygon edge or inside = 0), km
  pc_cls    class of that population centre (1 small 1k-30k, 2 medium 30k-100k, 3 large 100k+)
  burn_last most recent burn year in cell (NBAC MRB), 0 = none since 1972
  burn5/10/20 share of cell burned within the last 5 / 10 / 20 fire seasons (to 2025)

Run:  python -I scripts/build_layers.py
"""
import csv
import glob
import json
import pathlib
import sys

import numpy as np
import shapely
import shapely.geometry
from shapely.geometry import shape

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import grid  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
WORK = ROOT / "data" / "work"
PUB = ROOT / "public" / "data"
R_EARTH = 6371.0088
LAST_SEASON = 2025


def xyz(lon, lat):
    lo, la = np.radians(lon), np.radians(lat)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)]) * R_EARTH


def nearest_km(pts_lon, pts_lat, q_lon, q_lat):
    from scipy.spatial import cKDTree
    tree = cKDTree(xyz(pts_lon, pts_lat))
    d, i = tree.query(xyz(q_lon, q_lat), k=1, workers=-1)
    return d, i  # chord ~= arc for d << R


def lines_to_grid(geoms, clon, clat, cls=None):
    """Densified line segments -> per-cell length (km), centre->nearest distance (km), best class per cell."""
    geoms = np.asarray(geoms)
    parts_cls = None
    # split multilines into parts so consecutive vertices never jump between parts
    parts, pidx = shapely.get_parts(shapely.segmentize(geoms, 0.002), return_index=True)
    if cls is not None:
        parts_cls = np.asarray(cls)[pidx]
    coords, idx = shapely.get_coordinates(parts, return_index=True)
    same = idx[1:] == idx[:-1]
    a, b = coords[:-1][same], coords[1:][same]
    seg_part = idx[:-1][same]
    mid = (a + b) / 2
    lat = np.radians(mid[:, 1])
    km = np.hypot((b[:, 0] - a[:, 0]) * np.cos(lat) * 111.32, (b[:, 1] - a[:, 1]) * 110.57)
    cell = grid.cell_index(mid[:, 0], mid[:, 1])
    ok = cell >= 0
    length = np.bincount(cell[ok], weights=km[ok], minlength=grid.NCELL)
    best = None
    if parts_cls is not None:
        best = np.full(grid.NCELL, 99, dtype=np.int16)
        np.minimum.at(best, cell[ok], parts_cls[seg_part][ok].astype(np.int16))
        best[best == 99] = 0
    d, _ = nearest_km(coords[:, 0], coords[:, 1], clon, clat)
    print(f"    {km.sum():,.0f} km of line", flush=True)
    return length, d, best


def read_gj(p):
    gj = json.loads(pathlib.Path(p).read_text())
    return gj["features"]


def gj_geoms(feats):
    return np.array([shapely.make_valid(shape(f["geometry"])) if f["geometry"] else None for f in feats], dtype=object)


def write_fc(path, geoms, props):
    feats = [{"type": "Feature", "properties": pr, "geometry": shapely.geometry.mapping(g)}
             for g, pr in zip(geoms, props) if g is not None and not g.is_empty]
    path.write_text(round_geojson(json.dumps({"type": "FeatureCollection", "features": feats})), encoding="utf-8")


SKIP_TERMS = {"Railway Point", "Railway Stop", "Railway Junction", "Station", "Metro Station", "Industrial Park",
              "Abandoned Locality", "Former Locality", "Former First Nation Village", "Vacated or Seasonal Settlement",
              "Administrative Sector", "Post Office", "Landing"}
RAIL_KEEP = {"Main", "Spur", "Siding", "Connecting", "Crossover", "Wye"}


def main():
    import fiona
    import rasterio
    from rasterio import features as rfeat
    from rasterio.transform import from_origin

    WORK.mkdir(parents=True, exist_ok=True)
    PUB.mkdir(parents=True, exist_ok=True)
    clon, clat = grid.centres()
    out = {}

    # ---------------------------------------------------------------- land + province
    print("land mask", flush=True)
    prov = read_gj(RAW / "provinces" / "pr_2021.geojson")
    shapes = [(shape(f["geometry"]), int(f["properties"]["PRUID"])) for f in prov]
    SS = 10
    fine = rfeat.rasterize(shapes, out_shape=(grid.NY * SS, grid.NX * SS),
                           transform=from_origin(grid.W, grid.N, grid.DLON / SS, grid.DLAT / SS), fill=0, dtype="uint8")
    out["land"] = (fine > 0).reshape(grid.NY, SS, grid.NX, SS).mean(axis=(1, 3)).ravel().astype(np.float32)
    # province of cell = most common non-zero code among sub-pixels (cheap approximation: max)
    out["prov"] = fine.reshape(grid.NY, SS, grid.NX, SS).max(axis=(1, 3)).ravel()
    del fine
    prov_names = {int(f["properties"]["PRUID"]): f["properties"].get("PRENAME") or f["properties"].get("PRNAME") for f in prov}

    # ---------------------------------------------------------------- highways
    print("highways", flush=True)
    hwf = [f for f in read_gj(RAW / "highways" / "rnf2023_highways.geojson") if f["geometry"]]
    hwg = gj_geoms(hwf)
    hwc = np.array([int(f["properties"]["CLASS"]) for f in hwf])
    out["hw_len"], out["hw_d"], out["hw_cls"] = lines_to_grid(hwg, clon, clat, hwc)
    cls_list = sorted(set(hwc.tolist()))  # display copy: one simplified multiline per class
    write_fc(PUB / "highways.geojson", [shapely.line_merge(shapely.union_all(shapely.simplify(hwg[hwc == c], 0.002))) for c in cls_list],
             [{"CLASS": c} for c in cls_list])

    # ---------------------------------------------------------------- rail
    print("rail", flush=True)
    shp = sorted(set(glob.glob(str(RAW / "rail" / "*" / "**" / "*TRACK*.shp"), recursive=True)))
    print(f"  {len(shp)} track shapefiles", flush=True)
    rg, rmain = [], []
    for fn in shp:
        with fiona.open(fn) as src:  # NAD83(CSRS) geographic ~= WGS84 at this scale
            for ft in src:
                pr = ft["properties"]
                if pr.get("STATUS") != "Operational" or pr.get("TRACKCLASS") not in RAIL_KEEP or not ft["geometry"]:
                    continue
                rg.append(shape(ft["geometry"]))
                rmain.append(pr.get("TRACKCLASS") == "Main")
    rg, rmain = np.array(rg, dtype=object), np.array(rmain)
    print(f"  {len(rg)} operational track segments ({rmain.sum()} main)", flush=True)
    out["rl_len"], out["rl_d"], _ = lines_to_grid(rg, clon, clat)
    write_fc(PUB / "rail.geojson",
             [shapely.line_merge(shapely.union_all(shapely.simplify(rg[rmain], 0.002))),
              shapely.line_merge(shapely.union_all(shapely.simplify(rg[~rmain], 0.002)))],
             [{"main": 1}, {"main": 0}])

    # ---------------------------------------------------------------- populated places (CGN)
    print("places", flush=True)
    rows = []
    csvs = glob.glob(str(RAW / "places" / "csv" / "**" / "*.csv"), recursive=True)
    with open(csvs[0], encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            term = r.get("Generic Term", "")
            if not ((r.get("Generic Category") == "Populated Place" and term not in SKIP_TERMS) or r.get("Concise Code") == "IR"):
                continue
            try:
                rows.append((float(r["Longitude"]), float(r["Latitude"]), r["Geographical Name"], r.get("Generic Term", ""),
                             r.get("Province - Territory", "")))
            except (ValueError, KeyError):
                continue
    # one row per (name, rounded location): CGN repeats names in each official language
    uniq = {}
    for lo, la, nm, gt, pr in rows:
        uniq.setdefault((nm, round(lo, 2), round(la, 2)), (lo, la, nm, gt, pr))
    places = list(uniq.values())
    print(f"  {len(places)} populated places", flush=True)
    pl = np.array([[p[0], p[1]] for p in places])
    out["pl_d"], _ = nearest_km(pl[:, 0], pl[:, 1], clon, clat)
    terms = sorted({p[3] for p in places})
    (PUB / "places.json").write_text(json.dumps({
        "terms": terms,
        "p": [[round(p[0], 4), round(p[1], 4), p[2], terms.index(p[3])] for p in places]}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---------------------------------------------------------------- population centres (StatCan 2021)
    print("population centres", flush=True)
    pcf = read_gj(RAW / "popctr" / "pc_2021.geojson")
    pcg = gj_geoms(pcf)
    pc_cls = np.array([int(f["properties"]["PCCLASS"]) for f in pcf])
    bnd = shapely.segmentize(shapely.boundary(pcg), 0.002)
    coords, idx = shapely.get_coordinates(bnd, return_index=True)
    d, i = nearest_km(coords[:, 0], coords[:, 1], clon, clat)
    owner = idx[i]
    inside = rfeat.rasterize([(g, k + 1) for k, g in enumerate(pcg)], out_shape=(grid.NY, grid.NX),
                             transform=grid.transform(), fill=0, dtype="int32", all_touched=True).ravel()
    out["pc_d"] = np.where(inside > 0, 0, d)
    out["pc_cls"] = np.where(inside > 0, pc_cls[np.maximum(inside - 1, 0)], pc_cls[owner]).astype(np.uint8)
    rp = shapely.point_on_surface(pcg)
    (PUB / "popcentres.json").write_text(json.dumps(
        [{"n": f["properties"]["PCNAME"], "c": int(f["properties"]["PCCLASS"]), "x": round(p.x, 4), "y": round(p.y, 4)}
         for f, p in zip(pcf, rp)], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_fc(PUB / "popcentres.geojson", shapely.simplify(pcg, 0.002),
             [{"n": f["properties"]["PCNAME"], "c": int(f["properties"]["PCCLASS"])} for f in pcf])

    # ---------------------------------------------------------------- fire (NBAC most recent burn, 250 m)
    print("fire", flush=True)
    tif = glob.glob(str(RAW / "fire" / "mrb" / "**" / "*.tif"), recursive=True)[0]
    from pyproj import Transformer
    last = np.zeros(grid.NCELL, dtype=np.int16)
    burn = {k: np.zeros(grid.NCELL) for k in (5, 10, 20)}
    with rasterio.open(tif) as src:
        print(f"  {pathlib.Path(tif).name} {src.width}x{src.height} {src.crs} nodata={src.nodata}", flush=True)
        tr = Transformer.from_crs(src.crs, 4326, always_xy=True)
        px_km2 = abs(src.transform.a * src.transform.e) / 1e6
        for _, win in src.block_windows(1):
            a = src.read(1, window=win)
            rr, cc = np.nonzero((a >= 1972) & (a <= LAST_SEASON))
            if not len(rr):
                continue
            yr = a[rr, cc].astype(np.int16)
            xs, ys = rasterio.transform.xy(src.window_transform(win), rr, cc)
            lo, la = tr.transform(np.asarray(xs), np.asarray(ys))
            cell = grid.cell_index(lo, la)
            ok = cell >= 0
            cell, yr = cell[ok], yr[ok]
            np.maximum.at(last, cell, yr)
            for k in burn:
                m = yr > LAST_SEASON - k
                burn[k] += np.bincount(cell[m], minlength=grid.NCELL) * px_km2
    area = grid.cell_area_km2()
    out["burn_last"] = last
    for k in burn:
        out[f"burn{k}"] = np.clip(burn[k] / area, 0, 1).astype(np.float32)
    print(f"  cells with any burn: {(last > 0).sum():,}", flush=True)

    np.savez_compressed(WORK / "layers.npz", **out)
    (WORK / "provinces.json").write_text(json.dumps(prov_names), encoding="utf-8")
    print("wrote", WORK / "layers.npz")


def round_geojson(text, nd=4):
    """Shrink a GeoJSON string by rounding coordinates."""
    gj = json.loads(text)

    def rnd(c):
        return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], (int, float)) else [rnd(x) for x in c]
    def rgeom(g):
        if "geometries" in g:
            for x in g["geometries"]:
                rgeom(x)
        else:
            g["coordinates"] = rnd(g["coordinates"])
    for f in gj["features"]:
        if f["geometry"]:
            rgeom(f["geometry"])
        f.pop("id", None)
    return json.dumps(gj, separators=(",", ":"))


if __name__ == "__main__":
    main()
