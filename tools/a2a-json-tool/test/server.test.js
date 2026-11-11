import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createServer } from '../server.mjs';

let server;
let origin;
before(async () => {
  server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  origin = `http://127.0.0.1:${server.address().port}`;
});
after(() => {
  if (!server?.listening) return;
  return new Promise((resolve, reject) => {
    server.close(error => error ? reject(error) : resolve());
    server.closeAllConnections();
  });
});

test('本地页面与脚本可读取，使用正确类型与安全响应头', async () => {
  const page = await fetch(`${origin}/?mode=card`);
  assert.equal(page.status, 200);
  assert.match(page.headers.get('content-type'), /text\/html; charset=utf-8/);
  assert.match(page.headers.get('cache-control'), /no-store/);
  assert.match(page.headers.get('content-security-policy'), /connect-src 'none'/);
  assert.match(await page.text(), /A2A/);
  for (const file of ['converter.js', 'input-parser.js', 'message-mapper.js', 'convert-input.js', 'field-links.js']) {
    const script = await fetch(`${origin}/${file}`);
    assert.equal(script.status, 200, file);
    assert.match(script.headers.get('content-type'), /javascript/);
  }
});

test('HEAD 返回元数据而不返回正文', async () => {
  const response = await fetch(origin, { method: 'HEAD' });
  assert.equal(response.status, 200);
  assert.ok(Number(response.headers.get('content-length')) > 0);
  assert.equal(await response.text(), '');
});

test('仅公开白名单文件，不允许读取项目文档或穿越目录', async () => {
  for (const path of ['/README.md', '/package.json', '/..%2fREADME.md', '/missing']) {
    const response = await fetch(origin + path);
    assert.equal(response.status, 404, path);
  }
});

test('不接收业务数据或执行写请求', async () => {
  const response = await fetch(origin, { method: 'POST', body: '{"secret":"local"}' });
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('allow'), 'GET, HEAD');
});
