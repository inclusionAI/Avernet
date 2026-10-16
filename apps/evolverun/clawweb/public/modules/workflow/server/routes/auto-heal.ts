/**
 * AutoHeal API routes.
 *
 *   POST /auto-heal/diagnose
 *   GET  /auto-heal/diagnoses/:diagnosisId
 *   POST /auto-heal/save      — save a fixed spec to git + DB (no deploy tag)
 *   POST /auto-heal/deploy    — deploy active spec from DB with git tag + history
 *   POST /auto-heal/apply     — DEPRECATED, returns 410 unless opt-in
 *   POST /auto-heal/run       — trigger a workflow run (unchanged)
 */
import { Router, type Request, type Response } from "express";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
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
  gitConfig: ClawFlowsGitConfig;
  gitAuthor: GitAuthor;
  triggerRun?: (workflowId: string, actorId: string) => Promise<string | null>;
}

function getActor(req: Request): string {
  return resolveWorkflowActorId(req) || String(req.headers["x-user-id"] ?? "").trim() || "unknown";
}

export function createAutoHealRouter(deps: AutoHealRouteDependencies): Router {
  const router = Router();
  const saveService = new AutoHealSaveService(
    deps.db,
    deps.workflowSpecRepo,
    deps.deployHistoryRepo,
    deps.botPermRepo,
    deps.gitConfig,
    deps.gitAuthor,
  );
  const deployService = new AutoHealDeployService(
    deps.db,
    deps.workflowSpecRepo,
    deps.deployHistoryRepo,
    deps.botPermRepo,
    deps.gitConfig,
    deps.gitAuthor,
    deps.triggerRun,
  );

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
        const { workflowId, packId, diagnosisId, note, autoRun } = req.body as {
          workflowId?: string;
          packId?: string | null;
          diagnosisId?: string | null;
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
        const result = await deployService.deploy({
          workflowId,
          packId: packId ?? null,
          diagnosisId: diagnosisId ?? null,
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
