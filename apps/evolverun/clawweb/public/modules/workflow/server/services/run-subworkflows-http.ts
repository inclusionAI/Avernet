/** HTTP adapter and serialization boundary; this module never dispatches runtime commands. */
import type { Router, Request, Response } from 'express';
import { asyncHandler } from '@avernet/clawweb-shared/server/middleware/async-handler';
import { resolveWorkflowActorId } from '@avernet/clawweb-shared/server/services/workflow-access';
import { FanoutReadError, type RunReadInspection, type RunSubworkflowsReader } from '../contracts/run-subworkflows.js';

const statuses = { unauthenticated: 401, forbidden: 403, not_found: 404, invalid_query: 400,
  identity_conflict: 409, snapshot_changed: 409, invalid_state: 500, unavailable: 503 };
export function sendRunReadError(res: Response, error: unknown): void {
  if (error instanceof FanoutReadError) res.status(statuses[error.code]).json({ error: error.code, message: error.message });
  else res.status(500).json({ error: 'read_failed', message: 'Unable to read persisted run data' });
}
const actor = (req: Request) => ({ actorId: resolveWorkflowActorId(req), isAdmin: req.isAdmin === true });
function scalar(value: unknown, required = false): string | undefined {
  if (value === undefined && !required) return undefined;
  if (typeof value !== 'string' || !value.trim()) throw new FanoutReadError('invalid_query', 'Expected a single non-empty query value');
  return value;
}

export function installFanoutReadHandlers(router: Router, reader: RunSubworkflowsReader | null): void {
  // Temporary guard for new ordinary fanoutOrigin children; legacy managed children
  // and ordinary runs keep their existing behavior. This is not general write authorization.
  const guardWrite = asyncHandler(async (req, res, next) => {
    // Public bootstrap supplies the reader. Legacy router compositions without it retain their behavior.
    if (!reader) { next(); return; }
    try {
      const child = await reader.isFanoutOriginChild(String(req.params.flowId));
      if (child === null) { next(); return; }
      if (child) { res.status(403).json({ error: 'Forbidden', message: 'Public writes to fanout child runs are temporarily unavailable' }); return; }
      next();
    } catch {
      res.status(503).json({ error: 'Service Unavailable', message: 'Run state unavailable' });
    }
  });
  router.patch('/:flowId/session', guardWrite);
  router.post(['/:flowId/intervene', '/:flowId/rerun', '/:flowId/abort', '/:flowId/chat', '/:flowId/poll-message'], guardWrite);
  router.delete('/:flowId', guardWrite);
  router.get('/:flowId/nodes/:nodeId/subworkflows', asyncHandler(async (req, res) => {
    res.setHeader('Cache-Control', 'private, no-store');
    try {
      if (!reader) throw new FanoutReadError('unavailable', 'Run observation is not configured');
      const limit = scalar(req.query.limit);
      if (limit !== undefined && !/^\d+$/.test(limit)) throw new FanoutReadError('invalid_query', 'Invalid limit');
      res.json(await reader.readGroup(actor(req), String(req.params.flowId), String(req.params.nodeId), {
        groupExecutionId: scalar(req.query.groupExecutionId, true)!,
        ...(limit === undefined ? {} : { limit: Number(limit) }),
        ...(req.query.cursor === undefined ? {} : { cursor: scalar(req.query.cursor, true) }),
      }));
    } catch (error) { sendRunReadError(res, error); }
  }));

  router.use('/:flowId', asyncHandler(async (req, res, next) => {
    if ((req.method !== 'GET' && req.method !== 'HEAD') || !reader) { next(); return; }
    res.setHeader('Cache-Control', 'private, no-store');
    try {
      const inspection = await reader.inspectRun(actor(req), String(req.params.flowId));
      // Raw logs, replay, topology and dynamic summaries can embed downstream inputs/outputs.
      // Restricted parents must use the filtered snapshot rather than those legacy payloads.
      if (inspection.restricted && !['/', '/nodes', '/events', '/interventions'].includes(req.path)) {
        throw new FanoutReadError('forbidden', 'Use the permission-filtered subworkflow view');
      }
      res.locals.runRead = inspection;
      next();
    } catch (error) { sendRunReadError(res, error); }
  }));
}

export function projectRun(run: Record<string, unknown>, inspection?: RunReadInspection): Record<string, unknown> {
  if (!inspection?.managed) return run;
  // State includes inputs, historic attempts, session keys and cleanup IDs, not a public view.
  return { ...run, state_json: null, params_json: null, input_json: null, result_json: null,
    origin_session_key: null, origin_session_id: null,
    subworkflowGroups: inspection.groups, fanoutPayloadsRedacted: true };
}
const nodeMetadata = ['id', 'flow_id', 'workflow_id', 'node_id', 'executor_type', 'node_title',
  'status', 'attempt', 'started_at', 'completed_at', 'duration_ms', 'version', 'gmt_create'];
export function projectNodes<T extends object>(nodes: T[], inspection?: RunReadInspection): T[] {
  if (!inspection?.managed && !inspection?.sensitiveChild) return nodes;
  // Parent and independent child nodes can contain copied inputs, secrets or fanout results.
  return nodes.map(node => Object.fromEntries(nodeMetadata.filter(key => key in node).map(key => [key, node[key]])) as T);
}
