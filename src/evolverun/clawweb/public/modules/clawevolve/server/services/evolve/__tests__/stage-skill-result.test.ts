import { describe, expect, it } from "vitest";
import { parseStageSkillResult } from "../stage-skill-result.js";

describe("Stage Skill result protocol", () => {
  it("accepts a completed result", () => {
    expect(parseStageSkillResult({ hitl: false, result: { diagnosis: {}, cases: { items: [] } } }))
      .toEqual({ kind: "completed", result: { diagnosis: {}, cases: { items: [] } } });
  });

  it("accepts dynamic HTML interaction content", () => {
    expect(parseStageSkillResult({
      hitl: true,
      question: { tag: "confirm-priority", format: "html", content: "<form><input name='priority'></form>" },
    })).toMatchObject({ kind: "waiting", question: { format: "html" } });
  });

  it("accepts a structured form without exposing a Stage-owned tag", () => {
    expect(parseStageSkillResult({
      hitl: true,
      question: {
        format: "form",
        title: "确认加固方案",
        description: "请选择加固等级并确认重点。",
        questions: [
          {
            id: "hardening_tier",
            type: "single_choice",
            title: "加固等级",
            required: true,
            options: [
              { value: "tier1", label: "基础加固", recommended: true },
              { value: "tier2", label: "深度加固", description: "补齐更多边界场景" },
            ],
          },
          {
            id: "focus",
            type: "long_text",
            title: "补充说明",
            required: false,
            placeholder: "可选",
            validation: { maxLength: 4000 },
          },
        ],
      },
    })).toMatchObject({
      kind: "waiting",
      question: { format: "form", title: "确认加固方案", questions: [{ id: "hardening_tier" }, { id: "focus" }] },
    });
  });

  it("rejects malformed structured forms before persisting an interaction", () => {
    expect(() => parseStageSkillResult({
      hitl: true,
      question: {
        format: "form",
        title: "重复字段",
        questions: [
          { id: "scope", type: "short_text", title: "范围", required: true },
          { id: "scope", type: "single_choice", title: "仍是范围", required: true, options: [] },
        ],
      },
    })).toThrow("question.questions");
  });

  it("rejects an ambiguous waiting result", () => {
    expect(() => parseStageSkillResult({ hitl: true, question: { content: "missing tag" } }))
      .toThrow("question.tag");
  });
});
