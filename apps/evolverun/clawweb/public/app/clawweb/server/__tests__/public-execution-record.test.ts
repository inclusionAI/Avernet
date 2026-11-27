import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createHash } from 'node:crypto';
import { once } from 'node:events';
import type { Server } from 'node:http';
import Database from 'better-sqlite3';
import express from 'express';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { parse as parseYaml } from 'yaml';
import type { ClawWebLifecycle } from '../bootstrap.js';

const state = vi.hoisted(() => ({ db: null as IDatabase | null }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: state.db }), closeDatabase: vi.fn(),
}));
// Keep the real Host, run routes, authentication service and migrated repositories.
// Only unrelated engines/providers are excluded from this deterministic local test.
vi.mock('@avernet/clawevolve', async () => {
  const { Router } = await import('express');
  return { createClawevolveModule: () => ({ start: async () => {}, stop: async () => {}, publicRouter: Router(), benchRouter: Router() }) };
});
vi.mock('@avernet/clawinsight', async () => {
  const { Router } = await import('express');
  return { createInsightRuntime: () => ({ service: {} }), createInsightManagementRuntime: () => ({ router: Router() }), createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
});
vi.mock('../repair-ais-execution.js', () => ({ resolveRepairAisConfiguration: async () => null }));
import { createClawWebBootstrap } from '../public-bootstrap.js';

const fixtureToken = 'a'.repeat(64);
const rotatedToken = 'b'.repeat(64);
const digest = (value: string) => createHash('sha256').update(value).digest('hex');
const originalSpec = { id: 'record-workflow', title: 'Executed v1', nodes: [] };
let raw: Database.Database;
let lifecycle: ClawWebLifecycle | void;
let base: string;

function configure(value = fixtureToken) {
  raw.prepare(`INSERT INTO cm_app_config (config_key,config_yaml,enabled) VALUES ('api_token',?,1)
    ON CONFLICT(config_key) DO UPDATE SET config_yaml=excluded.config_yaml,enabled=1`)
    .run(`execution_record_sha256: "${digest(value)}"`);
}
function read(value?: string, extra: Record<string, string> = {}) {
  return fetch(`${base}/record-flow/execution-record?includeNodeOutput=true`, {
    headers: { ...(value ? { Authorization: `Bearer ${value}` } : {}), ...extra },
  });
}
beforeEach(async () => {
  raw = new Database(':memory:'); state.db = new SqliteDatabase(raw);
  await runMigrations(state.db, 'sqlite');
  raw.prepare(`INSERT INTO workflow_deploy_history (pack_id,workflow_id,deploy_number,version,spec_json,is_active,action)
    VALUES ('fixture-pack','record-workflow',1,1,?,0,'deploy'),('fixture-pack','record-workflow',2,2,?,1,'deploy')`)
    .run(JSON.stringify(originalSpec), JSON.stringify({ ...originalSpec, title: 'Current v2' }));
  raw.exec(`INSERT INTO flow_runs (flow_id,workflow_id,status,started_at,state_json,workflow_version,workflow_deploy_number)
    VALUES ('record-flow','record-workflow','succeeded',1,'{}',1,1);
    INSERT INTO node_executions (flow_id,workflow_id,node_id,status,attempt,started_at,output_json)
    VALUES ('record-flow','record-workflow','node','succeeded',1,1,'fixture-output');`);
  vi.stubEnv('PORT', '0');
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  let server: Server | undefined;
  const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) {
    server = listen.apply(this, args); return server;
  });
  lifecycle = await createClawWebBootstrap().start({ environment: 'prod', profile: 'public', config: { auth: { admins: ['fixture-admin'] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  base = `http://127.0.0.1:${(server!.address() as { port: number }).port}/api/runs`;
});
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  await state.db?.close(); state.db = null;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});

it('loads cm_app_config rather than ce_app_config and rejects unconfigured identity/admin callers', async () => {
  raw.prepare('INSERT INTO ce_app_config (config_key,config_json) VALUES (?,?)')
    .run('api_token', JSON.stringify({ execution_record_sha256: digest(fixtureToken) }));
  expect((await read(fixtureToken, { 'X-User-Id': 'fixture-admin' })).status).toBe(503);
  configure();
  const query = vi.spyOn(state.db!, 'query');
  expect((await read(undefined, { 'X-User-Id': 'fixture-admin', cookie: 'staff_id=fixture-admin' })).status).toBe(401);
  expect(query.mock.calls.some(([sql, params]) => /FROM cm_app_config\b/.test(sql) && params?.[0] === 'api_token')).toBe(true);
  expect(query.mock.calls.some(([sql]) => /FROM (flow_runs|node_executions)\b/.test(sql))).toBe(false);
});
it('authenticates token-only clients and returns the executed deployment YAML, not the active version', async () => {
  configure();
  const response = await read(fixtureToken);
  expect(response.status).toBe(200);
  expect(response.headers.get('cache-control')).toBe('no-store');
  const body = await response.json();
  expect(parseYaml(body.workflow_yaml)).toEqual(originalSpec);
  expect(body.workflow_snapshot).toMatchObject({ status: 'resolved', snapshot: {
    kind: 'deployment', workflowId: 'record-workflow', workflowVersion: 1, deployNumber: 1,
  } });
  expect(body.ext_info.node_executions[0].output_json).toBe('fixture-output');
  expect(body.ext_info.flow_runs.credentials_json).toBeNull();
  expect(body.ext_info.flow_runs).not.toHaveProperty('state_json');
  expect(JSON.stringify(body)).not.toContain('Current v2');
  expect(JSON.stringify(body)).not.toContain(digest(fixtureToken));
  // The dedicated service credential must not authenticate unrelated run APIs.
  expect((await fetch(`${base}/record-flow/nodes`, { headers: { Authorization: `Bearer ${fixtureToken}` } })).status).toBe(401);
});
it('applies rotations, disabling and deletion to subsequent requests without restarting the Host', async () => {
  configure(); expect((await read(fixtureToken)).status).toBe(200);
  configure(rotatedToken);
  expect((await read(fixtureToken)).status).toBe(401);
  expect((await read(rotatedToken)).status).toBe(200);
  raw.exec("UPDATE cm_app_config SET enabled=0 WHERE config_key='api_token'");
  expect((await read(rotatedToken)).status).toBe(503);
  raw.exec("DELETE FROM cm_app_config WHERE config_key='api_token'");
  expect((await read(rotatedToken)).status).toBe(503);
});
