// Canada Ground-Motion Risk dashboard - zero-dependency static server (Node >= 20).
// Serves public/ with gzip (precomputed national grid + Sentinel-1 stacks live in public/data).
'use strict';

const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');

const PORT = Number(process.env.PORT || 8090);
const HOST = process.env.HOST || '127.0.0.1';
const ROOT = path.join(__dirname, 'public');
const APP_PASSWORD = process.env.APP_PASSWORD || '';
const APP_USER = process.env.APP_USER || ''; // optional: when set, the user name must match too
const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.geojson': 'application/geo+json; charset=utf-8',
  '.bin': 'application/octet-stream', '.png': 'image/png', '.svg': 'image/svg+xml', '.csv': 'text/csv; charset=utf-8',
};
const gzCache = new Map(); // path -> { mtimeMs, gz }

function authorised(req) {
  if (!APP_PASSWORD) return true;
  const h = req.headers.authorization || '';
  const [, b64] = h.split(' ');
  const decoded = Buffer.from(b64 || '', 'base64').toString();
  const sep = decoded.indexOf(':');
  if (sep < 0) return false;
  const user = decoded.slice(0, sep); const pass = decoded.slice(sep + 1);
  const eq = (a, b) => { const x = Buffer.from(a); const y = Buffer.from(b); return x.length === y.length && crypto.timingSafeEqual(x, y); };
  return eq(pass, APP_PASSWORD) && (!APP_USER || eq(user, APP_USER));
}

function serveFile(req, res, file) {
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404, { 'Content-Type': 'text/plain' }); res.end('not found'); return; }
    const type = TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream';
    const etag = `"${st.size.toString(16)}-${Math.round(st.mtimeMs).toString(16)}"`;
    if (req.headers['if-none-match'] === etag) { res.writeHead(304); res.end(); return; }
    const headers = { 'Content-Type': type, ETag: etag, 'Cache-Control': 'no-cache' };
    const gzipOk = /\bgzip\b/.test(req.headers['accept-encoding'] || '') && st.size > 1024;
    if (!gzipOk) { res.writeHead(200, { ...headers, 'Content-Length': st.size }); fs.createReadStream(file).pipe(res); return; }
    const hit = gzCache.get(file);
    const send = (gz) => { res.writeHead(200, { ...headers, 'Content-Encoding': 'gzip', 'Content-Length': gz.length, Vary: 'Accept-Encoding' }); res.end(gz); };
    if (hit && hit.mtimeMs === st.mtimeMs) { send(hit.gz); return; }
    fs.readFile(file, (e, buf) => {
      if (e) { res.writeHead(500); res.end(); return; }
      zlib.gzip(buf, { level: 6 }, (e2, gz) => {
        if (e2) { res.writeHead(500); res.end(); return; }
        gzCache.set(file, { mtimeMs: st.mtimeMs, gz });
        send(gz);
      });
    });
  });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/healthz') { res.writeHead(200, { 'Content-Type': 'text/plain' }); res.end('ok'); return; }
  if (!authorised(req)) { res.writeHead(401, { 'WWW-Authenticate': 'Basic realm="risk"' }); res.end(); return; }
  if (url.pathname === '/api/health') {
    const ok = ['grid.bin', 'grid.json', 'stacks.json'].every((f) => fs.existsSync(path.join(ROOT, 'data', f)));
    res.writeHead(ok ? 200 : 503, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok, data: ok ? 'ready' : 'run the scripts/ pipeline first' }));
    return;
  }
  let rel = decodeURIComponent(url.pathname);
  if (rel.endsWith('/')) rel += 'index.html';
  const file = path.normalize(path.join(ROOT, rel));
  if (!file.startsWith(ROOT + path.sep)) { res.writeHead(403); res.end(); return; }
  serveFile(req, res, file);
});

server.listen(PORT, HOST, () => console.log(`Canada risk dashboard on http://${HOST}:${PORT}`));
