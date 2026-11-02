import { Router, type Request } from 'express';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunLinkRepository } from '@avernet/clawweb-shared/server/repositories/flow-run-link-repository';
import { productionRunSql } from '@avernet/clawweb-shared/server/services/run-purpose';
import type { RepairAuthorize } from './repair-batches.js';
import { createRunWorkflowSnapshotRouter } from './run-workflow-snapshot.js';

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
type Event = { node_id: unknown; attempt: unknown; data_json: unknown };
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
  router.use(createRunWorkflowSnapshotRouter(db, authorize));
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
      // Missing relation schema is an operational error, not evidence of no links.
      const links = new FlowRunLinkRepository(db);
      const stored = await links.listByParent(flowId, CHILD_LIMIT);
      const events = await db.query<Event>(
        `SELECT node_id, attempt, data_json FROM flow_events WHERE flow_id = ? AND workflow_id = ? AND event_type IN ('subworkflow_linked', 'subworkflow_finished') ORDER BY time DESC, id DESC LIMIT ${EVENT_LIMIT}`,
        [flowId, parent.workflow_id],
      );
      const candidates = new Map<string, { parentNodeId: string | null; parentAttempt: number | null; childWorkflowId?: string; stored: boolean }>();
      for (const link of stored) {
        if (!validId(link.childFlowId) || link.childFlowId === flowId || !validId(link.parentNodeId) || !validId(link.childWorkflowId)) continue;
        candidates.set(link.childFlowId, { parentNodeId: link.parentNodeId, parentAttempt: link.parentAttempt,
          childWorkflowId: link.childWorkflowId, stored: true });
      }
      for (const event of events) {
        const id = childId(event.data_json);
        if (!id || id === flowId) continue;
        const parentNodeId = validId(event.node_id) ? event.node_id : null;
        const parentAttempt = typeof event.attempt === 'number' && Number.isSafeInteger(event.attempt) && event.attempt > 0 ? event.attempt : null;
        const candidate = candidates.get(id);
        if (candidate) {
          // A newer finish event can omit attempt. Earlier evidence is usable
          // only for the same known parent node, never from node state.
          if (!candidate.stored && candidate.parentAttempt === null && candidate.parentNodeId !== null && candidate.parentNodeId === parentNodeId) candidate.parentAttempt = parentAttempt;
          continue;
        }
        if (candidates.size >= CHILD_LIMIT) continue;
        candidates.set(id, { parentNodeId, parentAttempt, stored: false });
      }
      const items: Array<{ parentNodeId: string | null; parentAttempt: number | null; childFlowId: string; childWorkflowId: string; status: string; startedAt: number | null; completedAt: number | null; nodes: Array<{ nodeId: string; nodeTitle: string | null; status: string; attempt: number | null }> }> = [];
      for (const [id, candidate] of candidates) {
        // A historical event cannot override an independently recorded relationship,
        // including one pointing to a different parent.
        if (!candidate.stored) {
          const recorded = await links.findByChild(id);
          if (recorded) {
            if (recorded.parentFlowId !== flowId || !validId(recorded.parentNodeId) || !validId(recorded.childWorkflowId)) continue;
            candidate.parentNodeId = recorded.parentNodeId;
            candidate.parentAttempt = recorded.parentAttempt;
            candidate.childWorkflowId = recorded.childWorkflowId;
          }
        }
        const child = await findRun(id);
        if (!child) continue; // Never disclose unknown, trial, or unauthorized identifiers.
        if (candidate.childWorkflowId !== undefined && candidate.childWorkflowId !== child.workflow_id) continue;
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
        items.push({ parentNodeId: candidate.parentNodeId, parentAttempt: typeof candidate.parentAttempt === 'number' && Number.isSafeInteger(candidate.parentAttempt) && candidate.parentAttempt > 0 ? candidate.parentAttempt : null, childFlowId: id,
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
