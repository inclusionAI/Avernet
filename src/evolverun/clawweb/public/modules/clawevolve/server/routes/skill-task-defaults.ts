import type { AppConfigRepository } from "../repositories/app-config-repository.js";
import { SKILL_TASK_STAGE_BINDINGS_KEY, parseSkillTaskStageBindings, resolveSkillTaskStageBindings, type SkillTaskStageBinding } from "../services/evolve/skill-task-stage-bindings.js";
import { Router } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { SpaceDirectory } from "../contracts/space-directory.js";
import type { SkillAssetRepository } from "../repositories/skill-asset-repository.js";
import type { StageSkillRepository } from "../repositories/stage-skill-repository.js";
import { canReadSpaceRecord } from "../services/evolve/space-access.js";
import { resolveSkillTaskHostPreset } from "../services/evolve/evolve-extensions.js";
import { type EvolveExtension, type SkillTaskHostAction } from "../contracts/evolve-extension.js";
import { spaceRequestIdentity } from "./evolve-spaces.js";

export function createSkillTaskDefaultsRouter(input: {
  config: Pick<AppConfigRepository, "findByKey">;
  skills: SkillAssetRepository;
  stages: StageSkillRepository;
  spaces?: SpaceDirectory;
  hostExtensions?: readonly EvolveExtension[];
}): Router {
  const router = Router();
  router.get("/skill-assets/:assetId/task-defaults", asyncHandler(async (req, res) => {
    const identity = spaceRequestIdentity(req);
    if (!identity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const asset = await input.skills.findAsset(String(req.params.assetId));
    const spaces = await input.spaces?.listAccessibleSpaces({ identity }) ?? [];
    if (!asset || !canReadSpaceRecord(asset, identity.userId, spaces)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    const available = await input.stages.listImplementations(identity.userId, spaces.filter((space) => space.type === "TEAM").map((space) => space.id));
    const context = {
      actorUserId: identity.userId,
      targetSkill: {
        assetId: asset.asset_id,
        botId: asset.bot_id,
        ownerUserId: asset.owner_user_id,
        displayName: asset.display_name,
        spaceId: asset.space_id ?? null,
        spaceType: asset.space_type ?? null,
      },
      availableStageImplementations: available.map((row) => ({
        stageSkillId: row.stage_skill_id,
        implementationId: row.implementation_id,
        ownerUserId: row.owner_user_id,
        displayName: row.display_name,
        spaceId: row.space_id ?? null,
        spaceType: row.space_type ?? null,
        stage: row.stage_key,
        mode: row.extension_mode,
        versionNo: row.version_no,
        status: row.status,
        integrationTestStatus: row.integration_test_status,
      })),
    };
    const storedConfig = await input.config.findByKey(SKILL_TASK_STAGE_BINDINGS_KEY);
    let bindings: SkillTaskStageBinding[] = [];
    try {
      if (storedConfig?.enabled === 1) bindings = parseSkillTaskStageBindings(JSON.parse(storedConfig.config_json));
    } catch {
      res.status(503).json({ error: "Skill 任务默认绑定配置不合法，请联系管理员" }); return;
    }
    const extensions: EvolveExtension[] = [...(input.hostExtensions ?? []), {
      id: "clawevolve.skill-task-stage-bindings",
      resolveSkillTaskPreset: (context) => resolveSkillTaskStageBindings(bindings, context),
    }];
    const preset = (action: SkillTaskHostAction, taskType: "diagnose" | "hardening" | "full", goal: string) => {
      const contribution = resolveSkillTaskHostPreset(extensions, { ...context, action });
      return {
        taskType,
        goal,
        stageExtensions: contribution?.stageExtensions ?? null,
        unavailableReason: contribution?.unavailableReason ?? null,
        launchDescription: contribution?.launchDescription ?? null,
      };
    };
    res.json({
      assetId: asset.asset_id, botId: asset.bot_id, userId: asset.owner_user_id,
      diagnose: preset("diagnose", "diagnose", `诊断 ${asset.display_name} 在真实会话中的准确性、可靠性与任务完成情况。`),
      hardening: preset("hardening", "hardening", `检查并加固 ${asset.display_name}，保留已确认的业务语义，形成可审阅的新版本。`),
      optimize: preset("optimize", "full", `改进 ${asset.display_name} 的准确性与可靠性，保留已经确认的业务语义。`),
    });
  }));
  return router;
}
