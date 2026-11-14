import { Router, type Request, type Response, type RequestHandler } from 'express';
import { WorkflowAiDraftError, type WorkflowAiDraftService } from '../contracts/workflow-ai-draft.js';

/** Existing Pack CSRF contract, applied to this router's own routes only. */
const guard = (publicOrigin?: string): RequestHandler => (req, res, next) => {
  res.setHeader('Cache-Control', 'no-store');
  const origin = req.get('origin');
  if ((origin && origin !== (publicOrigin ?? `${req.protocol}://${req.get('host')}`)) || req.get('sec-fetch-site') === 'cross-site'
    || (req.method !== 'GET' && req.method !== 'HEAD' && req.get('x-pack-version-csrf') !== '1')) {
    res.status(403).json({ code: 'CSRF_REJECTED', error: 'Forbidden' }); return;
  }
  next();
};
export function createWorkflowAiDraftRouter(options: {
  service: WorkflowAiDraftService; principal(request: Request): Promise<{ actorId: string } | null>;
  /** Host-owned canonical public origin for TLS-terminating ingress. Never a forwarded header. */
  publicOrigin?: string;
}): Router {
  let publicOrigin: string | undefined;
  if (options.publicOrigin !== undefined) {
    try {
      const configured = new URL(options.publicOrigin);
      if (!['https:', 'http:'].includes(configured.protocol) || configured.username || configured.password
        || configured.pathname !== '/' || configured.search || configured.hash
        || ![configured.origin, configured.origin + '/'].includes(options.publicOrigin)) throw new Error('invalid');
      publicOrigin = configured.origin;
    } catch { throw new Error('AI_DRAFT_PUBLIC_ORIGIN_INVALID'); }
  }
  const csrf = guard(publicOrigin);
  const router = Router();
  const handle = (run: (actorId: string, req: Request, res: Response) => Promise<void>): RequestHandler => async (req, res) => {
    try {
      const actor = await options.principal(req);
      if (!actor?.actorId?.trim()) { res.status(401).json({ code: 'UNAUTHORIZED', error: 'Unauthorized' }); return; }
      await run(actor.actorId, req, res);
    } catch (error) {
      if (error instanceof WorkflowAiDraftError) {
        const code = error.code;
        // STATE_CONFLICT may follow accepted dispatch; keep the browser's requestId recoverable.
        res.status(code === 'INVALID_INPUT' ? 400 : code === 'PAYLOAD_TOO_LARGE' ? 413 : code === 'FORBIDDEN' ? 403
          : code === 'NOT_FOUND' ? 404 : code === 'IDEMPOTENCY_CONFLICT' ? 409 : 503).json({ code, error: error.message });
      } else res.status(500).json({ code: 'INTERNAL_ERROR', error: 'AI draft request failed' });
    }
  };
  router.get('/:workflowId/ai-drafts/capabilities', csrf, handle(async (actorId, req, res) => {
    res.json(await options.service.capabilities(actorId, String(req.params.workflowId)));
  }));
  router.post('/:workflowId/ai-drafts', csrf, handle(async (actorId, req, res) => {
    res.status(202).json(await options.service.create(actorId, String(req.params.workflowId), req.body));
  }));
  router.get('/:workflowId/ai-drafts/:draftId', csrf, handle(async (actorId, req, res) => {
    res.json(await options.service.get(actorId, String(req.params.workflowId), String(req.params.draftId)));
  }));
  router.post('/:workflowId/ai-drafts/:draftId/cancel', csrf, handle(async (actorId, req, res) => {
    res.json(await options.service.cancel(actorId, String(req.params.workflowId), String(req.params.draftId)));
  }));
  return router;
}
