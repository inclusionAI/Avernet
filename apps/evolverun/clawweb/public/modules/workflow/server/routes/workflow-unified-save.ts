import { Router, raw, type Request, type RequestHandler } from 'express';
import { TextDecoder } from 'node:util';
import { performance } from 'node:perf_hooks';
import { assertLosslessJson } from '../services/lossless-json-validation.js';
import { validPackGitId } from '../services/pack-git-prepared.js';
import { fingerprintWorkflowSnapshotSaveRequest, type WorkflowSnapshotSaveRequest } from '../services/workflow-snapshot-save-request.js';
import type { WorkflowUnifiedSaveResult, WorkflowUnifiedSaveService } from '../services/workflow-unified-save-service.js';

/**
 * Experimental write endpoint for existing workflows. The public host denies by
 * default and enables it only with explicit trusted server-side dependencies.
 * The host MUST authenticate and enforce CSRF/origin policy before this router,
 * suppress request/response body and exception logging, and mount it BEFORE any
 * body parser, apply rate limits and request/authorization timeouts. The host must
 * independently authorize workflow edits AND bind an
 * appropriately authorized service, repository and Pack to the workflow.
 * The service's exact-candidate Pack policy and writer coordination remain host
 * responsibilities; this adapter does not make the prototype production-ready.
 * There is no journal, idempotency, retry, or cross-store atomicity.
 */
type Principal = Readonly<{ actorId: string }>;
type Binding = Readonly<{
  repositoryId: string;
  packId: string;
  service: Pick<WorkflowUnifiedSaveService, 'save'>;
}>;
export type WorkflowUnifiedSaveRouteDependencies = Readonly<{
  resolvePrincipal(req: Request): Promise<Principal | null>;
  /** Return null unless this principal may edit this exact workflow; never use a client grant. */
  resolveAuthorizedService(workflowId: string, principal: Principal): Promise<Binding | null>;
}>;

// A bounded wire limit, including JSON escaping/base64. This may reject a request
// even when individual service limits would permit its decoded contents.
const MAX_BODY = 8 * 1024 * 1024;
const parseBody = raw({ type: 'application/json', limit: MAX_BODY, inflate: false });
const bodyFields = ['requestId', 'sourceVersion', 'sourceCommit', 'expectedSavedHead', 'expectedDraftToken', 'specJson', 'changes'];
const actorPattern = /^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,254}$/;
const safeId = (id: string) => validPackGitId(id) && !/^__.+__$/.test(id);
const hex64 = /^[a-f0-9]{64}$/;
const gitOid = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const unknownResult = (requestId: string) => ({ requestId, outcome: 'unknown',
  evidence: { db: 'unknown', git: 'unknown' }, reconciliationRequired: true });

/** Wall-clock response deadline, not an admission limit. The host MUST supply
 * bounded in-flight authentication/work and per-principal or ingress rate
 * limits; never release an in-flight slot merely because an HTTP response ended.
 * Abort prevents subsequent side effects, not writes already issued. Those
 * remain unknown and must never be blindly retried.
 */
export function unifiedSaveHostGuard(): RequestHandler {
  return (_req, res, next) => {
    res.set('Cache-Control', 'no-store');
    const controller = new AbortController();
    res.locals.unifiedSaveControl = { signal: controller.signal, deadline: performance.now() + 30_000 };
    const timer = setTimeout(() => {
      controller.abort();
      if (res.headersSent || res.destroyed) return;
      const requestId: unknown = res.locals.unifiedSaveRequestId;
      if (typeof requestId === 'string') res.status(409).json(unknownResult(requestId));
      else res.status(503).json({ code: 'SERVICE_UNAVAILABLE' });
    }, 30_000);
    timer.unref();
    res.once('finish', () => clearTimeout(timer));
    res.once('close', () => { clearTimeout(timer); controller.abort(); });
    next();
  };
}

/** Evidence is confidential correlation information, not a current consistency
 * proof, authorization grant, durable receipt, or permission to retry. Only an
 * authorized caller receives the validated request ID and phase-gated evidence.
 * A lost process/HTTP receipt cannot be recovered by this unjournaled endpoint.
 */
function safeResult(result: WorkflowUnifiedSaveResult, requestId: string, inputHash: string): { status: number; body: object } {
  const db = result?.evidence?.db;
  const git = result?.evidence?.git;
  const outcome = result?.outcome;
  const evidence = result?.evidence;
  const phasesValid = (outcome === 'confirmed' && db === 'confirmed' && git === 'confirmed')
    || (outcome === 'rejected' && db === 'not_started' && git === 'not_started')
    || (outcome === 'partial' && db === 'confirmed' && git === 'not_started')
    || (outcome === 'unknown' && ((db === 'unknown' && git === 'not_started')
      || (db === 'confirmed' && git === 'unknown')));
  // Validate all supplied fields before using any. Match the hash against the
  // independently validated request, rather than trusting a well-formed hash.
  const hash = evidence?.inputHash;
  const commit = evidence?.commit;
  const draftToken = evidence?.draftToken;
  const fieldsValid = (hash === undefined || (typeof hash === 'string' && hex64.test(hash) && hash === inputHash))
    && (commit === undefined || (typeof commit === 'string' && gitOid.test(commit)))
    && (draftToken === undefined || (typeof draftToken === 'string' && hex64.test(draftToken)));
  // These outcomes require a candidate and DB acknowledgement for reconciliation.
  const completeEvidence = db !== 'confirmed'
    || (hash !== undefined && commit !== undefined && draftToken !== undefined);
  if (!phasesValid || !fieldsValid || !completeEvidence) return { status: 409, body: unknownResult(requestId) };
  return { status: outcome === 'confirmed' ? 200 : 409,
    body: { requestId, outcome, evidence: { db, git,
      ...(hash !== undefined ? { inputHash: hash } : {}),
      ...(db === 'confirmed' && commit !== undefined ? { commit } : {}),
      ...(db === 'confirmed' && draftToken !== undefined ? { draftToken } : {}),
    }, reconciliationRequired: outcome === 'partial' || outcome === 'unknown' } };
}

