import { SkillPackageStorage } from "../object-storage/skill-package-storage.js";
import type { BotSkillGateway } from "../../contracts/bot-skill-gateway.js";
import type { RequestIdentity } from "../../contracts/request-identity.js";
import type { SpaceDirectory } from "../../contracts/space-directory.js";
import { canReadSpaceRecord } from "./space-access.js";
import type { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";

export type FrozenSkillTarget = {
  assetId: string;
  skillId: string;
  name: string;
  ownerUserId?: string;
  spaceId?: string | null;
  spaceType?: "PERSONAL" | "TEAM" | null;
  baseline: { ref: string; sha256: string; versionId?: string; versionNo?: number };
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
    throw new Error("宿主 Skill 标识不能安全映射为候选目录");
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
  hostLocalSkills: BotSkillGateway;
  hostSpaces?: SpaceDirectory;
  identity: RequestIdentity;
  skillPackages: SkillPackageStorage;
}): Promise<FrozenSkillTarget> {
  const packages = input.skillPackages;
  const asset = await input.skillAssetRepo.findAsset(input.assetId);
  const spaces = asset?.space_id ? await input.hostSpaces?.listAccessibleSpaces({ identity: input.identity }) ?? [] : [];
  if (!asset || !canReadSpaceRecord(asset, input.identity.userId, spaces) || asset.bot_id !== input.botId) {
    throw Object.assign(new Error("待处理 Skill 不存在，或不属于当前 Bot"), { status: 404 });
  }
  const exported = await input.hostLocalSkills.exportLocalSkill({
    botId: asset.bot_id,
    skillId: asset.external_skill_id,
    ownerUserId: asset.owner_user_id,
    identity: input.identity,
  });
  const baselineKey = `evolve/skills/tasks/${input.taskId}/baseline/package.zip`;
  const candidateKey = `evolve/skills/tasks/${input.taskId}/candidate/package.zip`;
  const registeredVersion = exported.sha256 === asset.current_package_sha256
    ? await input.skillAssetRepo.findVersionByNumber(asset.asset_id, asset.current_version_no) : null;
  await packages.put(baselineKey, exported.packageBytes);
  return {
    assetId: asset.asset_id,
    ownerUserId: asset.owner_user_id,
    spaceId: asset.space_id ?? null,
    spaceType: asset.space_type ?? null,
    skillId: asset.external_skill_id,
    name: exported.displayName,
    baseline: {
      ref: packages.ref(baselineKey),
      sha256: exported.sha256,
      ...(registeredVersion?.package_sha256 === exported.sha256 ? {
        versionId: registeredVersion.version_id,
        versionNo: registeredVersion.version_no,
      } : {}),
    },
    candidate: { ref: packages.ref(candidateKey) },
  };
}
