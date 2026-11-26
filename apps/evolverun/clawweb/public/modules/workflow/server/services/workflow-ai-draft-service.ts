import { WorkflowAiDraftDispatchRejectedError, WorkflowAiDraftError, WorkflowAiDraftInvalidResultError, WorkflowAiDraftPollUnavailableError,
  WORKFLOW_AI_DRAFT_RECEIPT_TTL_MS, type WorkflowAiDraftDetail, type WorkflowAiDraftEnvelope,
  type WorkflowAiDraftExecutionPort, type WorkflowAiDraftReceipts, type WorkflowAiDraftReceiptClaims,
  type WorkflowAiDraftRequest, type WorkflowAiDraftService, type WorkflowAiDraftTarget,
  type WorkflowAiDraftPollResult } from '../contracts/workflow-ai-draft.js';
import { aiDraftSha256, aiDraftUuid, aiDraftValidText, aiDraftWorkflow, assertAiDraftWorkflowId,
  validateAiDraftRequest, validateAiDraftOutput } from './workflow-ai-draft-validation.js';

const safeProviderErrors = new Set(['AUTO_HEAL_BOT_UNAVAILABLE', 'BAAS_AUTH_FAILED', 'BAAS_REQUEST_REJECTED',
  'BAAS_GENERATION_FAILED', 'BAAS_PROVIDER_REJECTED', 'BAAS_EXECUTION_FAILED', 'GENERATION_FAILED', 'INVALID_GENERATION_RESULT',
  'BAAS_GENERATION_TIMEOUT', 'BAAS_GENERATION_CANCELLED', 'BAAS_SESSION_UNAVAILABLE', 'BAAS_RESULT_PERSISTENCE_FAILED']);
const operationalProviderErrors = new Set(['BAAS_AUTH_FAILED', 'BAAS_POLL_UNAVAILABLE']);
const MAX_CLOCK_SKEW_MS = 5_000;
const requestDigest = (request: WorkflowAiDraftRequest) => aiDraftSha256(JSON.stringify([request.mode, request.prompt, request.currentYaml]));
/** Stateless library service. Receipts bind metadata to the original exact
 * input; BaaS native message_id owns idempotency. No DB, Map, timers, fences or
 * durable cancellation. Only explicit create dispatches; recovery only polls. */
