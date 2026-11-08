/**
 * AutoHealSaveService — orchestrate the "save" stage of auto-heal.
 *
 * Flow:
 *   1. Validate request + spec.
 *   2. Resolve packId.
 *   3. Check edit permission.
 *   4. Clone clawflows to a temp directory.
 *   5. Write the normalized spec YAML + manifest.
 *   6. Commit + push to saved/{workflowId}.
 *   7. Upsert workflow_specs.
 *
 * This service does NOT write deploy_history (save is not a deploy) and does
 * NOT create a deploy tag.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { withGitOperation } from "@avernet/clawweb-shared/server/services/git-execution";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { stringify as stringifyYaml } from "yaml";
import { normalizeWorkflowSpec, validateWorkflowSemantics, type WorkflowSpec } from "../workflow.js";
import { validateSpec } from "../validators/workflow-validator.js";
import { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import type { WorkflowDeployHistoryRepository } from "../repositories/workflow-deploy-history-repository.js";
import {
  cloneClawflows,
  commitAndPushSaved,
  createTempRepo,
  ensurePackManifest,
  prepareBranchBase,
  pruneLegacyPackCopy,
  removeTempRepo,
  writeWorkflowSpecToRepo,
  type ClawFlowsGitConfig,
  type GitAuthor,
} from "./clawflows-git-client.js";

export type AutoHealSaveInput = {
  workflowId: string;
  spec: WorkflowSpec;
  packId?: string | null;
  diagnosisId?: string | null;
  note?: string | null;
  actorId: string;
};

export type AutoHealSaveResult = {
  ok: boolean;
  saved: boolean;
  workflowId: string;
  packId: string;
  branch: string;
  commit: string;
  version: number;
};

function isReservedWorkflowId(id: string): boolean {
  return /^__.+__$/.test(id);
}

function makeError(status: number, error: string, message: string): Error & { status: number; error: string } {
  const err = new Error(message) as Error & { status: number; error: string };
  err.status = status;
  err.error = error;
  return err;
}

export class AutoHealSaveService {
  constructor(
    private db: IDatabase,
    private specRepo: WorkflowSpecRepository,
    private deployHistoryRepo: WorkflowDeployHistoryRepository | null,
    private botPermRepo: BotWorkflowPermissionRepository | null,
    private gitConfig: ClawFlowsGitConfig,
    private author: GitAuthor,
  ) {}

  /**
   * Save a fixed spec: git snapshot on saved/{workflowId} + DB upsert.
   *
   * The operation is intentionally not transactional across git and DB.
   * If git succeeds and DB fails, we log loudly and return an error without
   * rolling back the git push — callers can re-save once DB recovers.
   */
  async save(input: AutoHealSaveInput): Promise<AutoHealSaveResult> {
    const { workflowId, spec, actorId } = input;

    if (!workflowId || !spec) {
      throw makeError(400, "Bad Request", "workflowId and spec are required");
    }
    if (isReservedWorkflowId(workflowId)) {
      throw makeError(400, "Bad Request", "workflowId uses a reserved prefix");
    }

    const validation = validateSpec(spec);
    if (!validation.valid || !validation.normalizedSpec) {
      throw makeError(
        400,
        "Validation Failed",
        `Spec validation failed: ${validation.issues.map((i) => `${i.path}: ${i.message}`).join("; ")}`,
      );
    }

    const normalized = normalizeWorkflowSpec(validation.normalizedSpec);
    validateWorkflowSemantics(normalized);

    if (normalized.id !== workflowId) {
      throw makeError(400, "Bad Request", `Spec ID '${normalized.id}' does not match workflowId '${workflowId}'`);
    }

    if (!this.botPermRepo) {
      throw makeError(403, "Forbidden", "Permission service not available");
    }
    const hasPermission = await this.botPermRepo.hasEditPermission(workflowId, actorId);
    if (!hasPermission) {
      throw makeError(403, "Forbidden", "No edit permission for this workflow");
    }

    const packId = await this.resolvePackId(workflowId, input.packId ?? null, normalized);

    const specJson = JSON.stringify({
      content: stringifyYaml(normalized, { lineWidth: 0 }),
      ...normalized,
    });

    const gitResult = await withGitOperation({ workflowId, operation: 'autoheal_save' }, async () => {
      let repoDir: string;
      try {
        repoDir = await createTempRepo();
      } catch (mkErr) {
        const msg = mkErr instanceof Error ? mkErr.message : String(mkErr);
        throw makeError(500, "Internal Server Error", `无法创建临时 git 目录: ${msg}`);
      }

      let gitResult: { branch: string; commit: string } | undefined;
      try {
        await cloneClawflows(repoDir, this.gitConfig);
        // Base the snapshot on the previous save/deploy content so Pack scripts and
        // assets are inherited instead of being dropped (see prepareBranchBase).
        await prepareBranchBase(repoDir, `saved/${workflowId}`, `wf/${workflowId}`);
        await writeWorkflowSpecToRepo(repoDir, packId, workflowId, normalized);
        await ensurePackManifest(repoDir, packId, workflowId);
        await pruneLegacyPackCopy(repoDir, packId);
        gitResult = await commitAndPushSaved(repoDir, workflowId, input.note ?? "auto-heal save", this.author);
      } finally {
        await removeTempRepo(repoDir);
      }
      return gitResult;
    });

    if (!gitResult) {
      throw makeError(500, "Internal Server Error", "Git save produced no result");
    }

    try {
      await this.specRepo.upsert(workflowId, packId, specJson, actorId);
    } catch (dbErr) {
      const msg = dbErr instanceof Error ? dbErr.message : String(dbErr);
      console.error(`[auto-heal] save git succeeded but DB upsert failed for ${workflowId}: ${msg}`);
      throw makeError(500, "Internal Server Error", `保存已成功推送到 git，但数据库写入失败: ${msg}`);
    }

    const version = await this.resolveVersion(workflowId);

    return {
      ok: true,
      saved: true,
      workflowId,
      packId,
      branch: gitResult.branch,
      commit: gitResult.commit,
      version,
    };
  }

  private async resolvePackId(
    workflowId: string,
    requestedPackId: string | null,
    spec: WorkflowSpec,
  ): Promise<string> {
    if (requestedPackId) return requestedPackId;
    if (typeof spec.packId === "string" && spec.packId) return spec.packId;

    const row = await this.specRepo.findByWorkflowId(workflowId);
    if (row?.pack_id) return row.pack_id;

    return workflowId;
  }

  private async resolveVersion(workflowId: string): Promise<number> {
    if (!this.deployHistoryRepo) return 1;
    return (await this.deployHistoryRepo.getLatestVersion(workflowId)) + 1;
  }
}
