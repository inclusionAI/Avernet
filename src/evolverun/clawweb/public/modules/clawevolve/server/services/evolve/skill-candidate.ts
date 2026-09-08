import type { OcbLocalSkillPort, OcbRequestIdentity } from "../../internal/module-api.js";
import type { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { getArtifactBucket, type ObjectStore } from "../object-storage/oss-object-store.js";

export type FrozenSkillTarget = {
  assetId: string;
  skillId: string;
  name: string;
  baseline: { ref: string; sha256: string };
  workspacePath: string;
  skillPath: string;
  candidate: {
    ref: string;
    artifact?: { ref: string; size: number; sha256: string; contentType: "application/zip" };
  };
};

function safeSkillDirectoryName(value: string): string {
  const normalized = value.trim();
  if (!/^[A-Za-z0-9._-]{1,128}$/.test(normalized)) {
    throw new Error("OCB Skill 标识不能安全映射为候选目录");
  }
  return normalized;
}

export async function freezeSkillTarget(input: {
  taskId: string;
  ownerUserId: string;
  botId: string;
  assetId: string;
  skillAssetRepo: SkillAssetRepository;
  ocbLocalSkills: OcbLocalSkillPort;
  artifactStore: { putObject: NonNullable<ObjectStore["putObject"]> };
  identity: OcbRequestIdentity;
}): Promise<FrozenSkillTarget> {
  const asset = await input.skillAssetRepo.findAsset(input.assetId);
  if (!asset || asset.owner_user_id !== input.ownerUserId || asset.bot_id !== input.botId) {
    throw Object.assign(new Error("待处理 Skill 不存在，或不属于当前 Bot"), { status: 404 });
  }
  const exported = await input.ocbLocalSkills.exportLocalSkill({
    botId: asset.bot_id,
    skillId: asset.ocb_skill_id,
    identity: input.identity,
  });
  const baselineKey = `evolve/skills/tasks/${input.taskId}/baseline/package.zip`;
  const candidateKey = `evolve/skills/tasks/${input.taskId}/candidate/package.zip`;
  await input.artifactStore.putObject(baselineKey, exported.packageBytes, "application/zip");
  const workspacePath = `/home/admin/.openclaw/clawevolve_workspaces/${input.taskId}/workspace`;
  return {
    assetId: asset.asset_id,
    skillId: asset.ocb_skill_id,
    name: exported.displayName,
    baseline: {
      ref: `oss://${getArtifactBucket()}/${baselineKey}`,
      sha256: exported.sha256,
    },
    workspacePath,
    // OCB's canonical Local Skill directory is derived from the validated
    // package name, not from the database row id used by its API.
    skillPath: `${workspacePath}/skills/skills-local/${safeSkillDirectoryName(exported.displayName)}`,
    candidate: { ref: `oss://${getArtifactBucket()}/${candidateKey}` },
  };
}
