# Canada Ground-Motion Risk Dashboard

Scopes a national Sentinel-1 InSAR ground-displacement risk layer for Canada. It screens the whole
country for steep terrain likely to fail, weighs it by proximity to highways, rail and communities,
and works out which Sentinel-1 IW SLC stacks (frames) you would need to process, with image counts,
data volume and compute.

Built on the same pattern as the Aleph Archive dashboard (`C:\Data_002\Satellogic_aleph`): no
dependencies, Node static server, Leaflet from a CDN, a canvas layer, collapsible cards, URL state.

## Run

```powershell
node server.js
```
Open http://127.0.0.1:8090. Set `PORT` to change the port and `APP_PASSWORD` to enable HTTP Basic auth.

## Rebuild the data

All inputs are public and the pipeline is repeatable (≈ 30–60 min, mostly the DEM download):

```powershell
.\run_pipeline.ps1
```
```powershell
.\run_pipeline.ps1 2024-01-01 2026-10-31
```
The second form sets the Sentinel-1 harvest window. Python 3.8+ with numpy, scipy, shapely 2, rasterio,
fiona, pyproj, Pillow and requests.

| Step | Script | Output |
|---|---|---|
| Vectors + fire | `scripts/fetch_vectors.py` | `data/raw/*` |
| Sentinel-1 STAC | `scripts/harvest_s1_stac.py` | `data/stac/s1_iw_slc.ndjson` |
| Exposure + fire per cell | `scripts/build_layers.py` | `data/work/layers.npz`, `public/data/*.geojson`, `places.json` |
| Slope per cell | `scripts/terrain_grid.py` | `data/work/terrain.npz` |
| InSAR stacks | `scripts/build_stacks.py` | `public/data/stacks.json` |
| BC terrain stability (calibration) | `scripts/fetch_tsm.py` | `data/raw/tsm/tsm_polygons.csv`, `data/work/tsm_cells.npz` |
| Calibrate susceptibility | `scripts/calibrate.py` | `public/data/calibration.json` |
| Seasonal snow calendar | `scripts/fetch_snow.py` | `data/work/snow.npz` |
| Cloud prices | `scripts/fetch_prices.py` | `public/data/prices.json` |
| Pack for browser | `scripts/pack_grid.py` | `public/data/grid.bin`, `grid.json` |

## Data sources

| Layer | Source | Notes |
|---|---|---|
| Highways | Statistics Canada **Road Network File 2023** (the open.canada.ca record you linked), ArcGIS REST layer 0 | Classes 10–13: Trans-Canada, National Highway System, major and secondary highways |
| Rail | NRCan **National Railway Network (NRWN)**, GeoBase shapefiles per province/territory | Operational track; yards and ferry routes excluded |
| Elevation | **AWS Terrain Tiles** (Terrarium), the same global DEM the Aleph dashboard uses | z10 south of 60° N (~100 m), z9 north of 60° N (≤ 150 m). Aleph's cached tiles are reused |
| Wildfire | CWFIS **National Burned Area Composite**, most-recent-burn raster 1972–2025, 250 m | Burned share in the last 5/10/20 seasons and last burn year per cell |
| Populated places | NRCan **Canadian Geographical Names** (populated places + Indian reserves); StatCan **2021 population centres** | Railway points, post offices, abandoned and former localities are dropped |
| Calibration | BC **Terrain Stability Mapping** (WHSE_TERRESTRIAL_ECOLOGY.STE_TER_STABILITY_POLYS_SVW), BC Data Catalogue WFS | 413k polygons; numeric, Roman and S/P/U class schemes normalised to I–V |
| Snow | NOAA/NSIDC **IMS daily snow and ice analysis** (G02156), 4 km, Jan 2024 → today | Actual snow state on each acquisition date, no login needed |
| Cloud prices | AWS public price files (EC2 on-demand, Spot feed, S3) and the **Azure Retail Prices API** | Canada Central, Linux, USD |
| Provinces / land mask | StatCan 2021 cartographic boundary file | |
| Sentinel-1 | **Copernicus Data Space STAC**, collection `sentinel-1-slc`, `sar:instrument_mode = IW` | Footprint, platform, relative/absolute orbit, ANX time, product size |

