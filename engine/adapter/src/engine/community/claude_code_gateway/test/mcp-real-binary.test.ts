// Real-binary integration test for MCP token injection.
//
// The mock-based tests in mcp.test.ts replace execFile, so they never run the
// real `mcporter` and cannot catch flag/version mismatches. This suite spawns
// the ACTUAL mcporter binary the way relay's handleToolsCall does, against a
// throwaway HTTP MCP server that records the CallerToken header it receives.
// It pins two things that mocks hid:
//   1. The `call` flag contract — relay's MCPORTER_CALL_OUTPUT_ARGS (`--output
//      json`) is accepted, and the old `--json` is rejected (the prod bug).
//   2. The end-to-end token path — a configured server header
//      `$env:MCPORTER_USER_TOKEN` + the per-call env var => downstream gets
//      `CallerToken: Bearer <token>`; no token => empty CallerToken.
//
// Skips automatically when `mcporter` is not on PATH (e.g. CI without it).
import { strict as assert } from 'node:assert';
import { execFile, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { MCPORTER_CALL_OUTPUT_ARGS } from '../src/mcp/handlers.js';

function hasMcporter(): boolean {
  try {
    execFileSync('mcporter', [ '-v' ], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

// Minimal streamable-http MCP server: answers initialize / tools/list /
// tools/call and records the CallerToken header from the last request.
function startEchoServer(): Promise<{ port: number; lastAuth: () => string | undefined; close: () => Promise<void> }> {
  let lastAuth: string | undefined;
  const server = http.createServer((req, res) => {
    let body = '';
    req.on('data', c => (body += c));
    req.on('end', () => {
      // Custom headers are typed string|string[]; coerce to a single string.
      const ct = req.headers.callertoken;
      if (typeof ct === 'string') lastAuth = ct;
      let msg: { id?: unknown; method?: string } = {};
      try { msg = JSON.parse(body || '{}'); } catch { /* ignore */ }
      const reply = (result: unknown) => {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ jsonrpc: '2.0', id: msg.id ?? null, result }));
      };
      if (msg.method === 'initialize') {
        return reply({ protocolVersion: '2024-11-05', capabilities: { tools: {} }, serverInfo: { name: 'echo', version: '0.0.1' } });
      }
      if (msg.method === 'notifications/initialized') { res.writeHead(202); return res.end(); }
      if (msg.method === 'tools/list') {
        return reply({ tools: [{ name: 'ping', description: 'ping', inputSchema: { type: 'object', properties: {} } }] });
      }
      if (msg.method === 'tools/call') {
        return reply({ content: [{ type: 'text', text: `auth=${lastAuth ?? ''}` }] });
      }
      res.writeHead(404); res.end();
    });
  });
  return new Promise(resolve => {
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port;
      resolve({
        port,
        lastAuth: () => lastAuth,
        close: () => new Promise<void>(r => server.close(() => r())),
      });
    });
  });
}

function runMcporter(args: string[], env: Record<string, string>): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise(resolve => {
    execFile('mcporter', args, { env: { ...process.env, ...env }, timeout: 30000, maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => {
        resolve({ code: error ? ((error as { code?: number }).code ?? 1) : 0, stdout: stdout || '', stderr: stderr || '' });
      });
  });
}

const maybe = hasMcporter() ? describe : describe.skip;

maybe('mcp token injection — real mcporter binary', () => {
  let echo: Awaited<ReturnType<typeof startEchoServer>>;
  let cfgDir: string;
  let cfgPath: string;

  beforeEach(async () => {
    echo = await startEchoServer();
    cfgDir = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-mcp-realbin-'));
    cfgPath = path.join(cfgDir, 'mcporter.json');
    fs.writeFileSync(cfgPath, JSON.stringify({
      mcpServers: {
        echo: {
          type: 'http',
          url: `http://127.0.0.1:${echo.port}/mcp`,
          // This is exactly what relay's store.ts injects for http/sse servers.
          headers: { CallerToken: '$env:MCPORTER_USER_TOKEN' },
          timeout_seconds: 30,
          enabled: true,
        },
      },
    }));
  });

  afterEach(async () => {
    await echo.close();
    fs.rmSync(cfgDir, { recursive: true, force: true });
  });

  // Build the call argv the same way handleToolsCall does, reusing relay's
  // exported flag constant so a future flag change is caught here.
  const callArgs = (sel: string) => [ 'call', sel, '--args', '{}', ...MCPORTER_CALL_OUTPUT_ARGS, '--config', cfgPath ];

  it('injects the user token into the downstream CallerToken header', async () => {
    const r = await runMcporter(callArgs('echo.ping'), { MCPORTER_USER_TOKEN: 'Bearer T1' });
    assert.equal(r.code, 0, `mcporter failed: ${r.stderr}`);
    assert.match(r.stdout, /Bearer T1/, 'downstream should receive CallerToken: Bearer T1');
    assert.equal(echo.lastAuth(), 'Bearer T1');
  });

  it('sends an empty CallerToken when no token is present (zero-regression)', async () => {
    const r = await runMcporter(callArgs('echo.ping'), { MCPORTER_USER_TOKEN: '' });
    assert.equal(r.code, 0, `mcporter failed: ${r.stderr}`);
    // Empty env => empty CallerToken value (or header omitted). Either way: no Bearer.
    assert.ok(!/Bearer\s+\S/.test(r.stdout), 'no bearer token should be present');
  });

  it('rejects the legacy --json flag on call (regression pin for the masked bug)', async () => {
    const r = await runMcporter([ 'call', 'echo.ping', '--json', '--config', cfgPath ], { MCPORTER_USER_TOKEN: 'Bearer T1' });
    assert.notEqual(r.code, 0, 'call --json must fail');
    assert.match(`${r.stdout}${r.stderr}`, /Unknown flag '--json'/, 'must be the unknown-flag error');
  });

  it('list also carries the user token in the CallerToken header', async () => {
    // mirrors handleToolsList: `mcporter list <server> --schema --json` + env.
    const r = await runMcporter([ 'list', 'echo', '--schema', '--json', '--config', cfgPath ],
      { MCPORTER_USER_TOKEN: 'Bearer T1' });
    assert.equal(r.code, 0, `mcporter list failed: ${r.stderr}`);
    assert.equal(echo.lastAuth(), 'Bearer T1', 'list requests should carry CallerToken: Bearer T1');
  });
});
