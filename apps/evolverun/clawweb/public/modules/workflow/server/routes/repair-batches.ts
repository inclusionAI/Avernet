import { projectTaskHistory } from '../services/repair-task-history.js';
import type { createRepairTrialService } from '../services/repair-trial-service.js';
import { Router, type Request, type Response } from 'express';
import type { RepairInboxFilter, RepairWorkbenchService } from '../contracts/repair-workbench.js';
import type { RepairReleaseService } from '../contracts/repair-release.js';
import type { createRepairVerificationService } from '../services/repair-verification-service.js';
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
  if (typeof value !== 'number' && (typeof value !== 'string' || !/^[1-9]\d*$/.test(value))) throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1 || parsed > max) throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
  return parsed;
}
function boolean(value: unknown, field: string, fallback = false): boolean {
  if (value === undefined) return fallback;
  if (value === 'true' || value === true) return true;
  if (value === 'false' || value === false) return false;
  throw new RepairBatchError('INVALID_INPUT', `Invalid ${field}`);
}

/** Transport adapter. Authorization is injected by a verified-login composition root.
 * No unsigned callback or publication route is exposed here.
 */
export function createRepairBatchesRouter(input: { service: RepairWorkbenchService; authorize: RepairAuthorize; release?: RepairReleaseService;
  trials?: ReturnType<typeof createRepairTrialService>; verification?: ReturnType<typeof createRepairVerificationService> }): Router {
  const router = Router();
  const { service, authorize } = input;
  router.use((request, response, next) => repairRequestDiagnostic(context => {
    response.setHeader('X-Repair-Request-Id', context.requestId);
    response.once('finish', () => finishRepairDiagnostic(context, request.method, request.route?.path ?? 'unmatched', response.statusCode));
    next();
  }));
  const handle = (fn: (request: Request, response: Response) => Promise<void>, bodyLimit = MAX_REQUEST_BYTES) => async (request: Request, response: Response) => {
    try {
      if (request.method !== 'GET') {
        try { boundedRepairJson(request.body, bodyLimit); } catch (error) {
          if (error instanceof RepairBatchError) throw new RepairBatchError(error.code, error.message, { ...error.details, stage: 'request_body', reasonCode: 'REPAIR_REQUEST_TOO_LARGE' });
          throw error;
        }
      }
      await fn(request, response);
    } catch (error) {
      if (error instanceof RepairBatchError) {
        if (error.code === 'PAYLOAD_TOO_LARGE') console.warn('[workflow-repair] payload limit', {
          requestId: response.getHeader('X-Repair-Request-Id'), stage: error.details.stage ?? 'service',
          reasonCode: error.details.reasonCode ?? error.code, limit: error.details.limit, actualBytes: error.details.actualBytes,
        });
        const status = error.code === 'INVALID_INPUT' ? 400 : error.code === 'PAYLOAD_TOO_LARGE' ? 413
          : error.code === 'CAPABILITY_UNAVAILABLE' ? 503
          : ['WORKFLOW_NOT_FOUND', 'TASK_NOT_FOUND', 'REVISION_NOT_FOUND', 'ITEM_NOT_FOUND'].includes(error.code) ? 404 : 409;
        response.status(status).json({ error: error.message, code: error.code, ...error.details, requestId: response.getHeader('X-Repair-Request-Id') });
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
    const task = await repairStage('task_detail', () => service.task(text(request.params.taskId, 'taskId')));
    const actor = await access(request, response, task.workflowId, mode);
    return actor ? { task, actor } : null;
  }
  // Machine read capability is restricted to immutable material and its active execution.
  router.get('/material-download/:taskId/:revision/:itemId',handle(async(req,res)=>{
    res.setHeader('Cache-Control','no-store');
    if(!service.materialChunk)throw new RepairBatchError('CAPABILITY_UNAVAILABLE','Material download unavailable');
    const offset=req.query.offset===undefined?0:Number(req.query.offset);
    const token=/^Bearer ([A-Za-z0-9_-]{43})$/.exec(req.get('Authorization')??'')?.[1]??'';
    const chunk=await service.materialChunk(text(req.params.taskId,'taskId'),revision(req.params.revision),text(req.params.itemId,'itemId'),token,offset);
    if(!chunk){res.status(401).json({code:'MATERIAL_ACCESS_DENIED'});return;}
    res.json(chunk);
  }));
  router.post('/proposal-integration/retry', handle(async (req, res) => {
    const workflowId = text(req.body?.workflowId, 'workflowId');
    if (!await access(req, res, workflowId, 'edit')) return;
    await service.retryProposalIntegration?.(workflowId); res.json({ ok: true });
  }));
  router.get('/:taskId/deployment/:deploymentId/verification', handle(async (req, res) => {
    const access = await taskAccess(req, res, 'view');
    if (!access) return;
    if (!input.verification) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '效果验证尚未配置');
    res.json({ evidence: await input.verification.evidence(access.task.taskId, text(req.params.deploymentId, 'deploymentId')),
      conclusions: await input.verification.conclusions(access.task.taskId) });
  }));
  router.post('/:taskId/deployment/:deploymentId/verification', handle(async (req, res) => {
    const access = await taskAccess(req, res, 'edit');
    if (!access) return;
    if (!input.verification) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '效果验证尚未配置');
    res.json(await input.verification.conclude(access.actor.actorId, access.task.taskId, text(req.params.deploymentId, 'deploymentId'), req.body));
  }));
  router.post('/:taskId/deployment/:deploymentId/reopen-item', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    if (!input.verification) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '效果验证尚未配置');
    res.json(await input.verification.reopen(selected.actor.actorId, selected.task.taskId, text(req.params.deploymentId, 'deploymentId'), req.body));
  }));
  const candidates = handle(async (req, res) => {
    const query = req.method === 'GET' ? req.query : req.body;
    const workflowId = text(query?.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'view');
    const state = query.state ?? 'all';
    const versionScope = query.versionScope;
    if (versionScope !== undefined && !['current', 'historical', 'all'].includes(versionScope)) throw new RepairBatchError('INVALID_INPUT', 'Invalid version scope');
    if (typeof state !== 'string' || !['pending', 'processing', 'awaiting_verification', 'closed', 'no_action', 'all'].includes(state)) throw new RepairBatchError('INVALID_INPUT', 'Invalid state');
    if (actor) res.json({ ...await service.candidates(workflowId, { versionScope, snapshotKey: query.snapshotKey === undefined ? undefined : text(query.snapshotKey, 'snapshotKey'), state: state as RepairInboxFilter,
      includeTasks: boolean(query.includeTasks, 'includeTasks', true),
      metadataPage: positive(query.metadataPage, 'metadataPage', 1), page: positive(query.page, 'page', 1), pageSize: positive(query.pageSize, 'pageSize', 20, 200),
      nodeId: query.nodeId === undefined ? undefined : text(query.nodeId, 'nodeId'),
      failureMode: query.failureMode === undefined ? undefined : text(query.failureMode, 'failureMode'),
      signature: query.signature === undefined ? undefined : text(query.signature, 'signature'),
      previewSignatures: req.method !== 'GET' ? query.previewSignatures : query.previewSignature === undefined ? undefined
        : (Array.isArray(query.previewSignature) ? query.previewSignature : [query.previewSignature]).map(value => text(value, 'previewSignature')),
      includeHistorical: boolean(query.includeHistorical, 'includeHistorical') }), canEdit: actor.canEdit });
  }, 256 * 1024);
  router.get('/candidates', candidates);
  // Body-bearing query only: view authorization, identical snapshot, no materialization or task writes.
  router.post('/candidates/query', candidates);
  router.post('/selection/query', handle(async (req, res) => {
    const workflowId = text(req.body?.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'view');
    if (actor) res.json({ ...await service.selectionState(workflowId, req.body.itemIds, boolean(req.body.includeHistorical, 'includeHistorical')), canEdit: actor.canEdit });
  }));
  router.get('/items/:itemId', handle(async (req, res) => {
    const workflowId = text(req.query.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'view');
    if (actor) res.json(await service.item(workflowId, text(req.params.itemId, 'itemId'), boolean(req.query.originalOnly, 'originalOnly')));
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
      ...(req.body.untilMs === undefined ? {} : { untilMs: req.body.untilMs }),
      ...(req.body.includeHistorical === undefined ? {} : { includeHistorical: req.body.includeHistorical }),
    }));
  }));
  router.get('/:taskId', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'view');
    if (selected) res.json(projectTaskHistory({ ...selected.task,
      ...(input.release ? { release: await repairStage('release_detail', () => input.release!.detail(selected.task.taskId, selected.task.latestSuccessful?.revision ?? null)),
        capabilities: { ...selected.task.capabilities, publication: input.release.configured && selected.actor.canEdit } } : {}) }, positive(req.query.revisionPage, 'revisionPage', 1), positive(req.query.revisionPageSize, 'revisionPageSize', 20, 20)));
  }));
  function release() {
    if (!input.release) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '当前宿主未接入候选审阅和部署服务');
    return input.release;
  }
  router.get('/requests/lookup', handle(async (req, res) => {
    const workflowId = text(req.query.workflowId, 'workflowId');
    const actor = await access(req, res, workflowId, 'edit');
    if (!actor) return;
    res.json({ revision: await service.request(workflowId, text(req.query.requestId, 'requestId')) });
  }));
  router.get('/:taskId/trials/options', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    if (!input.trials) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '试跑尚未接入');
    res.json(await input.trials.options(selected.actor.actorId, selected.task.taskId, revision(req.query.revision)));
  }));
  router.get('/:taskId/trials', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'view');
    if (!selected) return;
    const offset = req.query.offset === undefined ? 0 : Number(req.query.offset);
    if (!Number.isSafeInteger(offset) || offset < 0) throw new RepairBatchError('INVALID_INPUT', 'Invalid trial offset');
    const trials = input.trials ? await input.trials.list(selected.task.taskId, offset) : [];
    res.json({ trials, nextOffset: trials.length === 20 ? offset + 20 : null, configured: !!input.trials?.configured });
  }));
  router.post('/:taskId/trials', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    if (!input.trials) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '试跑尚未接入');
    res.status(202).json(await input.trials.start(selected.actor.actorId, selected.task.taskId, req.body));
  }));
  router.post('/:taskId/trials/:trialId/stop', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    if (!input.trials) throw new RepairBatchError('CAPABILITY_UNAVAILABLE', '试跑尚未接入');
    res.json(await input.trials.stop(selected.task.taskId, text(req.params.trialId, 'trialId')));
  }));
  router.get('/:taskId/deployment-targets', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.json({ targets: await release().targets(selected.actor.actorId, selected.task.taskId) });
  }));
  router.post('/:taskId/confirm', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.json(await release().confirm(selected.actor.actorId, selected.task.taskId, req.body));
  }));
  router.post('/:taskId/revoke-confirmation', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.json(await release().revoke(selected.actor.actorId, selected.task.taskId, req.body));
  }));
  router.post('/:taskId/deployment/:deploymentId/resume', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.json(await release().resume(selected.actor.actorId, selected.task.taskId, text(req.params.deploymentId, 'deploymentId'), text(req.body.requestId, 'requestId')));
  }));
  router.post('/:taskId/deployment/:deploymentId/stop', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.json(await release().stop(selected.actor.actorId, selected.task.taskId, text(req.params.deploymentId, 'deploymentId'), text(req.body.requestId, 'requestId')));
  }));
  router.post('/:taskId/deployment', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.status(202).json(await release().deploy(selected.actor.actorId, selected.task.taskId, req.body));
  }));
  router.post('/:taskId/publish', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.status(202).json(await release().publish(selected.actor.actorId, selected.task.taskId, req.body));
  }));
  router.post('/:taskId/deployment/:deploymentId/reconcile', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (selected) res.json(await release().reconcile(selected.actor.actorId, selected.task.taskId, text(req.params.deploymentId, 'deploymentId')));
  }));
  router.get('/:taskId/revisions/:revision/materials/:itemId',handle(async(req,res)=>{
    const selected=await taskAccess(req,res,'view');if(!selected)return;
    if(!service.material)throw new RepairBatchError('CAPABILITY_UNAVAILABLE','冻结材料读取尚未配置');
    res.json(await service.material(selected.task.taskId,revision(req.params.revision),text(req.params.itemId,'itemId')));
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
  router.post('/:taskId/retry-result', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    await service.retryResult(selected.actor.actorId, selected.task.taskId, revision(req.body?.expectedRevision));
    res.status(202).json({ ok: true });
  }));
  router.post('/:taskId/retry-generation', handle(async (req, res) => {
    const selected = await taskAccess(req, res, 'edit');
    if (!selected) return;
    res.status(202).json(await service.retryGeneration(selected.actor.actorId, selected.task.taskId,
      { expectedRevision: revision(req.body?.expectedRevision), requestId: text(req.body?.requestId, 'requestId') }));
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
