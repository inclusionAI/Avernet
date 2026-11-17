import { Router, type Request, type Response } from 'express';
import type { ResolveVerifiedAIStudioService } from '@avernet/clawweb-shared/server/middleware/aistudio-train-signature';
import { assertWatchRecord, assertWatchCas, assertWatchLeaseOwner, assertWatchDueCursor, isIdentity, isOwnerBotId, isEpochSeconds, isPositiveInteger,
  type WatchDueCursor, type AIStudioTrainWatchStore, type WatchRecord } from '../../contracts/aistudio-train.js';
import type { AIStudioRunTarget } from '../../repositories/aistudio-train-watch-repository.js';

type Dependencies = {
  store: (AIStudioTrainWatchStore & { isReady(): Promise<boolean> }) | null;
  findRunTarget: (flowId: string) => Promise<AIStudioRunTarget | null>;
  /** Must be supplied by a real service verifier, never untrusted user headers. Missing = 503. */
  resolveVerifiedService?: ResolveVerifiedAIStudioService;
};
const exact = (value: unknown, keys: string[]): value is Record<string, unknown> => !!value && typeof value === 'object'
  && !Array.isArray(value) && Object.keys(value).length === keys.length && keys.every(k => Object.hasOwn(value,k));
function integer(value: unknown): number | null {
  return typeof value === 'string' && /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) ? Number(value) : null;
}
function queryKeys(req: Request, allowed: string[]): boolean { return Object.keys(req.query).every(k => allowed.includes(k)); }
const fail = (res: Response, status: number, error: string) => res.status(status).json({ success:false,error });
/** Mount ONLY at /api/internal/aistudio-train-watches. Default-deny service port. */
export function createInternalAIStudioTrainWatchesRouter(deps: Dependencies): Router {
  const router = Router();
  router.use(async (req,res,next) => {
    res.set('Cache-Control','no-store');
    if (!deps.resolveVerifiedService || !deps.store) { fail(res,503,'AIStudio watch service unavailable'); return; }
    try {
      const principal = await deps.resolveVerifiedService(req);
      if (!principal?.serviceId?.trim()) { fail(res,401,'Service authentication required'); return; }
      if (!await deps.store.isReady()) { fail(res,503,'AIStudio watch store unavailable'); return; }
      next();
    } catch { fail(res,503,'AIStudio watch service unavailable'); }
  });
  async function owned(watch: WatchRecord): Promise<boolean> {
    const run = await deps.findRunTarget(watch.flowId);
    return !!run && run.flowId === watch.flowId && run.workflowId === watch.workflowId && run.ownerBotId === watch.ownerBotId;
  }
  router.post('/create',async (req,res) => {
    if (!exact(req.body,['watch'])) { fail(res,400,'Invalid create request'); return; }
    try { assertWatchRecord(req.body.watch); } catch { fail(res,400,'Invalid watch'); return; }
    const watch = req.body.watch;
    try {
      if (!await owned(watch)) { fail(res,403,'Watch target forbidden'); return; }
      // Repository may reuse only a confirmed same-target unique conflict, never a failed write.
      const created = await deps.store!.create(watch);
      res.json({success:true,data:{watch:created}});
    } catch { fail(res,503,'AIStudio watch persistence unavailable'); }
  });
  router.get('/find',async (req,res) => {
    const {flowId,nodeId,attempt} = req.query; const parsed = attempt === undefined ? undefined : integer(attempt);
    if (!queryKeys(req,['flowId','nodeId','attempt']) || !isIdentity(flowId) || !isIdentity(nodeId)
      || (parsed !== undefined && !isPositiveInteger(parsed))) { fail(res,400,'Invalid find request'); return; }
    try {
      const watch = await deps.store!.find(flowId,nodeId,parsed);
      if (watch && !await owned(watch)) { fail(res,403,'Watch target forbidden'); return; }
      res.json({success:true,data:{watch}});
    } catch { fail(res,503,'AIStudio watch store unavailable'); }
  });
  router.get('/due',async (req,res) => {
    const {ownerBotId} = req.query; const now = integer(req.query.now); const limit = integer(req.query.limit);
    let after: WatchDueCursor | undefined;
    const cursorKeys = ['afterManualPriority','afterNextPollAt','afterWatchId'];
    if (cursorKeys.some(key => req.query[key] !== undefined)) {
      const cursor = { manualPriority: integer(req.query.afterManualPriority), nextPollAt: integer(req.query.afterNextPollAt), watchId: req.query.afterWatchId };
      try { assertWatchDueCursor(cursor); after = cursor; } catch { fail(res,400,'Invalid due cursor'); return; }
    }
    if (!queryKeys(req,['ownerBotId','now','limit',...cursorKeys]) || !isOwnerBotId(ownerBotId) || !isEpochSeconds(now)
      || !isPositiveInteger(limit) || limit > 1000) { fail(res,400,'Invalid due request'); return; }
    try {
      const candidates = await deps.store!.findDue(ownerBotId,now,limit,after);
      const watches: WatchRecord[] = [];
      for (const watch of candidates) {
        if (watch.ownerBotId !== ownerBotId) { fail(res,403,'Watch target forbidden'); return; }
        const run = await deps.findRunTarget(watch.flowId);
        // SQL excludes pre-existing orphans before LIMIT. A concurrent deletion
        // after that snapshot is harmless: skip only a genuinely absent run.
        if (!run) continue;
        if (run.flowId !== watch.flowId || run.workflowId !== watch.workflowId || run.ownerBotId !== watch.ownerBotId) {
          fail(res,403,'Watch target forbidden'); return;
        }
        watches.push(watch);
      }
      res.json({success:true,data:{watches}});
    } catch { fail(res,503,'AIStudio watch store unavailable'); }
  });
  router.post('/cas',async (req,res) => {
    const hasGuard = req.body && Object.hasOwn(req.body,'leaseOwner');
    if (!exact(req.body,hasGuard ? ['expectedVersion','watch','leaseOwner'] : ['expectedVersion','watch'])) { fail(res,400,'Invalid CAS request'); return; }
    try { assertWatchCas(req.body.expectedVersion as number,req.body.watch as WatchRecord); if (hasGuard) assertWatchLeaseOwner(req.body.leaseOwner); }
    catch { fail(res,400,'Invalid watch version'); return; }
    const watch = req.body.watch as WatchRecord;
    try {
      if (!await owned(watch)) { fail(res,403,'Watch target forbidden'); return; }
      const updated = await deps.store!.compareAndSwap(req.body.expectedVersion as number,watch,hasGuard ? req.body.leaseOwner as string : undefined);
      res.json({success:true,data:{updated}});
    } catch { fail(res,503,'AIStudio watch persistence unavailable'); }
  });
  return router;
}
