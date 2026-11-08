import { Router, type Request } from 'express';
import type { PackScriptSnapshot, PackScriptVersion } from '../services/pack-script-snapshot.js';

type PackScriptsDependencies = {
  enabled?: boolean;
  resolvePrincipal: (req: Request) => Promise<{ actorId: string } | null>;
  botPermRepo: { hasEditPermission(workflowId: string, actorId: string): Promise<boolean> } | null;
  workflowSpecRepo: { findByWorkflowId(workflowId: string): Promise<{ workflow_id: string; pack_id: string | null } | null> };
  // Host policy must authorize the entire returned Pack; some hosts may grant this
  // through workflow edit permission when the workflow version is the boundary.
  authorizePackRead?: (workflowId: string, packId: string, actorId: string) => Promise<boolean>;
  // Declare only when readSnapshot honors the version and reports the actual ref.
  supportsVersionedSnapshots?: boolean;
  // Declare only when versioned reads resolve Pack identity from the selected commit.
  supportsVersionedPackResolution?: boolean;
  readSnapshot: (workflowId: string, packId: string, version?: PackScriptVersion) => Promise<PackScriptSnapshot>;
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
      const version = req.query.version;
      if (version !== undefined && version !== 'current' && version !== 'draft') {
        res.status(400).json({ error: 'Invalid snapshot version', code: 'INVALID_SNAPSHOT_VERSION' }); return;
      }
      const selectedVersion: PackScriptVersion | undefined = version === 'current' || version === 'draft' ? version : undefined;
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
      // The host must provide an explicit policy for the entire returned Pack.
      // An absent policy fails closed, even when workflow edit access is present.
      if (!deps.authorizePackRead) { res.status(503).json({ error: 'Pack authorization unavailable', code: 'SERVICE_UNAVAILABLE' }); return; }
      if (selectedVersion === undefined && await deps.authorizePackRead(row.workflow_id, packId, principal.actorId) !== true) {
        res.status(403).json({ error: 'Forbidden', code: 'FORBIDDEN' }); return;
      }
      if (selectedVersion !== undefined && (deps.supportsVersionedSnapshots !== true || deps.supportsVersionedPackResolution !== true)) {
        res.status(503).json({ error: 'Snapshot unavailable', code: 'SERVICE_UNAVAILABLE' }); return;
      }
      try {
        const snapshot = selectedVersion === undefined
          ? await deps.readSnapshot(row.workflow_id, packId)
          : await deps.readSnapshot(row.workflow_id, packId, selectedVersion);
        if (!snapshot || snapshot.workflowId !== row.workflow_id || !validId(snapshot.packId)
          || (selectedVersion === undefined ? snapshot.packId !== packId || snapshot.baseRef !== `origin/wf/${workflowId}` : !validSnapshotRef(snapshot.baseRef, workflowId, selectedVersion))) {
          res.status(503).json({ error: 'Snapshot unavailable', code: 'SERVICE_UNAVAILABLE' }); return;
        }
        // Versioned Packs can have historical identities different from the DB row.
        if (selectedVersion !== undefined && await deps.authorizePackRead(row.workflow_id, snapshot.packId, principal.actorId) !== true) {
          res.status(403).json({ error: 'Forbidden', code: 'FORBIDDEN' }); return;
        }
        res.json(snapshot);
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

function validSnapshotRef(ref: string, id: string, version: 'current' | 'draft'): boolean {
  if (version === 'draft') return ref === `origin/wf/${id}`;
  const prefix = `refs/tags/deploy/${id}/#`;
  return typeof ref === 'string' && ref.startsWith(prefix) && /^[1-9][0-9]*$/.test(ref.slice(prefix.length));
}
