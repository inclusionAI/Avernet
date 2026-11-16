import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { randomUUID } from 'node:crypto';
const config = vi.hoisted(() => ({ environment: 'pre', transport: {
  apiKey: 'baas-test', iamtoken: '', baseUrl: 'https://provider.example.invalid/api',
} }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(), resolveBaasConfig: () => config.transport,
}));
vi.mock('@avernet/clawweb-shared/server/env', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/env')>(), getCurrentEnv: () => config.environment,
}));
import { createWorkflowAiDraftRuntime } from '../workflow-ai-draft-runtime.js';
import type { WorkflowAiDraftExecutionRequest } from '@avernet/workflow/server/contracts/workflow-ai-draft';
beforeEach(() => {
  config.environment = 'pre';
  config.transport = { apiKey: 'baas-test', iamtoken: '', baseUrl: 'https://provider.example.invalid/api' };
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
  it.each(['credential', 'provider', 'environment'])('rejects an old receipt after a %s namespace change without provider access', async field => {
    const f = fixture(), first = f.make(), p = await first.service.prepare('owner', 'flow', f.input);
    if (field === 'credential') config.transport.apiKey = 'provider-test';
    if (field === 'provider') config.transport.baseUrl = 'https://other-provider.example.invalid/api';
    if (field === 'environment') config.environment = 'prod';
    const other = f.make();
    await expect(other.service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: f.input.currentYaml })).rejects.toBeInstanceOf(Error);
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each(['credential', 'provider', 'environment'])('rejects a receipt after a live %s change even on the original process', async field => {
    const f = fixture(), runtime = f.make(), p = await runtime.service.prepare('owner', 'flow', f.input);
    if (field === 'credential') config.transport.apiKey = 'provider-test';
    if (field === 'provider') config.transport.baseUrl = 'https://other-provider.example.invalid/api';
    if (field === 'environment') config.environment = 'prod';
    await expect(runtime.service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: f.input.currentYaml })).rejects.toBeInstanceOf(Error);
    await expect(runtime.service.create('owner', 'flow', { ...f.input, receipt: p.receipt })).rejects.toBeInstanceOf(Error);
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it.each(['', 'not-a-url', 'https://user:password@provider.example.invalid', 'https://provider.example.invalid/?secret=value'])
    ('fails closed when host transport cannot issue a safe receipt: %s', async endpoint => {
      const f = fixture(); config.transport.baseUrl = endpoint;
      expect(await f.make().service.capabilities('owner', 'flow')).toEqual({ available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' });
      expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.query).not.toHaveBeenCalled();
    });
});
