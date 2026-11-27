# Rebuild every dashboard input from the public sources. Each step skips downloads already on disk.
# Usage:  .\run_pipeline.ps1                 (default S-1 window: last 24 full months)
#         .\run_pipeline.ps1 2024-01-01 2026-10-31
param([string]$Start = "", [string]$End = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python scripts/fetch_vectors.py;                      if (-not $?) { throw "fetch_vectors failed" }
if ($Start) { python scripts/harvest_s1_stac.py $Start $End } else { python scripts/harvest_s1_stac.py }
if (-not $?) { throw "harvest_s1_stac failed" }
python scripts/build_layers.py;                       if (-not $?) { throw "build_layers failed" }
python scripts/terrain_grid.py;                       if (-not $?) { throw "terrain_grid failed" }
python scripts/build_stacks.py;                       if (-not $?) { throw "build_stacks failed" }
python scripts/fetch_tsm.py;                          if (-not $?) { throw "fetch_tsm failed" }
python scripts/calibrate.py;                          if (-not $?) { throw "calibrate failed" }
python scripts/fetch_snow.py;                         if (-not $?) { throw "fetch_snow failed" }
python scripts/fetch_prices.py;                       if (-not $?) { throw "fetch_prices failed" }
python scripts/pack_grid.py;                          if (-not $?) { throw "pack_grid failed" }
Write-Host "Done. Start the dashboard with: node server.js  ->  http://127.0.0.1:8090"
