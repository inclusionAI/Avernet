import { afterEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ botId: 'autoheal-test:configured-owner', apiKey: 'baas-test',
  baseUrl: 'http://127.0.0.1:1', iamtoken: '' }));
vi.mock('@avernet/clawweb-shared/server/db', () => ({
  resolveAutoHealConfig: () => ({ botId: state.botId, baasEnvironment: 'pre' }),
  resolveBaasConfig: () => ({ ...state, environments: {
    dev: { apiKey: '', baseUrl: '' }, pre: { apiKey: state.apiKey, baseUrl: state.baseUrl },
    prod: { apiKey: '', baseUrl: '' },
  } }),
}));
vi.mock('@avernet/clawweb-shared/server/env', () => ({ getCurrentEnv: () => 'pre' }));
import { WorkflowAiDraftInvalidResultError } from '@avernet/workflow/server/contracts/workflow-ai-draft';
import { decodeWorkflowAiDraftBaasReply } from '../workflow-ai-draft-baas-reply.js';

const taskId = 'd0594cc0-0ef7-4c93-ab90-a3d95f4316ab';
const yaml = 'id: flow\nversion: 7\nnodes:\n  - id: n\n    executor: {type: done}\n';
const fencedYaml = '```yaml\n' + yaml + '```';
const bindingError = 'BotBindingNotFoundError: Bot binding not found: hidden-bot:hidden-owner';
// Synthetic diagnostics must never become part of a public error or draft.
const privateError = bindingError + '\n    at /private/provider/worker.py:42'
  + '\nhttps://provider.example.invalid/private?apiKey=synthetic-secret Authorization: Bearer synthetic-token';
const envelope = (data: Record<string, unknown> = {}) => ({ code: 0, message: 'success', data: {
  message_id: taskId, bot_id: state.botId, session_id: 'synthetic-session',
  created_at: '2026-11-16T00:00:00Z', status: 'FAILED', result: null, error: privateError, ...data,
} });
afterEach(() => { vi.unstubAllGlobals(); });

