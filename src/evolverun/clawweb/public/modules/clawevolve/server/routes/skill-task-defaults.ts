import { Router } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { OcbSpacePort } from "../internal/module-api.js";
import type { SkillAssetRepository } from "../repositories/skill-asset-repository.js";
import type { StageSkillRepository } from "../repositories/stage-skill-repository.js";
import { canReadSpaceRecord } from "../services/evolve/space-access.js";
import type { SpacePresentationPolicy } from "../services/evolve/space-presentation.js";
import { spaceRequestIdentity } from "./evolve-spaces.js";

export function createSkillTaskDefaultsRouter(input: {
  skills: SkillAssetRepository;
  stages: StageSkillRepository;
  spaces?: OcbSpacePort;
  policies: readonly SpacePresentationPolicy[];
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
    const policies = [...input.policies].sort((left, right) => Number(right.spaceId === asset.space_id) - Number(left.spaceId === asset.space_id));
    const implementation = policies.flatMap((policy) => available.filter((row) =>
      row.stage_skill_id === policy.diagnosePreprocessStageSkillId && row.space_id === policy.spaceId
      && row.space_type === "TEAM" && row.stage_key === "diagnose" && row.extension_mode === "preprocess"
      && row.status === "registered" && row.integration_test_status === "test_passed"
      && canReadSpaceRecord(row, identity.userId, spaces)).sort((a, b) => b.version_no - a.version_no))[0];
    res.json({
      assetId: asset.asset_id, botId: asset.bot_id, userId: asset.owner_user_id,
      diagnose: { taskType: "diagnose", goal: `诊断 ${asset.display_name} 在真实会话中的准确性、可靠性与任务完成情况。` },
      optimize: {
        taskType: "full", goal: `改进 ${asset.display_name} 的准确性与可靠性，保留已经确认的业务语义。`,
        stageExtensions: implementation ? { diagnose: { preprocess: { enabled: true, implementationId: implementation.implementation_id } } } : null,
        unavailableReason: implementation ? null : "尚无可用的已通过集成测试的加固 Stage，请检查空间权限和 Stage 登记状态。",
      },
    });
  }));
  return router;
}