## Method

**Grid.** 0.1° × 0.05° cells (about 4–7 km), 890 × 840 over Canada. Every layer is a typed array in
`grid.bin`, so the browser can re-score the whole country instantly as you move the sliders.

**Slope.** Horn slope on every DEM pixel, the same as Aleph's `terrain.js`. Each cell stores mean slope,
mean elevation, relief, and the share of pixels steeper than 10/15/20/25/30/35°. A 100–150 m DEM
smooths short, steep slopes, so these shares are lower bounds. That is why the default steep threshold
is 25° and the saturation is 30 %.

**Failure susceptibility (0–1).**
`min(1, steepShare/saturation × (1 − w + w·min(1, relief/1500 m)) × (1 + amp × burnedShare))`.
Burn scars only amplify terrain that is already steep, since post-fire debris flows need slope. In
heuristic mode this is an uncalibrated screening index; see Calibration below for the default model.

**Calibration (default model).** No open BC landslide *event* inventory exists, so the index is calibrated
against BC Terrain Stability Mapping, where terrain geoscientists rate slopes I–V (IV/V = potentially
unstable / unstable). Sample: 2,116 BC cells with ≥ 20 % of their land classed. Target: ≥ 25 % of the
classed terrain is IV, IVR or V (base rate 54 %). Slope threshold, relief weight, burn window and burn
amplification were grid-searched for spatially cross-validated AUC (5 folds of 1° blocks), then the index
was mapped to a probability with a logistic curve in ln(index):

| | Value |
|---|---|
| Fitted parameters | steep > 25°, relief weight 0, burn window 5 seasons, amplification 1 |
| CV AUC, heuristic defaults → calibrated | 0.902 → **0.914** |
| Unconstrained logistic on all terrain + burn features | 0.911 (upper bound: the simple index loses nothing) |
| Brier score, out-of-fold (climatology) | 0.111 (0.248) |
| Reliability, predicted → observed | 0.02→0.04, 0.17→0.11, 0.38→0.32, 0.64→0.59, 0.89→0.90 |
| Cut-off sensitivity (15 % / 40 %) | 25° wins both times; relief weight ≤ 0.2 |

So in calibrated mode, "High" (≥ 0.5) and "Very high" (≥ 0.75) are probabilities that a cell has at
least a quarter of its terrain rated unstable or potentially unstable. Caveats:
- TSM rates static terrain and much of it predates recent fires, so it **cannot validate the burn term**
  (it moves AUC by < 0.01). The fitted amplification is kept, but treat post-fire amplification as unvalidated.
- TSM mapping concentrates on forested, operable terrain. Alpine and rock terrain, and everything outside
  BC (Rockies in Alberta, North, Québec), is extrapolation.
- Mapped mass-movement processes (R/F codes), used as an independent check, give AUC 0.74 for both models.

The **Heuristic** model option keeps the original manually weighted index with free sliders.

**Exposure (0–1).** `max over ticked assets of (1 − distance/radius)`, using the distance from the cell
centre to the nearest highway, rail track, population centre (0 inside) or community.
**Risk = susceptibility × exposure.** Classes: Minimal < 0.1, Low < 0.25, Moderate < 0.5, High < 0.75,
Very high ≥ 0.75.

**Sentinel-1 stacks.** ESA slices each datatake differently, so product footprints shift from pass to
pass. Stacks are defined on a fixed along-track grid instead: each relative orbit is cut into
9-burst windows (24.82 s ≈ 170 km) counted from the ascending-node crossing. A pass (platform +
absolute orbit) joins a frame when its products cover at least 50 % of the window. The products
touching the window are the SLCs to download for that date. The dashboard then:

