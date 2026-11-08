/**
 * AutoHeal API routes.
 *
 *   POST /auto-heal/diagnose
 *   GET  /auto-heal/diagnoses/:diagnosisId
 *   POST /auto-heal/save      — save a fixed spec to git + DB (no deploy tag)
 *   POST /auto-heal/deploy    — deploy active spec from DB with git tag + history
 *   POST /auto-heal/apply     — DEPRECATED, returns 410 unless opt-in
 *   POST /auto-heal/run       — run on the source Bot and lifecycle stage
 */
import { Router, type Request, type Response } from "express";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { sendIntervention } from "@avernet/clawevolve/server/services/baas-intervention";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import type { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";
import { createAutoHealRunRouter, type AutoHealRunSender } from "./auto-heal-run.js";
import { resolveAutoHealGitSettings } from "../config/auto-heal-git-config.js";
import { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../repositories/workflow-deploy-history-repository.js";
import { AutoHealSaveService } from "../services/auto-heal-save-service.js";
import { AutoHealDeployService } from "../services/auto-heal-deploy-service.js";
import type { ClawFlowsGitConfig, GitAuthor } from "../services/clawflows-git-client.js";

export interface AutoHealRouteDependencies {
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

function getActor(req: Request): string {
  return resolveWorkflowActorId(req) || String(req.headers["x-user-id"] ?? "").trim() || "unknown";
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
        const { workflowId, spec, packId, diagnosisId, note } = req.body as {
          workflowId?: string;
          spec?: unknown;
          packId?: string | null;
          diagnosisId?: string | null;
          note?: string | null;
        };
        if (!workflowId || !spec) {
          res.status(400).json({ error: "Bad Request", message: "workflowId and spec are required" });
          return;
        }
        const actor = getActor(req);
        if (!actor) {
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
          actorId: actor,
        });
        res.status(200).json(result);
      } catch (err) {
        const status = (err as { status?: number }).status ?? 500;
        const error = (err as { error?: string }).error ?? "Internal Server Error";
        const message = err instanceof Error ? err.message : String(err);
        res.status(status).json({ error, message });
      }
    }),
  );

  router.post(
    "/deploy",
    asyncHandler(async (req: Request, res: Response) => {
      try {
        const { workflowId, packId, diagnosisId, sourceFlowId, note, autoRun } = req.body as {
          workflowId?: string;
          packId?: string | null;
          diagnosisId?: string | null;
          /** Source run whose Bot the post-deploy re-run must target. */
          sourceFlowId?: string | null;
          note?: string | null;
          autoRun?: boolean;
        };
        if (!workflowId) {
          res.status(400).json({ error: "Bad Request", message: "workflowId is required" });
          return;
        }
        const actor = getActor(req);
        if (!actor) {
          res.status(401).json({ error: "Unauthorized", message: "Actor is required" });
          return;
        }
        const { gitConfig, gitAuthor } = await resolveAutoHealGitSettings(deps.db, deps.gitConfig, deps.gitAuthor);
        const deployService = new AutoHealDeployService(
          deps.db,
          deps.workflowSpecRepo,
          deps.deployHistoryRepo,
          deps.botPermRepo,
          gitConfig,
          gitAuthor,
          deps.triggerRun,
        );
        const result = await deployService.deploy({
          workflowId,
          packId: packId ?? null,
          diagnosisId: diagnosisId ?? null,
          sourceFlowId: sourceFlowId ?? null,
          note: note ?? null,
          autoRun,
          actorId: actor,
        });
        res.status(200).json(result);
      } catch (err) {
        const status = (err as { status?: number }).status ?? 500;
        const error = (err as { error?: string }).error ?? "Internal Server Error";
        const message = err instanceof Error ? err.message : String(err);
        res.status(status).json({ error, message });
      }
    }),
  );

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
