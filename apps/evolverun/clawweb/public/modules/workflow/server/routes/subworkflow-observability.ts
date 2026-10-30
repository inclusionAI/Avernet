import { Router, type Request } from 'express';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { productionRunSql } from '@avernet/clawweb-shared/server/services/run-purpose';
import type { RepairAuthorize } from './repair-batches.js';

// These are deliberately fixed: neither work nor response size is client-controlled.
const EVENT_LIMIT = 200;
const CHILD_LIMIT = 20;
const NODE_LIMIT = 50;
const STATUSES = new Set(['pending', 'running', 'postActionsRunning', 'waiting', 'succeeded', 'failed', 'rejected', 'blocked', 'skipped', 'cancelled', 'completed', 'paused']);
const validId = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= 255 && !/[\s\x00-\x1f\x7f/\\]/u.test(value);
const status = (value: unknown): string => typeof value === 'string' && STATUSES.has(value) ? value : 'unknown';
const timestamp = (value: unknown): number | null => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null;

type Run = { flow_id: string; workflow_id: string; status: unknown; started_at: unknown; completed_at: unknown };
type Event = { node_id: unknown; data_json: unknown };
type Node = { node_id: unknown; node_title: unknown; status: unknown; attempt: unknown };

function childId(data: unknown): string | null {
  try {
    // Some MySQL drivers decode JSON columns automatically; SQLite returns text.
    if (typeof data === 'string') {
      if (data.length > 65536) return null;
      data = JSON.parse(data) as unknown;
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
    const id = (data as { childFlowId?: unknown }).childFlowId;
    return validId(id) ? id : null;
  } catch {
    return null; // Historical malformed events are not authoritative links.
  }
}

/** Host must inject the verified-login repair authorizer (whole-workflow view only). */
export function createSubworkflowObservabilityRouter(db: IDatabase, authorize: RepairAuthorize): Router {
  const router = Router();
  router.get('/:flowId/subworkflows', async (req: Request, res) => {
    res.set('Cache-Control', 'no-store');
    const flowId = req.params.flowId;
    if (!validId(flowId) || Object.keys(req.query).length !== 0) {
      res.status(400).json({ error: 'Invalid request', code: 'INVALID_INPUT' });
      return;
    }
    if (!db || db.dbType === 'noop' || typeof authorize !== 'function') {
      res.status(503).json({ error: 'Observability unavailable', code: 'UNAVAILABLE' });
      return;
    }
    try {
      const findRun = async (id: string) => (await db.query<Run>(
        `SELECT flow_id, workflow_id, status, started_at, completed_at FROM flow_runs WHERE flow_id = ? AND ${productionRunSql('flow_runs.flow_id')} LIMIT 1`, [id],
      ))[0];
      const parent = await findRun(flowId);
      if (!parent) {
        res.status(404).json({ error: 'Run not found', code: 'NOT_FOUND' });
        return;
      }
      if (!validId(parent.workflow_id)) throw new Error('Invalid stored workflow');
      // Each workflow is evaluated once per request; denied decisions are cached too.
      const access = new Map<string, boolean>();
      const permitted = async (workflowId: string) => {
        if (access.has(workflowId)) return access.get(workflowId)!;
        const actor = await authorize(req, workflowId, 'view');
        const allowed = typeof actor?.actorId === 'string' && !!actor.actorId.trim();
        access.set(workflowId, allowed);
        return allowed;
      };
      if (!await permitted(parent.workflow_id)) {
        res.status(404).json({ error: 'Run not found', code: 'NOT_FOUND' });
        return;
      }
      const events = await db.query<Event>(
        `SELECT node_id, data_json FROM flow_events WHERE flow_id = ? AND workflow_id = ? AND event_type IN ('subworkflow_linked', 'subworkflow_finished') ORDER BY time DESC, id DESC LIMIT ${EVENT_LIMIT}`,
        [flowId, parent.workflow_id],
      );
      const seen = new Set<string>();
      const items: Array<{ parentNodeId: string | null; childFlowId: string; childWorkflowId: string; status: string; startedAt: number | null; completedAt: number | null; nodes: Array<{ nodeId: string; nodeTitle: string | null; status: string; attempt: number | null }> }> = [];
      for (const event of events) {
        const id = childId(event.data_json);
        if (!id || id === flowId || seen.has(id)) continue;
        if (seen.size >= CHILD_LIMIT) break;
        seen.add(id);
        const child = await findRun(id);
        if (!child) continue; // Never disclose unknown, trial, or unauthorized identifiers.
        if (!validId(child.workflow_id)) throw new Error('Invalid stored workflow');
        if (!await permitted(child.workflow_id)) continue;
        const rows = await db.query<Node>(
          `SELECT node_id, node_title, status, attempt FROM node_executions WHERE flow_id = ? AND workflow_id = ? ORDER BY id DESC LIMIT ${NODE_LIMIT}`,
          [id, child.workflow_id],
        );
        const nodes = rows.filter(row => validId(row.node_id)).map(row => ({
          nodeId: row.node_id as string,
          nodeTitle: typeof row.node_title === 'string' && row.node_title.length <= 255 ? row.node_title : null,
          status: status(row.status),
          attempt: typeof row.attempt === 'number' && Number.isSafeInteger(row.attempt) && row.attempt > 0 ? row.attempt : null,
        }));
        items.push({ parentNodeId: validId(event.node_id) ? event.node_id : null, childFlowId: id,
          childWorkflowId: child.workflow_id, status: status(child.status), startedAt: timestamp(child.started_at),
          completedAt: timestamp(child.completed_at), nodes });
      }
      res.json({ items, completeness: 'best_effort' });
    } catch {
      // Fail closed on database, schema, or authorization failures; never return diagnostics.
      res.status(503).json({ error: 'Observability unavailable', code: 'UNAVAILABLE' });
    }
  });
  return router;
}
