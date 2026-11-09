import { Router, type Request, type Response } from 'express';
import { logWorkflowBackground } from '@avernet/clawweb-shared/server/services/workflow-background-log';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError } from '../services/pack-candidate-save.js';
import type { PackScriptSnapshot, PackScriptVersion } from '../services/pack-script-snapshot.js';

export type PackVersionChange = { path: string; operation: 'delete' } | { path: string; operation: 'create' | 'replace'; contentBase64: string; mode: '100644' | '100755' };
type Dependencies = {
  resolvePrincipal(req: Request): Promise<{ actorId: string } | null>;
  botPermRepo: { hasEditPermission(workflowId: string, actorId: string): Promise<boolean>; hasExecutePermission?(workflowId: string, actorId: string): Promise<boolean> } | null;
  workflowSpecRepo: { findByWorkflowId(workflowId: string): Promise<{ workflow_id: string; pack_id: string | null } | null> } | null;
  readActiveSnapshot?(workflowId: string, version: PackScriptVersion): Promise<PackScriptSnapshot>;
  readSnapshot(workflowId: string, packId: string, version: PackScriptVersion): Promise<PackScriptSnapshot>;
  saveCandidate?(input: { actorId: string; requestId?: string; workflowId: string; sourceVersion: PackScriptVersion; sourceCommit: string; expectedSavedHead: string | null; changes: PackVersionChange[] }): Promise<{ branch: string; commit: string; requestId?: string }>;
  readOperation?(requestId: string, workflowId: string, actorId: string): Promise<unknown>;
  reconcileOperation?(requestId: string, workflowId: string, actorId: string): Promise<unknown>;
};
const validRequestId = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/.test(value);
function operationError(res: Response, error: unknown): boolean {
  const e = error as { status?: number; code?: string; requestId?: string; branch?: string; commit?: string } | null;
  if (!e || ![400, 403, 404, 409, 503].includes(e.status ?? 0) || !e.code) return false;
  res.status(e.status!).json({ error: 'Workflow operation could not be completed', code: e.code,
    ...(e.requestId ? { requestId: e.requestId } : {}), ...(e.branch ? { branch: e.branch, commit: e.commit } : {}) });
  return true;
}
const validId = (v: unknown): v is string => typeof v === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(v) && !v.includes('..') && !v.endsWith('.') && !v.endsWith('.lock');
const oid = (v: unknown): v is string => typeof v === 'string' && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(v);
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).every(k => keys.includes(k));
function validChanges(v: unknown): v is PackVersionChange[] {
  if (!Array.isArray(v) || v.length > 500) return false;
  let bytes = 0; const seen = new Set<string>();
  return v.every(c => {
    if (!object(c) || typeof c.path !== 'string' || !c.path || Buffer.byteLength(c.path) > 4096 || /[\\\u0000-\u001f\u007f]/.test(c.path)
      || /^[A-Za-z]:/.test(c.path) || c.path.split('/').some((p: string) => !p || p === '.' || p === '..' || p.toLowerCase() === '.git') || seen.has(c.path)) return false;
    seen.add(c.path);
    if (c.operation === 'delete') return exact(c, ['path', 'operation']);
    if ((c.operation !== 'create' && c.operation !== 'replace') || !exact(c, ['path', 'operation', 'contentBase64', 'mode']) || (c.mode !== '100644' && c.mode !== '100755')
      || typeof c.contentBase64 !== 'string' || c.contentBase64.length > Math.ceil(8 * 1024 * 1024 / 3) * 4
      || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(c.contentBase64)) return false;
    const data = Buffer.from(c.contentBase64, 'base64'); bytes += data.length;
    return data.toString('base64') === c.contentBase64 && data.length <= 8 * 1024 * 1024 && bytes <= 16 * 1024 * 1024;
  });
}
/** Mount only behind a trusted host's authentication, strict CORS and CSRF middleware. */
export function createPackVersionRouter(deps: Dependencies): Router {
  const router = Router();
  router.all('/:workflowId/pack-version', async (req, res) => {
    res.set('Cache-Control', 'no-store');
    if (req.method !== 'GET' && req.method !== 'PUT') { res.status(405).json({ error: 'Method not allowed', code: 'METHOD_NOT_ALLOWED' }); return; }
    let phase = 'identity';
    try {
      const principal = await deps.resolvePrincipal(req);
      if (!principal || typeof principal.actorId !== 'string' || !principal.actorId.trim()) { res.status(401).json({ error: 'Authentication required', code: 'AUTHENTICATION_REQUIRED' }); return; }
      const workflowId = req.params.workflowId;
      if (!validId(workflowId)) { res.status(400).json({ error: 'Invalid request', code: 'INVALID_REQUEST' }); return; }
      phase = 'permission';
      if (!deps.botPermRepo || await deps.botPermRepo.hasEditPermission(workflowId, principal.actorId) !== true) { res.status(403).json({ error: 'Forbidden', code: 'FORBIDDEN' }); return; }
      if (!deps.workflowSpecRepo) { res.status(503).json({ error: 'Service unavailable', code: 'SERVICE_UNAVAILABLE' }); return; }
      phase = 'workflow';
      const row = await deps.workflowSpecRepo.findByWorkflowId(workflowId);
      if (!row && (req.method !== 'GET' || !deps.readActiveSnapshot)) { res.status(404).json({ error: 'Workflow not found', code: 'WORKFLOW_NOT_FOUND' }); return; }
      const packId = row ? row.pack_id ?? row.workflow_id : undefined;
      if (row && (row.workflow_id !== workflowId || !validId(packId))) { res.status(503).json({ error: 'Service unavailable', code: 'SERVICE_UNAVAILABLE' }); return; }
      if (req.method === 'GET') {
        const version = req.query.version;
        if ((version !== 'current' && version !== 'draft') || Object.keys(req.query).some(k => k !== 'version')) { res.status(400).json({ error: 'Invalid request', code: 'INVALID_REQUEST' }); return; }
        phase = 'snapshot';
        const snapshot = row ? await deps.readSnapshot(workflowId, packId!, version) : await deps.readActiveSnapshot!(workflowId, version);
        if (!snapshot || snapshot.workflowId !== workflowId || !validId(snapshot.packId) || !validSnapshotRef(snapshot.baseRef, workflowId, version)
          || !oid(snapshot.baseCommit) || !(snapshot.draftHead === null || oid(snapshot.draftHead)) || !Array.isArray(snapshot.versionFiles)) throw new Error('Invalid snapshot');
        // Never reflect active-history evidence from the ordinary draft-backed
        // reader, even if a host accidentally returns an identically named field.
        const { activeHistory, activeHistoryMismatch, ...response } = snapshot as PackScriptSnapshot & { activeHistory?: unknown };
        const evidence = !row && version === 'current' && object(activeHistory)
          && Object.keys(activeHistory).length === 2 && activeHistory.protocol === 'active-history-v1'
          && typeof activeHistory.activeToken === 'string' && /^[a-f0-9]{64}$/.test(activeHistory.activeToken)
          ? { activeHistory } : {};
        const warning = version === 'current' && activeHistoryMismatch === true ? { activeHistoryMismatch: true } : {};
        res.json({ ...response, ...evidence, ...warning, canWrite: !!deps.saveCandidate }); return;
      }
      if (!deps.saveCandidate) { res.status(503).json({ error: 'Version saving unavailable', code: 'SERVICE_UNAVAILABLE' }); return; }
      if (Object.keys(req.query).length) { res.status(400).json({ error: 'Invalid request', code: 'INVALID_REQUEST' }); return; }
      const b: unknown = req.body;
      if (!object(b) || !exact(b, ['sourceVersion', 'sourceCommit', 'expectedSavedHead', 'changes', 'requestId']) || (b.sourceVersion !== 'current' && b.sourceVersion !== 'draft')
        || !oid(b.sourceCommit) || !validChanges(b.changes) || (b.requestId !== undefined && !validRequestId(b.requestId))) {
        res.status(400).json({ error: 'Invalid request', code: 'INVALID_REQUEST' }); return;
      }
      const expectedSavedHead = b.expectedSavedHead;
      if (expectedSavedHead !== null && !oid(expectedSavedHead)) { res.status(400).json({ error: 'Invalid request', code: 'INVALID_REQUEST' }); return; }
      const savedHead: string | null = typeof expectedSavedHead === 'string' ? expectedSavedHead : null;
      res.json(await deps.saveCandidate({ actorId: principal.actorId, ...(typeof b.requestId === 'string' ? { requestId: b.requestId } : {}), workflowId, sourceVersion: b.sourceVersion, sourceCommit: b.sourceCommit, expectedSavedHead: savedHead, changes: b.changes }));
    } catch (error) {
      if (error instanceof PackCandidateOutcomeUnknownError) { res.status(503).json({ error: 'Save outcome unknown; inspect the Git branch manually before retrying', code: 'SAVE_OUTCOME_UNKNOWN', branch: error.branch, commit: error.commit }); return; }
      if (error instanceof PackCandidateConflictError) { res.status(409).json({ error: 'Version conflict', code: 'VERSION_CONFLICT' }); return; }
      if (operationError(res, error)) return;
      const message = error instanceof Error ? error.message : '';
      if (message === 'Workflow snapshot not found' || message === 'Pack manifest not found') {
        res.status(404).json({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND' }); return;
      }
      if (req.method === 'GET') {
        // Log only fixed categories. Never serialize provider errors, paths, tokens or request data.
        const conflict = phase === 'snapshot' && (message === 'Pack manifest conflict' || message === 'Mixed Pack layout');
        const inputCode = (error as { code?: unknown } | null)?.code;
        const errorCode = conflict ? 'PACK_MANIFEST_CONFLICT' :
          inputCode === 'GIT_QUEUE_FULL' || inputCode === 'GIT_OPERATION_TIMEOUT' || inputCode === 'GIT_TIMEOUT' || inputCode === 'GIT_OUTPUT_LIMIT' || inputCode === 'PACK_REFS_CHANGED'
            ? inputCode : 'PACK_READ_FAILED';
        logWorkflowBackground('pack_version_read', 'failed', { phase, errorCode,
          ...(validId(req.params.workflowId) ? { workflowId: req.params.workflowId } : {}) });
        if (conflict) { res.status(409).json({ error: 'Version snapshot conflict', code: 'SNAPSHOT_CONFLICT' }); return; }
      }
      res.status(503).json({ error: 'Service unavailable', code: 'SERVICE_UNAVAILABLE' });
    }
  });
  router.all('/:workflowId/pack-operations/:requestId{/:action}', async (req, res) => {
    res.set('Cache-Control', 'no-store');
    const read = req.method === 'GET' && !req.params.action;
    const reconcile = req.method === 'POST' && req.params.action === 'reconcile';
    if (!read && !reconcile) { res.status(405).json({ code: 'METHOD_NOT_ALLOWED' }); return; }
    try {
      const principal = await deps.resolvePrincipal(req);
      if (!principal?.actorId?.trim()) { res.status(401).json({ code: 'AUTHENTICATION_REQUIRED' }); return; }
      const { workflowId, requestId } = req.params;
      if (!validId(workflowId) || !validRequestId(requestId)) { res.status(400).json({ code: 'INVALID_REQUEST' }); return; }
      // The scoped reader verifies the original actor and workflow before
      // exposing its kind. Deploy-only users must be able to recover their own
      // release, without granting them file-read or file-write permission.
      if (!deps.readOperation) { res.status(503).json({ code: 'SERVICE_UNAVAILABLE' }); return; }
      const operation = await deps.readOperation(requestId, workflowId, principal.actorId) as { kind?: string };
      const edit = await deps.botPermRepo?.hasEditPermission(workflowId, principal.actorId);
      const execute = operation.kind === 'deploy' && await deps.botPermRepo?.hasExecutePermission?.(workflowId, principal.actorId);
      if (!edit && !execute) { res.status(403).json({ code: 'FORBIDDEN' }); return; }
      if (read) { res.json(operation); return; }
      if (!deps.reconcileOperation) { res.status(503).json({ code: 'SERVICE_UNAVAILABLE' }); return; }
      res.json(await deps.reconcileOperation(requestId, workflowId, principal.actorId));
    } catch (error) {
      if (!operationError(res, error)) res.status(503).json({ code: 'SERVICE_UNAVAILABLE' });
    }
  });
  return router;
}

function validSnapshotRef(ref: string, id: string, version: 'current' | 'draft'): boolean {
  if (version === 'draft') return ref === `origin/wf/${id}`;
  const prefix = `refs/tags/deploy/${id}/#`;
  return typeof ref === 'string' && ref.startsWith(prefix) && /^[1-9][0-9]*$/.test(ref.slice(prefix.length));
}
