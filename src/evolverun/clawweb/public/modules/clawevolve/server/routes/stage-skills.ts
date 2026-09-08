import { randomUUID } from "node:crypto";
import { Router, type Request } from "express";
import multer from "multer";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { EvolveRepository } from "../repositories/evolve-repository.js";
import type { StageSkillRepository } from "../repositories/stage-skill-repository.js";
import type { SkillAssetRepository } from "../repositories/skill-asset-repository.js";
import type { OcbLocalSkillPort } from "../internal/module-api.js";
import type { ObjectStore } from "../services/object-storage/oss-object-store.js";
import { getArtifactBucket } from "../services/object-storage/oss-object-store.js";
import { getClawWebPublicBaseUrl } from "../env.js";
import type { EvolveDispatchInput } from "../services/evolve-dispatcher.js";
import {
  createStageDevelopmentPackage,
  inspectStageSkillPackage,
} from "../services/evolve/stage-development-package.js";
import {
  findOfficialStage,
  isStageExtensionMode,
  officialEvolveCatalog,
  stageRuntimeInputSchema,
  validateJsonSchema,
  type StageExtensionMode,
  type StageKey,
} from "../services/evolve/stage-catalog.js";
import { freezeSkillTarget } from "../services/evolve/skill-candidate.js";
import { skillPackageView } from "../services/evolve/skill-package-view.js";

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 5 * 1024 * 1024, files: 1 },
});

type StageSkillsRouterInput = {
  repo: StageSkillRepository;
  evolveRepo: EvolveRepository;
  artifactStore?: ObjectStore;
  skillAssetRepo: SkillAssetRepository;
  ocbLocalSkills: OcbLocalSkillPort | null;
  dispatch: (input: EvolveDispatchInput) => Promise<{ runId: string | null; sessionId: string | null; platformResponse: unknown }>;
};

function actor(req: Request): string | null {
  const value = String(req.header("X-User-Id") ?? "").trim();
  return value || null;
}

function implementationView(row: Awaited<ReturnType<StageSkillRepository["findImplementation"]>>) {
  if (!row) return null;
  const stage = findOfficialStage(row.stage_key);
  return {
    stageSkillId: row.stage_skill_id,
    implementationId: row.implementation_id,
    displayName: row.display_name,
    stage: row.stage_key,
    stageName: stage?.name ?? row.stage_key,
    mode: row.extension_mode,
    version: `v${row.version_no}`,
    status: row.status,
    packageSha256: row.package_sha256,
    staticValidation: JSON.parse(row.static_validation_json),
    integrationTestTaskId: row.integration_test_task_id,
    createdAt: row.gmt_create,
    updatedAt: row.gmt_modified,
  };
}

function stageCommand(taskId: string, stepId: string): string {
  return `/clawevolve-stage --task-id ${taskId} --step-id ${stepId} --clawweb-url ${getClawWebPublicBaseUrl()}`;
}

function packageObjectKey(ref: string): string {
  const prefix = `oss://${getArtifactBucket()}/`;
  if (!ref.startsWith(prefix)) throw new Error("Stage Skill 文件不属于当前文件存储");
  const key = ref.slice(prefix.length);
  if (!key || key.startsWith("/")
    || key.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error("Stage Skill 文件路径不合法");
  }
  return key;
}

