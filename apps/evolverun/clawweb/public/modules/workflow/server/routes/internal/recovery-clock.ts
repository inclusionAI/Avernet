import { Router } from 'express';
import type { FlowRunRepository } from '../../repositories/flow-run-repository.js';
import { isRecoveryClockCommand, RecoveryClockConflict, RecoveryRunNotFound } from '../../repositories/recovery-clock-repository.js';
import { asyncHandler } from '@avernet/clawweb-shared/server/middleware/async-handler';

/** Mounted inside the existing signature-protected internal runs router. */
export function createRecoveryClockRouter(repo: FlowRunRepository | null): Router {
  const router = Router();
  router.get('/:flowId/recovery-clock', asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ ok: false, error: 'Database not configured' }); return; }
    try {
      res.json({ clock: await repo.getRecoveryClock(String(req.params.flowId)) });
    } catch (error) {
      console.warn('[recovery-clock] Read failed:', error instanceof Error ? error.message : String(error));
      res.status(500).json({ ok: false, error: 'Recovery clock storage unavailable' });
    }
  }));
  router.put('/:flowId/recovery-clock', asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ ok: false, error: 'Database not configured' }); return; }
    if (!isRecoveryClockCommand(req.body)) {
      res.status(400).json({ ok: false, error: 'Invalid recovery clock command' }); return;
    }
    try {
      res.json({ ok: true, clock: await repo.putRecoveryClock(String(req.params.flowId), req.body) });
    } catch (error) {
      if (error instanceof RecoveryClockConflict) { res.status(409).json({ ok: false, error: error.message }); return; }
      if (error instanceof RecoveryRunNotFound) { res.status(404).json({ ok: false, error: error.message }); return; }
      console.warn('[recovery-clock] Write failed:', error instanceof Error ? error.message : String(error));
      res.status(500).json({ ok: false, error: 'Recovery clock storage unavailable' });
    }
  }));
  return router;
}
