"""Download the national vector / raster inputs into data/raw/<source>/.

Sources
  highways : StatCan Road Network File 2023, ArcGIS REST layer 0 (CLASS 10-13: Trans-Canada,
             National Highway System, major and secondary highways)
  rail     : NRCan National Railway Network (NRWN) GeoBase shapefiles, one zip per province/territory
  popctr   : StatCan 2021 cartographic population centres (PCCLASS) + provinces (land mask)
  places   : NRCan Canadian Geographical Names (CGN) - populated places
  fire     : CWFIS National Burned Area Composite, most-recent-burn (MRB) 250 m raster 1972-2025

Run:  python -I scripts/fetch_vectors.py            (idempotent - skips files already present)
"""
import json
import pathlib
import sys
import time
import zipfile

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
S = requests.Session()
S.headers["User-Agent"] = "large-area-risk-canada/0.1 (research)"


def _windows_ca_bundle():
    """certifi + the Windows ROOT/CA stores (some .gc.ca hosts only validate against the OS store)."""
    import ssl
    import certifi
    out = ROOT / "data" / "ca_bundle.pem"
    if not out.exists():
        pems = [pathlib.Path(certifi.where()).read_text()]
        if hasattr(ssl, "enum_certificates"):
            for store in ("ROOT", "CA"):
                for cert, enc, _ in ssl.enum_certificates(store):
                    if enc == "x509_asn":
                        pems.append(ssl.DER_cert_to_PEM_cert(cert))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(pems))
    return str(out)


S.verify = _windows_ca_bundle()


def get(url, **kw):
    for attempt in range(5):
        try:
            r = S.get(url, timeout=180, **kw)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                raise
            print(f"  retry {attempt + 1}: {e}", flush=True)
            time.sleep(3 * (attempt + 1))


def download(url, dest: pathlib.Path):
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with get(url, stream=True) as r, open(tmp, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    tmp.replace(dest)
    print(f"  {dest.name}: {dest.stat().st_size / 1e6:.1f} MB", flush=True)
    return dest


def unzip(zp: pathlib.Path, into: pathlib.Path):
    marker = into / ".unzipped"
    if marker.exists():
        return
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zp) as z:
        for m in z.infolist():  # guard against path traversal in untrusted archives
            target = (into / m.filename).resolve()
            if not str(target).startswith(str(into.resolve())):
                raise RuntimeError(f"unsafe path in {zp.name}: {m.filename}")
        z.extractall(into)
    marker.write_text("ok")


def arcgis_layer(base, layer, dest: pathlib.Path, where="1=1", fields="*", offset_deg=None, page=2000):
    """Page an ArcGIS MapServer layer to a single GeoJSON FeatureCollection (EPSG:4326)."""
    if dest.exists():
        return dest
    url = f"{base}/{layer}/query"
    ids = get(url, params={"where": where, "returnIdsOnly": "true", "f": "json"}).json()["objectIds"]
    ids.sort()
    print(f"  {dest.name}: {len(ids)} features", flush=True)
    feats = []
    for i in range(0, len(ids), page):
        chunk = ids[i:i + page]
        params = {"objectIds": ",".join(map(str, chunk)), "outFields": fields, "outSR": 4326,
                  "f": "geojson", "returnGeometry": "true"}
        if offset_deg:
            params["maxAllowableOffset"] = offset_deg
        r = S.post(url, data=params, timeout=300)
        r.raise_for_status()
        feats.extend(r.json()["features"])
        print(f"    {min(i + page, len(ids))}/{len(ids)}", flush=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return dest


def main(which):
    if "highways" in which:
        print("highways (StatCan RNF 2023, classes 10-13)")
        arcgis_layer("https://geo.statcan.gc.ca/geo_wa/rest/services/2023/lrnf000r23s_e/MapServer", 0,
                     RAW / "highways" / "rnf2023_highways.geojson",
                     fields="OBJECTID,NAME,TYPE,CLASS,PRNAME_L", offset_deg=0.0003)
    if "popctr" in which:
        print("population centres + provinces (StatCan 2021 cartographic)")
        cb = "https://geo.statcan.gc.ca/geo_wa/rest/services/2021/Cartographic_boundary_files/MapServer"
        arcgis_layer(cb, 7, RAW / "popctr" / "pc_2021.geojson", page=200, offset_deg=0.0005)
        arcgis_layer(cb, 0, RAW / "provinces" / "pr_2021.geojson", page=1, offset_deg=0.002)
    if "rail" in which:
        print("rail (NRCan NRWN)")
        for p in ["ab", "bc", "mb", "nb", "nl", "ns", "nt", "on", "qc", "sk", "yt"]:
            zp = download(f"https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_nrwn_rfn/{p}/nrwn_rfn_{p}_shp_en.zip",
                          RAW / "rail" / "zip" / f"nrwn_{p}.zip")
            unzip(zp, RAW / "rail" / p)
    if "places" in which:
        print("places (NRCan CGN)")
        zp = download("https://ftp.maps.canada.ca/pub/nrcan_rncan/vector/geobase_cgn_toponyme/prov_csv_eng/cgn_canada_csv_eng.zip",
                      RAW / "places" / "cgn_canada_csv_eng.zip")
        unzip(zp, RAW / "places" / "csv")
    if "fire" in which:
        print("fire (CWFIS NBAC most-recent-burn 250 m)")
        zp = download("https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/NBAC_MRB_1972to2025_250m.tif.zip",
                      RAW / "fire" / "NBAC_MRB_1972to2025_250m.tif.zip")
        unzip(zp, RAW / "fire" / "mrb")
    print("done")


if __name__ == "__main__":
    main(sys.argv[1:] or ["fire", "places", "rail", "popctr", "highways"])
