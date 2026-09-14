import { randomUUID } from "node:crypto";
import { Router, type Request } from "express";
import multer from "multer";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { StageDevelopmentRow, StageSkillRepository } from "../repositories/stage-skill-repository.js";
import type { ObjectStore } from "../services/object-storage/oss-object-store.js";
import { getArtifactBucket } from "../services/object-storage/oss-object-store.js";
import {
  createStageDevelopmentPackage,
  inspectStageSkillPackage,
} from "../services/evolve/stage-development-package.js";
import {
  findOfficialStage,
  isStageExtensionMode,
  officialStageContracts,
  type StageExtensionMode,
  type StageKey,
} from "../services/evolve/stage-catalog.js";
import {
  botEvolutionFlow,
  skillEvolutionFlow,
  type EvolutionFlowKey,
} from "../services/evolve/evolution-flow.js";
import { skillPackageView } from "../services/evolve/skill-package-view.js";
import type { OcbSpacePort } from "../internal/module-api.js";
import { spaceRequestIdentity } from "./evolve-spaces.js";
import { canReadSpaceRecord, registrationSpace, spaceColumns, type SpaceOwnedRecord } from "../services/evolve/space-access.js";

const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 5 * 1024 * 1024, files: 1 },
});

type StageSkillsRouterInput = {
  repo: StageSkillRepository;
  artifactStore?: ObjectStore;
  ocbSpaces?: OcbSpacePort;
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
    spaceId: row.space_id ?? null, spaceType: row.space_type ?? null, spaceName: row.space_name ?? null,
    ownerId: row.owner_user_id,
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
    integrationTestStatus: row.integration_test_status,
    createdAt: row.gmt_create,
    updatedAt: row.gmt_modified,
  };
}

