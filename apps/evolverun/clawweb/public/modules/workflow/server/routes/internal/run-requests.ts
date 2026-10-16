import { Router } from 'express';
import type { createRepairTrialService } from '../../services/repair-trial-service.js';
/** Read-only metadata endpoint. Host mounts behind existing internal signature verification. */
export function createRunRequestsReadRouter(source: Pick<ReturnType<typeof createRepairTrialService>, 'resolveRunRequest'>): Router {
  const router = Router();
  router.post('/reads/run-requests', async (req, res) => {
    const { requestId, botId, ownerId } = req.body ?? {};
    if (typeof requestId !== 'string' || !/^[a-f0-9-]{36}$/.test(requestId) || ![botId, ownerId].every(value => typeof value === 'string' && value.trim())) {
      res.status(400).json({ error: '运行请求身份无效' }); return;
    }
    try { res.json(await source.resolveRunRequest(requestId, { botId, ownerId })); }
    catch { res.status(409).json({ error: '运行请求身份或权限已变化，请核对原请求' }); }
  });
  return router;
}
