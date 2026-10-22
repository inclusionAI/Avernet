import { describe, expect, it } from "vitest";
import { recoveryHandbook, savedRecoveryHandbook } from "../handbook.js";
import type { RecoveryStoredTask, RecoveryTimelineEvent } from "../task-contracts.js";

function history(kind: RecoveryTimelineEvent["kind"], handbook: unknown): RecoveryTimelineEvent {
  return { event_id: `event-${kind}`, at_ms: 1, kind, actor: "service", summary: "手册引用", payload: { handbook } };
}
const task = (timeline: RecoveryTimelineEvent[]) => ({ timeline } as RecoveryStoredTask);

describe("recovery handbook references", () => {
  it("routes one category to multiple candidate topics without embedding advice", () => {
    expect(recoveryHandbook("TC.MCP.PERMISSION")).toEqual({
      skillName: "clawevolve-recovery-handbook", handbookVersion: "clawevolve-recovery-handbook/v2", category: "TC.MCP",
      chapters: ["mcp/handbook.md", "file-download/handbook.md"],
      verificationReference: expect.any(String),
    });
  });
  it.each(["TC.MODEL.DATA", "TC.TOOL.PERMISSION", "TC.SKILL.EXECUTION", "TC.SESSION_CONTEXT.DATA"])(
    "shares the file topic with %s", code => {
      expect(recoveryHandbook(code).chapters).toEqual(["file-download/handbook.md"]);
    });
  it.each([null, "TC.NEW.CATEGORY", "TC.MCPX.DATA", "TC.MCP-PERMISSION", "other"])(
    "does not select a fallback topic for %s", code => {
      expect(recoveryHandbook(code).chapters).toEqual([]);
    });
  it("does not let one task modify the next task's candidate list", () => {
    recoveryHandbook("TC.MCP.DATA").chapters.pop();
    expect(recoveryHandbook("TC.MCP.DATA").chapters).toEqual(["mcp/handbook.md", "file-download/handbook.md"]);
  });
  it("restores the saved list instead of rebuilding it from the current catalog", () => {
    const saved = { ...recoveryHandbook("TC.MCP.DATA"), chapters: ["mcp/handbook.md"] };
    expect(savedRecoveryHandbook(task([history("created", saved)]))).toEqual(saved);
  });
  it("uses the latest saved handbook event on resume", () => {
    const original = recoveryHandbook("TC.MCP.DATA"), prepared = recoveryHandbook("TC.MODEL.DATA");
    expect(savedRecoveryHandbook(task([history("created", original), history("handbook", original), history("handbook", prepared)]))).toEqual(prepared);
  });
  it.each(["mcp/handbook.md", null])("normalizes legacy chapter %s without adding current candidates or rewriting history", chapter => {
    const { chapters: _chapters, ...reference } = recoveryHandbook("TC.MCP.DATA");
    const saved = { ...reference, handbookVersion: "clawevolve-recovery-handbook/v1", chapter };
    const stored = task([history("created", saved)]);
    expect(savedRecoveryHandbook(stored)).toEqual({ ...reference, handbookVersion: saved.handbookVersion,
      chapters: chapter ? [chapter] : [] });
    expect(stored.timeline[0].payload.handbook).toEqual(saved);
  });
  it("keeps a missing historical reference missing", () => {
    expect(savedRecoveryHandbook(task([]))).toBeUndefined();
    expect(savedRecoveryHandbook(task([history("created", undefined)]))).toBeUndefined();
  });
});
