import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ botId: 'autoheal-bot:configured-owner', apiKey: 'baas-test',
  baseUrl: 'http://127.0.0.1:1', iamtoken: '', environment: 'pre', unavailable: false }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  resolveAutoHealConfig: () => ({ botId: state.botId }),
  resolveSmartOnboardingConfig: () => ({ defaultBotId: 'wrong-onboarding:logged-in-user' }),
  resolveBaasConfig: () => { if (state.unavailable) throw new Error('private config failure baas-test'); return state; },
}));
vi.mock('@avernet/clawweb-shared/server/env', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/env')>(), getCurrentEnv: () => state.environment,
}));
import { WorkflowAiDraftInvalidResultError, WorkflowAiDraftPollUnavailableError } from '@avernet/workflow/server/contracts/workflow-ai-draft';
import { createWorkflowAiDraftBaasExecution, workflowAiDraftMessage } from '../workflow-ai-draft-baas-execution.js';
import { decodeWorkflowAiDraftBaasReply } from '../workflow-ai-draft-baas-reply.js';
import type { WorkflowAiDraftExecutionRequest } from '@avernet/workflow/server/contracts/workflow-ai-draft';
const yaml = 'id: flow\nversion: 7\nnodes:\n  - id: n\n    executor: {type: agent}\n';
const taskId = 'd0594cc0-0ef7-4c93-ab90-a3d95f4316ab';
const request = (): WorkflowAiDraftExecutionRequest => ({ actorId: 'different-user', envelope: {
  schemaVersion: 'workflow-yaml-draft/v1', taskType: 'workflow_yaml_draft', taskId, target: { botId: state.botId },
  input: { schemaVersion: 'workflow-yaml-draft-input/v1', workflowId: 'flow', mode: 'modify', prompt: 'change timeout', workingCopy: { yaml, sha256: 'digest' } },
} });
const baasResult = (messageId: string, botId: string, data: Record<string, unknown> = {}) => ({ code: 0, data: {
  message_id: messageId, bot_id: botId, session_id: 'synthetic-session', created_at: '2026-11-15T00:00:00Z', status: 'completed', content: yaml, ...data,
} });
beforeEach(() => { Object.assign(state, { botId: 'autoheal-bot:configured-owner', apiKey: 'baas-test',
  baseUrl: 'http://127.0.0.1:1', iamtoken: '', environment: 'pre', unavailable: false }); });
