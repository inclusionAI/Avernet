// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { resolveRerunCommand } from "../rerun-command.js";

describe("rerun markers", () => {
  const lookupBoundCommand = vi.fn(async () => "facade");
  it("preserves recorded input and release tags while replacing run markers", async () => {
    const command = await resolveRerunCommand({
      workflowId: "wf", lookupBoundCommand,
      inputJson: JSON.stringify({ command: '/workflow run wf --tag release-v2 --task "two words" --run-env test --run-tag old' }),
      markers: { env: "prod", tag: "" },
    });
    expect(command).toBe('/workflow run wf --tag release-v2 --task "two words"');
  });
  it("keeps facade positional input and safely quotes marker values", async () => {
    const command = await resolveRerunCommand({
      workflowId: "wf", lookupBoundCommand,
      inputJson: JSON.stringify({ command: "/facade 12345" }),
      markers: { env: "test", tag: 'review "A" \\ sample' },
    });
    expect(command).toBe('/facade 12345 --run-env="test" --run-tag="review \\"A\\" \\\\ sample"');
  });
  it("replaces equal-form options without touching a quoted business message", async () => {
    expect(await resolveRerunCommand({
      workflowId: "wf", lookupBoundCommand,
      inputJson: JSON.stringify({ command: '/workflow run wf "discuss --run-env test" --run-env=prod --run-tag="old label"' }),
      markers: { env: "test", tag: "new" },
    })).toBe('/workflow run wf "discuss --run-env test" --run-env="test" --run-tag="new"');
  });
  it.each([
    { inputJson: JSON.stringify({ message: "12345" }), expected: '/facade 12345 --run-env="test" --run-tag="smoke"' },
    { inputJson: null, expected: '/workflow run wf --run-env="test" --run-tag="smoke"' },
  ])("marks fallback commands", async ({ inputJson, expected }) => {
    expect(await resolveRerunCommand({ workflowId: "wf", lookupBoundCommand, inputJson, markers: { env: "test", tag: "smoke" } })).toBe(expected);
  });
  it("uses defaults for historical null values and leaves unmarked callers compatible", async () => {
    const input = { workflowId: "wf", lookupBoundCommand, inputJson: null };
    expect(await resolveRerunCommand(input)).toBe("/workflow run wf");
    expect(await resolveRerunCommand({ ...input, markers: { env: null, tag: null } })).toBe('/workflow run wf');
  });
  it("does not send malformed quoted commands", async () => {
    await expect(resolveRerunCommand({ workflowId: "wf", lookupBoundCommand,
      inputJson: JSON.stringify({ command: '/workflow run wf --task "unfinished' }), markers: { env: "test", tag: "" },
    })).rejects.toThrow(/quote/i);
  });
});
