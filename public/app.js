/* Canada Ground-Motion Risk - frontend (Leaflet, no build step). Pattern follows the Aleph Archive dashboard. */
'use strict';

const $ = (id) => document.getElementById(id);
const DAY = 86400e3;

// ------------------------------------------------------------------ map + basemaps (as Aleph)
const map = L.map('map', { minZoom: 3, zoomControl: true, preferCanvas: true }).setView([58, -96], 4);
const streets = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' });
const gray = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}', { maxZoom: 16, attribution: 'Tiles &copy; Esri' });
const imagery = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics' });
const labels = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, pane: 'shadowPane' });
const dark = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}', { maxZoom: 16, attribution: 'Tiles &copy; Esri' });
const hillshade = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}', { maxZoom: 16, attribution: 'Hillshade &copy; Esri' });
const satellite = L.layerGroup([imagery, labels]);
const terrainBase = L.layerGroup([hillshade, L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19, pane: 'shadowPane' })]);
gray.addTo(map);
L.control.layers({ Map: streets, 'Light gray': gray, Hillshade: terrainBase, Satellite: satellite, Dark: dark }, null, { position: 'topright' }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);
map.createPane('gridPane').style.zIndex = 350;
map.createPane('vecPane').style.zIndex = 420;
map.createPane('framePane').style.zIndex = 430;
const vecRenderer = L.canvas({ pane: 'vecPane', padding: 0.3 });
const frameRenderer = L.canvas({ pane: 'framePane', padding: 0.3 });

// ------------------------------------------------------------------ data
let CAL = null; // BC TSM calibration (scripts/calibrate.py)
const G = { meta: null, L: {}, nx: 0, ny: 0, land: null, rowArea: null };
const S = { data: null, stacks: [], products: [], day0: 0, cells: [], bursts: [], byTrack: new Map() };
const D = { places: [], placeCell: null, pcs: [] };
const M = { haz: null, exp: null, risk: null, hazCls: null, riskCls: null, target: null, s1A: null, s1D: null, sel: [], covered: null };

const CLASSES = [
  { key: 0, label: 'Minimal', range: '< 0.10', color: null },
  { key: 1, label: 'Low', range: '0.10–0.25', color: '#f6d3b8' },
  { key: 2, label: 'Moderate', range: '0.25–0.50', color: '#ee9a66' },
  { key: 3, label: 'High', range: '0.50–0.75', color: '#d95926' },
  { key: 4, label: 'Very high', range: '≥ 0.75', color: '#8f2f0c' },
];
const clsOf = (v) => (v < 0.1 ? 0 : v < 0.25 ? 1 : v < 0.5 ? 2 : v < 0.75 ? 3 : 4);
const SLOPE_CLASSES = [ // same classes as the Aleph terrain panel (mean slope per cell)
  { key: 'flat', label: 'Flat', range: '< 3°', color: '#9fd18b' },
  { key: 'rolling', label: 'Rolling', range: '3–8°', color: '#e8d56b' },
  { key: 'moderate', label: 'Moderately steep', range: '8–15°', color: '#e8954a' },
  { key: 'steep', label: 'Steep', range: '≥ 15°', color: '#c4433b' },
];
const slopeCls = (s) => (s < 3 ? 0 : s < 8 ? 1 : s < 15 ? 2 : 3);
const BLUE = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
const ORANGE = ['#fbe3d3', '#f6c3a1', '#ee9a66', '#e5743c', '#d95926', '#b0441a', '#7e2e0e'];
const VIOLET = ['#ede9fe', '#c4b5fd', '#a78bfa', '#8b5cf6', '#7c3aed', '#5b21b6', '#3b0f7a'];
const PF = { '1A': { label: 'S1A', color: '#2a78d6', cls: 's1a' }, '1C': { label: 'S1C', color: '#1baf7a', cls: 's1c' }, '1D': { label: 'S1D', color: '#eda100', cls: 's1d' } };