afterEach(() => { vi.unstubAllGlobals(); });
describe('Editor AutoHeal/BaaS adapter', () => {
  it('sends the complete configured AutoHeal binding through the existing BaaS transport, not onboarding/user identity', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async (_url, init) => new Response(JSON.stringify({
      data: { message_id: JSON.parse(init!.body as string).message_id },
    }), { status: 200 }));
    vi.stubGlobal('fetch', fetch);
    const port = createWorkflowAiDraftBaasExecution()!;
    expect(await port.resolveTarget()).toEqual({ botId: 'autoheal-bot:configured-owner' });
    expect(await port.dispatch(request())).toEqual({ jobId: taskId });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('http://127.0.0.1:1/openapi/v1/messages');
    const sent = JSON.parse(init.body as string);
    expect(sent.bot_id).toBe('autoheal-bot:configured-owner');
    expect(sent.message_id).toBe(taskId);
    expect(sent.message_id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    expect(init.redirect).toBe('error');
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
  it('keeps DB-first binding errors uncertain without echoing provider details', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ message: '60001 Bot binding not found: hidden:owner' }), { status: 400 })));
    await expect(createWorkflowAiDraftBaasExecution()!.dispatch(request())).rejects.toThrow('BAAS_DISPATCH_UNCERTAIN');
  });
  it.each([
    [401, 'BAAS_AUTH_FAILED'], [403, 'BAAS_AUTH_FAILED'],
  ])('preserves positive rejection semantics for HTTP %s', async (status, code) => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify({ message: 'private diagnostic' }), { status: status as number }));
    vi.stubGlobal('fetch', fetch);
    await expect(createWorkflowAiDraftBaasExecution()!.dispatch(request())).rejects.toMatchObject({ code, message: code });
    expect(fetch).toHaveBeenCalledOnce();
  });
  it.each([
    { status: 200, body: { code: 60001, data: { message_id: taskId } } },
    { status: 200, body: { buserviceErrorCode: 'TOKEN_EXPIRED' } },
    { status: 400, body: { detail: { code: 60001, message: 'Bot binding not found' } } },
    { status: 401, body: { data: { message_id: taskId } } },
    { status: 403, body: { code: 60001, data: { message_id: taskId } } },
    { status: 404, body: { detail: { code: 60001 } } },
    { status: 422, body: {} }, { status: 429, body: {} }, { status: 503, body: {} },
  ])('keeps receipt recovery possible after ambiguous HTTP $status acknowledgements', async ({ status, body }) => {
    const fetch = vi.fn<typeof globalThis.fetch>(async (_url, init) => init?.method === 'POST'
      ? new Response(JSON.stringify(body), { status })
      : new Response(JSON.stringify(baasResult(taskId, state.botId))));
    vi.stubGlobal('fetch', fetch);
    const port = createWorkflowAiDraftBaasExecution()!;
    const frozen = request();
    await expect(port.dispatch(frozen)).rejects.toThrow('BAAS_DISPATCH_UNCERTAIN');
    expect(await port.poll({ ...frozen, jobId: taskId })).toMatchObject({ status: 'completed', result: { yaml } });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
  });
  it('does not retry or post when transport configuration becomes unavailable', async () => {
    const port = createWorkflowAiDraftBaasExecution()!;
    state.apiKey = '';
    const fetch = vi.fn<typeof globalThis.fetch>();
    vi.stubGlobal('fetch', fetch);
    await expect(port.dispatch(request())).rejects.toThrow('BAAS_NOT_CONFIGURED');
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each(['network', '502', 'missing-id'])('never retries an uncertain POST (%s)', async failure => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => {
      if (failure === 'network') throw new Error('connection lost');
      return new Response(JSON.stringify(failure === 'missing-id' ? { data: {} } : {}), { status: failure === '502' ? 502 : 200 });
    });
    vi.stubGlobal('fetch', fetch);
    await expect(createWorkflowAiDraftBaasExecution()!.dispatch(request())).rejects.toThrow('BAAS_DISPATCH_UNCERTAIN');
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][1]?.method).toBe('POST');
    expect(fetch.mock.calls[0][1]?.redirect).toBe('error');
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string).message_id).toBe(taskId);
  });
  it.each(['different-job', taskId.toUpperCase(), ` ${taskId}`, `${taskId} `])(
    'does not acknowledge an upstream message ID that differs from the expected ID (%s)', async messageId => {
      const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify({ data: { message_id: messageId } })));
      vi.stubGlobal('fetch', fetch);
      await expect(createWorkflowAiDraftBaasExecution()!.dispatch(request())).rejects.toThrow('BAAS_DISPATCH_UNCERTAIN');
      expect(fetch).toHaveBeenCalledOnce();
      expect(JSON.parse(fetch.mock.calls[0][1]!.body as string).message_id).toBe(taskId);
    },
  );
  it.each(['network', 'lost-body'])('recovers a lost acknowledgement (%s) by polling the fixed ID without another dispatch', async failure => {
    const frozen = request();
    let polls = 0;
    const fetch = vi.fn<typeof globalThis.fetch>(async (url, init) => {
      if (init?.method === 'POST') {
        expect(JSON.parse(init.body as string).message_id).toBe(frozen.envelope.taskId);
        if (failure === 'network') throw new Error('accepted, connection lost before acknowledgement');
        const response = new Response('');
        vi.spyOn(response, 'text').mockRejectedValue(new Error('acknowledgement body lost'));
        return response;
      }
      expect(url).toBe(`http://127.0.0.1:1/openapi/v1/messages/${frozen.envelope.taskId}`);
      if (++polls === 1) return new Response('', { status: 404 });
      return new Response(JSON.stringify(baasResult(frozen.envelope.taskId, frozen.envelope.target.botId)));
    });
    vi.stubGlobal('fetch', fetch);
    await expect(createWorkflowAiDraftBaasExecution()!.dispatch(frozen)).rejects.toThrow('BAAS_DISPATCH_UNCERTAIN');
    const recovered = createWorkflowAiDraftBaasExecution()!;
    expect(await recovered.poll({ ...frozen, jobId: frozen.envelope.taskId })).toEqual({ status: 'running' });
    expect(await recovered.poll({ ...frozen, jobId: frozen.envelope.taskId })).toMatchObject({ status: 'completed', result: { yaml } });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(JSON.parse(fetch.mock.calls[0][1]!.body as string).message_id).toBe(frozen.envelope.taskId);
    expect(fetch).toHaveBeenCalledTimes(3);
  });
  it.each([
    { failure: '401', status: 401, payload: {}, code: 'BAAS_AUTH_FAILED' },
    { failure: '403', status: 403, payload: {}, code: 'BAAS_AUTH_FAILED' },
    { failure: '500', status: 500, payload: {}, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'business-code-key-prefix-mismatch', status: 200, payload: { code: 60001, message: 'Message not found: private-prefix', data: null }, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'legacy-business-code', status: 200, payload: { buserviceErrorCode: 'TOKEN_EXPIRED', data: null }, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'business-code-with-candidate', status: 200, payload: { ...baasResult(taskId, 'wrong-bot:wrong-owner'), code: 60001 }, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'null-data', status: 200, payload: { code: 0, data: null }, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'network', status: 200, payload: {}, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'non-json', status: 200, payload: {}, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'empty-body', status: 200, payload: {}, code: 'BAAS_POLL_UNAVAILABLE' },
    { failure: 'lost-body', status: 200, payload: {}, code: 'BAAS_POLL_UNAVAILABLE' },
  ])('keeps polling the same accepted job after $failure without a second dispatch', async ({ failure, status, payload, code }) => {
    const frozen = request(); let polls = 0;
    const fetch = vi.fn<typeof globalThis.fetch>(async (url, init) => {
      if (init?.method === 'POST') return new Response(JSON.stringify({ code: 0, data: { message_id: taskId } }));
      expect(url).toBe(`http://127.0.0.1:1/openapi/v1/messages/${taskId}`);
      if (++polls > 1) return new Response(JSON.stringify(baasResult(taskId, frozen.envelope.target.botId)));
      if (failure === 'network') throw new Error('private transport diagnostic baas-test');
      if (failure === 'non-json') return new Response('private non-JSON gateway diagnostic');
      if (failure === 'empty-body') return new Response(null);
      if (failure === 'lost-body') return new Response(new ReadableStream({ start(controller) { controller.error(new Error('private stream diagnostic')); } }));
      return new Response(JSON.stringify(payload), { status });
    });
    vi.stubGlobal('fetch', fetch);
    const port = createWorkflowAiDraftBaasExecution()!;
    expect(await port.dispatch(frozen)).toEqual({ jobId: taskId });
    const failedQuery = port.poll({ ...frozen, jobId: taskId });
    await expect(failedQuery).rejects.toBeInstanceOf(WorkflowAiDraftPollUnavailableError);
    await expect(failedQuery).rejects.toMatchObject({ code, message: code });
    await expect(failedQuery).rejects.not.toBeInstanceOf(WorkflowAiDraftInvalidResultError);
    expect(await port.poll({ ...frozen, jobId: taskId })).toMatchObject({ status: 'completed', result: { yaml } });
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(3);
  });
  it('polls the acknowledged encoded BaaS message, handles pending and validates response budget', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response('', { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(baasResult('message', state.botId, { result: { content: [{ type: 'text', text: '```yaml\n' + yaml + '```' }] } }))))
      .mockResolvedValueOnce(new Response('x'.repeat(1_000_001)));
    vi.stubGlobal('fetch', fetch); const port = createWorkflowAiDraftBaasExecution()!;
    expect(await port.poll({ ...request(), jobId: 'message/a' })).toEqual({ status: 'running' });
    expect(fetch.mock.calls[0][0]).toBe('http://127.0.0.1:1/openapi/v1/messages/message%2Fa');
    expect(await port.poll({ ...request(), jobId: 'message' })).toMatchObject({ status: 'completed', result: { yaml } });
    const oversized = port.poll({ ...request(), jobId: 'message' });
    await expect(oversized).rejects.toBeInstanceOf(WorkflowAiDraftInvalidResultError);
    await expect(oversized).rejects.toThrow('BAAS_REPLY_TOO_LARGE');
  });
});
describe('BaaS provider namespace fence', () => {
  it.each(['dispatch', 'poll', 'resolveTarget'] as const)('blocks changed namespaces before %s I/O', async operation => {
    for (const field of ['origin', 'path', 'credential', 'environment'] as const) {
      state.baseUrl = 'http://127.0.0.1:1/provider/tenant-a'; state.apiKey = 'baas-test'; state.environment = 'pre';
      const port = createWorkflowAiDraftBaasExecution()!;
      const frozen = request();
      if (field === 'origin') state.baseUrl = 'http://127.0.0.1:2/provider/tenant-a';
      if (field === 'path') state.baseUrl = 'http://127.0.0.1:1/provider/tenant-b';
      if (field === 'credential') state.apiKey = 'provider-test';
      if (field === 'environment') state.environment = 'prod';
      const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch);
      const pending = operation === 'dispatch' ? port.dispatch(frozen) : operation === 'poll'
        ? port.poll({ ...frozen, jobId: taskId }) : port.resolveTarget();
      await expect(pending).rejects.toThrow(/^BAAS_PROVIDER_CHANGED$/);
      expect(fetch).not.toHaveBeenCalled();
    }
  });
  it.each(['dispatch', 'poll', 'resolveTarget'] as const)('fails safely on live config revocation before %s', async operation => {
    for (const field of ['missing-key', 'invalid-url', 'config-error'] as const) {
      state.apiKey = 'baas-test'; state.baseUrl = 'http://127.0.0.1:1'; state.unavailable = false;
      const port = createWorkflowAiDraftBaasExecution()!, frozen = request();
      if (field === 'missing-key') state.apiKey = '';
      if (field === 'invalid-url') state.baseUrl = 'https://user:private-credential@example.invalid/api?secret=value';
      if (field === 'config-error') state.unavailable = true;
      const fetch = vi.fn<typeof globalThis.fetch>(); vi.stubGlobal('fetch', fetch);
      const pending = operation === 'dispatch' ? port.dispatch(frozen) : operation === 'poll'
        ? port.poll({ ...frozen, jobId: taskId }) : port.resolveTarget();
      await expect(pending).rejects.toThrow(/^BAAS_NOT_CONFIGURED$/);
      expect(fetch).not.toHaveBeenCalled();
    }
  });
  it('canonicalizes the whole URL path and keeps dispatch/poll in the same namespace', async () => {
    state.baseUrl = 'HTTP://LOCALHOST:80/parent/../provider/tenant-a/';
    const port = createWorkflowAiDraftBaasExecution()!;
    state.baseUrl = 'http://localhost/provider/tenant-a';
    const fetch = vi.fn<typeof globalThis.fetch>(async (_url, init) => new Response(JSON.stringify(init?.method === 'POST'
      ? { code: 0, data: { message_id: taskId } } : baasResult(taskId, state.botId))));
    vi.stubGlobal('fetch', fetch);
    expect(await port.dispatch(request())).toEqual({ jobId: taskId });
    expect(await port.poll({ ...request(), jobId: taskId })).toMatchObject({ status: 'completed' });
    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      'http://localhost/provider/tenant-a/openapi/v1/messages',
      `http://localhost/provider/tenant-a/openapi/v1/messages/${taskId}`,
    ]);
  });
  it('allows IAM refresh within the same namespace but blocks live IAM revocation before I/O', async () => {
    vi.stubGlobal('process', Object.create(process, { platform: { value: 'darwin' } }));
    state.baseUrl = 'https://provider.example.invalid/tenant'; state.iamtoken = 'example';
    const port = createWorkflowAiDraftBaasExecution()!, frozen = request();
    state.iamtoken = 'test-token';
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(baasResult(taskId, frozen.envelope.target.botId))));
    vi.stubGlobal('fetch', fetch);
    expect(await port.poll({ ...frozen, jobId: taskId })).toMatchObject({ status: 'completed' });
    expect(fetch.mock.calls[0][1]?.headers).toMatchObject({ Authorization: 'Bearer baas-test', Cookie: 'iam_token=test-token' });
    state.iamtoken = '';
    await expect(port.poll({ ...frozen, jobId: taskId })).rejects.toThrow(/^BAAS_NOT_CONFIGURED$/);
    expect(fetch).toHaveBeenCalledOnce();
  });
  it('rechecks credential revocation after a successful dispatch without switching the poll key', async () => {
    const port = createWorkflowAiDraftBaasExecution()!, frozen = request();
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify({ data: { message_id: taskId } })));
    vi.stubGlobal('fetch', fetch);
    expect(await port.dispatch(frozen)).toEqual({ jobId: taskId });
    state.apiKey = 'provider-test';
    await expect(port.poll({ ...frozen, jobId: taskId })).rejects.toThrow(/^BAAS_PROVIDER_CHANGED$/);
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][1]?.headers).toMatchObject({ Authorization: 'Bearer baas-test' });
  });
});
describe('BaaS result identity fence', () => {
  it.each([
    {}, { message_id: taskId }, { bot_id: 'autoheal-bot:configured-owner' },
    { message_id: 'different-message', bot_id: 'autoheal-bot:configured-owner' },
    { message_id: taskId.toUpperCase(), bot_id: 'autoheal-bot:configured-owner' },
    { message_id: taskId, bot_id: 'other-bot:configured-owner' },
    { message_id: taskId, bot_id: 'autoheal-bot:other-owner' },
    { message_id: taskId, bot_id: 'autoheal-bot:configured-owner ' },
    { message_id: taskId, bot_id: null }, { message_id: taskId, bot_id: ['autoheal-bot:configured-owner'] },
  ])('rejects completed YAML without the exact expected identities: %j', async identity => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify({ code: 0,
      data: { status: 'completed', content: yaml, ...identity } })));
    vi.stubGlobal('fetch', fetch);
    const pending = createWorkflowAiDraftBaasExecution()!.poll({ ...request(), jobId: taskId });
    await expect(pending).rejects.toBeInstanceOf(WorkflowAiDraftInvalidResultError);
    await expect(pending).rejects.toThrow(/^BAAS_RESULT_IDENTITY_MISMATCH$/);
    expect(fetch).toHaveBeenCalledOnce();
  });
  it.each([
    { message_id: taskId, bot_id: 'autoheal-bot:configured-owner', status: 'completed', content: yaml },
    { message_id: taskId, bot_id: 'autoheal-bot:configured-owner', data: { status: 'completed', content: yaml } },
    { data: [] }, { data: 'private-yaml-payload' },
  ])('never falls back to outer-envelope identities or malformed data: %j', async payload => {
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(payload))));
    await expect(createWorkflowAiDraftBaasExecution()!.poll({ ...request(), jobId: taskId }))
      .rejects.toThrow(/^BAAS_RESULT_IDENTITY_MISMATCH$/);
  });
  it.each([
    { content: '', error: 'BAAS_EMPTY_REPLY' },
    { content: '```yaml\na: b\n```\n```yaml\nc: d\n```', error: 'BAAS_AMBIGUOUS_YAML' },
  ])('keeps genuine invalid Bot replies terminal rather than query-unavailable ($error)', async ({ content, error }) => {
    vi.stubGlobal('fetch', vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(baasResult(taskId, state.botId, { content })))));
    const pending = createWorkflowAiDraftBaasExecution()!.poll({ ...request(), jobId: taskId });
    await expect(pending).rejects.toBeInstanceOf(WorkflowAiDraftInvalidResultError);
    await expect(pending).rejects.toThrow(error);
    await expect(pending).rejects.not.toBeInstanceOf(WorkflowAiDraftPollUnavailableError);
  });
  it.each(['running', 'failed'])('rejects wrong identities even for %s results', async status => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(baasResult('different-message', state.botId, { status }))));
    vi.stubGlobal('fetch', fetch);
    await expect(createWorkflowAiDraftBaasExecution()!.poll({ ...request(), jobId: taskId }))
      .rejects.toBeInstanceOf(WorkflowAiDraftInvalidResultError);
  });
  it.each([
    ['autoheal-bot:00123456', 'autoheal-bot:123456'],
    ['autoheal-bot:000', 'autoheal-bot:0'],
    ['autoheal-bot:123456', 'autoheal-bot:00123456'],
    ['autoheal-bot:000900719925474099312345', 'autoheal-bot:900719925474099312345'],
  ])('matches BaaS normalize_bot_id without lossy numeric conversion (%s -> %s)', async (expectedBot, responseBot) => {
    state.botId = expectedBot;
    const frozen = request(), port = createWorkflowAiDraftBaasExecution()!;
    state.botId = 'changed-live-config:999';
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(baasResult(taskId, responseBot))));
    vi.stubGlobal('fetch', fetch);
    expect(await port.poll({ ...frozen, jobId: taskId })).toMatchObject({ status: 'completed', result: { yaml } });
  });
  it('does not equate distinct large owner IDs via JavaScript Number rounding', async () => {
    state.botId = 'autoheal-bot:9007199254740992';
    const port = createWorkflowAiDraftBaasExecution()!, frozen = request();
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(baasResult(taskId, 'autoheal-bot:9007199254740993'))));
    vi.stubGlobal('fetch', fetch);
    await expect(port.poll({ ...frozen, jobId: taskId })).rejects.toBeInstanceOf(WorkflowAiDraftInvalidResultError);
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

