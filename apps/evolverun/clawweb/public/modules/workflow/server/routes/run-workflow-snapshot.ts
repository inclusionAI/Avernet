import { Router } from 'express';
import { parse as parseYaml } from 'yaml';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { productionRunSql } from '@avernet/clawweb-shared/server/services/run-purpose';
import { WorkflowSnapshotRepository, parseWorkflowSnapshotBinding } from '@avernet/clawweb-shared/server/services/workflow-snapshot';
import type { RepairAuthorize } from './repair-batches.js';

const id = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.length <= 255 && !/[\s\x00-\x1f\x7f/\\]/u.test(value);
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);

/** Only static topology leaves the service; prompts, params and credentials never do. */
function projectNodes(spec: Record<string, unknown>): Array<{ id: string; title: string; dependsOn: string[]; executor: { type: string } }> {
  if (!Array.isArray(spec.nodes) || spec.nodes.length > 1000) throw new Error('Invalid snapshot');
  const seen = new Set<string>();
  return spec.nodes.map((value: unknown) => {
    if (!object(value) || !id(value.id) || seen.has(value.id)) throw new Error('Invalid snapshot');
    const executorType = value.type === 'loop-group' || (object(value.executor) && value.executor.type === 'loop-group')
      ? 'loop-group' : object(value.executor) && id(value.executor.type) ? value.executor.type : null;
    if (!executorType) throw new Error('Invalid snapshot');
    seen.add(value.id);
    const deps = value.dependsOn ?? [];
    if (!Array.isArray(deps) || deps.length > 1000 || !deps.every(id)) throw new Error('Invalid snapshot');
    const title = typeof value.title === 'string' && value.title.length <= 500 ? value.title : value.id;
    return { id: value.id, title, dependsOn: deps, executor: { type: executorType } };
  });
}

/** The caller must mount this route only with a verified whole-workflow authorizer. */
export function createRunWorkflowSnapshotRouter(db: IDatabase, authorize: RepairAuthorize): Router {
  const router = Router();
  router.get('/:flowId/workflow-snapshot', async (req, res) => {
    res.set('Cache-Control', 'no-store');
    const flowId = req.params.flowId;
    if (!id(flowId) || Object.keys(req.query).length) { res.status(400).json({ error: 'Invalid request' }); return; }
    if (!db || db.dbType === 'noop' || typeof authorize !== 'function') { res.status(503).json({ error: 'Snapshot unavailable' }); return; }
    try {
      const row = (await db.query<{ flow_id: string; workflow_id: string; workflow_snapshot_json: string | null }>(
        `SELECT flow_id, workflow_id, workflow_snapshot_json FROM flow_runs WHERE flow_id = ? AND ${productionRunSql('flow_runs.flow_id')} LIMIT 1`, [flowId],
      ))[0];
      if (!row || row.flow_id !== flowId || !id(row.workflow_id)) { res.status(404).json({ error: 'Run not found' }); return; }
      const actor = await authorize(req, row.workflow_id, 'view');
      if (!actor?.actorId?.trim()) { res.status(404).json({ error: 'Run not found' }); return; }
      // Require the immutable binding captured at registration: computing a new
      // digest during this read would not establish historical identity.
      const binding = parseWorkflowSnapshotBinding(row.workflow_snapshot_json);
      if (!binding || binding.status !== 'resolved' || binding.snapshot.workflowId !== row.workflow_id
        || (binding.snapshot.kind === 'saved' && binding.snapshot.sourceFlowId !== flowId)) {
        res.status(409).json({ error: 'Snapshot identity unavailable' }); return;
      }
      let spec: Record<string, unknown>;
      try { spec = (await new WorkflowSnapshotRepository(db).read(binding.snapshot)).workflowSpec; }
      catch { res.status(409).json({ error: 'Snapshot identity unavailable' }); return; }
      // The immutable digest covers the stored wrapper; unwrap only after verification.
      try {
        if (!('nodes' in spec) && typeof spec.content === 'string') {
          if (spec.content.length > 1_000_000) throw new Error('Snapshot too large');
          const parsed: unknown = parseYaml(spec.content, { maxAliasCount: 50 });
          if (!object(parsed)) throw new Error('Invalid snapshot');
          spec = parsed;
        }
        if (spec.id !== row.workflow_id) throw new Error('Snapshot identity mismatch');
        res.json({ nodes: projectNodes(spec) });
      }
      catch { res.status(409).json({ error: 'Snapshot identity unavailable' }); }
    } catch { res.status(503).json({ error: 'Snapshot unavailable' }); }
  });
  return router;
}
