import { createHmac, createSecretKey, timingSafeEqual } from 'node:crypto';
import { WorkflowAiDraftError, WORKFLOW_AI_DRAFT_RECEIPT_TTL_MS,
  type WorkflowAiDraftReceiptClaims, type WorkflowAiDraftReceiptInputClaims,
  type WorkflowAiDraftReceipts } from '../contracts/workflow-ai-draft.js';
import { aiDraftUuid, aiDraftValidText, assertAiDraftWorkflowId } from './workflow-ai-draft-validation.js';

const VERSION = 'workflow-ai-draft-receipt/v1';
const RECEIPT_DOMAIN = 'avernet/workflow-ai-draft/receipt/v1\0';
const ID_DOMAIN = 'avernet/workflow-ai-draft/job-id/v1\0';
const MAX_TOKEN_CHARS = 4096;
const inputKeys = ['actorId', 'workflowId', 'requestId', 'requestDigest', 'inputSpecDigest', 'target', 'issuedAt', 'expiresAt'];
function invalid(): never { throw new WorkflowAiDraftError('INVALID_INPUT'); }
function exactKeys(value: unknown, keys: readonly string[]): asserts value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) invalid();
  const actual = Object.keys(value);
  if (actual.length !== keys.length || actual.some(key => !keys.includes(key))) invalid();
}
const identity = (value: unknown, max: number): value is string => typeof value === 'string' && value.length > 0
  && value.length <= max && value.trim() === value && aiDraftValidText(value) && !/[\x00-\x1f\x7f]/.test(value);
function validateClaims(value: unknown): asserts value is WorkflowAiDraftReceiptClaims {
  exactKeys(value, ['schemaVersion', 'draftId', ...inputKeys]);
  if (value.schemaVersion !== VERSION || !aiDraftUuid(value.draftId) || !aiDraftUuid(value.requestId)
    || !identity(value.actorId, 128) || value.actorId === '*' || value.actorId === 'unknown'
    || typeof value.workflowId !== 'string') invalid();
  assertAiDraftWorkflowId(value.workflowId);
  for (const digest of [value.requestDigest, value.inputSpecDigest]) if (typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) invalid();
  exactKeys(value.target, ['botId']);
  if (!identity(value.target.botId, 512)) invalid();
  if (!Number.isSafeInteger(value.issuedAt) || !Number.isSafeInteger(value.expiresAt)
    || (value.issuedAt as number) < 0 || (value.expiresAt as number) <= (value.issuedAt as number)
    || (value.expiresAt as number) - (value.issuedAt as number) > WORKFLOW_AI_DRAFT_RECEIPT_TTL_MS) invalid();
}
/** One canonical encoding also rejects duplicate JSON fields, alternative base64
 * encodings and schema expansion. Only tokens issued by this codec are accepted. */
const canonical = (c: WorkflowAiDraftReceiptClaims): WorkflowAiDraftReceiptClaims => ({
  schemaVersion: VERSION, draftId: c.draftId, actorId: c.actorId, workflowId: c.workflowId,
  requestId: c.requestId.toLowerCase(), requestDigest: c.requestDigest, inputSpecDigest: c.inputSpecDigest,
  target: { botId: c.target.botId }, issuedAt: c.issuedAt, expiresAt: c.expiresAt,
});

/** No environment/config lookup, task storage, logging or exported key material. */
export function createWorkflowAiDraftReceipts(secret: string): WorkflowAiDraftReceipts {
  if (typeof secret !== 'string' || !secret.trim() || !aiDraftValidText(secret)) {
    throw new WorkflowAiDraftError('CAPABILITY_UNAVAILABLE', 'AI_DRAFT_RECOVERY_UNAVAILABLE');
  }
  const key = createSecretKey(Buffer.from(secret, 'utf8'));
  const mac = (domain: string, value: string) => createHmac('sha256', key).update(domain, 'utf8').update(value, 'utf8').digest();
  const draftId = (c: WorkflowAiDraftReceiptInputClaims) => {
    const bytes = mac(ID_DOMAIN, JSON.stringify([c.actorId, c.workflowId, c.requestId.toLowerCase(), c.requestDigest, c.target.botId])).subarray(0, 16);
    // RFC 9562 UUIDv8: deterministic opaque HMAC identity, not a timestamp UUID.
    bytes[6] = (bytes[6] & 0x0f) | 0x80; bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = bytes.toString('hex');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };
  return {
    issue(input) {
      try {
        exactKeys(input, inputKeys);
        // Validate before deriving any identity, including nested target fields.
        const candidate = { schemaVersion: VERSION, draftId: '00000000-0000-8000-8000-000000000000', ...input };
        validateClaims(candidate);
        const claims = canonical({ ...candidate, draftId: draftId(input) });
        const payload = Buffer.from(JSON.stringify(claims), 'utf8').toString('base64url');
        const receipt = `${payload}.${mac(RECEIPT_DOMAIN, payload).toString('base64url')}`;
        if (receipt.length > MAX_TOKEN_CHARS) invalid();
        return { draftId: claims.draftId, receipt, inputSpecDigest: claims.inputSpecDigest };
      } catch { return invalid(); }
    },
    verify(token) {
      try {
        if (typeof token !== 'string' || token.length > MAX_TOKEN_CHARS) invalid();
        const parts = /^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{43})$/.exec(token);
        if (!parts) invalid();
        const [, encoded, signature] = parts;
        const suppliedMac = Buffer.from(signature, 'base64url');
        if (suppliedMac.length !== 32 || suppliedMac.toString('base64url') !== signature
          || !timingSafeEqual(mac(RECEIPT_DOMAIN, encoded), suppliedMac)) invalid();
        const payload = Buffer.from(encoded, 'base64url');
        if (payload.toString('base64url') !== encoded) invalid();
        const text = payload.toString('utf8'), claims: unknown = JSON.parse(text);
        validateClaims(claims);
        const result = canonical(claims);
        if (JSON.stringify(result) !== text || result.draftId !== draftId(result)) invalid();
        return result;
      } catch { return invalid(); }
    },
  };
}
