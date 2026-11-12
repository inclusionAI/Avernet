/** Development/static delivery owned by the asset package, never by either frontend build. */
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('..', import.meta.url));
const { version } = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
export const assetPath = `/task-panel/${version}`;
export function createAssetServer() {
  const files = new Map([
    [`${assetPath}/index.umd.js`, ['index.umd.js', 'application/javascript; charset=utf-8']],
    [`${assetPath}/task-panel.css`, ['task-panel.css', 'text/css; charset=utf-8']],
  ]);
  return http.createServer(async (req, res) => {
    const entry = files.get(req.url);
    if (!entry || !['GET', 'HEAD'].includes(req.method)) { res.writeHead(404); res.end(); return; }
    try {
      const body = await fs.readFile(path.join(root, 'dist', entry[0]));
      res.writeHead(200, { 'Content-Type': entry[1], 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' });
      res.end(req.method === 'HEAD' ? undefined : body);
    } catch { res.writeHead(503); res.end('Build the independent task-panel asset first: npm run build'); }
  });
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 43820);
  createAssetServer().listen(port, '127.0.0.1', () => console.log(`TASK_PANEL_ASSET_BASE_URL=http://127.0.0.1:${port}${assetPath}`));
}
