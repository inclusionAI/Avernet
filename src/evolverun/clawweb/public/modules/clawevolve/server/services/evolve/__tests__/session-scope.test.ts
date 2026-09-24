import { describe, expect, it } from "vitest";
import { normalizeSessionIds, withFrozenSessionIds } from "../session-scope.js";
import { findOfficialStage, validateJsonSchema } from "../stage-catalog.js";

describe("exact Session scope contract", () => {
  it("preserves exact Unicode identifiers, deduplicates and permits omission/empty", () => {
    expect(normalizeSessionIds(["id", "agent:main:session:日报", "id"])).toEqual(["id", "agent:main:session:日报"]);
    expect(normalizeSessionIds(undefined)).toEqual([]);
    expect(normalizeSessionIds([], "service_export", false)).toEqual([]);
  });
  it.each([null, "id", [""], [" id"], ["a/b"], ["a\\b"], ["../id"], ["a.jsonl"], ["id;touch"], ["$(id)"], ["a'b"], ["a".repeat(513)], [1]])("rejects invalid input %j", (value) => {
    expect(() => normalizeSessionIds(value)).toThrow(/session/);
  });
  it("rejects unsupported scopes without treating them as absent", () => {
    expect(() => normalizeSessionIds(["id"], "service_export")).toThrow(/local/);
    expect(() => normalizeSessionIds(["id"], "local", false)).toThrow(/Diagnose/);
  });
  it("replaces old CLI scope but never edits scope-like text inside quoted intent", () => {
    const command = `/clawevolve-diagnose --intent '不要把 --session-id fake 当成平台限定' --session-id 'old' --session-id=other --session-i third --task-id T`;
    const scoped = withFrozenSessionIds(command, { mode: "local", session_ids: ["ID", "日报", "ID"] });
    expect(scoped).toContain(`--intent '不要把 --session-id fake 当成平台限定'`);
    expect(scoped).not.toContain("'old'");
    expect(scoped).not.toContain("other");
    expect(scoped).not.toContain("third");
    expect(scoped).toMatch(/--session-id 'ID' --session-id '日报'$/);
    expect(withFrozenSessionIds(command)).toBe(command);
    expect(withFrozenSessionIds(command, { session_ids: [] })).toBe(command);
  });
  it("quotes even leading-hyphen IDs without turning them into argparse flags", () => {
    expect(withFrozenSessionIds("/clawevolve-diagnose", { session_ids: ["-id"] })).toBe("/clawevolve-diagnose --session-id='-id'");
  });
  it("declares the optional exact array in the official Diagnose source schema", () => {
    const schema = findOfficialStage("diagnose")!.inputSchema.properties!.session_source!;
    expect(schema.properties!.session_ids).toMatchObject({ type: "array", items: { type: "string" } });
    expect(validateJsonSchema(schema, { mode: "local", session_ids: ["id"] })).toBeNull();
    expect(validateJsonSchema(schema, { mode: "local", session_ids: "id" })).not.toBeNull();
  });
});
