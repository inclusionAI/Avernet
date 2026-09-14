import type { OcbLocalSkillPort, OcbRequestIdentity } from "../../internal/module-api.js";
import type { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { getArtifactBucket, type ObjectStore } from "../object-storage/oss-object-store.js";

export type FrozenSkillTarget = {
  assetId: string;
  skillId: string;
  name: string;
  baseline: { ref: string; sha256: string };
  candidate: {
    ref: string;
    prepared?: PreparedSkillCandidate;
    artifact?: { ref: string; size: number; sha256: string; contentType: "application/zip" };
  };
};

export type PreparedSkillCandidate = {
  workspacePath: string;
  skillPath: string;
  preparedByStepId: string;
  baselineSha256: string;
};

function safeSkillDirectoryName(value: string): string {
  const normalized = value.trim();
  if (!/^[A-Za-z0-9._-]{1,128}$/.test(normalized)) {
    throw new Error("OCB Skill 标识不能安全映射为候选目录");
  }
  return normalized;
}

export function candidateWorkspacePath(taskId: string): string {
  if (!/^[A-Za-z0-9._-]{1,128}$/.test(taskId)) throw new Error("Task 标识不能安全映射为候选目录");
  return `/home/admin/.openclaw/clawevolve_workspaces/${taskId}/workspace`;
}

export function expectedCandidateSkillPath(taskId: string, displayName: string): string {
  return `${candidateWorkspacePath(taskId)}/skills/skills-local/${safeSkillDirectoryName(displayName)}`;
}

export function recordPreparedSkillCandidate(input: {
  taskId: string;
  stepId: string;
  target: FrozenSkillTarget;
  output: Record<string, unknown>;
}): FrozenSkillTarget {
  const workspacePath = String(input.output.workspace ?? "");
  const skillPath = String(input.output.targetSkillPath ?? "");
  const expectedWorkspace = candidateWorkspacePath(input.taskId);
  const expectedSkill = expectedCandidateSkillPath(input.taskId, input.target.name);
  if (input.output.prepared !== true || workspacePath !== expectedWorkspace || skillPath !== expectedSkill) {
    throw new Error("候选准备结果与本次任务的隔离目录不一致");
  }
  return {
    ...input.target,
    candidate: {
      ...input.target.candidate,
      prepared: {
        workspacePath,
        skillPath,
        preparedByStepId: input.stepId,
        baselineSha256: input.target.baseline.sha256,
      },
    },
  };
}

export function requirePreparedSkillCandidate(target: FrozenSkillTarget): PreparedSkillCandidate {
  const prepared = target.candidate.prepared;
  if (!prepared || prepared.baselineSha256 !== target.baseline.sha256) {
    throw new Error("待进化 Skill 尚未在本次任务中完成候选准备");
  }
  return prepared;
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
  return {
    assetId: asset.asset_id,
    skillId: asset.ocb_skill_id,
    name: exported.displayName,
    baseline: {
      ref: `oss://${getArtifactBucket()}/${baselineKey}`,
      sha256: exported.sha256,
    },
    candidate: { ref: `oss://${getArtifactBucket()}/${candidateKey}` },
  };
}
