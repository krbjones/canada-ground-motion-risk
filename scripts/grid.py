"""National analysis grid shared by every processing step (and mirrored in public/app.js).

Regular lon/lat grid, row 0 at the NORTH edge. 0.1 deg lon x 0.05 deg lat:
~7.3 x 5.6 km at 49 N, ~5.6 x 5.6 km at 60 N, ~3.8 x 5.6 km at 70 N.
"""
import numpy as np

W, S, E, N = -141.0, 41.5, -52.0, 83.5
DLON, DLAT = 0.1, 0.05
NX = int(round((E - W) / DLON))   # 890
NY = int(round((N - S) / DLAT))   # 840
NCELL = NX * NY


def cell_index(lon, lat):
    """Flat cell index for lon/lat arrays; -1 outside the grid."""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    col = np.floor((lon - W) / DLON).astype(np.int64)
    row = np.floor((N - lat) / DLAT).astype(np.int64)
    ok = (col >= 0) & (col < NX) & (row >= 0) & (row < NY)
    return np.where(ok, row * NX + col, -1)


def centres():
    """(lon, lat) of every cell centre, flattened in cell order."""
    lon = W + (np.arange(NX) + 0.5) * DLON
    lat = N - (np.arange(NY) + 0.5) * DLAT
    lo, la = np.meshgrid(lon, lat)
    return lo.ravel(), la.ravel()


def cell_area_km2():
    lat = N - (np.arange(NY) + 0.5) * DLAT
    a = (DLAT * 111.32) * (DLON * 111.32 * np.cos(np.radians(lat)))
    return np.repeat(a, NX)


def transform():
    """rasterio Affine for the grid."""
    from rasterio.transform import from_origin
    return from_origin(W, N, DLON, DLAT)
