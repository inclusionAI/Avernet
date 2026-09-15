import { describe, expect, it } from "vitest";
import {
  resolveSkillTaskHostPreset,
  resolveTaskHostPresentation,
  type EvolveHostExtension,
} from "../host-extensions.js";

const skillContext = {
  action: "diagnose" as const,
  actorUserId: "reader",
  targetSkill: {
    assetId: "asset-1",
    botId: "bot-1",
    ownerUserId: "owner",
    displayName: "Skill",
    spaceId: "team-1",
    spaceType: "TEAM" as const,
  },
  availableStageImplementations: [],
};

const presentationContext = {
  targetSkill: {
    assetId: "asset-1",
    skillId: "skill-1",
    name: "Skill",
    spaceId: "team-1",
    spaceType: "TEAM" as const,
    baseline: { ref: "fixture:baseline", sha256: "checksum" },
    candidate: { ref: "fixture:candidate" },
  },
  stageExtensions: {},
};

describe("Avernet host extension seam", () => {
  it("keeps standalone Avernet behavior when the host provides no extensions", () => {
    expect(resolveSkillTaskHostPreset([], skillContext)).toBeNull();
    expect(resolveTaskHostPresentation([], presentationContext)).toBeUndefined();
  });

  it("returns the single matching host contribution without interpreting its semantics", () => {
    const extension: EvolveHostExtension = {
      id: "host.example",
      resolveSkillTaskPreset: () => ({
        stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: "impl-1" } } },
        launchDescription: "Host supplied description",
      }),
      resolveTaskPresentation: () => ({ data: { implementationId: "impl-1" } }),
    };
    expect(resolveSkillTaskHostPreset([extension], skillContext)).toMatchObject({
      extensionId: "host.example",
      launchDescription: "Host supplied description",
    });
    expect(resolveTaskHostPresentation([extension], presentationContext)).toEqual({
      extensionId: "host.example",
      data: { implementationId: "impl-1" },
    });
  });

  it("fails closed when multiple host extensions claim the same task", () => {
    const extension = (id: string): EvolveHostExtension => ({
      id,
      resolveSkillTaskPreset: () => ({ unavailableReason: id }),
      resolveTaskPresentation: () => ({}),
    });
    expect(() => resolveSkillTaskHostPreset([extension("one"), extension("two")], skillContext))
      .toThrow("多个宿主扩展同时匹配");
    expect(() => resolveTaskHostPresentation([extension("one"), extension("two")], presentationContext))
      .toThrow("多个宿主扩展同时匹配");
  });
});
