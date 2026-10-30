import { Router, type Request } from 'express';
import type { PackScriptSnapshot } from '../services/pack-script-snapshot.js';

type PackScriptsDependencies = {
  enabled?: boolean;
  resolvePrincipal: (req: Request) => Promise<{ actorId: string } | null>;
  botPermRepo: { hasEditPermission(workflowId: string, actorId: string): Promise<boolean> } | null;
  workflowSpecRepo: { findByWorkflowId(workflowId: string): Promise<{ workflow_id: string; pack_id: string | null } | null> };
  // Must authorize the entire Pack independently of an individual workflow ACL.
  authorizePackRead?: (workflowId: string, packId: string, actorId: string) => Promise<boolean>;
  readSnapshot: (workflowId: string, packId: string) => Promise<PackScriptSnapshot>;
};

// Keep this aligned with the snapshot reader's Git ref and pack name rules.
function validId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(value)
    && !value.includes('..') && !value.endsWith('.') && !value.endsWith('.lock');
}

export function createPackScriptsRouter(deps: PackScriptsDependencies): Router {
  const router = Router();
  router.get('/:workflowId/pack-scripts', async (req, res) => {
    res.set('Cache-Control', 'no-store');
    if (deps.enabled === false) { res.status(503).json({ error: 'Pack scripts unavailable', code: 'SERVICE_UNAVAILABLE' }); return; }
    try {
      const principal = await deps.resolvePrincipal(req);
      if (!principal || typeof principal.actorId !== 'string' || !principal.actorId.trim()) {
        res.status(401).json({ error: 'Authentication required', code: 'AUTHENTICATION_REQUIRED' }); return;
      }
      const workflowId = req.params.workflowId;
      if (!validId(workflowId)) { res.status(400).json({ error: 'Invalid workflow ID', code: 'INVALID_WORKFLOW_ID' }); return; }
      if (!deps.botPermRepo || typeof deps.botPermRepo.hasEditPermission !== 'function'
        || await deps.botPermRepo.hasEditPermission(workflowId, principal.actorId) !== true) {
        res.status(403).json({ error: 'Forbidden', code: 'FORBIDDEN' }); return;
      }
      const row = await deps.workflowSpecRepo.findByWorkflowId(workflowId);
      if (!row) { res.status(404).json({ error: 'Workflow not found', code: 'WORKFLOW_NOT_FOUND' }); return; }
      const packId = row.pack_id ?? row.workflow_id;
      if (row.workflow_id !== workflowId || !validId(row.workflow_id) || !validId(packId)) {
        res.status(503).json({ error: 'Snapshot unavailable', code: 'SERVICE_UNAVAILABLE' }); return;
      }
      // A single-member manifest cannot prove that a Pack is not shared or that
      // residual files belong to this workflow. No authoritative policy: no read.
      if (!deps.authorizePackRead) { res.status(503).json({ error: 'Pack authorization unavailable', code: 'SERVICE_UNAVAILABLE' }); return; }
      if (await deps.authorizePackRead(row.workflow_id, packId, principal.actorId) !== true) {
        res.status(403).json({ error: 'Forbidden', code: 'FORBIDDEN' }); return;
      }
      try {
        res.json(await deps.readSnapshot(row.workflow_id, packId));
      } catch (error) {
        // Match only errors explicitly emitted by the snapshot reader. Never return or log details.
        const message = error instanceof Error ? error.message : '';
        if (message === 'Workflow ref conflict: histories diverged' || message === 'Pack manifest conflict' || message === 'Shared pack requires separate authorization') {
          res.status(409).json({ error: 'Snapshot conflict', code: 'SNAPSHOT_CONFLICT' });
        } else if (message === 'Workflow snapshot not found' || message === 'Pack manifest not found') {
          res.status(404).json({ error: 'Snapshot not found', code: 'SNAPSHOT_NOT_FOUND' });
        } else {
          res.status(503).json({ error: 'Snapshot unavailable', code: 'SERVICE_UNAVAILABLE' });
        }
      }
    } catch {
      res.status(503).json({ error: 'Service unavailable', code: 'SERVICE_UNAVAILABLE' });
    }
  });
  return router;
}
