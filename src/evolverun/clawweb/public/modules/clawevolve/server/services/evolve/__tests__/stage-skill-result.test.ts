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

  it("rejects an ambiguous waiting result", () => {
    expect(() => parseStageSkillResult({ hitl: true, question: { content: "missing tag" } }))
      .toThrow("question.tag");
  });
});
