import { createHmac } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { createWorkflowAiDraftReceipts } from '../workflow-ai-draft-receipts.js';
import { createWorkflowAiDraftService } from '../workflow-ai-draft-service.js';
import { aiDraftSha256 } from '../workflow-ai-draft-validation.js';
import { coreFixture, draftRequest, overlayYaml, receiptSecret, targetBotId } from './workflow-ai-draft-core-fixtures.js';
const claims = () => ({ actorId: 'owner', workflowId: 'flow', requestId: draftRequest().requestId,
  requestDigest: aiDraftSha256('exact request'), inputSpecDigest: aiDraftSha256(overlayYaml), target: { botId: targetBotId },
  issuedAt: 1_000_000, expiresAt: 2_800_000 });
const sign = (payload: string) => {
  const encoded = Buffer.from(payload).toString('base64url');
  return encoded + '.' + createHmac('sha256', receiptSecret).update('avernet/workflow-ai-draft/receipt/v1\0').update(encoded).digest('base64url');
};
describe('Signed metadata receipts (no task storage)', () => {
  it('round-trips strict metadata, never prompt/YAML/secret', () => {
    const codec = createWorkflowAiDraftReceipts(receiptSecret), input = claims(), p = codec.issue(input);
    expect(p).toEqual({ draftId: expect.stringMatching(/^[\da-f]{8}-[\da-f]{4}-8[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/), receipt: expect.any(String), inputSpecDigest: input.inputSpecDigest });
    expect(codec.verify(p.receipt)).toEqual({ ...input, schemaVersion: 'workflow-ai-draft-receipt/v1', draftId: p.draftId });
    const text = Buffer.from(p.receipt.split('.')[0], 'base64url').toString();
    for (const privateText of [overlayYaml, receiptSecret, 'Improve the workflow']) expect(text).not.toContain(privateText);
    expect(p.receipt.length).toBeLessThanOrEqual(4096);
  });
  it('derives ID from secret/actor/workflow/request/digest/Bot but not time', () => {
    const codec = createWorkflowAiDraftReceipts(receiptSecret), input = claims(), p = codec.issue(input);
    expect(codec.issue({ ...input, issuedAt: input.issuedAt + 1, expiresAt: input.expiresAt + 1 }).draftId).toBe(p.draftId);
    for (const change of [{ actorId: 'other' }, { workflowId: 'other-flow' }, { requestId: draftRequest().requestId },
      { requestDigest: aiDraftSha256('different request') }, { target: { botId: 'other:owner' } }]) expect(codec.issue({ ...input, ...change }).draftId).not.toBe(p.draftId);
    expect(createWorkflowAiDraftReceipts('rotated secret').issue(input).draftId).not.toBe(p.draftId);
  });
  it('rejects corruption and key rotation without leaking diagnostics', () => {
    const codec = createWorkflowAiDraftReceipts(receiptSecret), receipt = codec.issue(claims()).receipt;
    for (const bad of ['', 'broken', receipt + '.extra', receipt + '=', receipt.replace('.', '..'), 'x'.repeat(4097), receipt.slice(0, -1),
      receipt.replace(/.$/, receipt.endsWith('A') ? 'B' : 'A')]) expect(() => codec.verify(bad)).toThrow('INVALID_INPUT');
    expect(() => createWorkflowAiDraftReceipts('rotated secret').verify(receipt)).toThrow('INVALID_INPUT');
    expect(() => createWorkflowAiDraftReceipts('')).toThrow('AI_DRAFT_RECOVERY_UNAVAILABLE');
  });
  it.each([
    { schemaVersion: 'workflow-ai-draft-receipt/v2' }, { unknown: 'secret' }, { target: { botId: targetBotId, secret: 'private' } },
    { draftId: 'not-uuid' }, { actorId: '' }, { actorId: '\ud800' }, { workflowId: '../bad' }, { requestId: 'bad' },
    { requestDigest: 'x'.repeat(64) }, { inputSpecDigest: 'a'.repeat(63) }, { issuedAt: '1000000' },
    { expiresAt: null }, { issuedAt: -1 }, { expiresAt: 1_000_000 }, { issuedAt: 1.5 }, { target: null },
  ])('rejects correctly signed malformed/unknown claims: %j', change => {
    const codec = createWorkflowAiDraftReceipts(receiptSecret), valid = codec.verify(codec.issue(claims()).receipt);
    expect(() => codec.verify(sign(JSON.stringify({ ...valid, ...change })))).toThrow('INVALID_INPUT');
  });
  it('rejects duplicate fields and signatures without the fixed domain', () => {
    const codec = createWorkflowAiDraftReceipts(receiptSecret), p = codec.issue(claims()), payload = Buffer.from(p.receipt.split('.')[0], 'base64url').toString();
    expect(() => codec.verify(sign(payload.replace('{', '{"actorId":"other",')))).toThrow('INVALID_INPUT');
    const encoded = p.receipt.split('.')[0], mac = createHmac('sha256', receiptSecret).update(encoded).digest('base64url');
    expect(() => codec.verify(encoded + '.' + mac)).toThrow('INVALID_INPUT');
  });
  it('rejects extra issue fields, excessive lifetime and unbounded metadata', () => {
    const codec = createWorkflowAiDraftReceipts(receiptSecret);
    for (const input of [{ ...claims(), prompt: 'private' }, { ...claims(), expiresAt: Number.MAX_SAFE_INTEGER },
      { ...claims(), target: { botId: 'x'.repeat(5000) } }]) expect(() => codec.issue(input)).toThrow('INVALID_INPUT');
  });
  it('recovers across independent codecs and fails closed on rotation', async () => {
    const f = coreFixture(), { request, prepared, context } = await f.prepare(); await f.service.create('owner', 'flow', request);
    f.setResult({ yaml: overlayYaml, summary: 'Recovered' });
    expect(await createWorkflowAiDraftService({ ...f.options, receipts: createWorkflowAiDraftReceipts(receiptSecret) }).get('owner', 'flow', prepared.draftId, context)).toMatchObject({ status: 'completed' });
    await expect(createWorkflowAiDraftService({ ...f.options, receipts: createWorkflowAiDraftReceipts('rotated secret') }).get('owner', 'flow', prepared.draftId, context)).rejects.toMatchObject({ code: 'INVALID_INPUT', message: 'INVALID_INPUT' });
  });
  it('refuses not-yet-issued receipts', async () => {
    const f = coreFixture(), request = draftRequest(), requestDigest = aiDraftSha256(JSON.stringify([request.mode, request.prompt, request.currentYaml]));
    const p = f.options.receipts.issue({ ...claims(), requestId: request.requestId, requestDigest, issuedAt: 1_005_001, expiresAt: 2_805_001 });
    await expect(f.service.create('owner', 'flow', { ...request, receipt: p.receipt })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    expect(f.execution.dispatch).not.toHaveBeenCalled();
  });
});

it('allows only a small cross-instance clock skew without extending signed lifetime', async () => {
  const f = coreFixture(), request = draftRequest();
  const p = f.options.receipts.issue({ ...claims(), requestId: request.requestId,
    requestDigest: aiDraftSha256(JSON.stringify([request.mode, request.prompt, request.currentYaml])),
    issuedAt: 1_005_000, expiresAt: 2_805_000 });
  expect(await f.service.create('owner', 'flow', { ...request, receipt: p.receipt })).toMatchObject({ draftId: p.draftId, status: 'running' });
  f.advance(1_805_000);
  expect(await f.service.get('owner', 'flow', p.draftId, { receipt: p.receipt, currentYaml: request.currentYaml }))
    .toMatchObject({ status: 'failed', error: 'EXECUTION_TIMEOUT' });
  expect(f.execution.poll).not.toHaveBeenCalled();
});
