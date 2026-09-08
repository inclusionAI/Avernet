import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { officialEvolveCatalog } from "../stage-catalog.js";
import {
  createStageDevelopmentPackage,
  inspectStageSkillPackage,
} from "../stage-development-package.js";

describe("official Evolve Stage catalog", () => {
  it("describes the fixed Bot evolution flow and its extension cuts", () => {
    expect(officialEvolveCatalog.template.steps.map((step) => step.stage)).toEqual([
      "diagnose",
      "plan",
      "optimize",
    ]);
    for (const stage of officialEvolveCatalog.stages) {
      expect(stage.extensionModes).toEqual(["preprocess", "postprocess", "replace"]);
      expect(stage.description.length).toBeGreaterThan(20);
      expect(stage.inputSchema.type).toBe("object");
      expect(stage.resultSchema.type).toBe("object");
    }
  });
});

describe("Stage Skill developer package", () => {
  it("gives a local Agent the complete selected Stage context and one implementation entry", async () => {
    const archive = await createStageDevelopmentPackage({
      stage: "diagnose",
      mode: "preprocess",
    });
    const zip = await JSZip.loadAsync(archive);

    expect(Object.keys(zip.files).sort()).toEqual([
      "AGENT_TASK.md",
      "contract.json",
      "implementation/",
      "implementation/SKILL.md",
      "stage-skill.json",
    ]);
    const guide = await zip.file("AGENT_TASK.md")!.async("string");
    expect(guide).toContain("Bot 自进化");
    expect(guide).toContain("诊断");
    expect(guide).toContain("预处理");
    expect(guide).toContain("平台负责");
    expect(guide).toContain("hitl");
    expect(guide).toContain("implementation/SKILL.md");
  });

  it("documents the real postprocess input", async () => {
    const archive = await createStageDevelopmentPackage({
      stage: "diagnose",
      mode: "postprocess",
    });
    const zip = await JSZip.loadAsync(archive);
    const contract = JSON.parse(await zip.file("contract.json")!.async("string"));

    expect(contract.stage.input.required).toContain("stage_result");
    expect(contract.stage.input.properties.stage_result.description).toContain("平台内置诊断");
    expect(contract.stage.result).toEqual(officialEvolveCatalog.stages[0].resultSchema);
  });

  it("accepts a direct Stage Skill and rejects a package bound to another slot", async () => {
    const zip = new JSZip();
    zip.file("stage-skill.json", JSON.stringify({
      schema_version: "clawevolve.stage-skill/v1",
      stage: "diagnose",
      mode: "preprocess",
      entrypoint: "implementation/SKILL.md",
    }));
    zip.file("implementation/SKILL.md", "# 诊断预处理\n\n读取输入并完成结构检查。\n");
    const packageBytes = await zip.generateAsync({ type: "nodebuffer" });

    await expect(inspectStageSkillPackage(packageBytes, {
      stage: "diagnose",
      mode: "preprocess",
    })).resolves.toMatchObject({ status: "passed" });
    await expect(inspectStageSkillPackage(packageBytes, {
      stage: "plan",
      mode: "preprocess",
    })).resolves.toMatchObject({
      status: "failed",
      checks: expect.arrayContaining([
        expect.objectContaining({ id: "stage-binding", status: "failed" }),
      ]),
    });
  });

  it("rejects the untouched placeholder instead of treating any ZIP as an implementation", async () => {
    const archive = await createStageDevelopmentPackage({
      stage: "diagnose",
      mode: "preprocess",
    });

    const result = await inspectStageSkillPackage(archive, {
      stage: "diagnose",
      mode: "preprocess",
    });

    expect(result.status).toBe("failed");
    expect(result.checks).toContainEqual(expect.objectContaining({
      id: "entrypoint",
      status: "failed",
    }));
  });

  it("rejects a highly compressed package before expanding it", async () => {
    const zip = new JSZip();
    zip.file("large.txt", Buffer.alloc(21 * 1024 * 1024));
    const archive = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });

    const result = await inspectStageSkillPackage(archive, {
      stage: "diagnose", mode: "preprocess",
    });

    expect(result.status).toBe("failed");
    expect(result.checks).toContainEqual(expect.objectContaining({
      id: "archive-safety", status: "failed",
    }));
  });
});