it.each([false, true])('integrates stateless draft core with BaaS without a task table or redispatch (lost acknowledgement: %s)', async lostAck => {
  const { aiDraftFixture, draftRequest } = await import('./workflow-ai-draft-db-fixture.js');
  const { createWorkflowAiDraftRuntime } = await import('../workflow-ai-draft-runtime.js');
  const f = await aiDraftFixture();
  const generated = draftRequest().currentYaml.replace('Unsaved editor source', 'AI suggested title');
  let messageId = '';
  const fetch = vi.fn<typeof globalThis.fetch>().mockImplementationOnce(async (_url, init) => {
    messageId = JSON.parse(init!.body as string).message_id;
    if (lostAck) throw new Error('accepted before acknowledgement was lost');
    return new Response(JSON.stringify({ data: { message_id: messageId } }));
  }).mockImplementationOnce(async () => new Response(JSON.stringify(baasResult(messageId, state.botId, { result: { content: '```yaml\n' + generated + '```' } }))));
  vi.stubGlobal('fetch', fetch);
  try {
    const options = { principal: async () => ({ actorId: 'owner' }), execution: createWorkflowAiDraftBaasExecution(), receipts: f.receipts };
    const runtime = createWorkflowAiDraftRuntime(f.db, options);
    const input = { ...draftRequest(), botId: 'forged:caller' };
    const prepared = await runtime.service.prepare('owner', 'flow', input);
    const task = await runtime.service.create('owner', 'flow', { ...input, receipt: prepared.receipt });
    expect(task).toMatchObject({ draftId: prepared.draftId, status: lostAck ? 'dispatch_uncertain' : 'running' });
    // Simulate a new server process: restoration only polls the signed, fixed ID.
    const restored = createWorkflowAiDraftRuntime(f.db, options);
    expect(await restored.service.get('owner', 'flow', task.draftId, { receipt: prepared.receipt, currentYaml: input.currentYaml }))
      .toMatchObject({ status: 'completed', result: { yaml: generated } });
    const body = JSON.parse(fetch.mock.calls[0][1]!.body as string);
    expect(body.bot_id).toBe('autoheal-bot:configured-owner');
    expect(body.message_id).toBe(prepared.draftId);
    expect(fetch.mock.calls[1][0]).toBe(`http://127.0.0.1:1/openapi/v1/messages/${prepared.draftId}`);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(f.raw.prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'workflow_ai_drafts'").get()).toBeUndefined();
    expect(f.raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'flow'").get()).toEqual({ spec_json: '{}' });
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
  const fetch = vi.fn(async () => new Response(JSON.stringify(baasResult('accepted-message', frozen.envelope.target.botId))));
  vi.stubGlobal('fetch', fetch);
  expect(await port.poll({ ...frozen, jobId: 'accepted-message' })).toMatchObject({ status: 'completed', result: { yaml } });
  expect(fetch).toHaveBeenCalledOnce();
});
