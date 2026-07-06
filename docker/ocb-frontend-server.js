#!/usr/bin/env node
const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const { URL } = require('url');

const root = path.resolve(process.env.FRONTEND_DIST_DIR || '/opt/ocb/src/frontend/dist');
const port = Number(process.env.FRONTEND_PORT || 8000);
const bcsPort = process.env.BCS_PORT || 21000;
const bcsTarget = new URL(process.env.FRONTEND_BCS_TARGET || `http://127.0.0.1:${bcsPort}`);

const proxyRoutes = [
  { prefix: '/bcnproxy', target: bcsTarget },
];

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.txt': 'text/plain; charset=utf-8',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

function removeHopByHopHeaders(headers) {
  const next = { ...headers };
  for (const name of [
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'proxy-connection',
    'te',
    'trailer',
    'transfer-encoding',
    'upgrade',
  ]) {
    delete next[name];
  }
  return next;
}

function joinTargetPath(basePath, requestPath) {
  const base = basePath.replace(/\/$/, '');
  const next = requestPath.startsWith('/') ? requestPath : `/${requestPath}`;
  return `${base}${next}` || '/';
}

function proxyRequest(req, res, route) {
  const sourceUrl = new URL(req.url, 'http://127.0.0.1');
  const targetUrl = new URL(route.target.href);
  targetUrl.pathname = joinTargetPath(
    targetUrl.pathname,
    sourceUrl.pathname.slice(route.prefix.length) || '/',
  );
  targetUrl.search = sourceUrl.search;

  const headers = removeHopByHopHeaders(req.headers);
  headers.host = targetUrl.host;
  headers['x-forwarded-host'] = req.headers.host || '';
  headers['x-forwarded-proto'] = 'http';

  const client = targetUrl.protocol === 'https:' ? https : http;
  const upstream = client.request(
    {
      protocol: targetUrl.protocol,
      hostname: targetUrl.hostname,
      port: targetUrl.port,
      method: req.method,
      path: `${targetUrl.pathname}${targetUrl.search}`,
      headers,
    },
    (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode || 502, removeHopByHopHeaders(upstreamRes.headers));
      upstreamRes.pipe(res);
    },
  );

  upstream.on('error', (error) => {
    res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ error: 'bcn_proxy_failed', message: error.message }));
  });

  req.pipe(upstream);
}

function resolveStaticPath(pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const filePath = path.resolve(root, `.${decoded === '/' ? '/index.html' : decoded}`);
  return filePath === root || filePath.startsWith(`${root}${path.sep}`) ? filePath : null;
}

function sendFile(req, res, filePath, statusCode = 200) {
  const ext = path.extname(filePath);
  const headers = {
    'content-type': mimeTypes[ext] || 'application/octet-stream',
    'cache-control': path.basename(filePath) === 'index.html'
      ? 'no-cache'
      : 'public, max-age=31536000, immutable',
  };
  res.writeHead(statusCode, headers);
  if (req.method === 'HEAD') {
    res.end();
    return;
  }
  fs.createReadStream(filePath).pipe(res);
}

function serveStatic(req, res) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { 'content-type': 'text/plain; charset=utf-8' });
    res.end('Method Not Allowed');
    return;
  }

  const sourceUrl = new URL(req.url, 'http://127.0.0.1');
  let filePath = resolveStaticPath(sourceUrl.pathname);
  if (filePath) {
    try {
      const stat = fs.statSync(filePath);
      if (stat.isDirectory()) {
        filePath = path.join(filePath, 'index.html');
      }
      if (fs.statSync(filePath).isFile()) {
        sendFile(req, res, filePath);
        return;
      }
    } catch {
      // Fall through to SPA fallback.
    }
  }

  const indexPath = path.join(root, 'index.html');
  if (fs.existsSync(indexPath)) {
    sendFile(req, res, indexPath);
    return;
  }

  res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
  res.end('Frontend build output not found');
}

const server = http.createServer((req, res) => {
  const route = proxyRoutes.find((item) => req.url === item.prefix || req.url.startsWith(`${item.prefix}/`));
  if (route) {
    proxyRequest(req, res, route);
    return;
  }
  serveStatic(req, res);
});

server.listen(port, '0.0.0.0', () => {
  console.log(`Frontend static server listening on 0.0.0.0:${port}`);
  console.log(`/bcnproxy -> ${bcsTarget.origin}`);
});
