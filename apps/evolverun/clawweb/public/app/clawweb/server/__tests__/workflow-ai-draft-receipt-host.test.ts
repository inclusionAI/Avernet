import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { createHmac, randomUUID } from 'node:crypto';
import { createWorkflowAiDraftReceipts } from '@avernet/workflow/server/services/workflow-ai-draft-receipts';
const config = vi.hoisted(() => ({ environment: 'pre', baasEnvironment: 'pre', unavailable: false, transport: {
  apiKey: 'provider-test', iamtoken: '', baseUrl: 'https://prod.example.invalid/api',
  environments: {
    dev: { apiKey: '', baseUrl: '' },
    pre: { apiKey: 'baas-test', baseUrl: 'https://provider.example.invalid/api' },
    prod: { apiKey: 'provider-test', baseUrl: 'https://prod.example.invalid/api' },
  },
} }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  resolveBaasConfig: () => { if (config.unavailable) throw new Error('private config failure baas-test'); return config.transport; },
  resolveAutoHealConfig: () => ({ botId: 'autoheal:configured-owner', baasEnvironment: config.baasEnvironment }),
}));
vi.mock('@avernet/clawweb-shared/server/env', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/env')>(), getCurrentEnv: () => config.environment,
}));
import { createWorkflowAiDraftRuntime } from '../workflow-ai-draft-runtime.js';
import type { WorkflowAiDraftExecutionRequest } from '@avernet/workflow/server/contracts/workflow-ai-draft';
beforeEach(() => {
  config.environment = 'pre'; config.baasEnvironment = 'pre'; config.unavailable = false;
  config.transport = { apiKey: 'provider-test', iamtoken: '', baseUrl: 'https://prod.example.invalid/api', environments: {
    dev: { apiKey: '', baseUrl: '' },
    pre: { apiKey: 'baas-test', baseUrl: 'https://provider.example.invalid/api' },
    prod: { apiKey: 'provider-test', baseUrl: 'https://prod.example.invalid/api' },
  } };
});
afterEach(() => vi.restoreAllMocks());
function fixture() {
  const query = vi.fn(async () => { throw new Error('Draft generation must not use a database'); });
  const db = { dbType: 'noop', query, exec: query } as unknown as IDatabase;
  const execution = { resolveTarget: vi.fn(async () => ({ botId: 'autoheal:configured-owner' })),
    dispatch: vi.fn(async (input: WorkflowAiDraftExecutionRequest) => ({ jobId: input.envelope.taskId })),
    poll: vi.fn(async () => ({ status: 'running' as const })) };
  const make = () => createWorkflowAiDraftRuntime(db, {
    principal: async () => ({ actorId: 'owner' }), canEdit: async () => true, execution,
  });
  const input = { requestId: randomUUID(), mode: 'modify' as const, prompt: 'Improve the workflow',
    currentYaml: 'id: flow\nversion: 1\nnodes: [{id: n, executor: {type: done}}]\n' };
  return { make, execution, query, input };
}
function changeNamespace(field: string) {
  const pre = config.transport.environments.pre;
  if (field === 'credential') pre.apiKey = 'provider-test';
  if (field === 'provider') pre.baseUrl = 'https://other-provider.example.invalid/api';
  if (field === 'environment') config.environment = 'prod';
  if (field === 'target-environment') {
    // Same exact endpoint/key: the explicit target label must still fence it.
    config.transport.environments.prod = { ...pre }; config.baasEnvironment = 'prod';
  }
}
describe('Host receipt key composition without task storage', () => {
  it('shares receipts across replicas with the same provider configuration and no database access', async () => {
    const f = fixture(), first = f.make(), other = f.make();
    const p = await first.service.prepare('owner', 'flow', f.input);
    expect((await other.service.prepare('owner', 'flow', f.input)).draftId).toBe(p.draftId);
    await first.service.create('owner', 'flow', { ...f.input, receipt: p.receipt });
    expect(await other.service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: f.input.currentYaml }))
      .toMatchObject({ draftId: p.draftId, status: 'running' });
    expect(f.query).not.toHaveBeenCalled(); expect(f.execution.dispatch).toHaveBeenCalledOnce();
  });
  it.each(['dev', 'pre', 'prod'])('signs with the explicit pre pair, not top-level prod, on control host %s', async host => {
    config.environment = host;
    const f = fixture(), p = await f.make().service.prepare('owner', 'flow', f.input);
    const codec = (key: string, environment: string, baseUrl: string) => createWorkflowAiDraftReceipts(createHmac('sha256', key)
      .update(JSON.stringify(['workflow-ai-draft-receipts/v1', host, environment, baseUrl])).digest('base64url'));
    const pre = config.transport.environments.pre;
    expect(codec(pre.apiKey, 'pre', pre.baseUrl).verify(p.receipt)).toMatchObject({ draftId: p.draftId });
    expect(() => codec(config.transport.apiKey, 'pre', config.transport.baseUrl).verify(p.receipt)).toThrow();
    expect(() => codec(pre.apiKey, 'prod', pre.baseUrl).verify(p.receipt)).toThrow();
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.query).not.toHaveBeenCalled();
  });
  it('ignores changes to the unselected top-level/prod pair and keeps receipts valid', async () => {
    const f = fixture(), runtime = f.make(), p = await runtime.service.prepare('owner', 'flow', f.input);
    config.transport.apiKey = 'baas-test'; config.transport.baseUrl = 'https://other-prod.example.invalid';
    config.transport.environments.prod = { apiKey: '', baseUrl: '' };
    expect(await f.make().service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: f.input.currentYaml }))
      .toMatchObject({ draftId: p.draftId, status: 'running' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).toHaveBeenCalledOnce();
  });
  it.each(['credential', 'provider', 'environment', 'target-environment'])('rejects an old receipt after a %s namespace change without provider access', async field => {
    const f = fixture(), first = f.make(), p = await first.service.prepare('owner', 'flow', f.input);
    changeNamespace(field);
    const other = f.make();
    await expect(other.service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: f.input.currentYaml })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(other.service.create('owner', 'flow', { ...f.input, receipt: p.receipt })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each(['credential', 'provider', 'environment', 'target-environment'])('rejects a receipt after a live %s change even on the original process', async field => {
    const f = fixture(), runtime = f.make(), p = await runtime.service.prepare('owner', 'flow', f.input);
    changeNamespace(field);
    await expect(runtime.service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: f.input.currentYaml })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(runtime.service.create('owner', 'flow', { ...f.input, receipt: p.receipt })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each(['', 'not-a-url', 'https://user:password@provider.example.invalid', 'https://provider.example.invalid/?secret=value'])
    ('fails closed when host transport cannot issue a safe receipt: %s', async endpoint => {
      const f = fixture(); config.transport.environments.pre.baseUrl = endpoint;
      expect(await f.make().service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' });
      expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.query).not.toHaveBeenCalled();
    });
  it('sanitizes live signing configuration errors without revealing upstream diagnostics', async () => {
    const f = fixture(), runtime = f.make();
    config.unavailable = true;
    await expect(runtime.service.prepare('owner', 'flow', f.input)).rejects.toThrow(/^AI_DRAFT_RECOVERY_UNAVAILABLE$/);
    expect(await f.make().service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each(['key', 'url', 'target'])('disables signing instead of falling back to prod when the pre %s is missing', async field => {
    const f = fixture();
    if (field === 'key') config.transport.environments.pre.apiKey = '';
    if (field === 'url') config.transport.environments.pre.baseUrl = '';
    if (field === 'target') config.baasEnvironment = '';
    const runtime = f.make();
    expect(await runtime.service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' });
    await expect(runtime.service.prepare('owner', 'flow', f.input)).rejects.toMatchObject({ message: 'AI_DRAFT_RECOVERY_UNAVAILABLE' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each(['key', 'url', 'target'])('rejects live pre %s revocation before dispatch or poll', async field => {
    const f = fixture(), runtime = f.make(), p = await runtime.service.prepare('owner', 'flow', f.input);
    if (field === 'key') config.transport.environments.pre.apiKey = '';
    if (field === 'url') config.transport.environments.pre.baseUrl = '';
    if (field === 'target') config.baasEnvironment = 'gray';
    await expect(runtime.service.create('owner', 'flow', { ...f.input, receipt: p.receipt })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(runtime.service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: f.input.currentYaml })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
});
