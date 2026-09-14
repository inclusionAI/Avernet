import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { officialStageContracts, stageExtensionResultSchema, stageRuntimeInputSchema, validateJsonSchema } from "../stage-catalog.js";
import {
  createStageDevelopmentPackage,
  inspectStageSkillPackage,
} from "../stage-development-package.js";

describe("official Evolve Stage contracts", () => {
  it("keeps Stage contracts separate from fixed flow orchestration", () => {
    expect(officialStageContracts).not.toHaveProperty("template");
    expect(officialStageContracts.stages.map((stage) => stage.stage)).toEqual([
      "diagnose", "plan", "optimize",
    ]);
    for (const stage of officialStageContracts.stages) {
      expect(stage.extensionModes).toEqual(["preprocess", "postprocess", "replace"]);
      expect(stage.postprocessWritablePaths.length).toBeGreaterThan(0);
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
      flow: "skill_evolution",
    });
    const zip = await JSZip.loadAsync(archive);

    expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
    const guide = await zip.file("SKILL.md")!.async("string");
    expect(guide).toContain("Skill 自进化");
    expect(guide).toContain("诊断");
    expect(guide).toContain("前置处理");
    expect(guide).toContain("平台负责");
    expect(guide).toContain("背景与本次开发目标");
    expect(guide).toContain("平台提供的能力");
    expect(guide).toContain("提示用户将该 ZIP 上传到平台");
    expect(guide).not.toMatch(/contract\.json|inputFile|resultFile|runtime\.py|hitl|不要在 Skill 中写死/);
  });

  it("documents the real postprocess input", async () => {
    const archive = await createStageDevelopmentPackage({
      stage: "diagnose",
      mode: "postprocess",
    });
    const zip = await JSZip.loadAsync(archive);
    const guide = await zip.file("SKILL.md")!.async("string");
    expect(guide).toContain('"builtin_result"');
    expect(guide).toContain('"result_patch"');
    expect(guide).toContain("平台内置诊断已经生成的结果");
    expect(guide).toContain("允许修改的字段：diagnosis、cases");
    expect(guide).not.toContain('"target_skill"');
  });

  for (const flow of ["bot_evolution", "skill_evolution"] as const) {
    for (const stage of officialStageContracts.stages) {
      for (const mode of stage.extensionModes) {
        it(`documents actual ${flow}/${stage.stage}/${mode} inputs and business output`, async () => {
          const zip = await JSZip.loadAsync(await createStageDevelopmentPackage({ flow, stage: stage.stage, mode }));
          expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
          const guide = await zip.file("SKILL.md")!.async("string");
          expect(guide.match(/^## /gm)).toHaveLength(4);
          const blocks = [...guide.matchAll(/```json\n([\s\S]*?)\n```/g)].map((match) => JSON.parse(match[1]!));
          const input = blocks[0];
          const output = blocks[1];
          // New Plan business calls have per-phase contracts, covered by
          // plan-business-guide.test.ts; the final Stage schema is unchanged.
          const planBusiness = flow === "skill_evolution" && stage.stage === "plan" && mode === "replace";
          if (!planBusiness) {
          expect(validateJsonSchema(stageRuntimeInputSchema(stage, mode), input)).toBeNull();
          expect(validateJsonSchema(stageExtensionResultSchema(stage, mode), output)).toBeNull();
          expect(input.task.task_type).toBe("full");
          expect(Boolean(input.target_skill)).toBe(flow === "skill_evolution");
          expect(Boolean(input.builtin_result)).toBe(mode === "postprocess");
          if (stage.stage === "diagnose") {
            expect(input).toHaveProperty("diagnose_goal");
            expect(input).not.toHaveProperty("diagnose_result");
            expect(input).not.toHaveProperty("plan_result");
          } else if (stage.stage === "plan") {
            expect(input).toHaveProperty("goal");
            expect(input).toHaveProperty("diagnose_result");
            expect(input).not.toHaveProperty("plan_result");
          } else {
            expect(input).toHaveProperty("round", 1);
            expect(input).toHaveProperty("plan_result");
            expect(guide).toContain("previous_round_result");
          }
          expect(output).not.toHaveProperty("hitl");
          expect(output).not.toHaveProperty("result");
          expect(guide).not.toMatch(/contract\.json|inputFile|resultFile|runtime\.py|schema_version|\|---/);
          }
          expect(guide).toContain("请说明本次允许处理的范围");
          expect(guide).toContain("<form>");
          if (flow === "bot_evolution") expect(guide).not.toContain("待进化 Skill 的独立副本");
          const hasTestSkill = flow === "skill_evolution"
            && ((stage.stage === "diagnose" && mode === "preprocess") || (stage.stage === "plan" && mode === "replace"));
          expect(guide.includes("独立测试副本")).toBe(hasTestSkill);
          const background = guide.split("## 背景与本次开发目标\n\n")[1]!.split("\n## 输入与输出")[0]!;
          const skillName = `${stage.name}${mode === "replace" ? "" : mode === "preprocess" ? "前置" : "后置"} Skill`;
          expect(background.startsWith(`${flow === "skill_evolution" ? "Skill" : "Bot"} 自进化任务用于`)).toBe(true);
          expect(background.indexOf("任务主要包含三个环节：")).toBeLessThan(background.indexOf("本次需要你开发"));
          expect(background).toContain(`本次需要你开发一个“${skillName}”`);
          expect(background).toContain(`“${skillName}”是你需要开发的能力`);
          expect(background).not.toMatch(/你正在为|业务 Skill|完整替换|完成整个|不会再执行默认/);
          if (flow === "skill_evolution") {
            expect(background).toContain("下文将被改进的 Skill 称为“目标 Skill”");
            expect(background).toContain("“目标 Skill”是本次自进化任务要改进的对象");
          } else {
            expect(background).not.toContain("目标 Skill");
            expect(background).not.toContain("Skill 自进化");
            expect(background).toContain("所选 Bot 是本次自进化任务要改进的对象");
          }
          if (mode === "preprocess") {
            expect(background).toContain(`默认${stage.name}开始前调用${skillName}`);
            expect(background).toContain(`处理完成后继续执行默认${stage.name}`);
          } else if (mode === "postprocess") {
            expect(background).toContain(`默认${stage.name}完成后调用${skillName}`);
            expect(background).toContain("平台开放的结果字段");
            expect(background).toContain("平台合并并校验结果后");
          } else {
            expect(background).toContain("逻辑，供平台调用");
            // Copy changes do not claim the still-pending internal runtime integration is deployed.
            expect(background).not.toContain("平台保留");
          }
        });
      }
    }
  }

  it("preserves the approved Skill evolution planning background", async () => {
    const zip = await JSZip.loadAsync(await createStageDevelopmentPackage({
      flow: "skill_evolution", stage: "plan", mode: "replace",
    }));
    const guide = await zip.file("SKILL.md")!.async("string");
    const background = guide.split("## 背景与本次开发目标\n\n")[1]!.split("\n## 输入与输出")[0]!.trim();
    expect(background).toBe([
      "Skill 自进化任务用于改进用户指定的 Skill：结合真实会话发现问题，制定修改方案，并通过评测检查修改效果。下文将被改进的 Skill 称为“目标 Skill”。",
      "", "平台会读取目标 Skill，创建本次任务的独立副本。任务中的修改在副本上进行，不会直接覆盖原始 Skill。",
      "", "任务主要包含三个环节：", "",
      "- 诊断：读取真实会话，找出与目标 Skill 相关的问题和成功、失败案例。",
      "- 规划：根据诊断结果，确定改进目标、修改方案和评测案例。",
      "- 优化：按照方案修改目标 Skill 的副本，通过评测判断效果，决定保留、回退或继续调整。",
      "", "本次需要你开发一个“规划 Skill”，提供规划环节中的问题分析、优化策略制定和评测要求生成逻辑，供平台调用。",
      "", "“目标 Skill”是本次自进化任务要改进的对象；“规划 Skill”是你需要开发的能力，用于分析目标 Skill 并制定改进方案。",
    ].join("\n"));
  });

  it("accepts a direct Stage Skill and rejects a package bound to another slot", async () => {
    const zip = new JSZip();
    zip.file("stage-skill.json", JSON.stringify({
      schema_version: "clawevolve.stage-skill/v1",
      display_name: "诊断预处理",
      stage: "diagnose",
      mode: "preprocess",
      entrypoint: "implementation/SKILL.md",
    }));
    zip.file("implementation/SKILL.md", "# 诊断预处理\n\n读取输入并完成结构检查。\n");
    const packageBytes = await zip.generateAsync({ type: "nodebuffer" });

    await expect(inspectStageSkillPackage(packageBytes, {
      stage: "diagnose",
      mode: "preprocess",
    })).resolves.toMatchObject({ status: "passed", manifest: {
      display_name: "诊断预处理自定义实现", entrypoint: "implementation/SKILL.md",
    } });
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

  it("binds a plain Skill to the platform selection without a user manifest", async () => {
    const zip = new JSZip();
    zip.file("SKILL.md", "# 用户自由命名\n\n读取输入并完成结构检查。\n");
    zip.file("scripts/check.py", "print('check')");

    const result = await inspectStageSkillPackage(
      await zip.generateAsync({ type: "nodebuffer" }),
      { stage: "diagnose", mode: "preprocess" },
    );

    expect(result).toMatchObject({ status: "passed", manifest: {
      stage: "diagnose", mode: "preprocess", entrypoint: "SKILL.md",
      display_name: "诊断预处理自定义实现",
    } });
  });

  it("accepts a single wrapped Skill and ignores legacy display identity and entrypoint claims", async () => {
    const zip = new JSZip();
    zip.file("custom/SKILL.md", "# 自定义实现\n读取真实输入。");
    zip.file("stage-skill.json", JSON.stringify({ display_name: "用户名称", entrypoint: "wrong/SKILL.md" }));
    expect(await inspectStageSkillPackage(await zip.generateAsync({ type: "nodebuffer" }), {
      stage: "diagnose", mode: "postprocess",
    })).toMatchObject({ status: "passed", manifest: {
      display_name: "诊断后处理自定义实现", entrypoint: "custom/SKILL.md",
    } });
  });

  it("prefers the root Skill over nested supporting instructions", async () => {
    const zip = new JSZip();
    zip.file("SKILL.md", "# 实现\n读取真实输入。");
    zip.file("references/SKILL.md", "# 参考说明");
    expect(await inspectStageSkillPackage(await zip.generateAsync({ type: "nodebuffer" }), {
      stage: "diagnose", mode: "preprocess",
    })).toMatchObject({ status: "passed", manifest: { entrypoint: "SKILL.md" } });
  });

  it.each([
    { "one/SKILL.md": "one", "two/SKILL.md": "two" },
    { "nested/deep/SKILL.md": "deep" },
    { "SKILL.md": "   " },
    { "implementation/SKILL.md": "TODO: 请让本地 Agent 完成实现" },
  ])("rejects missing, ambiguous or undeveloped Skill entries: %j", async (files) => {
    const zip = new JSZip();
    for (const [name, content] of Object.entries(files)) zip.file(name, content);
    expect(await inspectStageSkillPackage(await zip.generateAsync({ type: "nodebuffer" }), {
      stage: "diagnose", mode: "preprocess",
    })).toMatchObject({ status: "failed", checks: expect.arrayContaining([
      expect.objectContaining({ id: "entrypoint", status: "failed" }),
    ]) });
  });

  it("rejects traversal names even when ZIP loading sanitizes them", async () => {
    const zip = new JSZip();
    zip.file("../SKILL.md", "# 实现");
    expect(await inspectStageSkillPackage(await zip.generateAsync({ type: "nodebuffer" }), {
      stage: "diagnose", mode: "preprocess",
    })).toMatchObject({ status: "failed", checks: expect.arrayContaining([
      expect.objectContaining({ id: "archive-safety", status: "failed" }),
    ]) });
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
