import { expect, it } from "vitest";
import type { SkillTaskHostPresetContext } from "../../../contracts/evolve-extension.js";
import { parseSkillTaskStageBindings, resolveSkillTaskStageBindings, type SkillTaskStageBinding } from "../skill-task-stage-bindings.js";

const binding: SkillTaskStageBinding = { spaceType: "TEAM", spaceId: "team-alpha", action: "optimize",
  stage: "diagnose", mode: "preprocess", stageSkillId: "STAGESKILL-EXAMPLE" };
const context: SkillTaskHostPresetContext = {
  action: "optimize", actorUserId: "reader",
  targetSkill: { assetId: "asset", botId: "bot", ownerUserId: "owner", displayName: "Target", spaceType: "TEAM", spaceId: "team-alpha" },
  availableStageImplementations: [{ stageSkillId: binding.stageSkillId, implementationId: "v1", ownerUserId: "publisher",
    displayName: "Example", spaceId: binding.spaceId, spaceType: binding.spaceType, stage: binding.stage, mode: binding.mode,
    versionNo: 1, status: "registered", integrationTestStatus: "test_passed" }],
};

it("accepts opaque provider-neutral space IDs and bindings for other stages", () => {
  const configured = [binding, { ...binding, stage: "plan", mode: "postprocess" }];
  expect(parseSkillTaskStageBindings({ bindings: configured })).toEqual(configured);
  expect(parseSkillTaskStageBindings({ bindings: [] })).toEqual([]);
});

it.each([null, [], {}, { bindings: [], unknown: true }, { bindings: [binding, binding] },
  ...[{ spaceType: ["TEAM"] }, { action: ["optimize"] }, { action: "unknown" }, { stage: "missing" },
    { spaceId: " " }, { mode: "unknown" }, { unknown: true }, { stageSkillId: "invalid" },
    { action: "hardening", stage: "diagnose" }, { action: "diagnose", stage: "optimize" }]
    .map((override) => ({ bindings: [{ ...binding, ...override }] })),
])("rejects invalid configuration without ignoring rows (%#)", (value) => {
  expect(() => parseSkillTaskStageBindings(value)).toThrow(/配置不合法/);
});

it("selects the latest eligible version and combines multiple positions", () => {
  const first = context.availableStageImplementations[0];
  const configured = parseSkillTaskStageBindings({ bindings: [binding, { ...binding, stage: "plan", mode: "postprocess" }] });
  const ctx: SkillTaskHostPresetContext = { ...context, availableStageImplementations: [first,
    { ...first, implementationId: "v2", versionNo: 2 },
    { ...first, implementationId: "unregistered", versionNo: 3, status: "validated" },
    { ...first, implementationId: "untested", versionNo: 4, integrationTestStatus: "untested" },
    { ...first, implementationId: "wrong-space", versionNo: 5, spaceId: "another" },
    { ...first, implementationId: "plan", stage: "plan", mode: "postprocess" },
  ] };
  expect(resolveSkillTaskStageBindings(configured, ctx)).toMatchObject({ unavailableReason: null, stageExtensions: {
    diagnose: { preprocess: { enabled: true, implementationId: "v2" } },
    plan: { postprocess: { enabled: true, implementationId: "plan" } },
  } });
});

it("does not bind another action or space, and never drops an unavailable matching binding", () => {
  expect(resolveSkillTaskStageBindings([binding], { ...context, action: "diagnose" })).toBeNull();
  expect(resolveSkillTaskStageBindings([binding], { ...context, targetSkill: { ...context.targetSkill, spaceId: "another" } })).toBeNull();
  expect(resolveSkillTaskStageBindings([binding], { ...context, availableStageImplementations: [] }))
    .toMatchObject({ unavailableReason: expect.stringContaining("没有可访问") });
  expect(resolveSkillTaskStageBindings([binding], { ...context,
    availableStageImplementations: [{ ...context.availableStageImplementations[0], mode: "replace" }] }))
    .toMatchObject({ unavailableReason: expect.stringContaining("没有可访问") });
});
