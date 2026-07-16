import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { McpStore, fromRaw, toRaw } from '../src/mcp/store.js';
import {
  handleConfigCreate,
  handleConfigDelete,
  handleConfigGet,
  handleConfigList,
  handleConfigUpdate,
  handleToolsCall,
  handleToolsList,
  _setExecFileRunner,
  _resetExecFileRunner,
} from '../src/mcp/handlers.js';

function tmpPath(): string {
  return path.join(os.tmpdir(), `teamclaw-relay-mcp-test-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
}

function cleanup(p: string) {
  if (fs.existsSync(p)) {
    try { fs.unlinkSync(p); } catch { /* ignore */ }
  }
}

describe('McpStore', () => {
  let p: string;

  beforeEach(() => { p = tmpPath(); });
  afterEach(() => { cleanup(p); });

  it('starts empty when file missing', () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    assert.deepEqual(s.list(), []);
    assert.equal(s.get('nope'), null);
  });

  it('create then get round-trips a full config', async () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    const created = s.create({
      serverCode: 'example',
      type: 'sse',
      url: 'https://mcp.example.com/sse',
      args: [],
      env: {},
      headers: { Authorization: 'Bearer tok' },
      timeout_seconds: 45,
      enabled: true,
      description: 'demo',
    });
    assert.equal(created.serverCode, 'example');
    assert.equal(created.url, 'https://mcp.example.com/sse');
    assert.equal(created.timeout_seconds, 45);
    assert.equal(created.headers.Authorization, 'Bearer tok');

    await s.flush();
    const s2 = new McpStore(p, { writeDebounceMs: 0 });
    const loaded = s2.get('example');
    assert.ok(loaded);
    assert.equal(loaded.url, 'https://mcp.example.com/sse');
    assert.equal(loaded.description, 'demo');
  });

  it('create rejects duplicate serverCode', () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    s.create({ serverCode: 'dup', type: 'sse', url: 'u', args: [], env: {}, headers: {}, timeout_seconds: 30, enabled: true });
    assert.throws(() => s.create({ serverCode: 'dup', type: 'sse', url: 'u', args: [], env: {}, headers: {}, timeout_seconds: 30, enabled: true }), /ALREADY_EXISTS/);
  });

  it('update merges with existing and throws NOT_FOUND on missing', () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    s.create({ serverCode: 'srv', type: 'http', url: 'u', args: [], env: {}, headers: {}, timeout_seconds: 30, enabled: true });
    const out = s.update('srv', { enabled: false, timeout_seconds: 60 });
    assert.equal(out.enabled, false);
    assert.equal(out.timeout_seconds, 60);
    assert.equal(out.url, 'u'); // preserved
    // CallerToken header should be auto-injected for HTTP servers
    assert.equal(out.headers.CallerToken, '$env:MCPORTER_USER_TOKEN');
    assert.throws(() => s.update('ghost', { enabled: false }), /NOT_FOUND/);
  });

  it('update does not inject CallerToken for stdio servers', () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    s.create({ serverCode: 'stdio', type: 'stdio', command: 'node', args: [], env: {}, headers: {}, timeout_seconds: 30, enabled: true });
    const out = s.update('stdio', { timeout_seconds: 45 });
    assert.equal(out.timeout_seconds, 45);
    assert.equal('CallerToken' in out.headers, false, 'stdio should not get CallerToken header');
  });

  it('update preserves explicit CallerToken header', () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    s.create({ serverCode: 'custom', type: 'http', url: 'u', args: [], env: {}, headers: { CallerToken: 'Bearer custom' }, timeout_seconds: 30, enabled: true });
    const out = s.update('custom', { enabled: false });
    assert.equal(out.headers.CallerToken, 'Bearer custom', 'explicit CallerToken should not be overwritten');
  });

  it('delete returns false for missing', () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    assert.equal(s.delete('ghost'), false);
    s.create({ serverCode: 'srv', type: 'sse', url: 'u', args: [], env: {}, headers: {}, timeout_seconds: 30, enabled: true });
    assert.equal(s.delete('srv'), true);
    assert.equal(s.list().length, 0);
  });

  it('stdio create with env round-trips through disk intact', async () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    // SKILL_ROOT, DATABASE_MODE and CLAWWEB_API_URL come from ClawMind's own
    // configs/application.yaml / process.cwd() fallback, not from mcporter.json env
    const env = {
      MCP_TRANSPORT: 'stdio',
      CCT_SOP_MCP_SERVER_MODE: 'prod',
    };
    const created = s.create({
      serverCode: 'clawmind',
      type: 'stdio',
      command: 'node',
      args: ['/home/admin/clawmind-mcp/dist/esm/platform/mcp-entry.js'],
      env,
      headers: {},
      timeout_seconds: 30,
      enabled: true,
      description: 'ClawMind workflow engine',
    });
    assert.equal(created.serverCode, 'clawmind');
    assert.equal(created.type, 'stdio');
    assert.equal(created.command, 'node');
    assert.deepEqual(created.args, ['/home/admin/clawmind-mcp/dist/esm/platform/mcp-entry.js']);
    assert.deepEqual(created.env, env);
    assert.equal('CallerToken' in created.headers, false, 'stdio should not get CallerToken on create');

    await s.flush();
    const s2 = new McpStore(p, { writeDebounceMs: 0 });
    const loaded = s2.get('clawmind');
    assert.ok(loaded);
    assert.equal(loaded.type, 'stdio');
    assert.equal(loaded.command, 'node');
    assert.deepEqual(loaded.args, ['/home/admin/clawmind-mcp/dist/esm/platform/mcp-entry.js']);
    assert.deepEqual(loaded.env, env, 'env must survive disk round-trip');
    assert.equal('CallerToken' in loaded.headers, false, 'stdio should not get CallerToken after reload');

    // Verify on-disk JSON has the env keys
    const disk = JSON.parse(fs.readFileSync(p, 'utf8'));
    const entry = disk.mcpServers.clawmind;
    assert.ok(entry.env);
    assert.equal(entry.env.MCP_TRANSPORT, 'stdio');
    assert.equal(entry.env.CCT_SOP_MCP_SERVER_MODE, 'prod');
    // SKILL_ROOT, DATABASE_MODE, CLAWWEB_API_URL not in mcporter env
    assert.equal('SKILL_ROOT' in entry.env, false);
    assert.equal('DATABASE_MODE' in entry.env, false);
    assert.equal('CLAWWEB_API_URL' in entry.env, false);
  });

  it('stdio update preserves env and does not inject CallerToken', async () => {
    const s = new McpStore(p, { writeDebounceMs: 0 });
    s.create({
      serverCode: 'clawmind',
      type: 'stdio',
      command: 'node',
      args: ['/home/admin/clawmind-mcp/dist/esm/platform/mcp-entry.js'],
      env: { MCP_TRANSPORT: 'stdio', CCT_SOP_MCP_SERVER_MODE: 'prod' },
      headers: {},
      timeout_seconds: 30,
      enabled: true,
    });
    const updated = s.update('clawmind', { timeout_seconds: 60 });
    assert.equal(updated.timeout_seconds, 60);
    assert.equal(updated.command, 'node'); // preserved
    assert.equal(updated.env.MCP_TRANSPORT, 'stdio'); // preserved
    assert.equal(updated.env.CCT_SOP_MCP_SERVER_MODE, 'prod'); // preserved
    assert.equal('CallerToken' in updated.headers, false, 'stdio update should not inject CallerToken');
  });

  it('preserves legacy key variants on disk', async () => {
    // Simulate an mcporter.json that uses `baseUrl`/`transport`/`timeoutSeconds`
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify({
      mcpServers: {
        legacy: {
          transport: 'sse',
          baseUrl: 'https://legacy.example.com/sse',
          timeoutSeconds: 20,
          enabled: true,
          headers: {},
          env: {},
          args: [],
        },
      },
    }, null, 2));

    const s = new McpStore(p, { writeDebounceMs: 0 });
    const loaded = s.get('legacy');
    assert.ok(loaded);
    assert.equal(loaded.url, 'https://legacy.example.com/sse');
    assert.equal(loaded.type, 'sse');
    assert.equal(loaded.timeout_seconds, 20);

    // An update should keep the legacy key names.
    s.update('legacy', { timeout_seconds: 42 });
    await s.flush();
    const disk = JSON.parse(fs.readFileSync(p, 'utf8'));
    const entry = disk.mcpServers.legacy;
    assert.ok('baseUrl' in entry, 'baseUrl alias preserved');
    assert.ok('transport' in entry, 'transport alias preserved');
    assert.ok('timeoutSeconds' in entry, 'timeoutSeconds alias preserved');
    assert.equal(entry.timeoutSeconds, 42);
    assert.ok(!('url' in entry) && !('type' in entry) && !('timeout_seconds' in entry));
  });
});

describe('fromRaw / toRaw', () => {
  it('normalises unknown transport to sse', () => {
    const cfg = fromRaw('x', { type: 'what', url: 'u' });
    assert.equal(cfg.type, 'sse');
  });
  it('maps streamable_http → http', () => {
    const cfg = fromRaw('x', { type: 'streamable_http', url: 'u' });
    assert.equal(cfg.type, 'http');
  });
  it('falls back to 30s when timeout malformed', () => {
    const cfg = fromRaw('x', { timeoutSeconds: 'nope' });
    assert.equal(cfg.timeout_seconds, 30);
  });
  it('toRaw scrubs the non-chosen alias keys', () => {
    const cfg = { serverCode: 'x', type: 'http' as const, url: 'u', args: [], env: {}, headers: {}, timeout_seconds: 10, enabled: true };
    const raw = toRaw(cfg, { baseUrl: 'old' });
    assert.ok('baseUrl' in raw);
    assert.ok(!('url' in raw));
  });
});

describe('mcp.config.* handlers', () => {
  let p: string;
  let store: McpStore;

  beforeEach(() => {
    p = tmpPath();
    store = new McpStore(p, { writeDebounceMs: 0 });
  });
  afterEach(async () => {
    await store.flush();
    cleanup(p);
  });

  it('create → get → list → delete happy path', async () => {
    const cr = await handleConfigCreate(store, {
      serverCode: 'a', type: 'sse', url: 'https://a.example/', timeout_seconds: 30,
    });
    assert.equal(cr.ok, true);

    const g = await handleConfigGet(store, { serverCode: 'a' });
    assert.equal(g.ok, true);
    if (g.ok) assert.equal(g.payload.server?.serverCode, 'a');

    const l = await handleConfigList(store, {});
    assert.equal(l.ok, true);
    if (l.ok) assert.equal(l.payload.servers.length, 1);

    const d = await handleConfigDelete(store, { serverCode: 'a' });
    assert.equal(d.ok, true);
    if (d.ok) assert.equal(d.payload.deleted, true);
  });

  it('create returns INVALID_PARAMS when url missing for sse', async () => {
    const r = await handleConfigCreate(store, { serverCode: 'a', type: 'sse' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_PARAMS');
  });

  it('create returns INVALID_PARAMS when command missing for stdio', async () => {
    const r = await handleConfigCreate(store, { serverCode: 'a', type: 'stdio' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'INVALID_PARAMS');
  });

  it('create returns ALREADY_EXISTS on duplicate', async () => {
    await handleConfigCreate(store, { serverCode: 'a', type: 'sse', url: 'u' });
    const again = await handleConfigCreate(store, { serverCode: 'a', type: 'sse', url: 'u' });
    assert.equal(again.ok, false);
    if (!again.ok) assert.equal(again.error.code, 'ALREADY_EXISTS');
  });

  it('update returns NOT_FOUND on missing', async () => {
    const r = await handleConfigUpdate(store, { serverCode: 'ghost', enabled: false });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_FOUND');
  });

  it('get returns NOT_FOUND on missing', async () => {
    const r = await handleConfigGet(store, { serverCode: 'ghost' });
    assert.equal(r.ok, false);
    if (!r.ok) assert.equal(r.error.code, 'NOT_FOUND');
  });

  it('delete returns {deleted: false} for missing (not an error)', async () => {
    const r = await handleConfigDelete(store, { serverCode: 'ghost' });
    assert.equal(r.ok, true);
    if (r.ok) assert.equal(r.payload.deleted, false);
  });

  it('create injects CallerToken placeholder for HTTP servers', async () => {
    const r = await handleConfigCreate(store, {
      serverCode: 'http-srv', type: 'http', url: 'https://example.com/mcp', timeout_seconds: 30,
    });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.server.headers.CallerToken, '$env:MCPORTER_USER_TOKEN',
        'HTTP server should auto-inject CallerToken: $env:MCPORTER_USER_TOKEN');
      // Must NOT touch Authorization — that belongs to a deployment's own headerPolicy.
      assert.equal('Authorization' in r.payload.server.headers, false,
        'must not inject Authorization (reserved for headerPolicy)');
    }
  });

  it('create does not inject CallerToken for stdio servers', async () => {
    const r = await handleConfigCreate(store, {
      serverCode: 'stdio-srv', type: 'stdio', command: '/usr/bin/node',
    });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal('CallerToken' in r.payload.server.headers, false,
        'stdio server should NOT have CallerToken header');
    }
  });

  it('create preserves explicit CallerToken header', async () => {
    const r = await handleConfigCreate(store, {
      serverCode: 'custom-auth',
      type: 'http',
      url: 'https://example.com/mcp',
      headers: { CallerToken: 'Bearer my-custom-token' },
      timeout_seconds: 30,
    });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.server.headers.CallerToken, 'Bearer my-custom-token',
        'explicit CallerToken should not be overwritten');
    }
  });

  it('update injects CallerToken when transport changes from stdio to http', async () => {
    await handleConfigCreate(store, {
      serverCode: 'morph', type: 'stdio', command: '/usr/bin/node',
    });
    // Change to http — should get the CallerToken placeholder
    const r = await handleConfigUpdate(store, {
      serverCode: 'morph', type: 'http', url: 'https://example.com/mcp',
    });
    assert.equal(r.ok, true);
    if (r.ok) {
      assert.equal(r.payload.server.headers.CallerToken, '$env:MCPORTER_USER_TOKEN',
        'changing stdio→http should inject CallerToken');
    }
  });
});

describe('mcp.tools.* handlers (phase-1 stubs)', () => {
  let store: McpStore;
  let p: string;
  beforeEach(() => {
    p = tmpPath();
    store = new McpStore(p, { writeDebounceMs: 0 });
  });
  afterEach(async () => {
    await store.flush();
    cleanup(p);
  });

  // Helper: create a stub execFile that returns a JSON response.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function stubExecFile(jsonResponse: unknown, capture?: { env?: Record<string, string> }): any {
    return (
      _cmd: string,
      _args: string[],
      opts: { env?: Record<string, string> },
      cb: (err: Error | null, stdout: string, stderr: string) => void,
    ) => {
      if (capture) capture.env = opts.env;
      cb(null, JSON.stringify(jsonResponse), '');
    };
  }

  it('list returns empty array', async () => {
    const r = await handleToolsList(store, {});
    assert.equal(r.ok, true);
    if (r.ok) assert.deepEqual(r.payload.tools, []);
  });

  it('list sets empty MCPORTER_USER_TOKEN env for zero-regression', async () => {
    try {
      const capture: { env?: Record<string, string> } = {};
      _setExecFileRunner(stubExecFile({ tools: [] }, capture));
      const r = await handleToolsList(store, { serverCode: 'test-srv' });
      assert.equal(r.ok, true);
      assert.ok(capture.env, 'env should be passed to execFile');
      assert.equal(capture.env!.MCPORTER_USER_TOKEN, '',
        'handleToolsList should set empty MCPORTER_USER_TOKEN');
    } finally {
      _resetExecFileRunner();
    }
  });

  it('list injects userToken into MCPORTER_USER_TOKEN env', async () => {
    try {
      const capture: { env?: Record<string, string> } = {};
      _setExecFileRunner(stubExecFile({ tools: [] }, capture));
      const r = await handleToolsList(store, { serverCode: 'test-srv', userToken: 'sno-330429' });
      assert.equal(r.ok, true);
      assert.ok(capture.env, 'env should be passed to execFile');
      assert.equal(capture.env!.MCPORTER_USER_TOKEN, 'Bearer sno-330429',
        'list should inject Bearer <userToken> into MCPORTER_USER_TOKEN (same as call)');
    } finally {
      _resetExecFileRunner();
    }
  });

  it('call returns well-formed error result', async () => {
    try {
      _setExecFileRunner(stubExecFile({ ok: true, data: { result: 'stubbed' } }));
      const r = await handleToolsCall(store, { toolName: 'x', serverCode: 'srv', arguments: {} });
      assert.equal(r.ok, true);
      if (r.ok) {
        assert.equal(r.payload.isError, false);
        assert.equal(r.payload.serverCode, 'srv');
        assert.equal(r.payload.content[0]?.type, 'text');
      }
    } finally {
      _resetExecFileRunner();
    }
  });

  it('call injects userToken into MCPORTER_USER_TOKEN env', async () => {
    try {
      const capture: { env?: Record<string, string> } = {};
      _setExecFileRunner(stubExecFile({ ok: true, data: {} }, capture));
      await handleToolsCall(store, {
        toolName: 'x',
        serverCode: 'srv',
        arguments: {},
        userToken: 'my-secret-token',
      });
      assert.ok(capture.env, 'env should be passed to execFile');
      assert.equal(capture.env!.MCPORTER_USER_TOKEN, 'Bearer my-secret-token',
        'userToken should be prefixed with Bearer and injected as MCPORTER_USER_TOKEN');
    } finally {
      _resetExecFileRunner();
    }
  });

  it('call sets empty MCPORTER_USER_TOKEN when no userToken', async () => {
    try {
      const capture: { env?: Record<string, string> } = {};
      _setExecFileRunner(stubExecFile({ ok: true, data: {} }, capture));
      await handleToolsCall(store, { toolName: 'x', serverCode: 'srv', arguments: {} });
      assert.ok(capture.env, 'env should always be passed to execFile');
      assert.equal(capture.env!.MCPORTER_USER_TOKEN, '',
        'empty userToken → empty MCPORTER_USER_TOKEN (zero-regression)');
    } finally {
      _resetExecFileRunner();
    }
  });

  it('call does not log token in debug output', async () => {
    try {
      _setExecFileRunner(stubExecFile({ ok: true, data: {} }));
      // With token: should not throw, env injection works
      const r = await handleToolsCall(store, {
        toolName: 'x', serverCode: 'srv', arguments: {}, userToken: 'secret',
      });
      assert.equal(r.ok, true);
      // Without token: should also work
      const r2 = await handleToolsCall(store, {
        toolName: 'y', serverCode: 'srv', arguments: {},
      });
      assert.equal(r2.ok, true);
    } finally {
      _resetExecFileRunner();
    }
  });
});