export function createWorkflowUnifiedSaveRouter(deps: WorkflowUnifiedSaveRouteDependencies): Router {
  const router = Router();
  const handler: RequestHandler = async (req, res) => {
    res.set('Cache-Control', 'no-store');
    if (req.method !== 'POST' && req.method !== 'GET') { res.status(405).json({ code: 'METHOD_NOT_ALLOWED' }); return; }
    let principal: Principal | null;
    try { principal = await deps.resolvePrincipal(req); } catch { if (!res.headersSent && !res.destroyed) res.status(503).json({ code: 'SERVICE_UNAVAILABLE' }); return; }
    if (res.headersSent || res.destroyed) return;
    if (!principal || typeof principal.actorId !== 'string' || !actorPattern.test(principal.actorId) || principal.actorId === 'unknown') {
      res.status(401).json({ code: 'AUTHENTICATION_REQUIRED' }); return;
    }
    const workflowId = req.params.workflowId;
    if (typeof workflowId !== 'string' || !safeId(workflowId) || Object.keys(req.query).length) { res.status(400).json({ code: 'INVALID_REQUEST' }); return; }
    let binding: Binding | null;
    try { binding = await deps.resolveAuthorizedService(workflowId, principal); }
    catch { if (!res.headersSent && !res.destroyed) res.status(503).json({ code: 'SERVICE_UNAVAILABLE' }); return; }
    if (res.headersSent || res.destroyed) return;
    if (!binding) { res.status(404).json({ code: 'NOT_FOUND' }); return; }
    if (!safeId(binding.packId) || typeof binding.repositoryId !== 'string' || !/^[a-f0-9]{64}$/.test(binding.repositoryId)
      || !binding.service || typeof binding.service.save !== 'function') {
      res.status(503).json({ code: 'SERVICE_UNAVAILABLE' }); return;
    }
    // Read-only protocol discovery is not a write permission grant. POST must
    // authenticate and resolve the workflow/Pack binding again.
    if (req.method === 'GET') {
      const control = res.locals.unifiedSaveControl;
      if (control && (control.signal.aborted || performance.now() >= control.deadline)) {
        res.status(503).json({ code: 'SERVICE_UNAVAILABLE' }); return;
      }
      res.json({ protocol: 'workflow-unified-save-v1' }); return;
    }
    if (!req.is('application/json') || (req.headers['content-encoding'] && req.headers['content-encoding'] !== 'identity')) {
      res.status(415).json({ code: 'UNSUPPORTED_MEDIA_TYPE' }); return;
    }
    // A previous parser may have discarded duplicate keys or bypassed our limit.
    if (req.readableEnded || req.body !== undefined) { res.status(400).json({ code: 'INVALID_REQUEST' }); return; }
    parseBody(req, res, async error => {
      if (res.headersSent || res.destroyed) return;
      if (error) {
        res.status((error as { type?: string }).type === 'entity.too.large' ? 413 : 400).json({ code: 'INVALID_REQUEST' }); return;
      }
      let body: Record<string, unknown>;
      try {
        if (!Buffer.isBuffer(req.body)) throw new Error();
        const text = new TextDecoder('utf-8', { fatal: true }).decode(req.body);
        assertLosslessJson(text);
        const value: unknown = JSON.parse(text);
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
        body = value as Record<string, unknown>;
        if (Object.keys(body).length !== bodyFields.length || Object.keys(body).some(key => !bodyFields.includes(key))) throw new Error();
      } catch { res.status(400).json({ code: 'INVALID_REQUEST' }); return; }
      let input: WorkflowSnapshotSaveRequest;
      let inputHash: string;
      try {
        if (!safeId(binding.packId) || typeof binding.repositoryId !== 'string' || !/^[a-f0-9]{64}$/.test(binding.repositoryId)) throw new Error();
        const snapshot = fingerprintWorkflowSnapshotSaveRequest({ ...body, workflowId, actorId: principal.actorId,
          packId: binding.packId, repositoryId: binding.repositoryId } as WorkflowSnapshotSaveRequest);
        input = snapshot.request; inputHash = snapshot.inputHash;
      } catch { res.status(400).json({ code: 'INVALID_REQUEST' }); return; }
      try {
        const control = res.locals.unifiedSaveControl;
        if (control && (control.signal.aborted || performance.now() >= control.deadline)) {
          res.status(503).json({ code: 'SERVICE_UNAVAILABLE' }); return;
        }
        res.locals.unifiedSaveRequestId = input.requestId;
        const response = safeResult(await binding.service.save(input, principal.actorId, control), input.requestId, inputHash);
        if (!res.headersSent && !res.destroyed) res.status(response.status).json(response.body);
      } catch {
        // Once called, a thrown exception cannot prove that either write did not happen.
        if (!res.headersSent && !res.destroyed) res.status(409).json(unknownResult(input.requestId));
      }
    });
  };
  router.all('/:workflowId/unified-save', handler);
  return router;
}