describe('BaaS terminal failure public classification', () => {
  it('maps the real FAILED result/error envelope to a fixed code without leaking diagnostics', () => {
    const result = decodeWorkflowAiDraftBaasReply(envelope());
    expect(result).toEqual({ status: 'failed', error: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    expect(JSON.stringify(result)).not.toMatch(/hidden-bot|hidden-owner|private|worker\.py|https?:|synthetic-secret|synthetic-token|BotBindingNotFoundError/);
  });

  it.each([
    'BotBindingNotFoundError', bindingError, 'Bot binding not found: hidden-bot:hidden-owner',
    '  Bot binding not found: hidden-bot:hidden-owner  ',
  ])('recognizes only an explicit binding failure signature: %s', error => {
    expect(decodeWorkflowAiDraftBaasReply(envelope({ error })))
      .toEqual({ status: 'failed', error: 'AUTO_HEAL_BOT_UNAVAILABLE' });
  });

  it('preserves the existing message_status terminal alias', () => {
    expect(decodeWorkflowAiDraftBaasReply(envelope({ status: undefined, message_status: 'FAILED' })))
      .toEqual({ status: 'failed', error: 'AUTO_HEAL_BOT_UNAVAILABLE' });
  });

  it.each([
    undefined, null, 60001, {}, { name: 'BotBindingNotFoundError', message: bindingError }, [bindingError],
    '', '60001', 'Bot is not ACTIVE', 'bot_id invalid', 'TimeoutError: execution expired',
    'NotBotBindingNotFoundError: different failure', 'BotBindingNotFoundErrorExtra: different failure',
    'Bot binding not found without the required colon', fencedYaml,
    'BotBindingNotFoundError is mentioned in assistant output',
    'UpstreamError: quoted Bot binding not found: hidden-bot:hidden-owner',
  ])('keeps unrelated or non-string terminal errors generic: %j', error => {
    expect(decodeWorkflowAiDraftBaasReply(envelope({ error })))
      .toEqual({ status: 'failed', error: 'BAAS_GENERATION_FAILED' });
  });

  it('bounds classification and never scans past the error string limit', () => {
    const bounded = bindingError + 'x'.repeat(4096 - bindingError.length);
    expect(decodeWorkflowAiDraftBaasReply(envelope({ error: bounded })))
      .toEqual({ status: 'failed', error: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    for (const error of [bounded + 'x', 'x'.repeat(4096) + bindingError]) {
      expect(decodeWorkflowAiDraftBaasReply(envelope({ error })))
        .toEqual({ status: 'failed', error: 'BAAS_GENERATION_FAILED' });
    }
  });

  it.each(['FAILED', 'ERROR', 'CANCELLED', 'CANCELED', 'STOPPED', 'ABORTED', 'TIME_OUT'])(
    'preserves %s terminal failure even when content contains valid fenced YAML', status => {
      for (const error of [privateError, 'TimeoutError: private diagnostic']) {
        const result = decodeWorkflowAiDraftBaasReply(envelope({ status, error,
          result: { content: fencedYaml }, content: fencedYaml,
          messages: [{ role: 'assistant', content: fencedYaml }],
        }));
        const expected = status === 'TIME_OUT' ? 'BAAS_GENERATION_TIMEOUT'
          : ['CANCELLED', 'CANCELED', 'STOPPED', 'ABORTED'].includes(status) ? 'BAAS_GENERATION_CANCELLED'
          : error === privateError ? 'AUTO_HEAL_BOT_UNAVAILABLE' : 'BAAS_GENERATION_FAILED';
        expect(result).toEqual({ status: 'failed', error: expected });
        expect(result).not.toHaveProperty('result');
      }
    },
  );

  it('does not classify assistant output, nested result diagnostics or outer-envelope errors', () => {
    expect(decodeWorkflowAiDraftBaasReply({ ...envelope({ error: null,
      result: { error: privateError, content: privateError }, message: privateError,
      messages: [{ role: 'assistant', content: privateError }],
    }), error: privateError })).toEqual({ status: 'failed', error: 'BAAS_GENERATION_FAILED' });
  });

  it('keeps unwrapped terminal envelopes generic without trusted provider data.error', () => {
    expect(decodeWorkflowAiDraftBaasReply({ status: 'FAILED', error: privateError, content: fencedYaml }))
      .toEqual({ status: 'failed', error: 'BAAS_GENERATION_FAILED' });
  });

  it('does not accept an error string as a COMPLETED assistant reply even if it contains YAML', () => {
    expect(() => decodeWorkflowAiDraftBaasReply(envelope({ status: 'COMPLETED', error: fencedYaml })))
      .toThrow('BAAS_EMPTY_REPLY');
  });

  it.each(['RUNNING', 'PENDING', 'QUEUED', 'UNKNOWN', ''])('does not infer failure from an error on nonterminal status %s', status => {
    expect(decodeWorkflowAiDraftBaasReply(envelope({ status }))).toEqual({ status: 'running' });
  });

  it('does not infer terminal failure from an error when status is missing', () => {
    const value = envelope();
    delete (value.data as Record<string, unknown>).status;
    expect(decodeWorkflowAiDraftBaasReply(value)).toEqual({ status: 'running' });
  });

  it('does not falsely fail a COMPLETED response because of error or result text', () => {
    const content = privateError + '\n' + fencedYaml;
    const result = decodeWorkflowAiDraftBaasReply(envelope({ status: 'COMPLETED', result: { content } }));
    expect(result).toMatchObject({ status: 'completed', result: { yaml } });
    expect(JSON.stringify(result)).not.toMatch(/hidden-bot|synthetic-secret|synthetic-token|worker\.py/);
  });

  it.each([
    { result: { content: [{ type: 'image', text: 'not output' }, { type: 'text', text: fencedYaml }] } },
    { messages: [{ role: 'user', content: 'not output' }, { sender: 'bot', body: fencedYaml }] },
    { content: fencedYaml },
  ])('accepts existing COMPLETED reply shapes without reinterpreting error text: %j', content => {
    expect(decodeWorkflowAiDraftBaasReply(envelope({ status: 'COMPLETED', ...content })))
      .toMatchObject({ status: 'completed', result: { yaml } });
  });

  it.each([{ code: 60001 }, { code: '60001' }, { buserviceErrorCode: 'TOKEN_EXPIRED' }])(
    'preserves outer business rejection precedence: %j', outer => {
      expect(decodeWorkflowAiDraftBaasReply({ ...envelope({ result: { content: fencedYaml } }), ...outer }))
        .toEqual({ status: 'failed', error: 'BAAS_PROVIDER_REJECTED' });
    },
  );
});

describe('terminal failure identity remains an upstream prerequisite', () => {
  const pollRequest = () => ({ actorId: 'different-actor', jobId: taskId, envelope: {
    schemaVersion: 'workflow-yaml-draft/v1' as const, taskType: 'workflow_yaml_draft' as const,
    taskId, target: { botId: state.botId },
  } });

  it('classifies the matching provider message without dispatching or accepting failed YAML', async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(envelope({ result: { content: fencedYaml } }))));
    vi.stubGlobal('fetch', fetch);
    const { createWorkflowAiDraftBaasExecution } = await import('../workflow-ai-draft-baas-execution.js');
    expect(await createWorkflowAiDraftBaasExecution()!.poll(pollRequest()))
      .toEqual({ status: 'failed', error: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe(`http://127.0.0.1:1/openapi/v1/messages/${taskId}`);
    expect(fetch.mock.calls[0][1]?.method).not.toBe('POST');
  });

  it.each([
    { message_id: 'wrong-message' }, { message_id: undefined },
    { bot_id: 'wrong-bot:configured-owner' }, { bot_id: 'autoheal-test:wrong-owner' }, { bot_id: undefined },
  ])('rejects mismatched identity before classifying failure: %j', async identity => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(envelope(identity)))));
    const { createWorkflowAiDraftBaasExecution } = await import('../workflow-ai-draft-baas-execution.js');
    const result = createWorkflowAiDraftBaasExecution()!.poll(pollRequest());
    await expect(result).rejects.toBeInstanceOf(WorkflowAiDraftInvalidResultError);
    await expect(result).rejects.toThrow(/^BAAS_RESULT_IDENTITY_MISMATCH$/);
  });
});
