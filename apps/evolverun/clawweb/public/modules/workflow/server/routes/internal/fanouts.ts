/** Internal fanout durability endpoints. Mounted under /api/internal/runs.
 *
 * GET/PUT /:flowId/fanouts/:nodeId
 *   PUT { group, expectedRevision: number|null, parentState?: object }
 *   -> { success: true, data: { group: object|null, parentStatus: string } }
 * GET/PUT /:parentFlowId/fanout-children/:childFlowId
 *   PUT FanoutChildStart -> { success: true, data: { created, run } } (201/200)
 *   GET -> { success: true, data: FanoutChildRecord }
 *   HTTP records encode stateJson as a JSON object STRING. Input accepts either
 *   an object or that string; nodeId aliases parentNodeId for existing clients.
 * PUT /:parentFlowId/fanout-children/:childFlowId/state
 *   FanoutChildCheckpoint -> { success: true, data: FanoutChildRecord }
 *
 * Admission: perWorkflow[workflowId/default].maxConcurrent, default 5.
 * 409 CAPACITY_BUSY leaves no child row; retry the SAME durable child ID.
 * 400 invalid schema; 404 absent run; 409 CAS/identity/lifecycle conflict;
 * 500 persistence failure; 503 database unavailable. No credentials or raw SQL
 * errors are returned, and snapshot bodies are never logged.
 */
import { Router, type Request, type Response } from 'express';
import { asyncHandler } from '@avernet/clawweb-shared/server/middleware/async-handler';
import { FanoutStateRepository, FanoutStorageError, type FanoutChildRecord } from '../../repositories/fanout-state-repository.js';
import { saveFanoutGroupWithDiagnostics } from '../../services/fanout-group-save-service.js';

function childBody(body: unknown): unknown {
  if (!body || typeof body !== 'object' || Array.isArray(body)) throw new FanoutStorageError(400, 'Invalid child body');
  const value = body as Record<string, unknown>;
  let stateJson = value.stateJson;
  if (typeof stateJson === 'string') {
    try { stateJson = JSON.parse(stateJson); }
    catch { throw new FanoutStorageError(400, 'stateJson must encode a JSON object'); }
  }
  if (!stateJson || typeof stateJson !== 'object' || Array.isArray(stateJson)) throw new FanoutStorageError(400, 'stateJson must be a JSON object');
  if (value.parentNodeId !== undefined && value.nodeId !== undefined && value.parentNodeId !== value.nodeId) {
    throw new FanoutStorageError(400, 'Conflicting parentNodeId and nodeId');
  }
  return { ...value, parentNodeId: value.parentNodeId ?? value.nodeId, stateJson };
}
const wireRun = (run: FanoutChildRecord) => ({ ...run, stateJson: JSON.stringify(run.stateJson) });

export function createInternalFanoutsRouter(
  repository: FanoutStateRepository | null,
  runLogRepository: Pick<import('../../repositories/run-log-repository.js').RunLogRepository, 'insertBatch'> | null = null,
): Router {
  const router = Router();
  const handle = (operation: (repo: FanoutStateRepository, req: Request, res: Response) => Promise<void>) =>
    asyncHandler(async (req: Request, res: Response) => {
      try {
        if (!repository) throw new FanoutStorageError(503, 'Database not configured');
        await operation(repository, req, res);
      } catch (error) {
        const status = error instanceof FanoutStorageError ? error.statusCode : 500;
        const labels: Record<number, string> = { 400: 'Bad Request', 404: 'Not Found', 409: 'Conflict', 500: 'Internal Server Error', 503: 'Service Unavailable' };
        const code = error instanceof FanoutStorageError ? error.code : undefined;
        res.status(status).json({ success: false, error: code ?? labels[status], ...(code ? { code } : {}),
          message: error instanceof FanoutStorageError ? error.message : 'Fanout persistence operation failed' });
      }
    });

  router.get('/:flowId/fanouts/:nodeId', handle(async (repo, req, res) => {
    const data = await repo.getGroup(String(req.params.flowId), String(req.params.nodeId));
    res.json({ success: true, data });
  }));
  router.put('/:flowId/fanouts/:nodeId', handle(async (repo, req, res) => {
    const data = await saveFanoutGroupWithDiagnostics(repo, runLogRepository, String(req.params.flowId), String(req.params.nodeId), req.body);
    res.json({ success: true, data });
  }));
  router.get('/:parentFlowId/fanout-children/:childFlowId', handle(async (repo, req, res) => {
    const data = await repo.getChild(String(req.params.parentFlowId), String(req.params.childFlowId));
    res.json({ success: true, data: wireRun(data) });
  }));
  router.put('/:parentFlowId/fanout-children/:childFlowId', handle(async (repo, req, res) => {
    const body = childBody(req.body) as Parameters<FanoutStateRepository['startOrGetChild']>[2];
    const data = await repo.startOrGetChild(String(req.params.parentFlowId), String(req.params.childFlowId), body);
    res.status(data.created ? 201 : 200).json({ success: true, data: { created: data.created, run: wireRun(data.run) } });
  }));
  router.put('/:parentFlowId/fanout-children/:childFlowId/state', handle(async (repo, req, res) => {
    const body = childBody(req.body) as Parameters<FanoutStateRepository['checkpointChild']>[2];
    const data = await repo.checkpointChild(String(req.params.parentFlowId), String(req.params.childFlowId), body);
    res.json({ success: true, data: wireRun(data) });
  }));
  return router;
}
