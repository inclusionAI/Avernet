import { SkillPackageStorage } from "../../object-storage/skill-package-storage.js";
import type { ObjectStore } from "../../object-storage/oss-object-store.js";
import { createHash } from "node:crypto";
import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { createStageTestFixturePackage, freezeStageTestFixture, stageTestFixtureInput, selectStageTestFixtureVersion } from "../stage-test-fixture.js";
function storage(putObject: NonNullable<ObjectStore["putObject"]>) {
  return new SkillPackageStorage(undefined, { putObject, getObject: async () => { throw new Error("unused"); },
    createSignedUrl: async () => { throw new Error("unused"); } });
}
describe("fixed Stage test Skill fixture", () => {
  it("selects constructed resources by integration position without historical tasks", () => {
    expect(selectStageTestFixtureVersion("skill_evolution", "diagnose", "preprocess")).toBe(2);
    expect(selectStageTestFixtureVersion("skill_evolution", "plan", "replace")).toBe(2);
    expect(selectStageTestFixtureVersion("skill_hardening", "hardening", "replace")).toBe(2);
    expect(selectStageTestFixtureVersion("bot_evolution", "plan", "replace")).toBeUndefined();
    expect(selectStageTestFixtureVersion("skill_evolution", "plan", "postprocess")).toBeUndefined();
  });
  it("packages the original daily-report Skill for v3 without changing its bytes or identity", async () => {
    const fixture = await createStageTestFixturePackage(3);
    expect(fixture).toMatchObject({ fixtureId: "skill-description-v3", version: 3 });
    expect(fixture.sha256).toBe("78f8b6f83aceb7869586e69feaef40fd3249b6ed9f7ccd338f83670e50349164");
    expect(fixture.packageBytes.byteLength).toBe(1233);
    const zip = await JSZip.loadAsync(fixture.packageBytes);
    expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
    const content = await zip.file("SKILL.md")!.async("nodebuffer");
    expect(content.byteLength).toBe(1979);
    // Independently measured from the supplied original, current Bot and registered asset ZIP.
    expect(createHash("sha256").update(content).digest("hex"))
      .toBe("eb866600542fc91fd5851d1b5d70a88e7d180a6b779608afb51d36323bbe8459");
    expect(content.toString()).toContain("name: daily-report-zh");
    expect(content.toString()).not.toContain("memory");
    expect((await createStageTestFixturePackage(3)).packageBytes.equals(fixture.packageBytes)).toBe(true);
    const frozen = await freezeStageTestFixture("EV-DAILY", storage(async () => ({ etag: "test" })), 3);
    expect(stageTestFixtureInput("EV-DAILY", frozen)).toMatchObject({
      kind: "stage_test_fixture", fixture_id: "skill-description-v3", name: "daily-report-zh",
      asset_id: "fixture:EV-DAILY:skill-description-v3", skill_id: "fixture:skill-description-v3",
      path: "/home/admin/.openclaw/clawevolve_workspaces/EV-DAILY/workspace/skills/skills-local/daily-report-zh",
      baseline_sha256: fixture.sha256,
    });
  });

  it("delivers one real root Skill with reproducible ZIP bytes and an honest baseline hash", async () => {
    const first = await createStageTestFixturePackage(1);
    const second = await createStageTestFixturePackage(1);
    expect(first.fixtureId).toBe("skill-description-v1");
    expect(first.version).toBe(1);
    expect(first.sha256).toBe("ba510d33b234af46de86eb5eeef3a581775314cc33f69f2d380a3c3d88e9e17d");
    expect(second.packageBytes.equals(first.packageBytes)).toBe(true);
    expect(first.sha256).toBe(createHash("sha256").update(first.packageBytes).digest("hex"));
    const zip = await JSZip.loadAsync(first.packageBytes);
    expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
    const skill = zip.file("SKILL.md")!;
    expect(skill.unixPermissions).toBe(0o100644);
    expect(skill.date.toISOString()).toBe("1980-01-01T00:00:00.000Z");
    const content = await skill.async("string");
    expect(content).toContain("name: stage-test-text-summary");
    expect(content).toContain("这里的“它”指用户提供的输出格式要求");
    expect(content).toContain("不补充没有依据的信息");
    expect(content).not.toMatch(/changed|hitl|resultFile|\/home\/admin/);
  });

  it("freezes new tests as v2 while retaining the exact v1 package and identity", async () => {
    const current = await createStageTestFixturePackage();
    const previous = await createStageTestFixturePackage(1);
    expect(current.fixtureId).toBe("skill-description-v2");
    expect(current.version).toBe(2);
    expect(current.sha256).not.toBe(previous.sha256);
    expect((await createStageTestFixturePackage()).packageBytes.equals(current.packageBytes)).toBe(true);
    const zip = await JSZip.loadAsync(current.packageBytes);
    expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
    const text = await zip.file("SKILL.md")!.async("string");
    expect(text).toContain("2. 按它整理要点。");
    expect(text).not.toContain("这里的“它”指");
    expect(text).not.toMatch(/changed|hitl|resultFile|必须修改|成功标记/);
    const writes: Array<[string, unknown]> = [];
    const frozen = await freezeStageTestFixture("EV-NEW", storage(async (key, bytes) => {
      writes.push([key, bytes]); return { etag: "test" };
    }));
    expect(writes).toHaveLength(1);
    expect(writes[0][0]).toBe("evolve/stage-tests/EV-NEW/fixtures/skill-description-v2/package.zip");
    expect(frozen).toMatchObject({ fixtureId: "skill-description-v2", version: 2, sha256: current.sha256 });
    expect(stageTestFixtureInput("EV-NEW", frozen).fixture_id).toBe("skill-description-v2");
    const old = { ...frozen, fixtureId: "skill-description-v1" as const, version: 1 as const,
      sha256: previous.sha256, ref: frozen.ref.replace("skill-description-v2", "skill-description-v1") };
    expect(stageTestFixtureInput("EV-NEW", old).fixture_id).toBe("skill-description-v1");
    expect(() => stageTestFixtureInput("EV-NEW", { ...old, version: 2 })).toThrow(/不一致/);
  });
});