export function createWorkflowAiDraftService(input: {
  canEdit(workflowId: string, actorId: string): Promise<boolean>;
  execution?: WorkflowAiDraftExecutionPort; receipts?: WorkflowAiDraftReceipts;
  unavailableReason?: string; now?: () => number;
}): WorkflowAiDraftService {
  const now = input.now ?? Date.now, port = input.execution, receipts = input.receipts;
  async function access(actorId: string, workflowId: string) {
    assertAiDraftWorkflowId(workflowId);
    if (typeof actorId !== 'string' || !actorId.trim() || actorId.trim() !== actorId || actorId.length > 128
      || !aiDraftValidText(actorId) || actorId === '*' || actorId === 'unknown' || /[\x00-\x1f\x7f]/.test(actorId)
      || !await input.canEdit(workflowId, actorId)) throw new WorkflowAiDraftError('FORBIDDEN');
  }
  function requireReceipts() {
    if (!receipts) throw new WorkflowAiDraftError('CAPABILITY_UNAVAILABLE', 'AI_DRAFT_RECOVERY_UNAVAILABLE');
    return receipts;
  }
  function requireProvider() {
    if (!port) throw new WorkflowAiDraftError('CAPABILITY_UNAVAILABLE');
    return port;
  }
  async function resolveTarget(): Promise<WorkflowAiDraftTarget> {
    const provider = requireProvider();
    try {
      const target = await provider.resolveTarget();
      if (typeof target?.botId !== 'string' || !target.botId.trim() || target.botId.trim() !== target.botId
        || target.botId.length > 512 || !aiDraftValidText(target.botId) || /[\x00-\x1f\x7f]/.test(target.botId)) throw new Error('invalid');
      return { botId: target.botId };
    } catch { throw new WorkflowAiDraftError('CAPABILITY_UNAVAILABLE', 'AUTO_HEAL_BOT_UNAVAILABLE'); }
  }
  function verify(actorId: string, workflowId: string, token: string, draftId?: string) {
    const codec = requireReceipts();
    if (typeof token !== 'string' || !token || token.length > 4096) throw new WorkflowAiDraftError('INVALID_INPUT');
    let claims: WorkflowAiDraftReceiptClaims;
    try { claims = codec.verify(token); } catch { throw new WorkflowAiDraftError('INVALID_INPUT'); }
    if (claims.actorId !== actorId || claims.workflowId !== workflowId || (draftId !== undefined && claims.draftId !== draftId)) {
      throw new WorkflowAiDraftError('NOT_FOUND');
    }
    // Wall-clock policy belongs to the service, not a codec with hidden time.
    if (claims.issuedAt > now() + MAX_CLOCK_SKEW_MS) throw new WorkflowAiDraftError('INVALID_INPUT');
    return claims;
  }
  const expired = (c: WorkflowAiDraftReceiptClaims) => now() >= c.expiresAt;
  const detail = (c: WorkflowAiDraftReceiptClaims, status: WorkflowAiDraftDetail['status'], error?: string): WorkflowAiDraftDetail => ({
    draftId: c.draftId, workflowId: c.workflowId, status, inputSpecDigest: c.inputSpecDigest, ...(error ? { error } : {}),
  });
  const timedOut = (c: WorkflowAiDraftReceiptClaims) => detail(c, 'failed', 'EXECUTION_TIMEOUT');
  const pollEnvelope = (c: WorkflowAiDraftReceiptClaims) => ({ schemaVersion: 'workflow-yaml-draft/v1' as const,
    taskType: 'workflow_yaml_draft' as const, taskId: c.draftId, target: { botId: c.target.botId } });
  return {
    async capabilities(actorId, workflowId) {
      await access(actorId, workflowId);
      if (!receipts) return { available: false, reason: 'AI_DRAFT_RECOVERY_UNAVAILABLE' };
      if (!port) return { available: false, reason: input.unavailableReason ?? 'AI_DRAFT_PROVIDER_UNAVAILABLE' };
      try { await resolveTarget(); await access(actorId, workflowId); return { available: true }; }
      catch (error) {
        if (error instanceof WorkflowAiDraftError && error.code === 'FORBIDDEN') throw error;
        return { available: false, reason: 'AUTO_HEAL_BOT_UNAVAILABLE' };
      }
    },
    async prepare(actorId, workflowId, request) {
      await access(actorId, workflowId);
      validateAiDraftRequest(workflowId, request);
      if (request.receipt !== undefined) throw new WorkflowAiDraftError('INVALID_INPUT');
      const codec = requireReceipts(), target = await resolveTarget();
      await access(actorId, workflowId);
      const issuedAt = now();
      return codec.issue({ actorId, workflowId, requestId: request.requestId.toLowerCase(), requestDigest: requestDigest(request),
        inputSpecDigest: aiDraftSha256(request.currentYaml), target, issuedAt, expiresAt: issuedAt + WORKFLOW_AI_DRAFT_RECEIPT_TTL_MS });
    },
    async create(actorId, workflowId, request) {
      await access(actorId, workflowId);
      // A legacy POST without a receipt must fail before provider resolution or dispatch.
      const claims = verify(actorId, workflowId, request?.receipt);
      validateAiDraftRequest(workflowId, request);
      if (claims.requestId !== request.requestId.toLowerCase() || claims.requestDigest !== requestDigest(request)
        || claims.inputSpecDigest !== aiDraftSha256(request.currentYaml)) throw new WorkflowAiDraftError('IDEMPOTENCY_CONFLICT');
      if (expired(claims)) return { draftId: claims.draftId, status: 'failed', error: 'EXECUTION_TIMEOUT' };
      const provider = requireProvider();
      await access(actorId, workflowId);
      if (expired(claims)) return { draftId: claims.draftId, status: 'failed', error: 'EXECUTION_TIMEOUT' };
      const envelope: WorkflowAiDraftEnvelope = { ...pollEnvelope(claims), input: {
        schemaVersion: 'workflow-yaml-draft-input/v1', workflowId, mode: request.mode, prompt: request.prompt,
        workingCopy: { yaml: request.currentYaml, sha256: claims.inputSpecDigest },
      } };
      let status: 'running' | 'dispatch_uncertain' | 'failed' = 'dispatch_uncertain', error: string | undefined;
      try {
        const acknowledgement = await provider.dispatch({ actorId, envelope });
        if (acknowledgement?.jobId === claims.draftId) status = 'running';
      } catch (failure) {
        if (failure instanceof WorkflowAiDraftDispatchRejectedError) {
          status = 'failed'; error = safeProviderErrors.has(failure.code) ? failure.code : 'BAAS_REQUEST_REJECTED';
        }
        // Unknown/lost ack is not rejection. Expected native ID remains pollable.
      }
      await access(actorId, workflowId);
      if (expired(claims)) return { draftId: claims.draftId, status: 'failed', error: 'EXECUTION_TIMEOUT' };
      return { draftId: claims.draftId, status, ...(error ? { error } : {}) };
    },
    async get(actorId, workflowId, draftId, context) {
      await access(actorId, workflowId);
      if (!aiDraftUuid(draftId)) throw new WorkflowAiDraftError('INVALID_INPUT');
      const claims = verify(actorId, workflowId, context?.receipt, draftId);
      aiDraftWorkflow(context?.currentYaml, workflowId);
      if (aiDraftSha256(context.currentYaml) !== claims.inputSpecDigest) throw new WorkflowAiDraftError('IDEMPOTENCY_CONFLICT');
      if (expired(claims)) return timedOut(claims);
      const provider = requireProvider();
      await access(actorId, workflowId);
      if (expired(claims)) return timedOut(claims);
      let generated: WorkflowAiDraftPollResult;
      try { generated = await provider.poll({ actorId, envelope: pollEnvelope(claims), jobId: claims.draftId }); }
      catch (failure) {
        generated = failure instanceof WorkflowAiDraftInvalidResultError ? { status: 'failed', error: 'INVALID_GENERATION_RESULT' }
          : failure instanceof WorkflowAiDraftPollUnavailableError
            ? { status: 'running', error: operationalProviderErrors.has(failure.code) ? failure.code : 'BAAS_POLL_UNAVAILABLE' }
            : { status: 'running' };
      }
      // Even failed/invalid/404 replies must not bypass live ACL/deadline checks.
      await access(actorId, workflowId);
      if (expired(claims)) return timedOut(claims);
      if (generated?.status === 'running') return detail(claims, 'running', generated.error
        ? (operationalProviderErrors.has(generated.error) ? generated.error : 'BAAS_POLL_UNAVAILABLE') : undefined);
      if (generated?.status === 'failed') return detail(claims, 'failed', safeProviderErrors.has(generated.error) ? generated.error : 'GENERATION_FAILED');
      if (generated?.status !== 'completed' || !generated.result) return detail(claims, 'failed', 'INVALID_GENERATION_RESULT');
      const result = generated.result;
      try {
        validateAiDraftOutput(result.yaml, context.currentYaml, workflowId);
        if (typeof result.summary !== 'string' || !aiDraftValidText(result.summary) || result.summary.includes('\0')
          || result.summary.length > 4000) throw new Error('invalid');
      } catch { return detail(claims, 'failed', 'INVALID_GENERATION_RESULT'); }
      return { ...detail(claims, 'completed'), result: { yaml: result.yaml, summary: result.summary } };
    },
    async cancel(actorId, workflowId, draftId, context) {
      await access(actorId, workflowId);
      if (!aiDraftUuid(draftId)) throw new WorkflowAiDraftError('INVALID_INPUT');
      const claims = verify(actorId, workflowId, context?.receipt, draftId);
      await access(actorId, workflowId);
      return expired(claims) ? timedOut(claims) : detail(claims, 'cancelled');
    },
    async reconcile() { return 0; },
  };
}
