import JSZip from "jszip";
import { describe, expect, it } from "vitest";
import { createStageDevelopmentPackage } from "../stage-development-package.js";
import { findOfficialStage, validateJsonSchema } from "../stage-catalog.js";

async function planningGuide(): Promise<string> {
  const zip = await JSZip.loadAsync(await createStageDevelopmentPackage({
    flow: "skill_evolution", stage: "plan", mode: "replace",
  }));
  expect(Object.keys(zip.files)).toEqual(["SKILL.md"]);
  return zip.file("SKILL.md")!.async("string");
}

describe("Plan business developer guide", () => {
  it("describes one current business phase per invocation, not a whole Stage result", async () => {
    const guide = await planningGuide();
    const blocks = [...guide.matchAll(/```json\n([\s\S]*?)\n```/g)].map((m) => JSON.parse(m[1]!));
    expect(blocks.filter((value) => value.phase).map((value) => value.phase)).toEqual([
      "discovery", "case_contract", "objective_document", "spec_document",
    ]);
    for (const input of blocks.filter((value) => value.phase)) {
      expect(Object.keys(input).sort()).toEqual(["input", "output_requirements", "phase"]);
      expect(typeof input.output_requirements).toBe("string");
    }
    expect(guide).not.toContain("必须包含 goal、spec、benchCases、benchDomains");
    expect(guide).not.toContain("已发布模板名称");
    expect(guide).toContain("平台保留");
    expect(guide).toContain("提示用户将该 ZIP 上传到平台");
  });

  it("documents the runtime question and raw string/object answer shapes", async () => {
    const guide = await planningGuide();
    const blocks = [...guide.matchAll(/```json\n([\s\S]*?)\n```/g)].map((m) => JSON.parse(m[1]!));
    const questions = blocks.filter((value) => value.question);
    expect(questions.map((value) => value.question.format)).toEqual(["text", "html"]);
    const resumed = blocks.filter((value) => value.hitl);
    expect(resumed).toHaveLength(2);
    expect(resumed.map((value) => typeof value.hitl.answer)).toEqual(["string", "object"]);
    for (const { hitl } of resumed) {
      expect(Object.keys(hitl).sort()).toEqual(["answer", "history", "question"]);
      expect(hitl.history.at(-1)).toEqual({ question: hitl.question, answer: hitl.answer });
      expect(questions).toContainEqual({ question: hitl.question });
    }
    expect(resumed[1].hitl.answer).toEqual({ scope: "只补充执行前的范围检查" });
    expect(guide).not.toMatch(/human_input|\bfields\b|business.context|entrypoint|internal import|inputFile|resultFile|runtime\.py|--business|contract\.json/);
  });

  it("gives schema-valid examples for each intermediate output, not published Plan IDs", async () => {
    const guide = await planningGuide();
    const blocks = [...guide.matchAll(/```json\n([\s\S]*?)\n```/g)].map((m) => JSON.parse(m[1]!));
    // Runtime contracts: discovery/schema.py, bench/case_contract.py and
    // business_adapter.py. These are business shapes, not Stage result schemas.
    const schemas = [
      { type: "object", required: ["schema_version", "analysis_summary", "case_findings", "target_findings", "merged_targets"], properties: {
        schema_version: { type: "string", enum: ["clawevolve.plan.discovery.v1"] }, analysis_summary: { type: "object" },
        case_findings: { type: "array", minItems: 1, items: { type: "object", required: ["case_id", "failure_mode", "inspected_files", "environment_analysis", "optimization_ideas"] } },
        target_findings: { type: "array", minItems: 1, items: { type: "object", required: ["path", "reason", "current_gap", "proposed_change", "related_case_ids", "failure_modes"] } },
        merged_targets: { type: "array", minItems: 1, items: { type: "string" } },
      } },
      { type: "object", required: ["contracts"], additionalProperties: false, properties: { contracts: {
        type: "array", minItems: 1, maxItems: 1, items: { type: "object",
          required: ["schema_version", "case_id", "template_id", "case_type", "split", "source_session_id", "task_contract", "grading_strategy", "automated_checks", "replayability", "provenance"],
          properties: { schema_version: { type: "string", enum: ["clawevolve.case-contract.v1"] }, task_contract: {
            type: "object", required: ["user_intent", "required_outcomes", "acceptable_approaches", "required_actions", "required_evidence", "completion_signals", "acceptable_failure_handling", "forbidden_behaviors"],
          } },
        },
      } } },
      { type: "object", required: ["objective_markdown"], additionalProperties: false, properties: { objective_markdown: { type: "string", minLength: 1 } } },
      { type: "object", required: ["spec_markdown"], additionalProperties: false, properties: { spec_markdown: { type: "string", minLength: 1 } } },
    ];
    const inputs = blocks.filter((value) => value.phase);
    const expectedInputFields = [
      ["allowed_targets", "source_path", "source_sha256", "workspace_root"],
      ["case", "discovery_notes", "goal", "user_intent", "validation_error"],
      ["context", "objective", "objective_template"],
      ["context", "objective_markdown", "spec", "spec_template"],
    ];
    for (const [index, input] of inputs.entries()) {
      expect(Object.keys(input.input).sort()).toEqual(expectedInputFields[index]);
      const output = blocks[blocks.indexOf(input) + 1];
      expect(validateJsonSchema(schemas[index]!, output)).toBeNull();
      expect(validateJsonSchema(findOfficialStage("plan")!.resultSchema, output)).not.toBeNull();
      expect(output).not.toHaveProperty("result");
      expect(output).not.toHaveProperty("benchCases");
      expect(output).not.toHaveProperty("benchDomains");
    }
    const contract = blocks[3].contracts[0];
    const source = inputs[1].input.case;
    for (const field of ["case_id", "template_id", "case_type", "split", "source_session_id"]) {
      expect(contract[field]).toBe(source[field]);
    }
    expect(contract.automated_checks).toEqual([]);
    expect(contract.grading_strategy.criteria.reduce((sum: number, value: { weight: number }) => sum + value.weight, 0)).toBe(100);
    for (const criterion of contract.grading_strategy.criteria) {
      for (const field of ["id", "name", "description", "score_1", "score_075", "score_05", "score_025", "score_0"]) expect(typeof criterion[field]).toBe("string");
    }
    expect(guide).toContain("训练与测试划分");
    expect(guide).toContain("评测模板渲染和发布");
    expect(guide).toContain("不修改目标文件");
    expect(guide).toContain("不编造成功结果");
    expect(guide).toContain("运行时，平台会向执行 Agent 提供本次输入文件和结果文件的具体位置。读取输入文件，完成处理后，将结果以 JSON 格式写入结果文件。");
  });
});
