/**
 * AutoHeal API routes.
 *
 *   POST /auto-heal/diagnose
 *   GET  /auto-heal/diagnoses/:diagnosisId
 *   POST /auto-heal/save      — save a fixed spec to the workflow database and Git Pack
 *   POST /auto-heal/editor-save-first-draft — save an Editor draft with mandatory snapshot preconditions
 *   POST /auto-heal/deploy    — deploy a confirmed saved candidate with git tag + history
 *   POST /auto-heal/editor-deploy — deploy with editor tree-preservation checks
 *   POST /auto-heal/apply     — DEPRECATED, returns 410 unless opt-in
 *   POST /auto-heal/run       — run on the source Bot and lifecycle stage
 */
import { Router, type Request, type Response } from "express";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { sendIntervention } from "@avernet/clawevolve/server/services/baas-intervention";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import type { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";
import { createAutoHealRunRouter, type AutoHealRunSender } from "./auto-heal-run.js";
import { resolveAutoHealGitSettings } from "../config/auto-heal-git-config.js";
import { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../repositories/workflow-deploy-history-repository.js";
import { AutoHealDeployService, type AuthorizeSnapshotDeploy } from "../services/auto-heal-deploy-service.js";
import { AutoHealSaveService, type AutoHealUnifiedSaveInput, type AutoHealUnifiedSaveResolver } from "../services/auto-heal-save-service.js";
import type { ClawFlowsGitConfig, GitAuthor } from "../services/clawflows-git-client.js";

export interface AutoHealRouteDependencies {
  /** Host-verified identity; absence fails closed for save and deploy. */
  resolvePrincipal?: (req: Request) => Promise<{ actorId: string } | null>;
  /** Trusted exact Pack deployment ACL; absent means both Editor deploy routes are unavailable. */
  authorizeSnapshotDeploy?: AuthorizeSnapshotDeploy;
  /** Trusted opt-in composition; absent means the separate unified protocol is unavailable. */
  resolveUnifiedSave?: AutoHealUnifiedSaveResolver;
  db: IDatabase;
  workflowSpecRepo: WorkflowSpecRepository;
  deployHistoryRepo: WorkflowDeployHistoryRepository;
  botPermRepo: BotWorkflowPermissionRepository | null;
  flowRunRepo?: FlowRunRepository | null;
  sendIntervention?: AutoHealRunSender;
  /** Supplies the slash command for source runs whose input_json records none. */
  facadeRepo?: FacadeBindingRepository | null;
  /** Bootstrap fallbacks; enabled DB Git settings are resolved per request. */
  gitConfig: ClawFlowsGitConfig;
  gitAuthor: GitAuthor;
  triggerRun?: (workflowId: string, actorId: string) => Promise<string | null>;
}

function safeStatus(status: unknown): number {
  return typeof status === "number" && Number.isInteger(status) && status >= 400 && status <= 599 ? status : 500;
}

export function createAutoHealRouter(deps: AutoHealRouteDependencies): Router {
  const router = Router();
  router.use(createAutoHealRunRouter({
    flowRunRepo: deps.flowRunRepo ?? null,
    botPermRepo: deps.botPermRepo,
    sendIntervention: deps.sendIntervention ?? sendIntervention,
    facadeRepo: deps.facadeRepo ?? null,
  }));

  // Availability only, not a per-workflow authorization grant. Hosts opt in by
  // providing the trusted resolver and must enforce unified-save's documented
  // transport guards; the actual save reauthorizes the actor and workflow.
  router.get("/capabilities", asyncHandler(async (req: Request, res: Response) => {
    res.set("Cache-Control", "no-store");
    try {
      const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
      if (!actor || actor === "unknown") { res.status(401).json({ code: "AUTHENTICATION_REQUIRED" }); return; }
      res.json({ autoHealUnifiedSave: typeof deps.resolveUnifiedSave === "function" ? "v1" : false });
    } catch {
      res.status(500).json({ code: "CAPABILITIES_UNAVAILABLE" });
    }
  }));

  router.post(
    "/save",
    asyncHandler(async (req: Request, res: Response) => {
      try {
        const { workflowId, spec, packId, diagnosisId, note, requestId, sourceCommit, expectedSavedHead, createOnly, expectedDraftAbsent, expectedActiveDeployNumber, expectedActiveToken } = req.body as {
          workflowId?: string;
          createOnly?: boolean; expectedDraftAbsent?: true; expectedActiveDeployNumber?: number; expectedActiveToken?: string; requestId?: string; sourceCommit?: string; expectedSavedHead?: string | null;
          spec?: unknown;
          packId?: string | null;
          diagnosisId?: string | null;
          note?: string | null;
        };
        if (!workflowId || !spec) {
          res.status(400).json({ error: "Bad Request", message: "workflowId and spec are required" });
          return;
        }
        if (expectedDraftAbsent !== undefined && (expectedDraftAbsent !== true || createOnly !== undefined)) {
          res.status(400).json({ error: "Bad Request", code: "INVALID_REQUEST", message: "expectedDraftAbsent must be true and cannot be combined with createOnly" });
          return;
        }
        if (expectedActiveDeployNumber !== undefined && (expectedDraftAbsent !== true || !Number.isSafeInteger(expectedActiveDeployNumber) || expectedActiveDeployNumber <= 0)) {
          res.status(400).json({ error: "Bad Request", code: "INVALID_REQUEST", message: "expectedActiveDeployNumber requires a positive integer and expectedDraftAbsent=true" });
          return;
        }
        if (expectedDraftAbsent === true && expectedActiveDeployNumber === undefined) {
          res.status(400).json({ error: "Bad Request", code: "INVALID_REQUEST", message: "expectedActiveDeployNumber is required" });
          return;
        }
        if (expectedActiveToken !== undefined && (expectedDraftAbsent !== true || expectedActiveDeployNumber === undefined
          || typeof expectedActiveToken !== "string" || !/^[0-9a-f]{64}$/i.test(expectedActiveToken))) {
          res.status(400).json({ error: "Bad Request", code: "INVALID_REQUEST", message: "expectedActiveToken requires expectedDraftAbsent, expectedActiveDeployNumber and a 64-character hex token" });
          return;
        }
        const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
        if (!actor || actor === "unknown") {
          res.status(401).json({ error: "Unauthorized", message: "Actor is required" });
          return;
        }
        // Every save requires Git after the database commit.
        const settings = await resolveAutoHealGitSettings(deps.db, deps.gitConfig, deps.gitAuthor);
        const saveService = new AutoHealSaveService(
          deps.db,
          deps.workflowSpecRepo,
          deps.deployHistoryRepo,
          deps.botPermRepo,
          settings.gitConfig,
          settings.gitAuthor,
        );
        const result = await saveService.save({
          workflowId,
          spec: spec as never,
          packId: packId ?? null,
          diagnosisId: diagnosisId ?? null,
          note: note ?? null,
          actorId: actor, requestId, sourceCommit, expectedSavedHead, createOnly, expectedDraftAbsent, expectedActiveDeployNumber, expectedActiveToken,
        });
        res.status(200).json(result);
      } catch (err) {
        const failure = err as { status?: number; code?: string; requestId?: string; branch?: string; commit?: string };
        res.status(safeStatus(failure.status)).json({ error: "Save could not be completed", code: failure.code ?? "SAVE_FAILED",
          requestId: failure.requestId, branch: failure.branch, commit: failure.commit });
      }
    }),
  );

  // Deliberately separate from /save: older servers must not silently ignore
  // the caller's baselines. Host must authenticate, enforce CSRF/origin,
  // reject duplicate envelope keys before parsing, limit the body, suppress body
  // logs, and apply timeouts/rate limits before enabling this protocol.
  router.post("/unified-save", asyncHandler(async (req: Request, res: Response) => {
    res.set("Cache-Control", "no-store");
    try {
      const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
      if (!actor || actor === "unknown") { res.status(401).json({ code: "AUTHENTICATION_REQUIRED" }); return; }
      const body = req.body;
      const fields = ["requestId", "workflowId", "packId", "sourceVersion", "sourceCommit", "expectedSavedHead", "expectedDraftToken", "specJson"];
      if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).length !== fields.length
        || Object.keys(body).some(key => !fields.includes(key)) || typeof body.workflowId !== "string"
        || typeof body.requestId !== "string" || typeof body.specJson !== "string") {
        res.status(400).json({ code: "INVALID_REQUEST" }); return;
      }
      const service = new AutoHealSaveService(deps.db, deps.workflowSpecRepo, deps.deployHistoryRepo, deps.botPermRepo);
      const result = await service.saveUnified({ ...body, actorId: actor } as AutoHealUnifiedSaveInput, deps.resolveUnifiedSave);
      res.status(result.outcome === "confirmed" ? 200 : 409).json(result);
    } catch (error) {
      const failure = error as { status?: number; code?: string };
      res.status(safeStatus(failure.status)).json({ code: failure.code ?? "UNIFIED_SAVE_FAILED" });
    }
  }));

  // This endpoint is a separate protocol: older /save handlers may silently
  // ignore snapshot preconditions, so Editor clients must not fall back to it.
  router.post("/editor-save-first-draft", asyncHandler(async (req: Request, res: Response) => {
    try {
      const { workflowId, spec, packId, diagnosisId, note, requestId, sourceCommit, expectedSavedHead,
        createOnly, expectedDraftAbsent, expectedActiveDeployNumber, expectedActiveToken } = req.body ?? {};
      if (typeof workflowId !== "string" || !workflowId.trim() || !spec
        || createOnly !== undefined || expectedDraftAbsent !== true
        || !Number.isSafeInteger(expectedActiveDeployNumber) || expectedActiveDeployNumber <= 0
        || typeof expectedActiveToken !== "string" || !/^[0-9a-f]{64}$/.test(expectedActiveToken)) {
        res.status(400).json({ error: "Bad Request", code: "INVALID_REQUEST", message: "Valid first-draft snapshot preconditions are required" });
        return;
      }
      const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
      if (!actor || actor === "unknown") {
        res.status(401).json({ error: "Unauthorized", message: "Actor is required" });
        return;
      }
      const service = new AutoHealSaveService(deps.db, deps.workflowSpecRepo, deps.deployHistoryRepo, deps.botPermRepo);
      const result = await service.saveEditorFirstDraft({ workflowId, spec, packId: packId ?? null, diagnosisId: diagnosisId ?? null,
        note: note ?? null, actorId: actor, requestId, sourceCommit, expectedSavedHead,
        expectedDraftAbsent, expectedActiveDeployNumber, expectedActiveToken });
      res.status(200).json(result);
    } catch (err) {
      const failure = err as { status?: number; code?: string; requestId?: string; branch?: string; commit?: string };
      res.status(safeStatus(failure.status)).json({ error: "Save could not be completed", code: failure.code ?? "SAVE_FAILED",
        requestId: failure.requestId, branch: failure.branch, commit: failure.commit });
    }
  }));

  // Errors (including 404/403) are never proof that a workflow ref is absent.
  router.get("/editor-deploy/snapshot-v2/state", asyncHandler(async (req: Request, res: Response) => {
    res.set("Cache-Control", "no-store");
    try {
      const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
      if (!actor || actor === "unknown") { res.status(401).json({ code: "AUTHENTICATION_REQUIRED" }); return; }
      if (typeof req.query.workflowId !== "string") { res.status(400).json({ code: "INVALID_REQUEST" }); return; }
      const { gitConfig, gitAuthor } = await resolveAutoHealGitSettings(deps.db, deps.gitConfig, deps.gitAuthor);
      const service = new AutoHealDeployService(deps.db, deps.workflowSpecRepo, deps.deployHistoryRepo, deps.botPermRepo, gitConfig, gitAuthor, undefined, deps.authorizeSnapshotDeploy);
      res.json(await service.editorDeploymentState(req.query.workflowId, actor));
    } catch (error) {
      const failure = error as { status?: number; code?: string };
      res.status(safeStatus(failure.status)).json({ code: failure.code ?? "DEPLOYMENT_STATE_UNAVAILABLE" });
    }
  }));

  const deploy = (editor: boolean, snapshot = false) => asyncHandler(async (req: Request, res: Response) => {
    try {
      const { workflowId, packId, candidateCommit, requestId, diagnosisId, sourceFlowId, note, autoRun, expectedDraftToken, snapshotProtocol, expectedActiveCommit, confirmedFileDiff } = req.body ?? {};
      if (typeof workflowId !== "string" || !workflowId) { res.status(400).json({ error: "Bad Request", message: "workflowId is required" }); return; }
      const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
      if (!actor || actor === "unknown") { res.status(401).json({ error: "Unauthorized" }); return; }
      if (editor && (snapshot ? snapshotProtocol !== 'saved-tree-v2' : snapshotProtocol !== undefined)) {
        res.status(400).json({ code: 'INVALID_REQUEST' }); return;
      }
      const { gitConfig, gitAuthor } = await resolveAutoHealGitSettings(deps.db, deps.gitConfig, deps.gitAuthor);
      const service = new AutoHealDeployService(deps.db, deps.workflowSpecRepo, deps.deployHistoryRepo,
        deps.botPermRepo, gitConfig, gitAuthor, deps.triggerRun, deps.authorizeSnapshotDeploy);
      const input = { workflowId, packId, candidateCommit, requestId, diagnosisId, sourceFlowId, note, autoRun, actorId: actor };
      res.json(await (editor ? service.editorDeploy({ ...input, expectedDraftToken, ...(snapshot ? { snapshotProtocol, expectedActiveCommit, confirmedFileDiff } : {}) }) : service.deploy(input)));
    } catch (error) {
      const e = error as { status?: number; code?: string; requestId?: string; branch?: string; commit?: string; tagName?: string };
      const branch = typeof e.branch === "string" && /^wf\/[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(e.branch) && !e.branch.includes("..") ? e.branch : undefined;
      const commit = typeof e.commit === "string" && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(e.commit) ? e.commit : undefined;
      const tagName = typeof e.tagName === "string" && /^deploy\/[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\/#\d+$/.test(e.tagName) && !e.tagName.includes("..") ? e.tagName : undefined;
      res.status(safeStatus(e.status)).json({ error: "Deployment could not be completed", code: e.code ?? "DEPLOYMENT_FAILED", requestId: e.requestId, branch, commit, tagName });
    }
  });
  router.post("/deploy", deploy(false));
  router.post("/editor-deploy", deploy(true));
  router.post("/editor-deploy/snapshot-v2", deploy(true, true));

  /**
   * Deprecated direct-write apply path.
   * Kept only as a safety hatch controlled by ALLOW_LEGACY_AUTO_HEAL_FIX.
   */
  router.post(
    "/apply",
    asyncHandler(async (_req: Request, res: Response) => {
      if (process.env.ALLOW_LEGACY_AUTO_HEAL_FIX !== "true") {
        res.status(410).json({
          error: "deprecated",
          message: "POST /api/auto-heal/apply 的直改 spec 路径已停用，请改用 POST /api/auto-heal/save + POST /api/auto-heal/deploy",
        });
        return;
      }
      res.status(501).json({ error: "Not Implemented", message: "Legacy apply path is disabled in this build" });
    }),
  );

  return router;
}
