import { afterEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';
import type { Server } from 'node:http';
import { appendFileSync } from 'node:fs';
import { formatHttpLog } from '@avernet/clawweb-shared/server/services/file-log-format';
import { createWorkflowAiDraftService } from '../../services/workflow-ai-draft-service.js';
import { createWorkflowAiDraftReceipts } from '../../services/workflow-ai-draft-receipts.js';
import { createWorkflowAiDraftRouter } from '../workflow-ai-drafts.js';
import { WorkflowAiDraftError, WorkflowAiDraftDispatchRejectedError } from '../../contracts/workflow-ai-draft.js';
const files = vi.hoisted(() => ({ appendFileSync: vi.fn() }));
vi.mock('node:fs', async importOriginal => ({ ...await importOriginal<typeof import('node:fs')>(), ...files }));
const channels = ['log', 'info', 'warn', 'error', 'debug', 'trace', 'dir', 'dirxml', 'table'] as const;
function captureLogs() {
  files.appendFileSync.mockClear();
  return channels.map(channel => vi.spyOn(console, channel).mockImplementation(() => {}));
}
const closing: Array<() => Promise<void>> = [];
afterEach(async () => { for (const close of closing.splice(0).reverse()) await close(); vi.restoreAllMocks(); });
const request = () => ({ requestId: randomUUID(), mode: 'modify' as const, prompt: 'Improve the workflow',
  currentYaml: 'id: flow\nversion: 7\nnodes:\n  - id: n\n    executor: {type: done}\n' });
async function fixture(publicOrigin?: string, publicOrigins?: readonly string[]) {
  let principal: { actorId: string } | null = { actorId: 'owner' }, allowed = true;
  const execution = { resolveTarget: vi.fn(async () => ({ botId: 'auto-heal-bot:configured-owner' })),
    dispatch: vi.fn(async input => ({ jobId: input.envelope.taskId })), poll: vi.fn(async () => ({ status: 'running' as const })) };
  const receipts = createWorkflowAiDraftReceipts('synthetic-route-receipt-key');
  const service = createWorkflowAiDraftService({ execution, receipts, canEdit: async () => allowed });
  const app = express(); app.use(express.json({ limit: '1mb' }));
  // Exercise the HTTP diagnostic-to-file formatter contract; the enterprise middleware has its own integration suite.
  app.use((_req, res, next) => {
    res.once('finish', () => {
      const diagnostic = res.locals.taskGuardApiDiagnostic;
      if (diagnostic) appendFileSync('/test/task-guard-http.log', formatHttpLog(new Date(), 'ERROR', 'http', diagnostic) + '\n');
    });
    next();
  });
  app.use('/api/workflows', createWorkflowAiDraftRouter({ service, principal: async () => principal, publicOrigin, publicOrigins }));
  const server: Server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  closing.push(() => new Promise((resolve, reject) => server.close(e => e ? reject(e) : resolve())));
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}`, url = `${base}/api/workflows/flow/ai-drafts`;
  const post = (path: string, body: unknown = request(), headers: Record<string, string> = {}) => fetch(url + path, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', ...headers }, body: JSON.stringify(body) });
  const prepare = async (input = request()) => {
    const prepared = await post('/prepare', input); expect(prepared.status).toBe(200);
    return { input, ...await prepared.json() as { draftId: string; receipt: string; inputSpecDigest: string } };
  };
  return { execution, service, base, url, post, prepare,
    setPrincipal: (value: typeof principal) => { principal = value; }, revoke: () => { allowed = false; } };
}
describe('Stateless workflow AI draft route security and browser contract', () => {
  it('prepares a signed handle before dispatch, polls without a table, and cancels only the local attempt', async () => {
    const f = await fixture();
    const cap = await fetch(f.url + '/capabilities'); expect(await cap.json()).toEqual({ available: true });
    expect(cap.headers.get('cache-control')).toBe('no-store');
    const p = await f.prepare(); expect(f.execution.dispatch).not.toHaveBeenCalled();
    const created = await f.post('', { ...p.input, receipt: p.receipt }); expect(created.status).toBe(202);
    expect(await created.json()).toEqual({ draftId: p.draftId, status: 'running' });
    const context = { receipt: p.receipt, currentYaml: p.input.currentYaml };
    const detail = await f.post(`/${p.draftId}/poll`, context);
    expect(await detail.json()).toMatchObject({ draftId: p.draftId, workflowId: 'flow', status: 'running', inputSpecDigest: p.inputSpecDigest });
    expect(await (await f.post(`/${p.draftId}/cancel`, { receipt: p.receipt })).json()).toMatchObject({ status: 'cancelled' });
    // No global cancellation row or remote stop is invented.
    expect(await (await f.post(`/${p.draftId}/poll`, context)).json()).toMatchObject({ status: 'running' });
    expect(f.execution.dispatch).toHaveBeenCalledOnce();
    expect((await fetch(f.url + '/' + p.draftId)).status).toBe(410);
  });
  it('does not accept forged actor/admin or unsigned headers as a principal', async () => {
    const f = await fixture(); f.setPrincipal(null);
    for (const path of ['/capabilities', '/00000000-0000-4000-8000-000000000000'])
      expect((await fetch(f.url + path, { headers: { 'x-user-id': 'owner', cookie: 'staff_id=owner', 'x-admin': 'true' } })).status).toBe(401);
    for (const path of ['', '/prepare', '/anything/poll', '/anything/cancel'])
      expect((await f.post(path, { ...request(), actorId: 'owner', isAdmin: true })).status).toBe(401);
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('requires CSRF, exact origin and same-site transport for prepare, dispatch, poll and cancel', async () => {
    const f = await fixture();
    for (const path of ['', '/prepare', '/anything/poll', '/anything/cancel']) {
      expect((await fetch(f.url + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' })).status).toBe(403);
      for (const headers of [{ origin: 'https://evil.example' }, { 'sec-fetch-site': 'cross-site' }])
        expect((await f.post(path, {}, headers)).status).toBe(403);
    }
    expect((await fetch(f.url + '/capabilities', { headers: { origin: 'https://evil.example' } })).status).toBe(403);
    expect((await f.post('/prepare', request(), { origin: f.base })).status).toBe(200);
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('binds signed handles to exact workflow/owner and rechecks live edit permission', async () => {
    const f = await fixture(), p = await f.prepare();
    f.setPrincipal({ actorId: 'other' });
    for (const suffix of ['poll', 'cancel']) {
      const response = await f.post(`/${p.draftId}/${suffix}`, { receipt: p.receipt, currentYaml: p.input.currentYaml });
      expect(response.status).toBeGreaterThanOrEqual(400);
    }
    f.setPrincipal({ actorId: 'owner' }); f.revoke();
    expect((await fetch(f.url + '/capabilities')).status).toBe(403);
    for (const [path, body] of [['/prepare', p.input], ['', { ...p.input, receipt: p.receipt }],
      [`/${p.draftId}/poll`, { receipt: p.receipt, currentYaml: p.input.currentYaml }], [`/${p.draftId}/cancel`, { receipt: p.receipt }]] as const)
      expect((await f.post(path, body)).status).toBe(403);
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it('rejects modified input or a forged receipt before provider access', async () => {
    const f = await fixture(), p = await f.prepare();
    for (const body of [{ ...p.input, receipt: p.receipt, prompt: 'changed' }, { ...p.input, receipt: p.receipt, currentYaml: p.input.currentYaml + '# changed\n' },
      { ...p.input, receipt: 'forged' }, p.input]) {
      const response = await f.post('', body); expect(response.status).toBeGreaterThanOrEqual(400);
    }
    const poll = await f.post(`/${randomUUID()}/poll`, { receipt: p.receipt, currentYaml: p.input.currentYaml });
    expect(poll.status).toBeGreaterThanOrEqual(400);
    expect(f.execution.dispatch).not.toHaveBeenCalled(); expect(f.execution.poll).not.toHaveBeenCalled();
  });
  it('validates UTF-8 byte limits, prompts, workflow and request UUID before preparation', async () => {
    const f = await fixture(), input = request();
    expect((await f.post('/prepare', { ...input, currentYaml: 'id: other\nversion: 1\nnodes: []' })).status).toBe(400);
    expect((await f.post('/prepare', { ...input, prompt: '大'.repeat(4001) })).status).toBe(413);
    expect((await f.post('/prepare', { ...input, currentYaml: input.currentYaml + '# ' + '大'.repeat(34_000) })).status).toBe(413);
    expect((await f.post('/prepare', { ...input, requestId: 'uuid' })).status).toBe(400);
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it('can recover an unacknowledged dispatch by read-only polling the prepared ID', async () => {
    const f = await fixture(), p = await f.prepare();
    f.execution.dispatch.mockRejectedValueOnce(new Error('ACK_LOST_PRIVATE_SECRET'));
    expect(await (await f.post('', { ...p.input, receipt: p.receipt })).json()).toEqual({ draftId: p.draftId, status: 'dispatch_uncertain' });
    for (let n = 0; n < 2; n++) await f.post(`/${p.draftId}/poll`, { receipt: p.receipt, currentYaml: p.input.currentYaml });
    expect(f.execution.dispatch).toHaveBeenCalledOnce();
    expect(f.execution.poll).toHaveBeenCalledTimes(2);
  });
  it.each(['TypeError', '<PRIVATE_SECRET_NAME>'])('logs only fixed metadata on every channel for unexpected create failure named %s', async name => {
    const logs = captureLogs(), f = await fixture();
    const error = Object.assign(new Error('SQL <PRIVATE_SECRET_MESSAGE> prompt=<PRIVATE_SECRET_PROMPT> currentYaml=<PRIVATE_SECRET_YAML>'), {
      config: { headers: { Authorization: '<PRIVATE_SECRET_TOKEN>' } }, token: '<PRIVATE_SECRET_TOKEN>',
    }); error.name = name;
    vi.spyOn(f.service, 'create').mockRejectedValue(error);
    const response = await f.post(''); expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ code: 'INTERNAL_ERROR', error: 'AI draft request failed' });
    const info = vi.mocked(console.info).mock.calls.map(call => String(call[0])); expect(info).toHaveLength(1);
    expect(JSON.parse(info[0].replace('[workflow-background] ', ''))).toMatchObject({ job: 'workflow_ai_draft_request', event: 'failed', operation: 'create', phase: 'service', errorCode: name === 'TypeError' ? name : 'INTERNAL_ERROR' });
    expect(console.error).toHaveBeenCalledWith('[workflow-ai-drafts] request failed:', expect.objectContaining({ stackSource: 'observation', stack: expect.stringContaining('Workflow protected operation failure observed') }));
    expect(files.appendFileSync).toHaveBeenCalledOnce();
    const line = files.appendFileSync.mock.calls[0][1] as string;
    expect(line).toContain(' ERROR [http] ');
    expect(JSON.parse(line.slice(line.indexOf('{')))).toEqual({ message: 'Protected workflow operation failed', stack: expect.any(String), stackSource: 'observation' });
    expect(JSON.stringify([...logs.map(log => log.mock.calls), files.appendFileSync.mock.calls])).not.toMatch(/PRIVATE_SECRET|prompt=|currentYaml|requestId|actorId|SQL|Authorization|config/);
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
  it.each(['FORBIDDEN', 'CAPABILITY_UNAVAILABLE'] as const)('keeps typed %s logs fixed without changing the response contract', async code => {
    const logs = captureLogs(), f = await fixture();
    const error = new WorkflowAiDraftError(code, '<PRIVATE_SECRET_TOKEN> prompt=<PRIVATE_SECRET_YAML>');
    vi.spyOn(f.service, 'create').mockRejectedValue(error);
    const response = await f.post(''); expect(response.status).toBe(code === 'FORBIDDEN' ? 403 : 503);
    expect(await response.json()).toEqual({ code, error: error.message });
    if (code === 'FORBIDDEN') {
      expect(console.warn).toHaveBeenCalledWith('[workflow-ai-drafts] request rejected:', { operation: 'create', phase: 'service', errorCode: code });
      expect(console.error).not.toHaveBeenCalled();
    } else {
      expect(console.error).toHaveBeenCalledWith('[workflow-ai-drafts] request failed:', expect.objectContaining({ errorCode: code, stackSource: 'observation' }));
      expect(files.appendFileSync).toHaveBeenCalledOnce();
    }
    expect(JSON.stringify([...logs.map(log => log.mock.calls), files.appendFileSync.mock.calls])).not.toMatch(/PRIVATE_SECRET|prompt=|currentYaml/);
  });
  it('never exposes raw provider credentials in errors', async () => {
    const f = await fixture(); f.execution.resolveTarget.mockRejectedValue(new Error('https://private/?token=SECRET'));
    const response = await f.post('/prepare'); expect(response.status).toBe(503); expect(await response.text()).not.toContain('SECRET');
  });
  it('ignores browser target overrides and reports a positive BaaS rejection directly', async () => {
    const f = await fixture(), p = await f.prepare();
    f.execution.dispatch.mockRejectedValue(new WorkflowAiDraftDispatchRejectedError('AUTO_HEAL_BOT_UNAVAILABLE'));
    const response = await f.post('', { ...p.input, receipt: p.receipt, botId: 'forged:owner', target: { botId: 'forged:owner' }, actorId: 'forged' });
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({ draftId: p.draftId, status: 'failed', error: 'AUTO_HEAL_BOT_UNAVAILABLE' });
    expect(f.execution.dispatch.mock.calls[0][0]).toMatchObject({ actorId: 'owner', envelope: { taskId: p.draftId, target: { botId: 'auto-heal-bot:configured-owner' } } });
  });
  it('accepts configured HTTPS aliases but never forwarded host/protocol as origin authority', async () => {
    const f = await fixture('https://workflow.example.invalid/', ['https://workflow.example.invalid', 'https://alias.example.invalid']);
    for (const origin of ['https://workflow.example.invalid', 'https://alias.example.invalid'])
      expect((await f.post('/prepare', request(), { origin })).status).toBe(200);
    expect((await f.post('/prepare', request(), { origin: f.base, 'x-forwarded-proto': 'https', 'x-forwarded-host': 'workflow.example.invalid' })).status).toBe(403);
    expect((await fetch(f.url + '/capabilities', { headers: { origin: 'https://evil.example' } })).status).toBe(403);
  });
  it.each(['https://user:password@example.invalid', 'https://example.invalid/path', 'https://example.invalid/?secret=1', 'https://example.invalid/#hash', 'file:///private'])('rejects invalid configured origin: %s', publicOrigin => {
    expect(() => createWorkflowAiDraftRouter({ service: {} as never, principal: async () => null, publicOrigin })).toThrow('AI_DRAFT_PUBLIC_ORIGINS_INVALID');
  });
});