function hex(h) { return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)); }
function ramp(stops, t) {
  t = Math.max(0, Math.min(1, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(t)); const f = t - i;
  const a = hex(stops[i]); const b = hex(stops[i + 1]);
  return a.map((v, k) => Math.round(v + (b[k] - v) * f));
}
const rgbCss = (c) => `rgb(${c.join(',')})`;

async function load() {
  setConn('loading…');
  const [meta, bin, stacks, places, pcs] = await Promise.all([
    fetch('data/grid.json').then((r) => r.json()),
    fetch('data/grid.bin').then((r) => r.arrayBuffer()),
    fetch('data/stacks.json').then((r) => r.json()),
    fetch('data/places.json').then((r) => r.json()),
    fetch('data/popcentres.json').then((r) => r.json()),
  ]);
  CAL = await fetch('data/calibration.json').then((r) => (r.ok ? r.json() : null)).catch(() => null);
  G.meta = meta; G.nx = meta.nx; G.ny = meta.ny;
  const n = meta.nx * meta.ny;
  for (const l of meta.layers) G.L[l.name] = new globalThis[`${l.type}Array`](bin, l.offset, n);
  G.scale = Object.fromEntries(meta.layers.map((l) => [l.name, l.scale]));
  G.rowArea = new Float64Array(meta.ny);
  for (let r = 0; r < meta.ny; r++) {
    const lat = meta.n - (r + 0.5) * meta.dlat;
    G.rowArea[r] = meta.dlat * 111.32 * meta.dlon * 111.32 * Math.cos((lat * Math.PI) / 180);
  }
  const land = []; for (let i = 0; i < n; i++) if (G.L.land[i] > 2) land.push(i);
  G.land = Int32Array.from(land);

  D.places = places.p.map((p) => ({ x: p[0], y: p[1], n: p[2], t: places.terms[p[3]] }));
  D.placeCell = Int32Array.from(D.places.map((p) => cellAt(p.y, p.x)));
  D.pcs = pcs;

  S.data = stacks; S.products = stacks.products; S.stacks = stacks.stacks;
  S.day0 = Date.parse(stacks.start + 'T00:00:00Z');
  prepStacks();

  const th = $('thresh');
  th.innerHTML = meta.thresholds.map((t) => `<option value="${t}"${t === 25 ? ' selected' : ''}>${t}°</option>`).join('');
  if (!CAL) { $('model').value = 'heur'; $('model').disabled = true; $('model').title = 'Run scripts/calibrate.py to enable'; }
  $('from').min = $('to').min = stacks.start; $('from').max = $('to').max = stacks.end;
  $('from').value = stacks.start; $('to').value = stacks.end;
  $('stacNote').textContent = `Sentinel-1 IW SLC from ${new URL(stacks.source).host} STAC, ${stacks.start} → ${stacks.end}: `
    + `${S.products.length.toLocaleString()} products over Canada in ${S.stacks.length} fixed frames (9 bursts ≈ ${stacks.frame_s.toFixed(1)} s along track). `
    + `A pass joins a frame when it covers ≥ ${Math.round(stacks.min_cover * 100)}% of it. Harvested ${stacks.harvested.slice(0, 10)}.`;
  $('sources').innerHTML = '<b>Sources</b><br>' + Object.values(meta.sources).map(esc).join('<br>');
  loadUrl();
  applyModel(false);
  setConn(`${G.land.length.toLocaleString()} cells`, true);
  recomputeAll();
}

const cellAt = (lat, lon) => {
  const m = G.meta; const c = Math.floor((lon - m.w) / m.dlon); const r = Math.floor((m.n - lat) / m.dlat);
  return c >= 0 && c < m.nx && r >= 0 && r < m.ny ? r * m.nx + c : -1;
};
const cellLatLng = (i) => [G.meta.n - (Math.floor(i / G.nx) + 0.5) * G.meta.dlat, G.meta.w + ((i % G.nx) + 0.5) * G.meta.dlon];
const val = (name, i) => G.L[name][i] * G.scale[name];
const cellArea = (i) => G.rowArea[Math.floor(i / G.nx)] * G.L.land[i] / 255;

// ------------------------------------------------------------------ Sentinel-1 stacks: rasterise frames onto the grid
function prepStacks() {
  const m = G.meta;
  for (const s of S.stacks) {
    const key = s.dir + s.ro;
    if (!S.byTrack.has(key)) S.byTrack.set(key, []);
    S.byTrack.get(key).push(s);
  }
  for (const list of S.byTrack.values()) list.sort((a, b) => a.k - b.k);
  S.stacks.forEach((s, si) => {
    s.si = si;
    // along-track axis from neighbouring frames on the same track (fallback: N/S)
    const list = S.byTrack.get(s.dir + s.ro); const j = list.indexOf(s);
    const prev = list[j - 1] && list[j - 1].k === s.k - 1 ? list[j - 1] : null;
    const next = list[j + 1] && list[j + 1].k === s.k + 1 ? list[j + 1] : null;
    const kx = Math.cos((s.c[1] * Math.PI) / 180);
    let ax; let ay;
    if (prev || next) {
      const a = prev ? prev.c : s.c; const b = next ? next.c : s.c;
      ax = (b[0] - a[0]) * kx; ay = b[1] - a[1];
    } else { ax = 0; ay = s.dir === 'A' ? 1 : -1; }
    const norm = Math.hypot(ax, ay) || 1; ax /= norm; ay /= norm;
    const proj = (lon, lat) => [((lon - s.c[0]) * kx) * ax + (lat - s.c[1]) * ay, -((lon - s.c[0]) * kx) * ay + (lat - s.c[1]) * ax];
    let u0 = Infinity; let u1 = -Infinity; let v0 = Infinity; let v1 = -Infinity;
    for (const [lon, lat] of s.r) { const [u, v] = proj(lon, lat); u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
    const cells = []; const bursts = [];
    fillPolygon(s.r, (i) => {
      if (G.L.land[i] <= 2) return;
      const [lat, lon] = cellLatLng(i); const [u, v] = proj(lon, lat);
      const bu = Math.min(8, Math.max(0, Math.floor(((u - u0) / (u1 - u0 || 1)) * 9)));
      const bv = Math.min(2, Math.max(0, Math.floor(((v - v0) / (v1 - v0 || 1)) * 3)));
      cells.push(i); bursts.push(bu * 3 + bv);
    });
    S.cells[si] = Int32Array.from(cells); S.bursts[si] = Uint8Array.from(bursts);
    s.bbox = [Math.min(...s.r.map((p) => p[0])), Math.min(...s.r.map((p) => p[1])), Math.max(...s.r.map((p) => p[0])), Math.max(...s.r.map((p) => p[1]))];
  });
}

// Scanline fill on cell centres; calls cb(cellIndex) for every cell whose centre is inside the ring.
function fillPolygon(ring, cb) {
  const m = G.meta;
  let ymin = Infinity; let ymax = -Infinity;
  for (const p of ring) { ymin = Math.min(ymin, p[1]); ymax = Math.max(ymax, p[1]); }
  const r0 = Math.max(0, Math.floor((m.n - ymax) / m.dlat)); const r1 = Math.min(m.ny - 1, Math.floor((m.n - ymin) / m.dlat));
  for (let r = r0; r <= r1; r++) {
    const lat = m.n - (r + 0.5) * m.dlat; const xs = [];
    for (let k = 0; k < ring.length - 1; k++) {
      const [x1, y1] = ring[k]; const [x2, y2] = ring[k + 1];
      if ((y1 <= lat && y2 > lat) || (y2 <= lat && y1 > lat)) xs.push(x1 + ((lat - y1) / (y2 - y1)) * (x2 - x1));
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const c0 = Math.max(0, Math.ceil((xs[k] - m.w) / m.dlon - 0.5)); const c1 = Math.min(m.nx - 1, Math.floor((xs[k + 1] - m.w) / m.dlon - 0.5));
      for (let c = c0; c <= c1; c++) cb(r * m.nx + c);
    }
  }
}

// ------------------------------------------------------------------ model
function readParams() {
  return {
    model: $('model').value, T: Number($('thresh').value), fsat: Number($('fsat').value) / 100, wr: Number($('relief').value),
    burnWin: $('burnWin').value, amp: Number($('burnAmp').value), R: Number($('radius').value),
    aHw: $('aHw').checked, aRl: $('aRl').checked, aPc: $('aPc').checked, aPl: $('aPl').checked,
  };
}

function computeModel() {
  const p = readParams(); const n = G.nx * G.ny;
  if (!M.haz) { M.haz = new Float32Array(n); M.exp = new Float32Array(n); M.risk = new Float32Array(n); M.hazCls = new Uint8Array(n); M.riskCls = new Uint8Array(n); }
  const steep = G.L[`f${p.T}`]; const relief = G.L.relief; const burn = G.L[`burn${p.burnWin}`];
  const hwd = G.L.hw_d; const rld = G.L.rl_d; const pcd = G.L.pc_d; const pld = G.L.pl_d;
  const invR = 1 / (p.R * 10); // distance codes are 0.1 km
  const cal = p.model === 'cal' && CAL; const la = cal ? CAL.logit.a : 0; const lb = cal ? CAL.logit.b : 0; const le = cal ? CAL.logit.eps : 0;
  for (const i of G.land) {
    const raw = (steep[i] / 255) * (1 - p.wr + p.wr * Math.min(1, relief[i] / 1500)) * (1 + p.amp * (burn[i] / 255));
    // calibrated: P(cell has >= X% unstable terrain) from the BC TSM logistic fit; heuristic: capped index
    const h = cal ? 1 / (1 + Math.exp(-(la + lb * Math.log(raw + le)))) : (raw === 0 ? 0 : Math.min(1, raw / p.fsat));
    let e = 0;
    if (p.aHw) e = Math.max(e, 1 - hwd[i] * invR);
    if (p.aRl) e = Math.max(e, 1 - rld[i] * invR);
    if (p.aPc) e = Math.max(e, 1 - pcd[i] * invR);
    if (p.aPl) e = Math.max(e, 1 - pld[i] * invR);
    e = Math.max(0, e);
    M.haz[i] = h; M.exp[i] = e; M.risk[i] = h * e;
    M.hazCls[i] = clsOf(h); M.riskCls[i] = clsOf(h * e);
  }
}

// Calibrated mode pins the ranking parameters to the fitted values; heuristic mode leaves them free.
function applyModel(recompute) {
  const cal = $('model').value === 'cal' && CAL;
  if (cal) {
    $('thresh').value = CAL.params.T; $('relief').value = CAL.params.w; $('burnWin').value = CAL.params.win; $('burnAmp').value = CAL.params.amp;
    $('reliefOut').textContent = Number(CAL.params.w).toFixed(1); $('burnOut').textContent = Number(CAL.params.amp).toFixed(2);
  }
  ['thresh', 'relief', 'burnWin', 'burnAmp', 'fsat'].forEach((id) => { $(id).disabled = !!cal; });
  $('calBox').hidden = !cal;
  if (cal) {
    const m = CAL.metrics;
    $('calBox').innerHTML = `<div class="kpis three">
        <div><b>${m.auc_calibrated.toFixed(2)}</b><span>CV AUC (was ${m.auc_default.toFixed(2)})</span></div>
        <div><b>${m.auc_full_logistic.toFixed(2)}</b><span>AUC upper bound</span></div>
        <div><b>${(CAL.sample.base_rate * 100).toFixed(0)}%</b><span>base rate, ${CAL.sample.cells.toLocaleString()} cells</span></div></div>
      <table class="classes rel"><tr><td class="muted">Predicted</td><td class="muted">cells</td><td class="muted">mean P</td><td class="muted">observed</td></tr>
      ${CAL.reliability.map((r) => `<tr><td>${r.bin[0].toFixed(2)}–${r.bin[1].toFixed(2)}</td><td>${r.n.toLocaleString()}</td><td>${r.pred.toFixed(2)}</td><td>${r.obs.toFixed(2)}</td></tr>`).join('')}</table>`;
  }
  $('hazNote').innerHTML = cal
    ? `Susceptibility = P(${esc(CAL.target.replace('cell has', 'cell has'))}), logistic in ln(index), index = steep share &gt; ${CAL.params.T}° × relief term (w ${CAL.params.w}) × (1 + ${CAL.params.amp} × burned share, ${CAL.params.win} seasons). Fitted on ${CAL.sample.cells.toLocaleString()} BC cells mapped by terrain geoscientists, validated on held-out 1° blocks (table: out-of-fold). TSM rates static terrain, so it cannot confirm the burn term (it moves AUC by &lt; 0.01). Outside BC this is an extrapolation.`
    : 'Susceptibility = steep share ÷ saturation × (1 − w + w·relief/1500 m) × (1 + amp · burned share), capped at 1. Uncalibrated screening index from a ~100–150 m DEM, not a probability.';
  if (recompute) recomputeAll();
}

// ------------------------------------------------------------------ grid canvas layer
const GridLayer = L.Layer.extend({
  onAdd(m) {
    this._map = m;
    this._canvas = L.DomUtil.create('canvas', 'leaflet-zoom-hide');
    this._canvas.style.pointerEvents = 'none';
    m.getPane('gridPane').appendChild(this._canvas);
    m.on('moveend zoomend resize', this.redraw, this);
    this.redraw();
  },
  onRemove(m) { m.off('moveend zoomend resize', this.redraw, this); this._canvas.remove(); },
  redraw() {
    if (!this._map) return;
    const m = this._map; const size = m.getSize(); const dpr = window.devicePixelRatio || 1; const c = this._canvas;
    L.DomUtil.setPosition(c, m.containerPointToLayerPoint([0, 0]));
    c.width = size.x * dpr; c.height = size.y * dpr; c.style.width = size.x + 'px'; c.style.height = size.y + 'px';
    const ctx = c.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, size.x, size.y);
    if (!offscreen || $('layerSel').value === 'none') return;
    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = Number($('opacity').value) / 100;
    const g = G.meta; const b = m.getBounds();
    const r0 = Math.max(0, Math.floor((g.n - b.getNorth()) / g.dlat)); const r1 = Math.min(g.ny - 1, Math.floor((g.n - b.getSouth()) / g.dlat));
    const x0 = m.latLngToContainerPoint([g.n, g.w]).x; const x1 = m.latLngToContainerPoint([g.n, g.e]).x;
    let prevY = m.latLngToContainerPoint([g.n - r0 * g.dlat, g.w]).y;
    for (let r = r0; r <= r1; r++) {
      const y = m.latLngToContainerPoint([g.n - (r + 1) * g.dlat, g.w]).y;
      ctx.drawImage(offscreen, 0, r, g.nx, 1, x0, prevY, x1 - x0, Math.max(0.5, y - prevY + 0.35));
      prevY = y;
    }
  },
});
let offscreen = null;
const gridLayer = new GridLayer().addTo(map);

function colourCell(layer, i) { // -> [r,g,b] or null (transparent)
  switch (layer) {
    case 'risk': { const k = M.riskCls[i]; return k ? hex(CLASSES[k].color) : null; }
    case 'haz': { const k = M.hazCls[i]; return k ? hex(CLASSES[k].color) : null; }
    case 'exp': return M.exp[i] > 0 ? ramp(BLUE, M.exp[i]) : null;
    case 'slope': return hex(SLOPE_CLASSES[slopeCls(val('slope', i))].color);
    case 'steep': { const v = G.L[`f${$('thresh').value}`][i] / 255; return v > 0.005 ? ramp(ORANGE, Math.sqrt(v)) : null; }
    case 'relief': return ramp(BLUE, Math.min(1, G.L.relief[i] / 2000));
    case 'burn': { const y = G.L.burn_last[i]; return y ? ramp(VIOLET, (y - 1972) / (2025 - 1972)) : null; }
    case 'tsm': { const cv = G.L.tsm_cover ? G.L.tsm_cover[i] : 0; if (cv < 51) return null; return hex(CLASSES[clsOf(G.L.tsm_unst[i] / 255 / 0.5)].color || '#e9e7e1'); }
    case 's1': { const v = M.s1A[i] + M.s1D[i]; return v ? ramp(BLUE, Math.min(1, v / s1Max())) : null; }
    case 'targets': return M.target[i] ? (M.covered[i] ? [208, 59, 59] : [120, 120, 120]) : null;
    default: return null;
  }
}
let s1MaxCache = 1;
const s1Max = () => s1MaxCache;

function rebuildImage() {
  const g = G.meta; const layer = $('layerSel').value;
  if (!offscreen) { offscreen = document.createElement('canvas'); offscreen.width = g.nx; offscreen.height = g.ny; }
  const ctx = offscreen.getContext('2d'); const img = ctx.createImageData(g.nx, g.ny); const d = img.data;
  if (layer === 's1') { let mx = 1; for (const i of G.land) mx = Math.max(mx, M.s1A[i] + M.s1D[i]); s1MaxCache = mx; }
  for (const i of G.land) {
    const c = colourCell(layer, i);
    if (!c) continue;
    const o = i * 4; d[o] = c[0]; d[o + 1] = c[1]; d[o + 2] = c[2]; d[o + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  gridLayer.redraw();
  updateLegend();
}

function updateLegend() {
  const el = $('legend'); const layer = $('layerSel').value; el.hidden = layer === 'none';
  const grad = (stops) => `linear-gradient(90deg, ${stops.join(',')})`;
  const calNote = $('model').value === 'cal' && CAL ? '<div class="muted">calibrated to BC terrain stability mapping</div>' : '';
  const classes = (title) => `<b>${title}</b>${calNote}` + CLASSES.slice(1).map((c) => `<div class="row-sw"><span class="swatch" style="background:${c.color}"></span>${c.label} <span class="muted">${c.range}</span></div>`).join('');
  if (layer === 'risk') el.innerHTML = classes('Risk (susceptibility × exposure)');
  else if (layer === 'haz') el.innerHTML = classes('Failure susceptibility');
  else if (layer === 'exp') el.innerHTML = `<b>Exposure</b><div class="ramp" style="background:${grad(BLUE)}"></div><div class="ends"><span>0 (≥ ${$('radius').value} km)</span><span>1 (at asset)</span></div>`;
  else if (layer === 'slope') el.innerHTML = '<b>Mean slope per cell</b>' + SLOPE_CLASSES.map((c) => `<div class="row-sw"><span class="swatch" style="background:${c.color}"></span>${c.label} <span class="muted">${c.range}</span></div>`).join('');
  else if (layer === 'steep') el.innerHTML = `<b>Share of cell steeper than ${$('thresh').value}°</b><div class="ramp" style="background:${grad(ORANGE)}"></div><div class="ends"><span>0%</span><span>25%</span><span>100%</span></div>`;
  else if (layer === 'relief') el.innerHTML = `<b>Relief (max − min elevation)</b><div class="ramp" style="background:${grad(BLUE)}"></div><div class="ends"><span>0 m</span><span>≥ 2000 m</span></div>`;
  else if (layer === 'burn') el.innerHTML = `<b>Most recent burn (NBAC)</b><div class="ramp" style="background:${grad(VIOLET)}"></div><div class="ends"><span>1972</span><span>2025</span></div>`;
  else if (layer === 'tsm') el.innerHTML = '<b>BC terrain stability mapping</b><div class="muted">share of classed terrain in class IV/IVR/V<br>(cells with ≥ 20% mapped)</div>'
    + [['#e9e7e1', '< 5%'], [CLASSES[1].color, '5–12.5%'], [CLASSES[2].color, '12.5–25%'], [CLASSES[3].color, '25–37.5%'], [CLASSES[4].color, '≥ 37.5%']].map(([c, t]) => `<div class="row-sw"><span class="swatch" style="background:${c}"></span>${t}</div>`).join('');
  else if (layer === 's1') el.innerHTML = `<b>S-1 IW acquisitions per cell</b><div class="ramp" style="background:${grad(BLUE)}"></div><div class="ends"><span>1</span><span>${s1MaxCache.toLocaleString()}</span></div><div class="muted">asc + desc, date window &amp; platforms applied</div>`;
  else if (layer === 'targets') el.innerHTML = '<b>Processing targets</b><div class="row-sw"><span class="swatch" style="background:#d03b3b"></span>Covered by selected frames</div><div class="row-sw"><span class="swatch" style="background:#787878"></span>Not covered (too few acquisitions)</div>';
}

// ------------------------------------------------------------------ view statistics
function viewRange() {
  const g = G.meta; const b = map.getBounds();
  return {
    r0: Math.max(0, Math.floor((g.n - b.getNorth()) / g.dlat)), r1: Math.min(g.ny - 1, Math.floor((g.n - b.getSouth()) / g.dlat)),
    c0: Math.max(0, Math.floor((b.getWest() - g.w) / g.dlon)), c1: Math.min(g.nx - 1, Math.floor((b.getEast() - g.w) / g.dlon)),
  };
}

function updateStats() {
  const { r0, r1, c0, c1 } = viewRange();
  const hz = [0, 0, 0, 0, 0]; const sl = [0, 0, 0, 0];
  let landA = 0; let riskA = 0; let hwKm = 0; let rlKm = 0;
  const hot = new Map(); const prov = new Map();
  const land = G.L.land;
  for (let r = r0; r <= r1; r++) {
    const ra = G.rowArea[r];
    for (let c = c0; c <= c1; c++) {
      const i = r * G.nx + c; if (land[i] <= 2) continue;
      const a = ra * land[i] / 255; landA += a;
      hz[M.hazCls[i]] += a; sl[slopeCls(val('slope', i))] += a;
      const high = M.hazCls[i] >= 3; const rk = M.riskCls[i] >= 3;
      if (high) { hwKm += G.L.hw_len[i] * 0.01; rlKm += G.L.rl_len[i] * 0.01; }
      if (rk) {
        riskA += a;
        const [lat, lon] = cellLatLng(i); const k = `${Math.floor(lon)}:${Math.floor(lat)}`;
        hot.set(k, (hot.get(k) || 0) + a);
      }
      if (high || rk) {
        const pc = G.L.prov[i]; const o = prov.get(pc) || { h: 0, r: 0, hw: 0, rl: 0 };
        if (high) { o.h += a; o.hw += G.L.hw_len[i] * 0.01; o.rl += G.L.rl_len[i] * 0.01; }
        if (rk) o.r += a;
        prov.set(pc, o);
      }
    }
  }
  // places sitting in or next to a high+ susceptibility cell
  let nPl = 0;
  const b = map.getBounds();
  D.places.forEach((p, k) => {
    if (!b.contains([p.y, p.x])) return;
    const i = D.placeCell[k]; if (i < 0) return;
    for (let dr = -1; dr <= 1; dr++) for (let dc = -1; dc <= 1; dc++) {
      const j = i + dr * G.nx + dc;
      if (j >= 0 && j < M.hazCls.length && M.hazCls[j] >= 3) { nPl++; return; }
    }
  });
  const highA = hz[3] + hz[4];
  $('kLand').textContent = fmtInt(landA); $('kHigh').textContent = fmtInt(highA); $('kRisk').textContent = fmtInt(riskA);
  $('kHw').textContent = fmtInt(hwKm); $('kRl').textContent = fmtInt(rlKm); $('kPl').textContent = fmtInt(nPl);
  $('viewNote').textContent = map.getZoom() <= 4 ? '(national)' : '';
  stackBar('hazBar', 'hazTable', CLASSES.map((c, k) => ({ label: c.label, range: c.range, color: c.color || 'var(--line)', a: hz[k] })), landA);
  stackBar('slopeBar', 'slopeTable', SLOPE_CLASSES.map((c, k) => ({ label: c.label, range: c.range, color: c.color, a: sl[k] })), landA);
  const top = [...hot.entries()].sort((a, b2) => b2[1] - a[1]).slice(0, 8);
  $('topCells').innerHTML = top.length ? top.map(([k, a]) => {
    const [x, y] = k.split(':').map(Number);
    return `<li class="clickable" data-x="${x}" data-y="${y}"><span>${esc(nearestPc(y + 0.5, x + 0.5))} <span class="muted">${fmtLat(y + 0.5)} ${fmtLng(x + 0.5)}</span></span><span>${fmtInt(a)}</span></li>`;
  }).join('') : '<li class="muted">No high-risk cells in view</li>';
  const pv = [...prov.entries()].sort((a, b2) => b2[1].h - a[1].h).slice(0, 13);
  $('byProv').innerHTML = '<tr><td class="muted">Province</td><td class="muted">high+ km²</td><td class="muted">risk km²</td><td class="muted">hwy km</td><td class="muted">rail km</td></tr>'
    + pv.map(([code, o]) => `<tr><td title="${esc(G.meta.provinces[code] || code)}">${esc(G.meta.provinces[code] || code)}</td><td>${fmtInt(o.h)}</td><td>${fmtInt(o.r)}</td><td>${fmtInt(o.hw)}</td><td>${fmtInt(o.rl)}</td></tr>`).join('');
}

function stackBar(barId, tableId, rows, total) {
  $(barId).innerHTML = rows.map((r) => (r.a > 0 ? `<span style="width:${(r.a / (total || 1)) * 100}%;background:${r.color}" title="${esc(r.label)}"></span>` : '')).join('');
  $(tableId).innerHTML = rows.map((r) => `<tr><td><span class="swatch" style="background:${r.color}"></span>${esc(r.label)}</td><td class="muted">${r.range}</td><td>${pct(r.a, total)}</td><td>${fmtInt(r.a)} km²</td></tr>`).join('');
}

function nearestPc(lat, lon) {
  let best = null; let bd = Infinity; const kx = Math.cos((lat * Math.PI) / 180);
  for (const p of D.pcs) { const d = ((p.x - lon) * kx) ** 2 + (p.y - lat) ** 2; if (d < bd) { bd = d; best = p; } }
  return best ? `near ${best.n}` : '';
}

// ------------------------------------------------------------------ processing scope
function scopeParams() {
  const f = Date.parse($('from').value + 'T00:00:00Z'); const t = Date.parse($('to').value + 'T00:00:00Z');
  return {
    target: $('target').value, area: $('scopeArea').value, geom: $('geom').value, minAcq: Number($('minAcq').value) || 1,
    d0: Math.round((f - S.day0) / DAY), d1: Math.round((t - S.day0) / DAY),
    pfs: new Set([...document.querySelectorAll('.pf:checked')].map((e) => e.value)), cpu: Number($('cpu').value),
  };
}

function computeScope() {
  const p = scopeParams(); const n = G.nx * G.ny;
  if (!M.target) { M.target = new Uint8Array(n); M.covered = new Uint8Array(n); M.s1A = new Uint16Array(n); M.s1D = new Uint16Array(n); }
  M.target.fill(0); M.covered.fill(0); M.s1A.fill(0); M.s1D.fill(0);
  const cls = p.target.startsWith('risk') ? M.riskCls : M.hazCls; const minCls = Number(p.target.slice(-1));
  let vr = null; if (p.area === 'view') vr = viewRange();
  let targetA = 0;
  for (const i of G.land) {
    if (cls[i] < minCls) continue;
    if (vr) { const r = Math.floor(i / G.nx); const c = i % G.nx; if (r < vr.r0 || r > vr.r1 || c < vr.c0 || c > vr.c1) continue; }
    M.target[i] = 1; targetA += cellArea(i);
  }
  // acquisitions per stack in window
  for (const s of S.stacks) s.acqF = s.acq.filter((a) => a[0] >= p.d0 && a[0] <= p.d1 && p.pfs.has(a[1]));
  // per-cell acquisition counts: max over frames of a track, summed over tracks
  for (const list of S.byTrack.values()) {
    const tmp = new Map();
    for (const s of list) { const v = s.acqF.length; if (!v) continue; for (const i of S.cells[s.si]) if ((tmp.get(i) || 0) < v) tmp.set(i, v); }
    const arr = list[0].dir === 'A' ? M.s1A : M.s1D;
    for (const [i, v] of tmp) arr[i] = Math.min(65535, arr[i] + v);
  }
  // candidate stacks and greedy set cover (fewest frames that cover the targets)
  const cand = S.stacks.filter((s) => s.acqF.length >= p.minAcq && (p.geom === 'both' || p.geom === 'best' || s.dir === p.geom));
  for (const s of cand) { let a = 0; for (const i of S.cells[s.si]) if (M.target[i]) a += cellArea(i); s.tA = a; }
  const runCover = (stacks) => {
    const cov = new Uint8Array(n); const chosen = [];
    let pool = stacks.filter((s) => s.tA > 0).map((s) => ({ s, gain: s.tA }));
    while (pool.length) {
      pool.sort((a, b) => b.gain - a.gain || b.s.acqF.length - a.s.acqF.length);
      const top = pool[0]; let g = 0;
      for (const i of S.cells[top.s.si]) if (M.target[i] && !cov[i]) g += cellArea(i);
      if (g <= 0.5) { pool.shift(); continue; }
      if (pool.length > 1 && g < pool[1].gain) { top.gain = g; continue; } // lazy greedy re-evaluation
      chosen.push(top.s); pool.shift();
      for (const i of S.cells[top.s.si]) if (M.target[i]) cov[i] = 1;
    }
    return { chosen, cov };
  };
  let sel = []; const covDir = {};
  if (p.geom === 'both') {
    for (const d of ['A', 'D']) { const r = runCover(cand.filter((s) => s.dir === d)); sel = sel.concat(r.chosen); covDir[d] = r.cov; }
  } else {
    const r = runCover(cand); sel = r.chosen; covDir.any = r.cov;
  }
  M.sel = sel;
  let covA = 0; let covAll = 0;
  const covers = Object.values(covDir);
  for (const i of G.land) {
    if (!M.target[i]) continue;
    const any = covers.some((c) => c[i]); const all = covers.every((c) => c[i]);
    if (any) { M.covered[i] = 1; covA += cellArea(i); }
    if (all) covAll += cellArea(i);
  }
  // volumes
  const prods = new Set(); let stackAcq = 0; let burstSlc = 0;
  for (const s of sel) {
    const bset = new Set(); const cells = S.cells[s.si]; const bur = S.bursts[s.si];
    for (let k = 0; k < cells.length; k++) if (M.target[cells[k]]) bset.add(bur[k]);
    s.nBurst = bset.size; s.gb = 0;
    for (const a of s.acqF) { stackAcq++; for (const pi of a[3]) { prods.add(pi); s.gb += S.products[pi][2]; } }
    burstSlc += s.nBurst * s.acqF.length;
  }
  let tb = 0; for (const pi of prods) tb += S.products[pi][2];
  tb /= 1000;
  const gbPerBurst = meanGb() / 29.4; // IW SLC ~ 3 sub-swaths x ~9.8 bursts
  $('kTargets').textContent = fmtInt(targetA);
  $('kStacks').textContent = `${sel.length}`;
  $('kAcq').textContent = fmtInt(stackAcq);
  $('kSlc').textContent = fmtInt(prods.size);
  $('kTb').textContent = tb.toFixed(tb < 10 ? 2 : 1);
  $('kBurst').textContent = fmtInt(burstSlc);
  const btb = (burstSlc * gbPerBurst) / 1000;
  $('kBurstTb').textContent = btb.toFixed(btb < 10 ? 2 : 1);
  $('kCover').textContent = targetA ? (p.geom === 'both' ? `${pct(covAll, targetA)} both / ${pct(covA, targetA)} any` : pct(covA, targetA)) : '–';
  const coreH = stackAcq * p.cpu;
  $('cpuLine').textContent = `≈ ${fmtInt(coreH)} core-hours (${fmtInt(coreH / 24 / 64)} days on a 64-core node) for coregistration + interferogram generation across ${sel.length} stacks.`;
  drawMonthChart(sel, p);
  drawStackTable(sel);
  drawFrames(sel);
  M.scopeParams = p;
}

let meanGbCache = 0;
function meanGb() { if (!meanGbCache) { let s = 0; for (const p of S.products) s += p[2]; meanGbCache = s / (S.products.length || 1); } return meanGbCache; }

function drawMonthChart(sel, p) {
  const start = new Date(S.day0 + p.d0 * DAY); const end = new Date(S.day0 + p.d1 * DAY);
  const months = []; const d = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
  while (d <= end) { months.push(`${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}`); d.setUTCMonth(d.getUTCMonth() + 1); }
  const bins = new Map(months.map((m) => [m, { '1A': 0, '1C': 0, '1D': 0 }]));
  for (const s of sel) for (const a of s.acqF) { const m = new Date(S.day0 + a[0] * DAY).toISOString().slice(0, 7); const b = bins.get(m); if (b) b[a[1]]++; }
  const vals = months.map((m) => bins.get(m)); const max = Math.max(1, ...vals.map((v) => v['1A'] + v['1C'] + v['1D']));
  const W = 340; const H = 80; const bw = W / Math.max(1, months.length);
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Acquisitions per month">`;
  vals.forEach((v, k) => {
    let y = H - 12;
    for (const pf of ['1A', '1C', '1D']) {
      if (!v[pf]) continue;
      const h = (v[pf] / max) * (H - 16); y -= h;
      svg += `<rect class="${PF[pf].cls}" x="${k * bw + 0.5}" y="${y}" width="${Math.max(1, bw - 1.5)}" height="${h}"><title>${months[k]} ${PF[pf].label}: ${v[pf]}</title></rect>`;
    }
  });
  svg += `<text x="0" y="${H - 1}">${months[0] || ''}</text><text x="${W}" y="${H - 1}" text-anchor="end">${months[months.length - 1] || ''}</text>`;
  svg += `<text x="${W}" y="9" text-anchor="end">max ${max}/month</text></svg>`;
  $('chartMonths').innerHTML = svg;
  $('pfLegend').innerHTML = Object.values(PF).map((x) => `<span class="swatch" style="background:${x.color}"></span>${x.label}`).join(' &nbsp; ');
}

function drawStackTable(sel) {
  const min = Number($('minAcq').value) || 1;
  const rows = sel.slice().sort((a, b) => b.tA - a.tA);
  $('stackTable').innerHTML = '<tr><th>Frame</th><th>acq</th><th>target km²</th><th>bursts</th><th>GB</th></tr>'
    + (rows.length ? rows.map((s) => `<tr class="row-click${s.acqF.length < min ? ' thin' : ''}" data-si="${s.si}"><td><span class="dir${s.dir}">${s.dir === 'A' ? 'ASC' : 'DSC'}</span> ${s.id}</td><td>${s.acqF.length}</td><td>${fmtInt(s.tA)}</td><td>${s.nBurst}/27</td><td>${fmtInt(s.gb)}</td></tr>`).join('')
      : '<tr><td colspan="5" class="muted">No targets with the current settings</td></tr>');
}

const frameLayer = L.layerGroup().addTo(map);
function drawFrames(sel) {
  frameLayer.clearLayers();
  if (!$('showFrames').checked) return;
  for (const s of sel) {
    const col = getComputedStyle(document.documentElement).getPropertyValue(s.dir === 'A' ? '--frame-a' : '--frame-d').trim();
    L.polygon(s.r.map(([x, y]) => [y, x]), { renderer: frameRenderer, color: col, weight: 1.5, fill: false, dashArray: s.dir === 'D' ? '5 4' : null })
      .bindTooltip(`${s.dir === 'A' ? 'Ascending' : 'Descending'} ${s.id} · ${s.acqF.length} acq · ${fmtInt(s.tA)} km² targets`, { sticky: true })
      .addTo(frameLayer);
  }
}

// ------------------------------------------------------------------ vector overlays (lazy)
const overlays = {};
async function toggleOverlay(key) {
  const on = $(key === 'hw' ? 'showHw' : key === 'rl' ? 'showRl' : 'showPc').checked;
  if (!on) { if (overlays[key]) map.removeLayer(overlays[key]); return; }
  if (!overlays[key]) {
    const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    if (key === 'hw') {
      const gj = await fetch('data/highways.geojson').then((r) => r.json());
      overlays.hw = L.geoJSON(gj, { renderer: vecRenderer, interactive: false, style: (f) => ({ color: css('--hw'), weight: f.properties.CLASS <= 11 ? 2 : 1, opacity: f.properties.CLASS <= 11 ? 0.95 : 0.6 }) });
    } else if (key === 'rl') {
      const gj = await fetch('data/rail.geojson').then((r) => r.json());
      overlays.rl = L.geoJSON(gj, { renderer: vecRenderer, interactive: false, style: (f) => ({ color: css('--rl'), weight: f.properties.main ? 1.8 : 1, opacity: f.properties.main ? 0.95 : 0.55, dashArray: f.properties.main ? null : '3 3' }) });
    } else {
      overlays.pc = L.layerGroup(D.pcs.map((p) => L.circleMarker([p.y, p.x], { renderer: vecRenderer, radius: 2 + p.c * 1.5, color: css('--pc'), weight: 1, fillOpacity: 0.5 })
        .bindTooltip(`${esc(p.n)} (${['', 'small', 'medium', 'large urban'][p.c]} population centre)`)));
    }
  }
  overlays[key].addTo(map);
}

// ------------------------------------------------------------------ cell popup
map.on('click', (e) => {
  if (!G.meta) return;
  const i = cellAt(e.latlng.lat, e.latlng.lng);
  if (i < 0 || G.L.land[i] <= 2) return;
  L.popup({ maxWidth: 330 }).setLatLng(e.latlng).setContent(cellHtml(i, e.latlng)).openOn(map);
});

function cellHtml(i, ll) {
  const p = readParams(); const [lat, lon] = cellLatLng(i);
  let best = null; let bd = Infinity; const kx = Math.cos((ll.lat * Math.PI) / 180);
  for (const q of D.places) { const d = ((q.x - ll.lng) * kx) ** 2 + (q.y - ll.lat) ** 2; if (d < bd) { bd = d; best = q; } }
  const covering = S.stacks.filter((s) => s.acqF && binHas(S.cells[s.si], i)).map((s) => `${s.id} (${s.acqF.length})`);
  const thr = G.meta.thresholds.map((t) => `${t}°: ${pct(G.L[`f${t}`][i], 255)}`).join(' · ');
  const by = G.L.burn_last[i];
  const row = (k, v) => `<tr><td>${k}</td><td><b>${v}</b></td></tr>`;
  return `<h4>${fmtLat(lat)} ${fmtLng(lon)} · ${esc(G.meta.provinces[G.L.prov[i]] || '')}</h4>
    <div class="muted">Nearest place: ${best ? `${esc(best.n)} (${esc(best.t)}, ${(Math.sqrt(bd) * 111).toFixed(1)} km)` : '–'}</div>
    <div class="sec">Model</div><table>
    ${row(p.model === 'cal' && CAL ? 'Susceptibility (P)' : 'Susceptibility', `${M.haz[i].toFixed(2)} · ${CLASSES[M.hazCls[i]].label}`)}
    ${row('Exposure', M.exp[i].toFixed(2))}
    ${row('Risk', `${M.risk[i].toFixed(2)} · ${CLASSES[M.riskCls[i]].label}`)}</table>
    <div class="sec">Terrain (${G.L.slope[i] ? 'AWS Terrain Tiles' : 'no DEM'})</div><table>
    ${row('Mean slope', `${val('slope', i).toFixed(1)}°`)}${row('Mean elevation', `${G.L.elev[i]} m`)}${row('Relief', `${G.L.relief[i]} m`)}
    </table><div class="muted small">Steeper than ${thr}</div>
    ${G.L.tsm_cover && G.L.tsm_cover[i] > 0 ? `<div class="sec">BC terrain stability mapping</div><table>${row('Mapped share of cell', pct(G.L.tsm_cover[i], 255))}${row('Class IV/IVR/V share', pct(G.L.tsm_unst[i], 255))}</table>` : ''}
    <div class="sec">Fire (NBAC)</div><table>
    ${row('Last burn', by ? by : 'none since 1972')}${row(`Burned, last ${p.burnWin} seasons`, pct(G.L[`burn${p.burnWin}`][i], 255))}</table>
    <div class="sec">Exposure</div><table>
    ${row('Highway', `${fmtKm(val('hw_d', i))}${G.L.hw_len[i] ? ` · ${val('hw_len', i).toFixed(1)} km in cell (class ${G.L.hw_cls[i]})` : ''}`)}
    ${row('Rail', `${fmtKm(val('rl_d', i))}${G.L.rl_len[i] ? ` · ${val('rl_len', i).toFixed(1)} km in cell` : ''}`)}
    ${row('Population centre', `${fmtKm(val('pc_d', i))} (${['', 'small', 'medium', 'large'][G.L.pc_cls[i]] || ''})`)}
    ${row('Community / reserve', fmtKm(val('pl_d', i)))}</table>
    <div class="sec">Sentinel-1 (window)</div><table>
    ${row('Ascending / descending', `${M.s1A[i]} / ${M.s1D[i]}`)}</table>
    <div class="muted small">${covering.length ? `Frames: ${covering.join(', ')}` : 'No frame covers this cell'}</div>`;
}
function binHas(arr, v) { let lo = 0; let hi = arr.length - 1; while (lo <= hi) { const m = (lo + hi) >> 1; if (arr[m] === v) return true; if (arr[m] < v) lo = m + 1; else hi = m - 1; } return false; }

// ------------------------------------------------------------------ exports
function download(name, text, type) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
$('exportStacks').onclick = () => {
  const head = 'frame,direction,relative_orbit,frame_index,anx_start_s,anx_end_s,acquisitions,first,last,target_km2,bursts_with_targets,gb_slc';
  const lines = M.sel.map((s) => {
    const d = s.acqF.map((a) => a[0]); const f = d.length ? fmtDate(S.day0 + Math.min(...d) * DAY) : ''; const l = d.length ? fmtDate(S.day0 + Math.max(...d) * DAY) : '';
    return [s.id, s.dir === 'A' ? 'ascending' : 'descending', s.ro, s.k, s.anx[0], s.anx[1], s.acqF.length, f, l, s.tA.toFixed(1), s.nBurst, s.gb.toFixed(1)].join(',');
  });
  download('insar_stacks.csv', [head, ...lines].join('\n'), 'text/csv');
};
$('exportSlc').onclick = () => {
  const lines = ['frame,acq_date,platform,coverage,product_id,gb'];
  for (const s of M.sel) for (const a of s.acqF) for (const pi of a[3]) { const pr = S.products[pi]; lines.push([s.id, fmtDate(S.day0 + pr[1] * DAY), 'S' + a[1], a[2], pr[0], pr[2]].join(',')); }
  download('insar_slc_products.csv', lines.join('\n'), 'text/csv');
};
$('exportGeo').onclick = () => {
  const fc = { type: 'FeatureCollection', features: M.sel.map((s) => ({ type: 'Feature', properties: { frame: s.id, direction: s.dir, relative_orbit: s.ro, acquisitions: s.acqF.length, target_km2: +s.tA.toFixed(1), bursts: s.nBurst, gb: +s.gb.toFixed(1) }, geometry: { type: 'Polygon', coordinates: [s.r] } })) };
  download('insar_frames.geojson', JSON.stringify(fc), 'application/geo+json');
};

// ------------------------------------------------------------------ wiring
function recomputeAll() { computeModel(); computeScope(); rebuildImage(); updateStats(); saveUrl(); }
function rescope() { computeScope(); if (['s1', 'targets'].includes($('layerSel').value)) rebuildImage(); saveUrl(); }
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const recomputeSoon = debounce(recomputeAll, 120);
const rescopeSoon = debounce(rescope, 120);

for (const [id, out, fmt] of [['fsat', 'fsatOut'], ['relief', 'reliefOut', (v) => Number(v).toFixed(1)], ['burnAmp', 'burnOut', (v) => Number(v).toFixed(2)], ['radius', 'radOut'], ['opacity', 'opOut'], ['cpu', 'cpuOut']]) {
  $(id).addEventListener('input', () => { $(out).textContent = fmt ? fmt($(id).value) : $(id).value; });
}
['thresh', 'fsat', 'relief', 'burnWin', 'burnAmp', 'radius', 'aHw', 'aRl', 'aPc', 'aPl'].forEach((id) => $(id).addEventListener(id.startsWith('a') && id.length === 3 ? 'change' : 'input', recomputeSoon));
['target', 'scopeArea', 'geom', 'minAcq', 'from', 'to', 'cpu'].forEach((id) => $(id).addEventListener('input', rescopeSoon));
document.querySelectorAll('.pf').forEach((e) => e.addEventListener('change', rescopeSoon));
document.querySelectorAll('[data-months]').forEach((b) => b.addEventListener('click', () => {
  const end = new Date(S.data.end + 'T00:00:00Z'); const st = new Date(end); st.setUTCMonth(st.getUTCMonth() - Number(b.dataset.months)); st.setUTCDate(st.getUTCDate() + 1);
  const minD = new Date(S.data.start + 'T00:00:00Z');
  $('from').value = fmtDate(Math.max(st.getTime(), minD.getTime())); $('to').value = S.data.end; rescope();
}));
$('layerSel').addEventListener('change', () => { rebuildImage(); saveUrl(); });
$('model').addEventListener('change', () => applyModel(true));
$('opacity').addEventListener('input', () => gridLayer.redraw());
$('showHw').addEventListener('change', () => toggleOverlay('hw'));
$('showRl').addEventListener('change', () => toggleOverlay('rl'));
$('showPc').addEventListener('change', () => toggleOverlay('pc'));
$('showFrames').addEventListener('change', () => drawFrames(M.sel));
$('topCells').addEventListener('click', (e) => { const li = e.target.closest('li[data-x]'); if (li) map.fitBounds([[+li.dataset.y, +li.dataset.x], [+li.dataset.y + 1, +li.dataset.x + 1]]); });
$('stackTable').addEventListener('click', (e) => {
  const tr = e.target.closest('tr[data-si]'); if (!tr) return; const s = S.stacks[+tr.dataset.si];
  map.fitBounds([[s.bbox[1], s.bbox[0]], [s.bbox[3], s.bbox[2]]], { padding: [20, 20] });
});
map.on('moveend', debounce(() => { if (!G.meta) return; updateStats(); if ($('scopeArea').value === 'view') rescope(); else saveUrl(); }, 200));

// collapsible panels (state remembered per browser)
const panels = [...document.querySelectorAll('details[data-panel]')];
try { const saved = JSON.parse(localStorage.getItem('riskPanels') || '{}'); panels.forEach((p) => { if (p.dataset.panel in saved) p.open = saved[p.dataset.panel]; }); } catch { /* storage unavailable */ }
const savePanels = () => { try { localStorage.setItem('riskPanels', JSON.stringify(Object.fromEntries(panels.map((p) => [p.dataset.panel, p.open])))); } catch { /* ignore */ } };
panels.forEach((p) => p.addEventListener('toggle', savePanels));
$('expandAll').onclick = () => { panels.forEach((p) => { p.open = true; }); savePanels(); };
$('collapseAll').onclick = () => { panels.forEach((p) => { p.open = false; }); savePanels(); };

// URL state
const URL_KEYS = ['model', 'thresh', 'fsat', 'relief', 'burnWin', 'burnAmp', 'radius', 'target', 'scopeArea', 'geom', 'minAcq', 'from', 'to', 'cpu', 'layerSel', 'opacity'];
const URL_CHECKS = ['aHw', 'aRl', 'aPc', 'aPl'];
function saveUrl() {
  const q = new URLSearchParams();
  URL_KEYS.forEach((k) => q.set(k, $(k).value));
  URL_CHECKS.forEach((k) => q.set(k, $(k).checked ? 1 : 0));
  q.set('pf', [...document.querySelectorAll('.pf:checked')].map((e) => e.value).join(','));
  const c = map.getCenter(); q.set('v', `${c.lat.toFixed(3)},${c.lng.toFixed(3)},${map.getZoom()}`);
  history.replaceState(null, '', '#' + q.toString());
}
function loadUrl() {
  const q = new URLSearchParams(location.hash.slice(1));
  URL_KEYS.forEach((k) => { if (q.has(k)) $(k).value = q.get(k); });
  URL_CHECKS.forEach((k) => { if (q.has(k)) $(k).checked = q.get(k) === '1'; });
  if (q.has('pf')) { const s = new Set(q.get('pf').split(',')); document.querySelectorAll('.pf').forEach((e) => { e.checked = s.has(e.value); }); }
  if (q.has('v')) { const [la, lo, z] = q.get('v').split(',').map(Number); if (Number.isFinite(z)) map.setView([la, lo], z); }
  [['fsat', 'fsatOut'], ['radius', 'radOut'], ['opacity', 'opOut'], ['cpu', 'cpuOut']].forEach(([a, b]) => { $(b).textContent = $(a).value; });
  $('reliefOut').textContent = Number($('relief').value).toFixed(1); $('burnOut').textContent = Number($('burnAmp').value).toFixed(2);
}

// ------------------------------------------------------------------ formatting
const pad2 = (n) => String(n).padStart(2, '0');
const fmtDate = (ms) => new Date(ms).toISOString().slice(0, 10);
const fmtInt = (v) => Math.round(v).toLocaleString();
const fmtKm = (v) => (v >= 6000 ? '–' : v < 1 ? '< 1 km' : `${v.toFixed(v < 10 ? 1 : 0)} km`);
const fmtLat = (v) => `${Math.abs(v).toFixed(2)}°${v >= 0 ? 'N' : 'S'}`;
const fmtLng = (v) => `${Math.abs(v).toFixed(2)}°${v >= 0 ? 'E' : 'W'}`;
const pct = (a, b) => { if (!b || !a) return '0%'; const p = (a / b) * 100; return p < 1 ? '<1%' : `${Math.round(p)}%`; };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function setConn(text, ok) { const el = $('conn'); el.textContent = text; el.className = 'pill' + (ok === true ? ' ok' : ok === false ? ' bad' : ''); }

load().catch((e) => { console.error(e); setConn('data missing', false); alert('Could not load data/ - run the scripts/ pipeline first.\n' + e.message); });
