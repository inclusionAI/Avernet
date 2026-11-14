import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ botId: 'autoheal-bot:configured-owner', apiKey: 'synthetic-secret' }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  resolveAutoHealConfig: () => ({ botId: state.botId }),
  resolveSmartOnboardingConfig: () => ({ defaultBotId: 'wrong-onboarding:logged-in-user' }),
  resolveBaasConfig: () => ({ baseUrl: 'http://127.0.0.1:1', apiKey: state.apiKey, iamtoken: '' }),
}));
import { createWorkflowAiDraftBaasExecution, workflowAiDraftMessage } from '../workflow-ai-draft-baas-execution.js';
import { aiDraftFixture, draftRequest } from './workflow-ai-draft-db-fixture.js';
import { createWorkflowAiDraftRuntime } from '../workflow-ai-draft-runtime.js';
import { decodeWorkflowAiDraftBaasReply } from '../workflow-ai-draft-baas-reply.js';
import type { WorkflowAiDraftExecutionRequest } from '@avernet/workflow/server/contracts/workflow-ai-draft';
const yaml = 'id: flow\nversion: 7\nnodes:\n  - id: n\n    executor: {type: agent}\n';
const request = (): WorkflowAiDraftExecutionRequest => ({ actorId: 'different-user', envelope: {
  schemaVersion: 'workflow-yaml-draft/v1', taskType: 'workflow_yaml_draft', taskId: 'draft', target: { botId: state.botId },
  input: { schemaVersion: 'workflow-yaml-draft-input/v1', workflowId: 'flow', mode: 'modify', prompt: 'change timeout', workingCopy: { yaml, sha256: 'digest' } },
} });
beforeEach(() => { state.botId = 'autoheal-bot:configured-owner'; state.apiKey = 'synthetic-secret'; });
afterEach(() => { vi.unstubAllGlobals(); });
describe('Editor AutoHeal/BaaS adapter', () => {
  it('sends the complete configured AutoHeal binding through the existing BaaS transport, not onboarding/user identity', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ data: { message_id: 'message-id' } }), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const port = createWorkflowAiDraftBaasExecution()!;
    expect(await port.resolveTarget()).toEqual({ botId: 'autoheal-bot:configured-owner' });
    expect(await port.dispatch(request())).toEqual({ jobId: 'message-id' });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('http://127.0.0.1:1/openapi/v1/messages');
    const sent = JSON.parse(init.body as string);
    expect(sent.bot_id).toBe('autoheal-bot:configured-owner');
    expect(sent.metadata).toEqual({ sender_options: { from: 'owner' } });
    expect(sent.message).toContain(JSON.stringify(yaml));
    expect(workflowAiDraftMessage(request())).not.toContain('run_workflow');
    expect(fetch).toHaveBeenCalledOnce();
  });
  it('disables capability without complete AutoHeal binding or transport config', () => {
    state.botId = ''; expect(createWorkflowAiDraftBaasExecution()).toBeUndefined();
    state.botId = 'bare-id'; expect(createWorkflowAiDraftBaasExecution()).toBeUndefined();
    state.botId = 'bot:owner'; state.apiKey = ''; expect(createWorkflowAiDraftBaasExecution()).toBeUndefined();
  });
  it('reports binding rejection without echoing provider details', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ message: '60001 Bot binding not found: hidden:owner' }), { status: 400 })));
    await expect(createWorkflowAiDraftBaasExecution()!.dispatch(request())).rejects.toMatchObject({ code: 'AUTO_HEAL_BOT_UNAVAILABLE', message: 'AUTO_HEAL_BOT_UNAVAILABLE' });
  });
  it.each(['network', '502', 'missing-id'])('never retries an uncertain POST (%s)', async failure => {
    const fetch = vi.fn(async () => {
      if (failure === 'network') throw new Error('connection lost');
      return new Response(JSON.stringify(failure === 'missing-id' ? { data: {} } : {}), { status: failure === '502' ? 502 : 200 });
    });
    vi.stubGlobal('fetch', fetch);
    await expect(createWorkflowAiDraftBaasExecution()!.dispatch(request())).rejects.toThrow('BAAS_DISPATCH_UNCERTAIN');
    expect(fetch).toHaveBeenCalledOnce();
  });
  it('polls the acknowledged encoded BaaS message, handles pending and validates response budget', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response('', { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { status: 'completed', result: { content: [{ type: 'text', text: '```yaml\n' + yaml + '```' }] } } })))
      .mockResolvedValueOnce(new Response('x'.repeat(1_000_001)));
    vi.stubGlobal('fetch', fetch); const port = createWorkflowAiDraftBaasExecution()!;
    expect(await port.poll({ ...request(), jobId: 'message/a' })).toEqual({ status: 'running' });
    expect(fetch.mock.calls[0][0]).toBe('http://127.0.0.1:1/openapi/v1/messages/message%2Fa');
    expect(await port.poll({ ...request(), jobId: 'message' })).toMatchObject({ status: 'completed', result: { yaml } });
    await expect(port.poll({ ...request(), jobId: 'message' })).rejects.toThrow('BAAS_REPLY_TOO_LARGE');
  });
});
describe('BaaS complete YAML extraction', () => {
  it.each([
    { data: { status: 'DONE', result: { content: yaml } } },
    { data: { message_status: 'succeeded', content: [{ type: 'text', text: yaml }] } },
    { data: { status: 'completed', messages: [{ role: 'user', content: 'not a Bot reply' }, { role: 'assistant', content: yaml }] } },
    { data: { content: yaml } },
  ])('accepts existing Bot reply shapes without truncating', value => {
    expect(decodeWorkflowAiDraftBaasReply(value)).toMatchObject({ status: 'completed', result: { yaml } });
  });
  it('does not turn running, empty or ambiguous replies into success', () => {
    expect(decodeWorkflowAiDraftBaasReply({ status: 'running', content: yaml })).toEqual({ status: 'running' });
    expect(() => decodeWorkflowAiDraftBaasReply({ status: 'completed' })).toThrow('BAAS_EMPTY_REPLY');
    expect(() => decodeWorkflowAiDraftBaasReply({ status: 'completed', content: '```yaml\na: b\n```\n```yaml\nc: d\n```' })).toThrow('BAAS_AMBIGUOUS_YAML');
    expect(decodeWorkflowAiDraftBaasReply({ status: 'failed', message: 'private' })).toEqual({ status: 'failed', error: 'BAAS_GENERATION_FAILED' });
  });
});

