import { describe, expect, it, vi } from "vitest";
import {
  candidateWorkspacePath,
  freezeSkillTarget,
  recordPreparedSkillCandidate,
  requirePreparedSkillCandidate,
  type FrozenSkillTarget,
} from "../skill-candidate.js";

const frozenTarget: FrozenSkillTarget = {
  assetId: "ASSET-1",
  skillId: "SKILL-1",
  name: "mcp-logql-builder",
  baseline: { ref: "oss://bucket/baseline.zip", sha256: "a".repeat(64) },
  candidate: { ref: "oss://bucket/candidate.zip" },
};

describe("isolated Skill candidate handle", () => {
  it("does not expose a runnable path before prepare succeeds", () => {
    expect(() => requirePreparedSkillCandidate(frozenTarget)).toThrow("尚未");
  });

  it("persists the exact prepared handle used by every later Stage", () => {
    const taskId = "EV-20260909-TEST";
    const workspacePath = candidateWorkspacePath(taskId);
    const prepared = recordPreparedSkillCandidate({
      taskId,
      stepId: "STEP-PREPARE",
      target: frozenTarget,
      output: {
        prepared: true,
        workspace: workspacePath,
        targetSkillPath: `${workspacePath}/skills/skills-local/mcp-logql-builder`,
      },
    });

    expect(requirePreparedSkillCandidate(prepared)).toEqual({
      workspacePath,
      skillPath: `${workspacePath}/skills/skills-local/mcp-logql-builder`,
      preparedByStepId: "STEP-PREPARE",
      baselineSha256: "a".repeat(64),
    });
  });

  it("rejects a runtime report that points outside the task workspace", () => {
    expect(() => recordPreparedSkillCandidate({
      taskId: "EV-20260909-TEST",
      stepId: "STEP-PREPARE",
      target: frozenTarget,
      output: {
        prepared: true,
        workspace: "/home/admin/.openclaw/workspace",
        targetSkillPath: "/home/admin/.openclaw/workspace/skills-local/mcp-logql-builder",
      },
    })).toThrow("隔离目录不一致");
  });
});

 it('reads and freezes only the registered Bot environment, rejecting a mismatched task', async () => {
  const exportLocalSkill = vi.fn(async () => ({ packageBytes: Buffer.from('skill'), sha256: 'sha', displayName: 'example', botEnv: 'prod' }));
  const put = vi.fn();
  const input = {
    taskId: 'EV-ENV', ownerUserId: 'owner', botId: 'default', botEnv: 'prod', assetId: 'ASSET',
    identity: { userId: 'owner' }, hostLocalSkills: { exportLocalSkill },
    skillAssetRepo: { findAsset: async () => ({ asset_id: 'ASSET', bot_id: 'default', bot_env: 'prod', owner_user_id: 'owner', external_skill_id: '47' }) },
    skillPackages: { put, ref: (key: string) => key },
  } as unknown as Parameters<typeof freezeSkillTarget>[0];
  const frozen = await freezeSkillTarget(input);
  expect(frozen.botEnv).toBe('prod');
  expect(exportLocalSkill).toHaveBeenCalledWith(expect.objectContaining({ botId: 'default', botEnv: 'prod' }));
  exportLocalSkill.mockClear(); put.mockClear();
  await expect(freezeSkillTarget({ ...input, botEnv: 'pre' })).rejects.toMatchObject({ status: 409 });
  expect(exportLocalSkill).not.toHaveBeenCalled();
  expect(put).not.toHaveBeenCalled();
});
