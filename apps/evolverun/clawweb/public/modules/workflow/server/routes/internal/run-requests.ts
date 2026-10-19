import { Router } from 'express';
import type { createRepairTrialService } from '../../services/repair-trial-service.js';
/** Request read and startup feedback. Host mounts behind existing signature verification. */
export function createRunRequestsReadRouter(source: Pick<ReturnType<typeof createRepairTrialService>, 'resolveRunRequest' | 'reportStartFailure'>): Router {
  const router = Router();
  router.post('/reads/run-requests', async (req, res) => {
    const { requestId, botId, ownerId } = req.body ?? {};
    if (typeof requestId !== 'string' || !/^[a-f0-9-]{36}$/.test(requestId) || ![botId, ownerId].every(value => typeof value === 'string' && value.trim())) {
      res.status(400).json({ error: '运行请求身份无效' }); return;
    }
    try { res.json(await source.resolveRunRequest(requestId, { botId, ownerId })); }
    catch { res.status(409).json({ error: '运行请求身份或权限已变化，请核对原请求' }); }
  });
  router.post('/run-requests/failures', async (req, res) => {
    const { requestId, botId, ownerId, code, httpStatus } = req.body ?? {};
    if (typeof requestId !== 'string' || !/^[a-f0-9-]{36}$/.test(requestId)
      || ![botId, ownerId, code].every(value => typeof value === 'string' && value.trim())) {
      res.status(400).json({ error: '试跑失败反馈无效' }); return;
    }
    try { res.json(await source.reportStartFailure({ requestId, botId, ownerId, code, httpStatus })); }
    catch (error) {
      const invalid = (error as { code?: string }).code === 'INVALID_INPUT';
      const conflict = (error as { code?: string }).code === 'STATE_CONFLICT';
      res.status(invalid ? 400 : conflict ? 409 : 503).json({ error: invalid ? '试跑失败原因无效' : '试跑失败反馈未登记，请核对原请求' });
    }
  });
  return router;
}