export function createStageSkillsRouter(input: StageSkillsRouterInput): Router {
  const router = Router();

  router.get("/stage-catalog", (_req, res) => {
    res.json(officialEvolveCatalog);
  });

  router.get("/stage-skills/developer-package", asyncHandler(async (req, res) => {
    const stage = String(req.query.stage ?? "") as StageKey;
    const mode = String(req.query.mode ?? "") as StageExtensionMode;
    if (!findOfficialStage(stage) || !isStageExtensionMode(mode)) {
      res.status(400).json({ error: "请选择有效的 Stage 和接入方式" }); return;
    }
    const archive = await createStageDevelopmentPackage({ stage, mode });
    res.setHeader("Content-Type", "application/zip");
    res.setHeader("Content-Disposition", `attachment; filename="${stage}-${mode}-stage-skill.zip"`);
    res.send(archive);
  }));

  router.get("/stage-skills", asyncHandler(async (req, res) => {
    const owner = actor(req);
    if (!owner) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const rows = await input.repo.listImplementations(owner);
    res.json({ items: rows.map(implementationView) });
  }));

  router.get("/stage-skills/:implementationId", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const row = await input.repo.findImplementation(String(req.params.implementationId));
    if (!owner || !row || row.owner_user_id !== owner) {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    res.json(implementationView(row));
  }));

  router.get("/stage-skills/:implementationId/content", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const row = await input.repo.findImplementation(String(req.params.implementationId));
    if (!owner || !row || row.owner_user_id !== owner || row.status === "deleted") {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    if (!input.artifactStore) {
      res.status(503).json({ error: "Stage Skill 文件存储不可用" }); return;
    }
    const stored = await input.artifactStore.getObject(packageObjectKey(row.package_ref));
    res.json(await skillPackageView(stored.content, String(req.query.path ?? "")));
  }));

  router.post(
    "/stage-skills/uploads",
    upload.single("package"),
    asyncHandler(async (req, res) => {
      const owner = actor(req);
      if (!owner) { res.status(401).json({ error: "无法识别当前用户" }); return; }
      if (!input.artifactStore?.putObject) {
        res.status(503).json({ error: "Stage Skill 文件存储不可用" }); return;
      }
      const stage = String(req.body?.stage ?? "") as StageKey;
      const mode = String(req.body?.mode ?? "") as StageExtensionMode;
      if (!findOfficialStage(stage) || !isStageExtensionMode(mode) || !req.file?.buffer) {
        res.status(400).json({ error: "Stage、接入方式和 ZIP 均为必填项" }); return;
      }
      const inspection = await inspectStageSkillPackage(req.file.buffer, { stage, mode });
      if (inspection.status !== "passed") {
        res.status(422).json({ error: "静态校验未通过", validation: inspection }); return;
      }
      const requestedStageSkillId = String(req.body?.stageSkillId ?? "").trim();
      const existingStageSkill = requestedStageSkillId
        ? await input.repo.findLatestStageSkill(requestedStageSkillId)
        : null;
      if (requestedStageSkillId && (!existingStageSkill || existingStageSkill.owner_user_id !== owner
        || existingStageSkill.stage_key !== stage || existingStageSkill.extension_mode !== mode)) {
        res.status(404).json({ error: "要升级的 Stage Skill 不存在，或与当前 Stage/接入方式不一致" }); return;
      }
      const stageSkillId = existingStageSkill?.stage_skill_id
        ?? `STAGESKILL-${randomUUID().slice(0, 12).toUpperCase()}`;
      const displayName = existingStageSkill?.display_name ?? inspection.manifest!.display_name;
      const implementationId = `IMPL-${randomUUID().slice(0, 12).toUpperCase()}`;
      const versionNo = await input.repo.nextVersion(stageSkillId);
      const objectKey = `evolve/stage-implementations/${implementationId}/v${versionNo}/package.zip`;
      await input.artifactStore.putObject(objectKey, req.file.buffer, "application/zip");
      const created = await input.repo.createImplementation({
        stageSkillId,
        implementationId,
        ownerUserId: owner,
        displayName,
        stage,
        mode,
        versionNo,
        packageRef: `oss://${getArtifactBucket()}/${objectKey}`,
        packageSha256: inspection.packageSha256,
        staticValidation: inspection,
      });
      res.status(201).json(implementationView(created));
    }),
  );

  router.post("/stage-skills/:implementationId/register", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const current = await input.repo.findImplementation(String(req.params.implementationId));
    if (!owner || !current || current.owner_user_id !== owner) {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    const row = await input.repo.registerImplementation(current.implementation_id);
    if (!row || row.status !== "registered") {
      res.status(409).json({ error: "当前状态不能注册" }); return;
    }
    res.json(implementationView(row));
  }));

  router.delete("/stage-skills/:implementationId", asyncHandler(async (req, res) => {
    const owner = actor(req);
    if (!owner || !await input.repo.deleteImplementation(String(req.params.implementationId), owner)) {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    res.json({ deleted: true });
  }));

  router.post("/stage-skills/:implementationId/integration-tests", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const implementation = await input.repo.findImplementation(String(req.params.implementationId));
    const botId = String(req.body?.botId ?? "").trim();
    if (!owner || !implementation || implementation.owner_user_id !== owner) {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    if (implementation.status === "deleted") {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    if (!botId || !req.body || typeof req.body.caseInput !== "object" || Array.isArray(req.body.caseInput)) {
      res.status(400).json({ error: "请选择测试 Bot，并按当前 Stage 输入填写测试 Case" }); return;
    }
    const stage = findOfficialStage(implementation.stage_key);
    if (!stage) { res.status(409).json({ error: "Stage 定义不存在" }); return; }
    const caseInput = req.body.caseInput as Record<string, unknown>;
    const runtimeCaseInput = {
      ...caseInput,
      ...(implementation.stage_key === "diagnose" ? { session_source: { mode: "local" } } : {}),
    };
    const inputError = validateJsonSchema(
      stageRuntimeInputSchema(stage, implementation.extension_mode),
      {
        ...runtimeCaseInput,
        task: {
          task_id: "stage-test-preview",
          task_type: "stage_test",
          target_bot_id: botId,
        },
      },
    );
    if (inputError) {
      res.status(400).json({ error: `测试 Case 不符合当前 Stage 输入协议：${inputError}` }); return;
    }
    const taskId = `EVT-${randomUUID().slice(0, 12).toUpperCase()}`;
    const extensionStepId = `STEP-${randomUUID().slice(0, 12).toUpperCase()}`;
    const botEnv = String(req.body.botEnv ?? "");
    const targetSkillAssetId = String(req.body.targetSkillAssetId ?? "").trim();
    let targetSkill;
    if (targetSkillAssetId) {
      if (!input.ocbLocalSkills || !input.artifactStore?.putObject) {
        res.status(503).json({ error: "真实目标 Skill 测试服务不可用" }); return;
      }
      try {
        targetSkill = await freezeSkillTarget({
          taskId,
          ownerUserId: owner,
          botId,
          assetId: targetSkillAssetId,
          skillAssetRepo: input.skillAssetRepo,
          ocbLocalSkills: input.ocbLocalSkills,
          artifactStore: { putObject: input.artifactStore.putObject.bind(input.artifactStore) },
          identity: {
            userId: owner,
            authorization: req.header("Authorization") || undefined,
            cookie: req.header("Cookie") || undefined,
          },
        });
      } catch (error) {
        res.status(Number((error as { status?: unknown })?.status) || 502).json({
          error: `无法从 OCB 读取测试 Skill: ${error instanceof Error ? error.message : String(error)}`,
        }); return;
      }
    }
    const firstStepId = targetSkill ? `STEP-${randomUUID().slice(0, 12).toUpperCase()}` : extensionStepId;
    const firstStepType = targetSkill ? "skill_prepare" : "stage_extension";
    const firstCommand = targetSkill
      ? `/clawevolve-stage --action prepare --task-id ${taskId} --step-id ${firstStepId} --clawweb-url ${getClawWebPublicBaseUrl()}`
      : stageCommand(taskId, firstStepId);
    await input.evolveRepo.createTaskWithStep({
      task: {
        taskId,
        taskType: "stage_test",
        taskName: `${implementation.display_name} 集成测试`,
        userId: owner,
        botId,
        configJson: JSON.stringify({
          stageTest: true,
          botEnv,
          caseInput: runtimeCaseInput,
          stageExtension: {
            stage: implementation.stage_key,
            mode: implementation.extension_mode,
            implementationId: implementation.implementation_id,
          },
          ...(targetSkill ? { targetSkill } : {}),
        }),
        createdBy: owner,
      },
      step: { stepId: firstStepId, stepType: firstStepType, stepNo: 1, command: firstCommand },
    });
    if (targetSkill) {
      await input.evolveRepo.createStep({
        stepId: extensionStepId,
        taskId,
        stepType: "stage_extension",
        stepNo: 2,
        command: stageCommand(taskId, extensionStepId),
      });
    }
    await input.repo.createExtensionRun({
      stepId: extensionStepId,
      taskId,
      stage: implementation.stage_key,
      mode: implementation.extension_mode,
      implementationId: implementation.implementation_id,
      initialInput: runtimeCaseInput,
    });
    await input.repo.updateIntegrationTest(implementation.implementation_id, taskId, "testing");
    const runtime = await input.evolveRepo.resolveEvolveBotRuntime(owner, botId, botEnv);
    const firstStep = await input.evolveRepo.findStep(firstStepId);
    if (!firstStep) throw new Error("集成测试初始 Step 创建失败");
    try {
      const result = await input.dispatch({
        taskId, stepPk: firstStep.id, stepId: firstStepId,
        stepType: firstStepType, userId: owner, botId,
        command: firstCommand, mode: "message",
        callbackUrl: `${getClawWebPublicBaseUrl()}/api/evolve/internal/tasks/${taskId}/steps/${firstStepId}/bot-callback`,
        runtime,
        runtimeMaintenance: false,
      });
      await input.evolveRepo.markDispatched(firstStepId, result.runId, result.sessionId, result.platformResponse);
    } catch (error) {
      await input.evolveRepo.markDispatchFailed(firstStepId, error instanceof Error ? error.message : String(error));
      await input.repo.updateIntegrationTest(implementation.implementation_id, taskId, "test_failed");
    }
    res.status(201).json({ taskId, stepId: firstStepId, extensionStepId });
  }));

  return router;
}
