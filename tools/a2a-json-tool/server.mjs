import { createServer as createHttpServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import { randomBytes } from 'node:crypto';

const routes = new Map([
  ['/', ['index.html', 'text/html']],
  ['/index.html', ['index.html', 'text/html']],
  ['/style.css', ['style.css', 'text/css']],
  ['/app.js', ['app.js', 'text/javascript']],
  ['/json-editor.js', ['json-editor.js', 'text/javascript']],
  ['/converter.js', ['converter.js', 'text/javascript']],
  ['/examples.js', ['examples.js', 'text/javascript']],
  ['/input-parser.js', ['input-parser.js', 'text/javascript']],
  ['/message-mapper.js', ['message-mapper.js', 'text/javascript']],
  ['/convert-input.js', ['convert-input.js', 'text/javascript']],
  ['/field-links.js', ['field-links.js', 'text/javascript']]
]);

export function createServer() {
  return createHttpServer(async (request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Referrer-Policy', 'no-referrer');
    const nonce = randomBytes(18).toString('base64');
    response.setHeader('Content-Security-Policy', `default-src 'self'; script-src 'self'; style-src 'self' 'nonce-${nonce}'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`);
    const send = (status, body, type = 'text/plain') => {
      response.writeHead(status, {
        'Content-Type': `${type}; charset=utf-8`,
        'Content-Length': Buffer.byteLength(body)
      });
      response.end(request.method === 'HEAD' ? undefined : body);
    };
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.setHeader('Allow', 'GET, HEAD');
      send(405, '仅支持 GET 和 HEAD。');
      return;
    }
    let pathname;
    try {
      pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    } catch {
      send(400, '请求地址无效。');
      return;
    }
    const route = routes.get(pathname);
    if (!route) {
      send(404, '文件不存在。');
      return;
    }
    try {
      let body = await readFile(new URL(`./web/${route[0]}`, import.meta.url));
      if (route[0] === 'index.html') body = body.toString('utf8').replace('__CSP_NONCE__', nonce);
      send(200, body, route[1]);
    } catch (error) {
      console.error(`读取页面文件失败：${error.message}`);
      send(500, '页面文件读取失败，请检查本地服务日志。');
    }
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const portText = process.env.PORT ?? '4173';
  const port = Number(portText);
  if (!/^\d+$/.test(portText) || !Number.isInteger(port) || port < 1 || port > 65535) {
    console.error('PORT 必须是 1–65535 之间的整数。');
    process.exitCode = 1;
  } else {
    const server = createServer();
    server.on('error', error => {
      console.error(`启动失败：${error.message}`);
      process.exitCode = 1;
    });
    server.listen(port, '127.0.0.1', () => {
      console.log(`JSON → A2A 1.0：http://127.0.0.1:${port}`);
      console.log('数据仅在浏览器本地处理。按 Ctrl+C 停止服务。');
    });
  }
}
