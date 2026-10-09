import { Router, type Request, type Response } from 'express';
import type { RepairInboxFilter, RepairWorkbenchService } from '../contracts/repair-workbench.js';
import { RepairBatchError, boundedRepairJson, MAX_REQUEST_BYTES } from '../contracts/repair-batch.js';
import { finishRepairDiagnostic, recordRepairAccess, repairRequestDiagnostic, repairStage } from '../observability/repair-diagnostics.js';

export type RepairAuthorize = (request: Request, workflowId: string, mode: 'view' | 'edit') => Promise<{ actorId: string; canEdit: boolean } | null>;
function text(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
  return value;
}
function revision(value: unknown): number {
  const parsed = typeof value === 'string' && /^[1-9]\d*$/.test(value) ? Number(value) : value;
  if (!Number.isSafeInteger(parsed) || Number(parsed) < 1) throw new RepairBatchError('INVALID_INPUT', 'Invalid revision');
  return Number(parsed);
}
function positive(value: unknown, field: string, fallback: number, max = Number.MAX_SAFE_INTEGER): number {
  if (value === undefined) return fallback;
  if (typeof value !== 'string' || !/^[1-9]\d*$/.test(value)) throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed > max) throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
  return parsed;
}
function boolean(value: unknown, field: string, fallback = false): boolean {
  if (value === undefined) return fallback;
  if (value === 'true') return true;
  if (value === 'false') return false;
  throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
}

/** Transport adapter. Authorization is injected by a verified-login composition root.
 * No unsigned callback or publication route is exposed here.
 */