it('integrates protected draft core with the real BaaS send/poll adapter without saving the workflow', async () => {
  const f = await aiDraftFixture();
  const generated = draftRequest().currentYaml.replace('Unsaved editor source', 'AI suggested title');
  const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ data: { message_id: 'message' } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ data: { status: 'completed', result: { content: '```yaml\n' + generated + '```' } } })));
  vi.stubGlobal('fetch', fetch);
  try {
    const runtime = createWorkflowAiDraftRuntime(f.db, { principal: async () => ({ actorId: 'owner' }), execution: createWorkflowAiDraftBaasExecution() });
    const input = { ...draftRequest(), botId: 'forged:caller' };
    const task = await runtime.service.create('owner', 'flow', input);
    expect(task.status).toBe('running');
    f.raw.exec('UPDATE workflow_ai_drafts SET next_poll_at_ms = 0');
    expect(await runtime.service.get('owner', 'flow', task.draftId)).toMatchObject({ status: 'completed', result: { yaml: generated } });
    expect(JSON.parse(fetch.mock.calls[0][1].body).bot_id).toBe('autoheal-bot:configured-owner');
    expect(f.raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'flow'").get()).toEqual({ spec_json: '{}' });
    expect(await runtime.service.create('owner', 'flow', input)).toEqual({ draftId: task.draftId, status: 'completed' });
    expect(fetch).toHaveBeenCalledTimes(2);
  } finally { await f.db.close(); }
});

it.each(['ABORTED', 'TIME_OUT'])('recognizes the BaaS terminal state %s', status => {
  expect(decodeWorkflowAiDraftBaasReply({ data: { status } })).toEqual({ status: 'failed', error: 'BAAS_GENERATION_FAILED' });
});
it.each([{ code: 60001, data: null }, { buserviceErrorCode: 'TOKEN_EXPIRED' }, { code: '60001', data: { status: 'completed', content: yaml } }])('never treats provider diagnostics as Bot YAML', envelope => {
  expect(decodeWorkflowAiDraftBaasReply({ ...envelope, message: '```yaml\n' + yaml + '```' })).toEqual({ status: 'failed', error: 'BAAS_PROVIDER_REJECTED' });
});
it('continues polling accepted work after AutoHeal Bot configuration is removed', async () => {
  const port = createWorkflowAiDraftBaasExecution()!;
  const frozen = request(); state.botId = '';
  const fetch = vi.fn(async () => new Response(JSON.stringify({ data: { status: 'completed', content: yaml } })));
  vi.stubGlobal('fetch', fetch);
  expect(await port.poll({ ...frozen, jobId: 'accepted-message' })).toMatchObject({ status: 'completed', result: { yaml } });
  expect(fetch).toHaveBeenCalledOnce();
});
