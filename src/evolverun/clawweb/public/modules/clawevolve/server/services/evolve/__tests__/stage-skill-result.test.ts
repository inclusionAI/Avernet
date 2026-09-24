import { describe, expect, it } from "vitest";
import { parseStageSkillResult } from "../stage-skill-result.js";

describe("Stage Skill result protocol", () => {
  it("accepts a completed result", () => {
    expect(parseStageSkillResult({ hitl: false, result: { diagnosis: {}, cases: { items: [] } } }))
      .toEqual({ kind: "completed", result: { diagnosis: {}, cases: { items: [] } } });
  });

  it("accepts a valid business result that requests one more feedback round", () => {
    expect(parseStageSkillResult({
      hitl: false,
      result: { summary: "第一轮已完成", changed: true },
      loop: {
        action: "request_feedback",
        prompt: "可以接受当前结果，或补充意见后再处理一轮。",
        accepts: { text: true, files: [".jsonl", ".xlsx", ".txt"] },
      },
    })).toEqual({
      kind: "completed",
      result: { summary: "第一轮已完成", changed: true },
      loopRequest: {
        action: "request_feedback",
        prompt: "可以接受当前结果，或补充意见后再处理一轮。",
        accepts: { text: true, files: [".jsonl", ".xlsx", ".txt"] },
      },
    });
  });

  it("rejects a feedback loop without an accepted feedback channel", () => {
    expect(() => parseStageSkillResult({
      hitl: false,
      result: { summary: "第一轮已完成" },
      loop: { action: "request_feedback", prompt: "请反馈", accepts: { text: false, files: [] } },
    })).toThrow("loop.accepts");
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
        contents: [{
          id: "upgrade-inventory",
          title: "升级问题清单",
          format: "markdown",
          content: "## 目标现状\n\n保留原有业务语义。",
        }],
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
      question: {
        format: "form",
        title: "确认加固方案",
        contents: [{ id: "upgrade-inventory", format: "markdown" }],
        questions: [{ id: "hardening_tier" }, { id: "focus" }],
      },
    });
  });

  it("accepts read-only Markdown pages without interactive questions", () => {
    expect(parseStageSkillResult({
      hitl: true,
      question: {
        format: "form",
        title: "查看分析材料",
        contents: [{ id: "analysis", title: "分析结果", format: "markdown", content: "# 结论\n\n暂无 P0 问题。" }],
        questions: [],
      },
    })).toMatchObject({ kind: "waiting", question: { contents: [{ id: "analysis" }], questions: [] } });
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

  it("rejects a form without read-only content or questions", () => {
    expect(() => parseStageSkillResult({
      hitl: true,
      question: { format: "form", title: "空表单", contents: [], questions: [] },
    })).toThrow("至少需要一项内容");
  });

  it("rejects an ambiguous waiting result", () => {
    expect(() => parseStageSkillResult({ hitl: true, question: { content: "missing tag" } }))
      .toThrow("question.tag");
  });
});
