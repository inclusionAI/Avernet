// Live e2e regression for resolveDefaultSessionModel() default-model resolution.
//
// Drives the real relay gateway in-process over a ws client. For each case it
// uses an ISOLATED temp HOME + CLAUDE_CONFIG_DIR so the real ~/.claude is never
// touched, creates a "bare" session via session.new (no model param), and reads
// back binding.model from the response — exercising the full priority chain
// (RELAY_DEFAULT_MODEL > settings.json env.ANTHROPIC_MODEL > 'GLM-5.1') with
// zero real model network calls.
import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import WebSocket from 'ws';

type Case = {
  name: string;
  // mutate process.env + write settings.json into the given config dir
  setup: (configDir: string) => void;
  expectModel: string;
};

const BASE_PORT = 18923; // off the default 18900 to avoid clashing with anything

function clearEnv() {
  delete process.env.RELAY_DEFAULT_MODEL;
  delete process.env.RELAY_CLAUDE_CONFIG_DIR;
  delete process.env.CLAUDE_CONFIG_DIR;
  delete process.env.RELAY_CLAUDE_HOME;
}

function wsRpc(ws: WebSocket, method: string, params: Record<string, unknown>): Promise<any> {
  return new Promise((resolve, reject) => {
    const id = randomUUID();
    const timer = setTimeout(() => reject(new Error(`rpc timeout: ${method}`)), 10_000);
    const onMsg = (raw: WebSocket.RawData) => {
      let frame: any;
      try { frame = JSON.parse(raw.toString()); } catch { return; }
      if (frame.type === 'res' && frame.id === id) {
        clearTimeout(timer);
        ws.off('message', onMsg);
        if (frame.ok) resolve(frame.payload);
        else reject(new Error(`rpc error ${method}: ${JSON.stringify(frame.error)}`));
      }
    };
    ws.on('message', onMsg);
    ws.send(JSON.stringify({ type: 'req', id, method, params }));
  });
}

async function runCase(c: Case, port: number): Promise<{ name: string; pass: boolean; detail: string }> {
  clearEnv();
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-e2e-home-'));
  const configDir = path.join(tmpHome, '.claude');
  fs.mkdirSync(configDir, { recursive: true });
  // Point the relay at the isolated config dir; case setup layers on top.
  process.env.RELAY_CLAUDE_CONFIG_DIR = configDir;
  process.env.RELAY_DEFAULT_CWD = tmpHome; // valid existing dir for bare session
  c.setup(configDir);

  // Import fresh per case so module-eval env (DEFAULT_CWD) is read at import.
  // startGatewayServer reads RELAY_DEFAULT_CWD lazily via DEFAULT_CWD constant,
  // which is module-scoped; resolveDefaultSessionModel() reads env at call time,
  // so a single import is fine — we re-import only to be safe across cases.
  const mod = await import('../src/server.ts');
  const server = mod.startGatewayServer({ port });
  await new Promise(r => setTimeout(r, 150));

  let pass = false;
  let detail = '';
  let ws: WebSocket | undefined;
  try {
    ws = new WebSocket(`ws://127.0.0.1:${port}`);
    await new Promise<void>((res, rej) => {
      ws!.once('open', () => res());
      ws!.once('error', rej);
    });
    await wsRpc(ws, 'connect', {});
    const sessionKey = `session:${randomUUID()}:user:x`;
    // BARE session: no model param → must seed binding.model via the chain.
    const payload = await wsRpc(ws, 'session.new', { sessionKey });
    const actual = payload?.model;
    pass = actual === c.expectModel;
    detail = `binding.model="${actual}" expected="${c.expectModel}"`;
  } catch (err) {
    detail = `ERROR: ${(err as Error).message}`;
  } finally {
    try { ws?.close(); } catch {}
    await server.close().catch(() => {});
    try { fs.rmSync(tmpHome, { recursive: true, force: true }); } catch {}
    clearEnv();
    delete process.env.RELAY_DEFAULT_CWD;
  }
  return { name: c.name, pass, detail };
}

const cases: Case[] = [
  {
    name: 'TC1: settings.json env.ANTHROPIC_MODEL used (no RELAY_DEFAULT_MODEL)',
    setup: (cfg) => {
      fs.writeFileSync(path.join(cfg, 'settings.json'),
        JSON.stringify({ env: { ANTHROPIC_MODEL: 'GLM-FROM-SETTINGS' } }));
    },
    expectModel: 'GLM-FROM-SETTINGS',
  },
  {
    name: 'TC2: RELAY_DEFAULT_MODEL overrides settings.json',
    setup: (cfg) => {
      fs.writeFileSync(path.join(cfg, 'settings.json'),
        JSON.stringify({ env: { ANTHROPIC_MODEL: 'GLM-FROM-SETTINGS' } }));
      process.env.RELAY_DEFAULT_MODEL = 'GLM-FROM-ENV';
    },
    expectModel: 'GLM-FROM-ENV',
  },
  {
    name: 'TC3: no settings.json → fallback GLM-5.1',
    setup: () => {
      // intentionally leave configDir empty (no settings.json)
    },
    expectModel: 'GLM-5.1',
  },
];

(async () => {
  const results: Array<{ name: string; pass: boolean; detail: string }> = [];
  let port = BASE_PORT;
  for (const c of cases) {
    const r = await runCase(c, port++);
    results.push(r);
    console.log(`[${r.pass ? 'PASS' : 'FAIL'}] ${r.name} | ${r.detail}`);
  }
  const allPass = results.every(r => r.pass);
  console.log(`\nSUMMARY: ${results.filter(r => r.pass).length}/${results.length} passed`);
  assert.ok(allPass, 'one or more live e2e cases failed');
  process.exit(0);
})().catch(err => {
  console.error('HARNESS ERROR:', err);
  process.exit(1);
});
