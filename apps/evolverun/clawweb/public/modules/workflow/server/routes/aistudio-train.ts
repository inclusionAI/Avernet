import { captureApiError } from "./error-log-format.js";
import { Router, type Request, type Response } from 'express';
import { isIdentity, isOwnerBotId, isPositiveInteger, safeDetailUrl, toWatchDto, MANUAL_POLL_COOLDOWN_SECONDS,
  type WatchRecord } from '../contracts/aistudio-train.js';
import type { AIStudioRunTarget, AIStudioNodeTarget } from '../repositories/aistudio-train-watch-repository.js';

export type AIStudioTrainPrincipal = { actorId: string };
export type AIStudioTrainPublicAuth = {
  /** Host must verify IAM/session cryptographically; headers/cookie decoding are NOT verification. */
  resolveVerifiedPrincipal?: (request: Request) => Promise<AIStudioTrainPrincipal | null>;
  /** Full server-owned target is provided, including the exact composite bot:owner identity. */
  authorizeRunRead?: (principal: AIStudioTrainPrincipal, run: AIStudioRunTarget) => Promise<boolean>;
  /** Explicit owner/collaborator policy. No policy is a denial, not a bot-id-only projection. */
  authorizeOwnerOperation?: (principal: AIStudioTrainPrincipal, run: AIStudioRunTarget) => Promise<boolean>;
  authorizeWorkflowExecute?: (principal: AIStudioTrainPrincipal, run: AIStudioRunTarget) => Promise<boolean>;
};
type Dependencies = AIStudioTrainPublicAuth & {
  store: {
    find(flowId: string,nodeId: string,attempt?: number): Promise<WatchRecord | null>;
    isReady(): Promise<boolean>;
    requestManualPoll(expectedVersion: number,next: WatchRecord): Promise<boolean>;
  } | null;
  findRunTarget(flowId: string): Promise<AIStudioRunTarget | null>;
  findNodeTarget(flowId: string,nodeId: string): Promise<AIStudioNodeTarget | null>;
  /** Configured external origin, not an untrusted Host/X-Forwarded-Host header. Required for POST. */
  publicOrigin?: string;
  /** Authoritative host presentation URL; sanitized and projected only, never stored. */
  resolveDetailUrl?: (recordId: string) => string | null;
  now?: () => number;
};
const path = '/:flowId/nodes/:nodeId/aistudio-train';
const fail = (res: Response,status: number,error: string) => res.status(status).json({error});
const integer = (v: unknown) => typeof v === 'string' && /^\d+$/.test(v) && isPositiveInteger(Number(v)) ? Number(v) : null;

/** Exported host integration factory. Without a real principal/ACL provider the
 * public runner deliberately returns 503, including in local development. */
