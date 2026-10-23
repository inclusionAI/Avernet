/**
 * AutoHealDeployService — orchestrate the "deploy" stage of auto-heal.
 *
 * Flow:
 *   1. Re-read the latest spec from DB.
 *   2. Check edit permission.
 *   3. Compute version and deployNumber (MAX from DB and git tags + 1).
 *   4. Clone clawflows to a temp directory.
 *   5. Write the spec YAML + manifest.
 *   6. Commit + annotated tag + push to wf/{workflowId}.
 *   7. Insert/activate workflow_deploy_history.
 *   8. Optionally trigger a new flow run.
 *
 * The deploy writes an immutable git tag and a deploy_history row with
 * action="deploy", matching ClawMind semantics.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { withGitOperation } from "@avernet/clawweb-shared/server/services/git-execution";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { stringify as stringifyYaml, parse as parseYaml } from "yaml";
import { normalizeWorkflowSpec, validateWorkflowSemantics, type WorkflowSpec } from "../workflow.js";
import { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../repositories/workflow-deploy-history-repository.js";
import {
  cloneClawflows,
  commitAndPushDeploy,
  createTempRepo,
  ensurePackManifest,
  getMaxDeployNumberFromGitTags,
  removeTempRepo,
  writeWorkflowSpecToRepo,
  type ClawFlowsGitConfig,
  type GitAuthor,
} from "./clawflows-git-client.js";

export type AutoHealDeployInput = {
  workflowId: string;
  packId?: string | null;
  diagnosisId?: string | null;
  /**
   * Run being repaired. The post-deploy re-run must reach the Bot that ran this
   * source, so the target Bot is read from it rather than from autoHeal config.
   */
  sourceFlowId?: string | null;
  note?: string | null;
  autoRun?: boolean;
  actorId: string;
};

export type AutoHealDeployResult = {
  ok: boolean;
  deployed: boolean;
  workflowId: string;
  packId: string;
  deployNumber: number;
  version: number;
  tagName: string;
  /** BaaS message id from the post-deploy re-run, not a ClawMind flow id. */
  messageId?: string | null;
};

function isReservedWorkflowId(id: string): boolean {
  return /^__.+__$/.test(id);
}

/**
 * Deterministic git tag name for a deploy number.
 *
 * Must stay in sync with `commitAndPushDeploy` in clawflows-git-client.ts.
 * Used to satisfy the NOT NULL constraint on `workflow_deploy_history.tag_name`
 * when reserving the deploy row, before the git push reveals the real tag.
 */
function buildDeployTagName(workflowId: string, deployNumber: number): string {
  return `deploy/${workflowId}/#${deployNumber}`;
}

function makeError(status: number, error: string, message: string): Error & { status: number; error: string } {
  const err = new Error(message) as Error & { status: number; error: string };
  err.status = status;
  err.error = error;
  return err;
}

export class AutoHealDeployService {
  constructor(
    private db: IDatabase,
    private specRepo: WorkflowSpecRepository,
    private deployHistoryRepo: WorkflowDeployHistoryRepository,
    private botPermRepo: BotWorkflowPermissionRepository | null,
    private gitConfig: ClawFlowsGitConfig,
    private author: GitAuthor,
    private triggerRun?: (
      workflowId: string,
      actorId: string,
      sourceFlowId?: string | null,
    ) => Promise<string | null>,
  ) {}

