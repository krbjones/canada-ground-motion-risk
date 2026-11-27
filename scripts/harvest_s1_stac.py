"""Harvest Sentinel-1 IW SLC footprints over Canada from the Copernicus Data Space STAC.

Each item is slimmed to the fields needed for stack planning and written to
data/stac/s1_iw_slc.ndjson (one JSON object per line).

Stack ("frame") definition used downstream: (orbit direction, relative orbit, frame index), where
frame index = round(seconds since ascending-node crossing / FRAME_S). Seconds-since-ANX is stable
from one repeat pass to the next, so it identifies the same along-track position on every cycle.

Run:  python -I scripts/harvest_s1_stac.py [START] [END]     (default: last 24 full months)
"""
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import sys
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "stac"
STAC = "https://stac.dataspace.copernicus.eu/v1/search"
BBOX = [-141.1, 41.6, -52.5, 83.2]  # Canada
S = requests.Session()
S.headers["User-Agent"] = "large-area-risk-canada/0.1 (research)"


def slim(f):
    p = f["properties"]
    g = f["geometry"]
    ring = g["coordinates"][0] if g["type"] == "Polygon" else max(g["coordinates"], key=lambda r: len(r[0]))[0]
    t0 = dt.datetime.fromisoformat(p["start_datetime"].replace("Z", "+00:00"))
    anx = dt.datetime.fromisoformat(p["sat:anx_datetime"].replace("Z", "+00:00")) if p.get("sat:anx_datetime") else None
    return {
        "id": f["id"],
        "pf": p.get("platform", "")[-2:].upper(),          # 1A / 1B / 1C / 1D
        "t": p["start_datetime"][:19],
        "dir": "A" if p.get("sat:orbit_state", "").startswith("asc") else "D",
        "ro": p.get("sat:relative_orbit"),
        "ao": p.get("sat:absolute_orbit"),
        "anx": round((t0 - anx).total_seconds(), 1) if anx else None,
        "pol": "".join(x[0] + x[1] for x in p.get("sar:polarizations", [])),
        "gb": round(((p.get("_private") or {}).get("product_size") or 0) / 1e9, 2),
        "r": [[round(x, 3), round(y, 3)] for x, y in ring],
    }


def harvest(start, end):
    """All IW SLC items for one time slice, following STAC 'next' links."""
    body = {"collections": ["sentinel-1-slc"], "bbox": BBOX, "limit": 1000,
            "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z",
            "filter": {"op": "=", "args": [{"property": "sar:instrument_mode"}, "IW"]}, "filter-lang": "cql2-json"}
    out, url, method = [], STAC, "POST"
    while url:
        for attempt in range(6):
            try:
                r = S.post(url, json=body, timeout=180) if method == "POST" else S.get(url, timeout=180)
                r.raise_for_status()
                d = r.json()
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 5:
                    raise
                time.sleep(4 * (attempt + 1))
        out.extend(slim(f) for f in d["features"] if f["properties"].get("sar:instrument_mode") == "IW")
        nxt = next((l for l in d.get("links", []) if l["rel"] == "next"), None)
        if not nxt or not d["features"]:
            break
        url, method = nxt["href"], nxt.get("method", "GET").upper()
        if method == "POST":
            body = nxt.get("body", body) if not nxt.get("merge") else {**body, **nxt.get("body", {})}
    return start, out


def month_slices(start, end):
    a = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    while a <= e:
        b = min((a.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1), e)
        yield a.isoformat(), b.isoformat()
        a = b + dt.timedelta(days=1)


def main():
    today = dt.date.today().replace(day=1)
    end = sys.argv[2] if len(sys.argv) > 2 else (today - dt.timedelta(days=1)).isoformat()
    start = sys.argv[1] if len(sys.argv) > 1 else today.replace(year=today.year - 2).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    seen, rows = set(), []
    with cf.ThreadPoolExecutor(6) as ex:
        for s, items in ex.map(lambda se: harvest(*se), list(month_slices(start, end))):
            new = [i for i in items if i["id"] not in seen]
            seen.update(i["id"] for i in new)
            rows.extend(new)
            print(f"{s}: {len(items)} items", flush=True)
    rows.sort(key=lambda i: i["t"])
    with open(OUT / "s1_iw_slc.ndjson", "w") as f:
        for i in rows:
            f.write(json.dumps(i, separators=(",", ":")) + "\n")
    (OUT / "harvest_meta.json").write_text(json.dumps(
        {"source": STAC, "collection": "sentinel-1-slc", "mode": "IW", "bbox": BBOX, "start": start, "end": end,
         "items": len(rows), "harvested": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}, indent=1))
    print(f"total {len(rows)} items {start}..{end}")


if __name__ == "__main__":
    main()