export function createAIStudioTrainRouter(deps: Dependencies): Router {
  const router = Router();
  let expectedOrigin: string | null = null;
  try { const u = new URL(deps.publicOrigin ?? ''); if (['http:','https:'].includes(u.protocol) && !u.username && !u.password) expectedOrigin=u.origin; } catch { /* Missing origin disables writes. */ }
  async function handle(req: Request,res: Response,manual: boolean): Promise<void> {
    res.set('Cache-Control','no-store');
    if (!deps.store || !deps.resolveVerifiedPrincipal || !deps.authorizeRunRead
      || (manual && (!deps.authorizeOwnerOperation || !deps.authorizeWorkflowExecute || !expectedOrigin))) {
      fail(res,503,'AIStudio watch authorization unavailable'); return;
    }
    try {
      const principal = await deps.resolveVerifiedPrincipal(req);
      if (!principal || typeof principal.actorId !== 'string' || !principal.actorId.trim() || principal.actorId.length > 255) {
        fail(res,401,'Authentication required'); return;
      }
      if (manual && (req.get('origin') !== expectedOrigin || req.get('sec-fetch-site') === 'cross-site')) {
        fail(res,403,'Same-origin request required'); return;
      }
      const {flowId,nodeId} = req.params;
      if (!isIdentity(flowId) || !isIdentity(nodeId) || Object.keys(req.query).some(k => !(!manual && k === 'attempt'))) {
        fail(res,400,'Invalid watch target'); return;
      }
      let attempt: number | undefined;
      if (manual) {
        if (!req.body || typeof req.body !== 'object' || Array.isArray(req.body) || Object.keys(req.body).length !== 1
          || !isPositiveInteger(req.body.attempt)) { fail(res,400,'Invalid attempt'); return; }
        attempt=req.body.attempt;
      } else if (req.query.attempt !== undefined) {
        const parsed=integer(req.query.attempt);
        if (parsed === null) { fail(res,400,'Invalid attempt'); return; }
        attempt=parsed;
      }
      if (!await deps.store.isReady()) { fail(res,503,'AIStudio watch store unavailable'); return; }
      const run=await deps.findRunTarget(flowId);
      if (!run) { fail(res,404,'Run not found'); return; }
      if (run.flowId !== flowId || !isIdentity(run.workflowId) || !isOwnerBotId(run.ownerBotId)) { fail(res,503,'Run owner unavailable'); return; }
      if (await deps.authorizeRunRead(principal,run) !== true) { fail(res,403,'Forbidden'); return; }
      const canOperate = async () => !!deps.authorizeOwnerOperation && !!deps.authorizeWorkflowExecute
        && await deps.authorizeOwnerOperation(principal,run) === true && await deps.authorizeWorkflowExecute(principal,run) === true;
      if (manual && !await canOperate()) { fail(res,403,'Forbidden'); return; }
      const node=await deps.findNodeTarget(flowId,nodeId);
      if (!node) { fail(res,404,'Node not found'); return; }
      if (node.executorType !== 'aistudio-train' || (attempt !== undefined && attempt !== node.attempt)) {
        fail(res,409,'Node type or attempt has changed'); return;
      }
      const watch=await deps.store.find(flowId,nodeId,attempt ?? node.attempt);
      if (!watch) { fail(res,404,'Watch not found'); return; }
      if (watch.flowId !== run.flowId || watch.nodeId !== nodeId || watch.ownerBotId !== run.ownerBotId || watch.workflowId !== run.workflowId) {
        fail(res,403,'Watch target forbidden'); return;
      }
      const now=deps.now?.() ?? Math.floor(Date.now()/1000);
      const detailUrl=safeDetailUrl(deps.resolveDetailUrl ? deps.resolveDetailUrl(watch.recordId) : watch.detailUrl);
      const responseWatch=(record: WatchRecord) => ({...toWatchDto(record,now),detailUrl});
      const dto=responseWatch(watch);
      const activeTarget = ['running','waiting'].includes(run.status) && node.status === 'waiting';
      if (!manual) {
        dto.manualPollAllowed = dto.manualPollAllowed && activeTarget && await canOperate();
        res.json({watch:dto}); return;
      }
      if (!activeTarget || watch.canceled || !['waiting','polling'].includes(watch.phase) || watch.timeoutAt <= now) {
        fail(res,409,'Watch is not active'); return;
      }
      if (watch.manualRequestedAt !== null) { fail(res,409,'Manual poll already pending'); return; }
      if (watch.lastManualRequestedAt !== null && now < watch.lastManualRequestedAt + MANUAL_POLL_COOLDOWN_SECONDS) {
        res.set('Retry-After',String(Math.max(1,watch.lastManualRequestedAt + MANUAL_POLL_COOLDOWN_SECONDS - now)));
        res.status(429).json({error:'Manual poll cooldown',manualPollRetryAt:dto.manualPollRetryAt}); return;
      }
      const next={...watch,manualRequestedAt:now,manualRequestedBy:principal.actorId,lastManualRequestedAt:now,version:watch.version+1};
      // CAS prevents lost watcher updates; repository also atomically fences newer attempts.
      if (!await deps.store.requestManualPoll(watch.version,next)) { fail(res,409,'Watch changed; refresh before retrying'); return; }
      res.status(202).json({ok:true,resolved:false,watch:responseWatch(next)});
    } catch (error) { console.error("[aistudio-train] request failed:", captureApiError(res, error)); fail(res,503,'AIStudio watch service unavailable'); }
  }
  router.get(path,(req,res) => handle(req,res,false));
  router.post(`${path}/poll-manual`,(req,res) => handle(req,res,true));
  return router;
}