  async deploy(input: AutoHealDeployInput): Promise<AutoHealDeployResult> {
    const { workflowId, actorId } = input;

    if (!workflowId) {
      throw makeError(400, "Bad Request", "workflowId is required");
    }
    if (isReservedWorkflowId(workflowId)) {
      throw makeError(400, "Bad Request", "workflowId uses a reserved prefix");
    }

    if (!this.botPermRepo) {
      throw makeError(403, "Forbidden", "Permission service not available");
    }
    // Deploy (and optional auto-run) requires execute permission. Edit permission
    // implies execute in the auto-heal context, so we accept either.
    const [canExecute, canEdit] = await Promise.all([
      this.botPermRepo.hasExecutePermission(workflowId, actorId),
      this.botPermRepo.hasEditPermission(workflowId, actorId),
    ]);
    if (!canExecute && !canEdit) {
      throw makeError(403, "Forbidden", "No execute permission for this workflow");
    }

    const specRow = await this.specRepo.findByWorkflowId(workflowId);
    if (!specRow) {
      throw makeError(404, "Not Found", `Workflow "${workflowId}" not found`);
    }

    let spec: WorkflowSpec;
    try {
      const parsed = JSON.parse(specRow.spec_json) as Record<string, unknown>;
      const raw = typeof parsed.content === "string" ? parseYaml(parsed.content) : parsed;
      spec = normalizeWorkflowSpec(raw);
      validateWorkflowSemantics(spec);
    } catch (parseErr) {
      const msg = parseErr instanceof Error ? parseErr.message : String(parseErr);
      throw makeError(500, "Internal Server Error", `无法解析 workflow_specs 中的 YAML: ${msg}`);
    }

    if (spec.id !== workflowId) {
      throw makeError(400, "Bad Request", `Spec ID '${spec.id}' does not match workflowId '${workflowId}'`);
    }

    const packId = input.packId ?? specRow.pack_id ?? workflowId;
    // Reuse the canonical spec_json from workflow_specs; avoid a round-trip
    // through normalize/stringify which could introduce drift.
    const specJson = specRow.spec_json;

    const currentMaxVersion = await this.deployHistoryRepo.getLatestVersion(workflowId);
    const newVersion = currentMaxVersion + 1;

    // Reserve the deploy record first so deploy_number is allocated under DB
    // serialization. If git fails later we have an inactive orphan row that
    // can be audited; if git succeeds we activate it. This prevents duplicate
    // deploy numbers under concurrent requests and avoids pushing a git tag
    // before the DB is updated.
    let reservedDeployNumber = 0;
    try {
      reservedDeployNumber = await this.db.transaction(async (tx) => {
        const txDeployRepo = new WorkflowDeployHistoryRepository(tx);
        const dbMax = await txDeployRepo.getMaxDeployNumber(packId, workflowId);
        const next = dbMax + 1;
        await txDeployRepo.insert({
          packId,
          workflowId,
          deployNumber: next,
          version: newVersion,
          // The real tag name is only known after the git push, but
          // workflow_deploy_history.tag_name is NOT NULL on managed MySQL. Use
          // the deterministic tag this deploy will produce; the activation step
          // overwrites it with the authoritative value from gitResult.
          tagName: buildDeployTagName(workflowId, next),
          action: "deploy",
          specJson,
          note: input.note ?? "auto-heal deploy",
          botId: actorId,
          ownerId: actorId,
          isActive: false,
        });
        return next;
      });
    } catch (dbErr) {
      const msg = dbErr instanceof Error ? dbErr.message : String(dbErr);
      throw makeError(500, "Internal Server Error", `无法预留部署记录: ${msg}`);
    }

    const gitResult = await withGitOperation({ workflowId, operation: 'autoheal_deploy' }, async () => {
      let repoDir: string;
      try {
        repoDir = await createTempRepo();
      } catch (mkErr) {
        const msg = mkErr instanceof Error ? mkErr.message : String(mkErr);
        throw makeError(500, "Internal Server Error", `无法创建临时 git 目录: ${msg}`);
      }

      let gitResult: { branch: string; tagName: string; commit: string; deployNumber: number } | undefined;
      try {
        await cloneClawflows(repoDir, this.gitConfig);
        await writeWorkflowSpecToRepo(repoDir, packId, workflowId, spec);
        await ensurePackManifest(repoDir, packId, workflowId);

        // Fall back to git tags if there are tags not yet reflected in the DB
        // (e.g. manual/external deploys). This is a safety net, not the primary
        // allocation mechanism.
        const gitMaxDeployNumber = await getMaxDeployNumberFromGitTags(repoDir, workflowId);
        const deployNumber = Math.max(reservedDeployNumber, gitMaxDeployNumber) + 1;

        gitResult = await commitAndPushDeploy(
          repoDir,
          workflowId,
          deployNumber,
          newVersion,
          input.note ?? "auto-heal deploy",
          this.author,
        );
      } finally {
        await removeTempRepo(repoDir);
      }
      return gitResult;
    });

    if (!gitResult) {
      throw makeError(500, "Internal Server Error", "Git deploy produced no result");
    }

    try {
      await this.db.transaction(async (tx) => {
        const txDeployRepo = new WorkflowDeployHistoryRepository(tx);
        if (gitResult!.deployNumber === reservedDeployNumber) {
          // Common path: the reserved number was usable. Activate it with the tag.
          await tx.exec(
            `UPDATE workflow_deploy_history
             SET tag_name = ?, is_active = 1, gmt_modified = ?
             WHERE workflow_id = ? AND version = ? AND deploy_number = ?`,
            [gitResult!.tagName, tx.dialect.now(), workflowId, newVersion, reservedDeployNumber],
          );
        } else {
          // Git had a newer max (e.g. external/manual deploy). Keep the reserved
          // row as an inactive placeholder and insert the real active record.
          await txDeployRepo.insert({
            packId,
            workflowId,
            deployNumber: gitResult!.deployNumber,
            version: newVersion,
            tagName: gitResult!.tagName,
            action: "deploy",
            specJson,
            note: input.note ?? "auto-heal deploy",
            botId: actorId,
            ownerId: actorId,
            isActive: true,
          });
        }
        await tx.exec(
          `UPDATE workflow_deploy_history SET is_active = 0 WHERE workflow_id = ? AND is_active = 1 AND deploy_number <> ?`,
          [workflowId, gitResult!.deployNumber],
        );
        await tx.exec(
          "UPDATE workflow_specs SET version = ?, gmt_modified = ? WHERE workflow_id = ?",
          [newVersion, tx.dialect.now(), workflowId],
        );
      });
    } catch (dbErr) {
      const msg = dbErr instanceof Error ? dbErr.message : String(dbErr);
      console.error(`[auto-heal] deploy git tag ${gitResult.tagName} pushed but DB write failed for ${workflowId}: ${msg}`);
      throw makeError(500, "Internal Server Error", `部署 tag 已推送，但部署记录写入失败: ${msg}`);
    }

    let messageId: string | null | undefined;
    if (input.autoRun !== false && this.triggerRun) {
      try {
        messageId = await this.triggerRun(workflowId, actorId, input.sourceFlowId ?? null);
      } catch (runErr) {
        const msg = runErr instanceof Error ? runErr.message : String(runErr);
        console.warn(`[auto-heal] deploy succeeded but auto-run failed for ${workflowId}: ${msg}`);
      }
    }

    return {
      ok: true,
      deployed: true,
      workflowId,
      packId,
      deployNumber: gitResult.deployNumber,
      version: newVersion,
      tagName: gitResult.tagName,
      messageId: messageId ?? null,
    };
  }
}
