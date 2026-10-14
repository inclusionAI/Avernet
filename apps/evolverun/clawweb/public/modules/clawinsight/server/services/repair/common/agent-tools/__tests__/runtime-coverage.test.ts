// @vitest-environment node
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";
import { AgentRuntimeTool } from "../runtime-tool.js";
import type { AgentToolContext, AgentRuntimeInspectInput } from "../contracts.js";

const run = promisify(execFile);
// macOS realpath lacks GNU -e. Only adapt the path guard; the actual read/search
// programs and their returned evidence still run against real files.
const resolverShim = process.platform === "darwin"
  ? 'realpath() { if test "$1" = -e; then shift; fi; if test "$1" = --; then shift; fi; test -e "$1" && command realpath "$1"; }; ' : "";
const context = { taskId: "repair-coverage", target: { environment: "prod", provider: "baas", bindingId: "1", deviceId: "device" } } as AgentToolContext;
const tool = new AgentRuntimeTool({ executeShell: async ({ command }) => {
  try {
    const output = await run("/bin/bash", ["-c", resolverShim + command], { maxBuffer: 2 * 1024 * 1024 });
    return { status: "success", exitCode: 0, ...output, durationMs: 1 };
  } catch (error) {
    const output = error as { stdout: string; stderr: string; code: number };
    return { status: "failed", exitCode: output.code, stdout: output.stdout, stderr: output.stderr, durationMs: 1 };
  }
} });
let directory: string | undefined;
afterEach(async () => { if (directory) await rm(directory, { recursive: true, force: true }); });
async function fixture(text: string) {
  directory = await mkdtemp(join(tmpdir(), "repair-coverage-"));
  const path = join(directory, "sessions.json");
  await writeFile(path, text);
  return path;
}
it("marks a default 200-line index slice as incomplete when more lines exist", async () => {
  const originalKey = "agent:main:session:original:user:owner";
  const path = await fixture(JSON.stringify({ first: { meta: Array.from({ length: 565 }, (_, i) => `line-${i}`) },
    [originalKey]: { sessionId: "original" } }, null, 2) + "\n");
  const result = await tool.inspect(context, { operation: "fs_read", path });
  expect(result.readCoverage).toMatchObject({ startLine: 1, endLine: 200, hasMore: true, eof: false, completeFile: false });
  expect(result.stdout).not.toContain(originalKey);
  const exact = await tool.inspect(context, { operation: "fs_search", path, pattern: originalKey, maxMatches: 20 });
  expect(exact.stdout).toContain(originalKey);
  expect(exact.searchCoverage).toMatchObject({ complete: true, hasMore: false });
});
it("distinguishes reaching EOF from covering the entire file", async () => {
  const path = await fixture("first\nsecond\nthird");
  expect((await tool.inspect(context, { operation: "fs_read", path, startLine: 2, lines: 2 })).readCoverage)
    .toMatchObject({ startLine: 2, endLine: 3, eof: true, hasMore: false, completeFile: false });
  expect((await tool.inspect(context, { operation: "fs_read", path })).readCoverage)
    .toMatchObject({ startLine: 1, endLine: 3, eof: true, completeFile: true });
});
it("reports search overflow without treating its bounded excerpt as a complete search", async () => {
  const path = await fixture("match\nmatch\nmatch\nmatch\n");
  const result = await tool.inspect(context, { operation: "fs_search", path, pattern: "match", maxMatches: 2 });
  expect(result.status).toBe("success");
  expect(result.searchCoverage).toMatchObject({ maxMatches: 2, limitReached: true, hasMore: true, complete: false });
  expect(String(result.stdout).trim().split("\n")).toHaveLength(2);
});
it("preserves output truncation even when the search did not reach its match limit", async () => {
  const path = await fixture("match " + "x".repeat(36000) + "\n");
  const result = await tool.inspect(context, { operation: "fs_search", path, pattern: "match", maxMatches: 20 });
  expect(result.searchCoverage).toMatchObject({ outputTruncated: true, complete: false });
});
it("honours explicit regex mode instead of silently doing a literal search", async () => {
  const path = await fixture("trace-1\ntrace-2\nother\n");
  const request = { operation: "fs_search", path, pattern: "^trace-[0-9]+$", matchMode: "regex" } as AgentRuntimeInspectInput;
  const result = await tool.inspect(context, request);
  expect(result.stdout).toContain("trace-1");
  expect(result.searchCoverage).toMatchObject({ matchMode: "regex", hasMore: false, complete: true });
});
it("does not call a clipped file read complete even when the remote reader reached EOF", async () => {
  const path = await fixture("x".repeat(36000));
  const result = await tool.inspect(context, { operation: "fs_read", path });
  expect(result.readCoverage).toMatchObject({ eof: true, outputTruncated: true, endLine: null, completeFile: false, nextStartLine: null });
});
it("leaves coverage unknown for a transport response without its actual metadata", async () => {
  const incomplete = new AgentRuntimeTool({ executeShell: async () => ({ status: "success", exitCode: 0, stdout: "partial index\n", stderr: "", durationMs: 1 }) });
  expect((await incomplete.inspect(context, { operation: "fs_read", path: "/sessions.json" })).readCoverage)
    .toMatchObject({ eof: null, hasMore: null, metadataVerified: false, completeFile: false });
});
it("distinguishes a complete empty search from an invalid regex", async () => {
  const path = await fixture("other\n");
  const empty = await tool.inspect(context, { operation: "fs_search", path, pattern: "missing" });
  expect(empty.searchCoverage).toMatchObject({ matchingLinesBeforeTimeFilter: 0, complete: true });
  const invalid = await tool.inspect(context, { operation: "fs_search", path, pattern: "[", matchMode: "regex" });
  expect(invalid.status).toBe("failed");
  expect(invalid.searchCoverage).toMatchObject({ complete: false, metadataVerified: false });
});
