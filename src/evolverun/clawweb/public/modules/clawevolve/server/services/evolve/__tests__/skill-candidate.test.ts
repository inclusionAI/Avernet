import { describe, expect, it } from "vitest";
import {
  candidateWorkspacePath,
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
