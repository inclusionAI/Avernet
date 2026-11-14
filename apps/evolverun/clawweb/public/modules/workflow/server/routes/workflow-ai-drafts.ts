import { Router, type Request, type Response, type RequestHandler } from 'express';
import { WorkflowAiDraftError, type WorkflowAiDraftService } from '../contracts/workflow-ai-draft.js';

/** Existing Pack CSRF contract, applied to this router's own routes only. */
const guard = (publicOrigins?: ReadonlySet<string>): RequestHandler => (req, res, next) => {
  res.setHeader('Cache-Control', 'no-store');
  const origin = req.get('origin');
  const allowedOrigin = publicOrigins ? (!origin || publicOrigins.has(origin))
    : (!origin || origin === `${req.protocol}://${req.get('host')}`);
  if ((!allowedOrigin) || req.get('sec-fetch-site') === 'cross-site'
    || (req.method !== 'GET' && req.method !== 'HEAD' && req.get('x-pack-version-csrf') !== '1')) {
    res.status(403).json({ code: 'CSRF_REJECTED', error: 'Forbidden' }); return;
  }
  next();
};
export function createWorkflowAiDraftRouter(options: {
  service: WorkflowAiDraftService; principal(request: Request): Promise<{ actorId: string } | null>;
  /** Host-owned exact public origins for TLS-terminating ingress. Never use forwarded headers. */
  publicOrigin?: string;
  publicOrigins?: readonly string[];
}): Router {
  const configuredOrigins = options.publicOrigins ?? (options.publicOrigin === undefined ? undefined : [options.publicOrigin]);
  let publicOrigins: Set<string> | undefined;
  if (configuredOrigins !== undefined) {
    try {
      publicOrigins = new Set(configuredOrigins.map(value => {
        const configured = new URL(value);
        if (!['https:', 'http:'].includes(configured.protocol) || configured.username || configured.password
          || configured.pathname !== '/' || configured.search || configured.hash
          || ![configured.origin, configured.origin + '/'].includes(value)) throw new Error('invalid');
        return configured.origin;
      }));
      if (!publicOrigins.size) throw new Error('empty');
    } catch { throw new Error('AI_DRAFT_PUBLIC_ORIGINS_INVALID'); }
  }
  const csrf = guard(publicOrigins);
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