1. marks the target cells (high+ risk or susceptibility, nationally or in view),
2. keeps the frames with at least *N* acquisitions in the chosen date window and platforms,
3. runs a greedy set-cover to find the **fewest frames** covering the targets, per geometry
   (ascending and descending separately, best single geometry, or one direction only),
4. reports stack-acquisitions, **unique** SLC products and TB (products shared by neighbouring frames
   counted once), approximate burst-SLCs (frames split 9 along × 3 sub-swaths; only bursts that
   contain targets count), and core-hours.

Exports: a stacks CSV, the full SLC product list per frame and date (ready for a download script),
and frame footprints as GeoJSON.

**Seasonal snow.** Wet or fresh snow destroys C-band coherence, so winter passes over seasonally snow-covered
targets add noise rather than signal. From IMS daily snow maps (2024 to today), each cell gets its snow-free
season per year: the longest snow-free run after a 15-day running majority filter. Coastal cells use the nearest
IMS land pixel so open water doesn't read as "snow-free". Cells count as **permanent snow/ice** (imagery
always kept) when they have no ≥ 20-day snow-free run in any complete year, or fewer than 45 snow-free days a
year on average (ice caps whose bare ice briefly reads snow-free). That is 22.6k cells, mostly Arctic ice caps,
mountain icefields and the coldest high-Arctic plateaus.

With **Drop acquisitions under seasonal snow** on (default), each frame-acquisition is checked against the
snow state of that frame's target cells on that date. It is dropped when more than X % (default 50 %) of them
are under seasonal snow. Dropped passes leave the counts, TB, CPU and cost figures, show grey in the monthly
chart, appear as "−n" in the stack table, and are excluded from the SLC list. The S-1 acquisitions map layer
then counts, for each cell, only the passes when that cell itself was snow-free. With default settings this
drops 34 % of frame-acquisitions (24.3 → 16.2 TB).

**Cloud cost.** CPU-hours are converted to VM-hours on like-for-like 64-vCPU / 256 GiB Linux VMs in Canada
Central (AWS m7i.16xlarge, Azure D64s v5), on-demand and spot, using list prices fetched by
`scripts/fetch_prices.py`. Storage is the SLC download × a multiplier (default 2×, for coregistered stacks
and products) × months kept, at S3 Standard / Blob Hot LRS rates. SLC download from the Copernicus Data
Space and cloud ingress are free. At default settings **storage dominates**: about $180–190 compute
on-demand versus about $3.9–4.9k for six months of storage. So keeping time and burst-only downloads matter
more than the VM choice. The 2 core-hours per stack-acquisition default is a light coregistration +
interferogram budget; PS/SBAS time-series inversion can need several times that, so set it to your processor's
measured rate.

## Limits

- The DEM is coarse, so slope classes are approximate. The model has no geology, permafrost,
  precipitation or landslide-inventory calibration.
- Burst positions are approximated from the frame footprint. For exact burst IDs, use the ESA
  burst-ID map or the CDSE burst API.
- Highways cover RNF classes 10–13 only. Resource and local roads are excluded.
- The CDSE STAC `product_size` gives the SLC zip size, which is what you download.

## Deploy

The image only serves `public/` (the pipeline runs locally; commit the refreshed `public/data` to redeploy).
No API credentials are involved — every input is open data.

### Render (recommended)

[`render.yaml`](render.yaml) is a Render Blueprint:

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, pick the repo.
3. Render generates `APP_PASSWORD` (see the service's **Environment** tab). Sign in as user `risk` with that password.

The free plan sleeps after 15 minutes idle (about a minute to wake). Pushes to `main` redeploy automatically.

### Any container host

```powershell
docker build -t canada-risk .
```
```powershell
docker run -p 8090:8090 -e APP_USER=risk -e APP_PASSWORD=choose-one canada-risk
```

Set `APP_PASSWORD` whenever others can reach the app: it turns on HTTP Basic auth for every route except
`/healthz`. Leave it unset only to publish openly. The susceptibility model is calibrated for BC only, so
keep the dashboard's model notes visible when sharing outside the team.
