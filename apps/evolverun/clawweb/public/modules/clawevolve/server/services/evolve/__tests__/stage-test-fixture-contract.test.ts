import { describe, expect, it } from "vitest";
import { findOfficialStage, validateJsonSchema } from "../stage-catalog.js";

describe("Stage test fixture provenance contract", () => {
  it.each(["diagnose", "plan"])("documents explicit test identity without changing legacy %s targets", (stage) => {
    const schema = findOfficialStage(stage)!.inputSchema.properties!.target_skill;
    expect(schema.properties!.kind.enum).toEqual(["stage_test_fixture"]);
    expect(schema.properties!.fixture_id.enum).toEqual(stage === "plan"
      ? ["skill-description-v1", "skill-description-v2", "skill-description-v3"]
      : ["skill-description-v1", "skill-description-v2"]);
    expect(schema.properties!.asset_id.description).toContain("fixture:");
    expect(schema.properties!.skill_id.description).toContain("不是 Host");
    expect(schema.properties!.baseline_sha256.description).toContain("固定 ZIP");
    const legacy = { asset_id: "asset-1", skill_id: "ocb-1", name: "original",
      workspace: "/workspace", path: "/workspace/skill", baseline_sha256: "a".repeat(64) };
    expect(validateJsonSchema(schema, legacy)).toBeNull();
    expect(validateJsonSchema(schema, { ...legacy, kind: "stage_test_fixture", fixture_id: "skill-description-v1" })).toBeNull();
    expect(validateJsonSchema(schema, { ...legacy, kind: "stage_test_fixture", fixture_id: "skill-description-v2" })).toBeNull();
    const v3Error = validateJsonSchema(schema, { ...legacy, kind: "stage_test_fixture", fixture_id: "skill-description-v3" });
    if (stage === "plan") expect(v3Error).toBeNull();
    else expect(v3Error).not.toBeNull();
    expect(validateJsonSchema(schema, { ...legacy, kind: "untrusted_source" })).not.toBeNull();
  });
});
