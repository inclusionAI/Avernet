/** Notification-only recovery adapter. Identity is supplied by the trusted host,
 * never by X-User-Id, cookies decoded here, or the legacy request.isAdmin flag. */
import { Router, type Request, type Response, type RequestHandler } from 'express';
import type { CallbackCompensationServiceApi } from '../services/callback-compensation/types.js';

export type CallbackCompensationPrincipal = { actorId: string; isAdmin: boolean };
export type CallbackCompensationPrincipalResolver = (request: Request) => Promise<CallbackCompensationPrincipal | null>;

type Service = Pick<CallbackCompensationServiceApi, 'capabilities' | 'preview' | 'create' | 'get' | 'stop'>;
const base = '/:workflowId/callback-compensations';

/** Run before generic CORS and API logging. Exact origins come from host config;
 * the incoming Host/forwarded headers never expand this allowlist. */
export function callbackCompensationSecurity(trustedOrigins: readonly string[]): RequestHandler {
  const origins = new Set(trustedOrigins.map(value => {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== value || url.hostname.includes('*') || url.username || url.password)
      throw new Error('Invalid callback compensation origin');
    return value;
  }));
  return (req, res, next) => {
    if (!/\/callback-compensations(?:\/|$)/i.test(req.path)) return next();
    res.locals.callbackCompensationSensitive = true;
    res.locals.packScriptsCorsHandled = true;
    res.set('Cache-Control', 'no-store');
    res.set('X-Content-Type-Options', 'nosniff');
    res.vary('Origin');
    const supplied = req.rawHeaders.flatMap((key, index, headers) =>
      index % 2 === 0 && key.toLowerCase() === 'origin' ? [headers[index + 1]] : []);
    const origin = supplied[0];
    const allowed = supplied.length === 1 && origins.has(origin);
    const write = req.method === 'POST';
    if (supplied.length > 1 || (origin !== undefined && !allowed) || (write && !allowed)
      || req.get('sec-fetch-site') === 'cross-site' && !allowed) {
      res.status(403).json({ code: 'ORIGIN_NOT_ALLOWED', message: 'Origin not allowed' }); return;
    }
    if (allowed) {
      res.set('Access-Control-Allow-Origin', origin);
      res.set('Access-Control-Allow-Credentials', 'true');
    }
    if (req.method === 'OPTIONS') {
      const method = req.get('access-control-request-method');
      const headers = (req.get('access-control-request-headers') ?? '').toLowerCase().split(',').map(s => s.trim()).filter(Boolean);
      const allowedHeaders = ['content-type', 'authorization', 'x-user-id', 'x-user-name', 'x-callback-compensation-csrf'];
      if (!allowed || !['GET', 'POST'].includes(method ?? '') || headers.some(h => !allowedHeaders.includes(h))) {
        res.status(403).json({ code: 'CSRF_REJECTED', message: 'Preflight rejected' }); return;
      }
      res.set('Access-Control-Allow-Methods', 'GET, POST');
      res.set('Access-Control-Allow-Headers', allowedHeaders.join(', '));
      res.vary('Access-Control-Request-Method'); res.vary('Access-Control-Request-Headers');
      res.status(204).end(); return;
    }
    if (write && (req.get('x-callback-compensation-csrf') !== '1' || !req.is('application/json'))) {
      res.status(403).json({ code: 'CSRF_REJECTED', message: 'JSON and CSRF header required' }); return;
    }
    next();
  };
}

function body(req: Request, keys: string[]): Record<string, unknown> | null {
  const value: unknown = req.body;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (Object.keys(value).some(key => !keys.includes(key))) return null;
  return value as Record<string, unknown>;
}
function inputError(res: Response) { res.status(400).json({ code: 'INVALID_INPUT', message: 'Invalid compensation request' }); }

export function createCallbackCompensationsRouter(options: {
  service: Service; principal: CallbackCompensationPrincipalResolver;
}): Router {
  const router = Router();
  const handle = (run: (req: Request, res: Response, actorId: string, workflowId: string) => Promise<void>): RequestHandler =>
    async (req, res) => {
      res.set('Cache-Control', 'no-store');
      if (res.locals.callbackCompensationSensitive !== true) {
        res.status(503).json({ code: 'COMPENSATION_SECURITY_NOT_MOUNTED' }); return;
      }
      try {
        const actor = await options.principal(req);
        if (!actor?.actorId?.trim()) { res.status(401).json({ code: 'UNAUTHENTICATED' }); return; }
        if (actor.isAdmin !== true) { res.status(403).json({ code: 'COMPENSATION_ADMIN_REQUIRED' }); return; }
        const workflowId = String(req.params.workflowId);
        if (!workflowId || workflowId.length > 255 || /^__.*__$/.test(workflowId)) { inputError(res); return; }
        await run(req, res, actor.actorId, workflowId);
      } catch (error) {
        // No raw exception/body: database/transport messages can contain secrets.
        const e = error as { code?: unknown; status?: unknown };
        const status = typeof e?.status === 'number' && [400, 403, 404, 409, 410, 413, 422, 503].includes(e.status) ? e.status : 503;
        const code = typeof e?.code === 'string' && /^[A-Z_]{3,64}$/.test(e.code) ? e.code : 'COMPENSATION_UNAVAILABLE';
        res.status(status).json({ code, message: 'Compensation request could not be completed' });
      }
    };
  router.get(`${base}/capabilities`, handle(async (_req, res, _actor, workflow) => {
    res.json(await options.service.capabilities(workflow));
  }));
  router.post(`${base}/preview`, handle(async (req, res, actor, workflow) => {
    const value = body(req, ['configId', 'from', 'to', 'flowIds']);
    if (!value || typeof value.configId !== 'string' || (value.from !== undefined && typeof value.from !== 'string') || (value.to !== undefined && typeof value.to !== 'string')
      || value.flowIds !== undefined && (!Array.isArray(value.flowIds) || value.flowIds.some(id => typeof id !== 'string'))) {
      inputError(res); return;
    }
    res.json(await options.service.preview(workflow, actor, value as { configId: string; from: string; to: string; flowIds?: string[] }));
  }));
  router.post(base, handle(async (req, res, actor, workflow) => {
    const value = body(req, ['previewId', 'itemIds', 'reason', 'requestKey']);
    if (!value || typeof value.previewId !== 'string' || !Array.isArray(value.itemIds)
      || value.itemIds.some(id => typeof id !== 'string') || typeof value.reason !== 'string' || typeof value.requestKey !== 'string') {
      inputError(res); return;
    }
    const batch = await options.service.create(workflow, actor, value as { previewId: string; itemIds: string[]; reason: string; requestKey: string });
    res.status(202).json(batch);
  }));
  router.get(`${base}/:batchId`, handle(async (req, res, actor, workflow) => {
    res.json(await options.service.get(workflow, actor, String(req.params.batchId)));
  }));
  router.post(`${base}/:batchId/stop`, handle(async (req, res, actor, workflow) => {
    if (!body(req, [])) { inputError(res); return; }
    res.json(await options.service.stop(workflow, actor, String(req.params.batchId)));
  }));
  return router;
}
