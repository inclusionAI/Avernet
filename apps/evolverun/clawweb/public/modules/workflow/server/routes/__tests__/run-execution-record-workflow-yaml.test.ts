import express from 'express';
import type { Server } from 'node:http';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parse } from 'yaml';
import { workflowConfigurationDigest } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import { AppConfigRepository } from '../../repositories/app-config-repository.js';
import type { NodeExecutionRepository } from '../../repositories/node-execution-repository.js';
import { executedSpec, executionRecordWorkflowFixture } from '../../repositories/__tests__/execution-record-workflow.fixture.js';
import { registerRunExecutionRecord } from '../run-execution-record.js';
import { executionRecordTokenHeaders, seedExecutionRecordToken } from './execution-record-token.fixture.js';

describe('execution-record executed-version YAML route boundary', () => {
  let context: ReturnType<typeof executionRecordWorkflowFixture>;
  let server: Server;
  let base: string;
  let nodes: { findExecutionRecordsByFlowIdStrict: ReturnType<typeof vi.fn> };
  const read = (headers: Record<string, string> = executionRecordTokenHeaders) => fetch(`${base}/runs/flow/execution-record`, { headers });
  beforeEach(async () => {
    context = executionRecordWorkflowFixture(); context.deployment();
    seedExecutionRecordToken(context.raw);
    nodes = { findExecutionRecordsByFlowIdStrict: vi.fn().mockResolvedValue([]) };
    const router = express.Router();
    registerRunExecutionRecord(router, context.repository, nodes as unknown as NodeExecutionRepository, {
      executionRecordConfigRepo: new AppConfigRepository(context.db), restrictFanoutPayloads: true,
    });
    const app = express(); app.use('/runs', router);
    server = await new Promise<Server>(resolve => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
    base = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  });
  afterEach(async () => {
    vi.restoreAllMocks();
    if (server) await new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()); });
    await context.db.close();
  });

  it('returns executed v1 YAML and provenance even when active and draft v2 differ', async () => {
    context.deployment({ ...executedSpec, title: 'Active v2' }, 2, 11, 1);
    context.raw.prepare('INSERT INTO workflow_specs VALUES (?,?)').run('wf', JSON.stringify({ ...executedSpec, title: 'Mutable draft' }));
    const query = vi.spyOn(context.db, 'query');
    const response = await read(); expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('no-store');
    const body = await response.json();
    expect(parse(body.workflow_yaml)).toEqual(executedSpec);
    expect(body.workflow_snapshot).toEqual({ status: 'resolved', snapshot: {
      kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 10, configDigest: workflowConfigurationDigest(executedSpec),
    } });
    expect(body.ext_info.flow_runs.credentials_json).toBeNull();
    expect(body.ext_info.flow_runs).not.toHaveProperty('workflow_version');
    expect(body.ext_info.flow_runs).not.toHaveProperty('workflow_snapshot_json');
    expect(nodes.findExecutionRecordsByFlowIdStrict).toHaveBeenCalledWith('flow', false);
    const sql = query.mock.calls.map(([text]) => text).join('\n').replace(/NULL AS credentials_json/g, '');
    expect(sql).not.toMatch(/SELECT\s+\*.*flow_runs|credentials_json|workflow_specs|is_active|\bLIMIT\b/i);
  });

  it('returns null plus identity mismatch, not changed history under a stored digest pin', async () => {
    context.raw.prepare('UPDATE flow_runs SET workflow_snapshot_json = ?').run(JSON.stringify({ status: 'resolved', snapshot: {
      kind: 'deployment', workflowId: 'wf', workflowVersion: 1, deployNumber: 10, configDigest: 'a'.repeat(64),
    } }));
    const response = await read(); expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ workflow_yaml: null, workflow_snapshot: { status: 'unresolved', reason: 'IDENTITY_MISMATCH' } });
  });

  it('does not query run/history/nodes before token authorization', async () => {
    const query = vi.spyOn(context.db, 'query');
    expect((await read({})).status).toBe(401);
    expect(query.mock.calls.every(([sql]) => !/flow_runs|workflow_deploy_history|workflow_specs/i.test(sql))).toBe(true);
    expect(nodes.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
  });

  it.each([
    ['{"flowId":"other"}', 409], ['{"subworkflowFanouts":{"group":{}}}', 403], ['{"fanoutOrigin":null}', 403],
  ])('keeps same-state guards before history or node reads: %s', async (state, status) => {
    context.raw.prepare('UPDATE flow_runs SET state_json = ?').run(state);
    const query = vi.spyOn(context.db, 'query');
    const response = await read(); expect(response.status).toBe(status);
    expect(query.mock.calls.every(([sql]) => !/workflow_deploy_history|workflow_specs/.test(sql))).toBe(true);
    expect(nodes.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
  });

  it('sanitizes database failure instead of returning historical-unavailable success', async () => {
    const original = context.db.query.bind(context.db);
    vi.spyOn(context.db, 'query').mockImplementation(async (sql, params) => {
      if (/workflow_deploy_history/.test(sql)) throw new Error('synthetic-history-driver-details');
      return original(sql, params);
    });
    const response = await read(); expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: 'Internal Server Error', message: 'Failed to load execution record' });
    expect(nodes.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toContain('synthetic-history-driver-details');
  });
});
