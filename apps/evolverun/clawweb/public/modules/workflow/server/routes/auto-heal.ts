/**
 * AutoHeal API routes.
 *
 *   POST /auto-heal/diagnose
 *   GET  /auto-heal/diagnoses/:diagnosisId
 *   POST /auto-heal/save      — save a fixed spec to git + DB (no deploy tag)
 *   POST /auto-heal/deploy    — deploy a confirmed saved candidate with git tag + history
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
import { AutoHealDeployService } from "../services/auto-heal-deploy-service.js";
import { AutoHealSaveService } from "../services/auto-heal-save-service.js";
import type { ClawFlowsGitConfig, GitAuthor } from "../services/clawflows-git-client.js";

export interface AutoHealRouteDependencies {
  /** Host-verified identity; absence fails closed for save and deploy. */
  resolvePrincipal?: (req: Request) => Promise<{ actorId: string } | null>;
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

  router.post(
    "/save",
    asyncHandler(async (req: Request, res: Response) => {
      try {
        const { workflowId, spec, packId, diagnosisId, note, requestId, sourceCommit, expectedSavedHead, createOnly } = req.body as {
          workflowId?: string;
          createOnly?: boolean; requestId?: string; sourceCommit?: string; expectedSavedHead?: string | null;
          spec?: unknown;
          packId?: string | null;
          diagnosisId?: string | null;
          note?: string | null;
        };
        if (!workflowId || !spec) {
          res.status(400).json({ error: "Bad Request", message: "workflowId and spec are required" });
          return;
        }
        const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
        if (!actor || actor === "unknown") {
          res.status(401).json({ error: "Unauthorized", message: "Actor is required" });
          return;
        }
        const { gitConfig, gitAuthor } = await resolveAutoHealGitSettings(deps.db, deps.gitConfig, deps.gitAuthor);
        const saveService = new AutoHealSaveService(
          deps.db,
          deps.workflowSpecRepo,
          deps.deployHistoryRepo,
          deps.botPermRepo,
          gitConfig,
          gitAuthor,
        );
        const result = await saveService.save({
          workflowId,
          spec: spec as never,
          packId: packId ?? null,
          diagnosisId: diagnosisId ?? null,
          note: note ?? null,
          actorId: actor, requestId, sourceCommit, expectedSavedHead, createOnly,
        });
        res.status(200).json(result);
      } catch (err) {
        const failure = err as { status?: number; code?: string; requestId?: string; branch?: string; commit?: string };
        res.status(safeStatus(failure.status)).json({ error: "Save could not be completed", code: failure.code ?? "SAVE_FAILED",
          requestId: failure.requestId, branch: failure.branch, commit: failure.commit });
      }
    }),
  );

  router.post("/deploy", asyncHandler(async (req: Request, res: Response) => {
    try {
      const { workflowId, packId, candidateCommit, requestId, diagnosisId, sourceFlowId, note, autoRun } = req.body ?? {};
      if (typeof workflowId !== "string" || !workflowId) { res.status(400).json({ error: "Bad Request", message: "workflowId is required" }); return; }
      const actor = (await deps.resolvePrincipal?.(req))?.actorId?.trim();
      if (!actor || actor === "unknown") { res.status(401).json({ error: "Unauthorized" }); return; }
      const { gitConfig, gitAuthor } = await resolveAutoHealGitSettings(deps.db, deps.gitConfig, deps.gitAuthor);
      const service = new AutoHealDeployService(deps.db, deps.workflowSpecRepo, deps.deployHistoryRepo,
        deps.botPermRepo, gitConfig, gitAuthor, deps.triggerRun);
      res.json(await service.deploy({ workflowId, packId, candidateCommit, requestId, diagnosisId, sourceFlowId, note, autoRun, actorId: actor }));
    } catch (error) {
      const e = error as { status?: number; code?: string; requestId?: string };
      res.status(safeStatus(e.status)).json({ error: "Deployment could not be completed", code: e.code ?? "DEPLOYMENT_FAILED", requestId: e.requestId });
    }
  }));

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