function developmentView(row: StageDevelopmentRow) {
  return {
    stageSkillId: row.stage_skill_id, ownerId: row.owner_user_id,
    spaceId: row.space_id ?? null, spaceType: row.space_type ?? null, spaceName: row.space_name ?? null,
    displayName: row.display_name, flow: row.flow_key, stage: row.stage_key,
    stageName: findOfficialStage(row.stage_key)?.name ?? row.stage_key,
    mode: row.extension_mode, createdAt: row.gmt_create, updatedAt: row.gmt_modified,
  };
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

  async function readable(row: SpaceOwnedRecord, req: Request): Promise<boolean> {
    const identity = spaceRequestIdentity(req);
    return !!identity && canReadSpaceRecord(row, identity.userId,
      row.space_id ? await input.ocbSpaces?.listAccessibleSpaces({ identity }) ?? [] : []);
  }

  router.get("/stage-catalog", (req, res) => {
    const taskType: "diagnose" | "full" = req.query.taskType === "diagnose" ? "diagnose" : "full";
    const inputMode = req.query.inputMode === "direct_goal" ? "direct_goal" : "diagnose_goal";
    const goal = req.query.hasGoal === "true" ? "provided" : "";
    const startInput = {
      taskType,
      inputMode,
      goal,
    };
    res.json({
      schemaVersion: "clawevolve.stage-development/v2",
      flows: [
        botEvolutionFlow.describe({ ...startInput, hasTargetSkill: false }),
        skillEvolutionFlow.describe({ ...startInput, taskType: "full", hasTargetSkill: true }),
      ],
      stages: officialStageContracts.stages,
    });
  });

  router.post("/stage-developments", asyncHandler(async (req, res) => {
    const owner = actor(req);
    if (!owner) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const stage = findOfficialStage(String(req.body?.stage ?? ""));
    const mode = req.body?.mode;
    const flow = req.body?.flow;
    const definition = flow === "bot_evolution" ? botEvolutionFlow
      : flow === "skill_evolution" ? skillEvolutionFlow : null;
    if (!stage || !isStageExtensionMode(mode) || !stage.extensionModes.includes(mode) || !definition) {
      res.status(400).json({ error: "请选择有效的流程、Stage 和开放位置" }); return;
    }
    const descriptor = definition.describe({ taskType: "full", inputMode: "diagnose_goal", goal: "", hasTargetSkill: flow === "skill_evolution" });
    if (!descriptor.stages.some((item) => item.key === stage.stage)) {
      res.status(400).json({ error: "当前流程不包含该 Stage" }); return;
    }
    const names = { preprocess: "前置处理", postprocess: "后置处理", replace: "整体替换" };
    const space = await registrationSpace(input.ocbSpaces, spaceRequestIdentity(req)!, req.body?.spaceId);
    const displayName = typeof req.body?.displayName === "string" ? req.body.displayName.trim() : "";
    if (displayName.length > 255) { res.status(400).json({ error: "名称不能超过 255 个字符" }); return; }
    const row = await input.repo.createDevelopment({
      stageSkillId: `STAGESKILL-${randomUUID().slice(0, 12).toUpperCase()}`,
      ownerUserId: owner, displayName: displayName || `${stage.name}${names[mode]}自定义实现`,
      ...spaceColumns(space),
      flow, stage: stage.stage, mode,
    });
    res.status(201).json(developmentView(row));
  }));

  router.get("/stage-developments", asyncHandler(async (req, res) => {
    const owner = actor(req);
    if (!owner) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const spaces = await input.ocbSpaces?.listAccessibleSpaces({ identity: spaceRequestIdentity(req)! }) ?? [];
    const rows = (await input.repo.listDevelopments(owner))
      .filter((row) => canReadSpaceRecord(row, owner, spaces));
    res.json({ items: rows.map(developmentView) });
  }));

  router.get("/stage-developments/:id", asyncHandler(async (req, res) => {
    const row = await input.repo.findDevelopment(String(req.params.id));
    if (!row || row.owner_user_id !== actor(req) || !await readable(row, req)) {
      res.status(404).json({ error: "开发记录不存在" }); return;
    }
    res.json(developmentView(row));
  }));

  router.delete("/stage-developments/:id", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const row = await input.repo.findDevelopment(String(req.params.id));
    if (!owner || !row || row.owner_user_id !== owner || !await readable(row, req)
      || !await input.repo.deleteDraft(row.stage_skill_id, owner)) {
      res.status(409).json({ error: "开发记录不存在或已有上传版本，请在详情中管理版本" }); return;
    }
    res.json({ deleted: true });
  }));

  router.get("/stage-skills/developer-package", asyncHandler(async (req, res) => {
    const developmentId = String(req.query.developmentId ?? "");
    if (developmentId) {
      const row = await input.repo.findDevelopment(developmentId);
      if (!row || row.owner_user_id !== actor(req) || !await readable(row, req)) {
        res.status(404).json({ error: "开发记录不存在" }); return;
      }
      const archive = await createStageDevelopmentPackage({ stage: row.stage_key, mode: row.extension_mode, flow: row.flow_key });
      res.type("application/zip").attachment(`${row.stage_key}-${row.extension_mode}.zip`).send(archive);
      return;
    }
    const stage = String(req.query.stage ?? "") as StageKey;
    const mode = String(req.query.mode ?? "") as StageExtensionMode;
    const flow = String(req.query.flow ?? "bot_evolution") as EvolutionFlowKey;
    if (!findOfficialStage(stage) || !isStageExtensionMode(mode)
      || !new Set<EvolutionFlowKey>(["bot_evolution", "skill_evolution"]).has(flow)) {
      res.status(400).json({ error: "请选择有效的流程、Stage 和接入方式" }); return;
    }
    const archive = await createStageDevelopmentPackage({ stage, mode, flow });
    res.setHeader("Content-Type", "application/zip");
    res.setHeader("Content-Disposition", `attachment; filename="${stage}-${mode}-stage-skill.zip"`);
    res.send(archive);
  }));

  router.get("/stage-skills", asyncHandler(async (req, res) => {
    const owner = actor(req);
    if (!owner) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const spaces = await input.ocbSpaces?.listAccessibleSpaces({ identity: spaceRequestIdentity(req)! }) ?? [];
    const search = String(req.query.search ?? "").trim().toLocaleLowerCase();
    const rows = (await input.repo.listImplementations(owner, spaces.filter((space) => space.type === "TEAM").map((space) => space.id)))
      .filter((row) => canReadSpaceRecord(row, owner, spaces)
        && (!search || `${row.display_name} ${row.stage_skill_id} ${row.space_name ?? ""}`.toLocaleLowerCase().includes(search)));
    res.json({ items: rows.map(implementationView) });
  }));

  router.get("/stage-skills/:implementationId", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const row = await input.repo.findImplementation(String(req.params.implementationId));
    if (!owner || !row || !await readable(row, req)) {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    res.json(implementationView(row));
  }));

  router.get("/stage-skills/:implementationId/content", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const row = await input.repo.findImplementation(String(req.params.implementationId));
    if (!owner || !row || !await readable(row, req) || row.status === "deleted") {
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
      const development = requestedStageSkillId
        ? await input.repo.findDevelopment(requestedStageSkillId) : null;
      const existingStageSkill = requestedStageSkillId
        ? await input.repo.findLatestStageSkill(requestedStageSkillId)
        : null;
      const binding = development ?? existingStageSkill;
      if (!requestedStageSkillId || !binding || binding.owner_user_id !== owner
        || !await readable(binding, req)
        || binding.stage_key !== stage || binding.extension_mode !== mode) {
        res.status(404).json({ error: "要升级的 Stage Skill 不存在，或与当前 Stage/接入方式不一致" }); return;
      }
      const stageSkillId = binding.stage_skill_id;
      const displayName = binding.display_name;
      const implementationId = `IMPL-${randomUUID().slice(0, 12).toUpperCase()}`;
      const versionNo = await input.repo.nextVersion(stageSkillId);
      const objectKey = `evolve/stage-implementations/${implementationId}/v${versionNo}/package.zip`;
      await input.artifactStore.putObject(objectKey, req.file.buffer, "application/zip");
      const created = await input.repo.createImplementation({
        stageSkillId,
        implementationId,
        ownerUserId: owner,
        spaceId: binding.space_id, spaceType: binding.space_type, spaceName: binding.space_name,
        displayName,
        stage,
        mode,
        versionNo,
        packageRef: `oss://${getArtifactBucket()}/${objectKey}`,
        packageSha256: inspection.packageSha256,
        // Freeze execution semantics only for this new upload. Neither package
        // fields nor later deployment changes can upgrade a historical version.
        staticValidation: {
          ...inspection,
          ...(development?.flow_key === "skill_evolution" && stage === "plan" && mode === "replace"
            ? { executionContract: "clawevolve.plan-business/v1" }
            : {}),
        },
      });
      res.status(201).json(implementationView(created));
    }),
  );

  router.post("/stage-skills/:implementationId/register", asyncHandler(async (req, res) => {
    const owner = actor(req);
    const current = await input.repo.findImplementation(String(req.params.implementationId));
    if (!owner || !current || current.owner_user_id !== owner || !await readable(current, req)) {
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
    const row = await input.repo.findImplementation(String(req.params.implementationId));
    if (!owner || !row || row.owner_user_id !== owner || !await readable(row, req)
      || !await input.repo.deleteImplementation(row.implementation_id, owner)) {
      res.status(404).json({ error: "Stage Skill 不存在" }); return;
    }
    res.json({ deleted: true });
  }));

  return router;
}
