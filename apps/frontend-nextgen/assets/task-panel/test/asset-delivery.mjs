import assert from 'node:assert/strict';
import { createAssetServer, assetPath } from '../scripts/serve-assets.mjs';
const server = createAssetServer();
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
try {
  const js = await fetch(`${base}${assetPath}/index.umd.js`);
  assert.equal(js.status, 200); assert.match(js.headers.get('content-type'), /javascript/);
  assert.match(await js.text(), /taskPanel/);
  const css = await fetch(`${base}${assetPath}/task-panel.css`);
  assert.equal(css.status, 200); assert.match(css.headers.get('content-type'), /text\/css/);
  for (const forbidden of ['/src/index.ts', '/package.json', '/node_modules/react/index.js', '/task-panel/latest/index.umd.js', `${assetPath}/helpers.js`]) {
    assert.equal((await fetch(`${base}${forbidden}`)).status, 404, forbidden);
  }
  console.log('PASS: independent immutable-path UMD/CSS HTTP delivery; no source/helpers exposure');
} finally { await new Promise(resolve => server.close(resolve)); }