export function createRepairBatchesRouter(input: { service: RepairWorkbenchService; authorize: RepairAuthorize }): Router {
  const router = Router();
  const { service, authorize } = input;
  router.use((request, response, next) => repairRequestDiagnostic(context => {
    response.setHeader('X-Repair-Request-Id', context.requestId);
    response.once('finish', () => finishRepairDiagnostic(context, request.method, request.route?.path ?? 'unmatched', response.statusCode));
    next();
  }));
  const handle = (fn: (request: Request, response: Response) => Promise<void>) => async (request: Request, response: Response) => {
    try {
      if (request.method !== 'GET') boundedRepairJson(request.body, MAX_REQUEST_BYTES);
      await fn(request, response);
    } catch (error) {
      if (error instanceof RepairBatchError) {
        const status = error.code === 'INVALID_INPUT' ? 400 : error.code === 'PAYLOAD_TOO_LARGE' ? 413
          : error.code === 'CAPABILITY_UNAVAILABLE' ? 503
          : ['WORKFLOW_NOT_FOUND', 'TASK_NOT_FOUND', 'REVISION_NOT_FOUND', 'ITEM_NOT_FOUND'].includes(error.code) ? 404 : 409;
        response.status(status).json({ error: error.message, code: error.code, ...error.details });
      } else {
        console.error('[workflow-repair] request failed', {
          method: request.method,
          path: request.path,
          requestId: response.getHeader('X-Repair-Request-Id'),
          error,
        });
        response.status(500).json({ error: 'Repair request failed', code: 'INTERNAL_ERROR', requestId: response.getHeader('X-Repair-Request-Id') });
      }
    }
  };
  async function access(request: Request, response: Response, workflowId: string, mode: 'view' | 'edit') {
    const actor = await repairStage('authorization', () => authorize(request, workflowId, mode));
    recordRepairAccess({ workflowId, mode, actorId: actor?.actorId ?? null,
      reason: actor && (mode === 'view' || actor.canEdit) ? 'ALLOWED' : 'HOST_DENIED' }, true);
    if (!actor || (mode === 'edit' && !actor.canEdit)) {
      response.status(403).json({ error: 'Workflow access denied', code: 'FORBIDDEN', requestId: response.getHeader('X-Repair-Request-Id') });
      return null;
    }
    return actor;
  }
  async function taskAccess(request: Request, response: Response, mode: 'view' | 'edit') {
    const task = await service.task(text(request.params.taskId, 'taskId'));
    const actor = await access(request, response, task.workflowId, mode);
    return actor ? { task, actor } : null;
  }
  router.get('/candidates', handle(async (req, res) => {
    const workflowId = text(req.query.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'view');
    const state = req.query.state ?? 'all';
    if (typeof state !== 'string' || !['pending', 'processing', 'awaiting_verification', 'closed', 'no_action', 'all'].includes(state)) throw new RepairBatchError('INVALID_INPUT', 'Invalid state');
    if (actor) res.json({ ...await service.candidates(workflowId, { state: state as RepairInboxFilter,
      page: positive(req.query.page, 'page', 1), pageSize: positive(req.query.pageSize, 'pageSize', 20, 200),
      nodeId: req.query.nodeId === undefined ? undefined : text(req.query.nodeId, 'nodeId'),
      failureMode: req.query.failureMode === undefined ? undefined : text(req.query.failureMode, 'failureMode'),
      signature: req.query.signature === undefined ? undefined : text(req.query.signature, 'signature'),
      previewSignatures: req.query.previewSignature === undefined ? undefined
        : (Array.isArray(req.query.previewSignature) ? req.query.previewSignature : [req.query.previewSignature]).map(value => text(value, 'previewSignature')),
      includeHistorical: boolean(req.query.includeHistorical, 'includeHistorical') }), canEdit: actor.canEdit });
  }));
  router.get('/items/:itemId', handle(async (req, res) => {
    const workflowId = text(req.query.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'view');
    if (actor) res.json(await service.item(workflowId, text(req.params.itemId, 'itemId')));
  }));
  router.post('/', handle(async (req, res) => {
    const workflowId = text(req.body?.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'edit');
    if (actor) res.status(202).json(await service.create(actor.actorId, req.body));
  }));
  router.post('/items/:itemId/disposition', handle(async (req, res) => {
    const workflowId = text(req.body?.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'edit');
    if (actor) res.json(await service.disposition(actor.actorId, {
      workflowId, itemId: text(req.params.itemId, 'itemId'), inputDigest: req.body.inputDigest,
      expectedStateVersion: req.body.expectedStateVersion, contentRevision: req.body.contentRevision,
      action: req.body.action, reason: req.body.reason, requestId: req.body.requestId,
      ...(req.body.includeHistorical === undefined ? {} : { includeHistorical: req.body.includeHistorical }),
    }));
  }));
  router.get('/:taskId', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'view');
    if (selected) res.json(selected.task);
  }));
  router.get('/:taskId/revisions/:revision', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'view');
    if (!selected) return;
    const number = revision(req.params.revision);
    const result = selected.task.revisions.find(item => item.revision === number);
    if (!result) throw new RepairBatchError('REVISION_NOT_FOUND', 'Repair revision does not exist');
    res.json(result);
  }));
  router.post('/:taskId/revisions', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    if (req.body?.workflowId !== selected.task.workflowId) { res.status(403).json({ error: 'Task workflow does not match', code: 'FORBIDDEN' }); return; }
    res.status(202).json(await service.revise(selected.actor.actorId, selected.task.taskId, req.body));
  }));
  router.post('/:taskId/cancel', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    await service.cancel(selected.actor.actorId, selected.task.taskId, revision(req.body?.expectedRevision));
    res.json({ ok: true });
  }));
  router.post('/:taskId/retry-dispatch', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    await service.retryDispatch(selected.actor.actorId, selected.task.taskId, revision(req.body?.expectedRevision));
    res.status(202).json({ ok: true });
  }));
  router.get('/:taskId/revisions/:revision/diff', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'view');
    if (!selected) return;
    const base = req.query.base ?? 'baseline';
    if (base !== 'baseline' && base !== 'parent') throw new RepairBatchError('INVALID_INPUT', 'Invalid diff base');
    res.json(await service.diff(selected.task.taskId, revision(req.params.revision), base));
  }));
  return router;
}
