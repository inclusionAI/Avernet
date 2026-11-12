import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(new URL('..', import.meta.url).pathname);
const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' };
http.createServer(async (req, res) => {
  const requested = req.url === '/' ? '/test/browser-smoke.html' : req.url.split('?')[0];
  const file = path.resolve(root, `.${requested}`);
  if (!file.startsWith(`${root}/`)) { res.writeHead(403); res.end(); return; }
  try { const content = await fs.readFile(file); res.writeHead(200, { 'Content-Type': types[path.extname(file)] ?? 'application/octet-stream' }); res.end(content); }
  catch { res.writeHead(404); res.end(); }
}).listen(43819, '127.0.0.1', () => console.log('Synthetic UMD fixture: http://127.0.0.1:43819 (not product E2E)'));
