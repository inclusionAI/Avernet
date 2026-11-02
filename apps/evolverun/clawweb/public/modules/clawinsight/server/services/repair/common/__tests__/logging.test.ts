import { existsSync, mkdtempSync, mkdirSync, readFileSync, renameSync, rmSync, truncateSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { withWorkflowBackgroundContext } from "@avernet/clawweb-shared/server/services/workflow-background-log";

const home = vi.hoisted(() => ({ path: "" }));
vi.mock("node:os", async importOriginal => ({ ...await importOriginal<typeof import("node:os")>(), homedir: () => home.path }));
let logFile: string;
beforeEach(() => {
  vi.resetModules();
  home.path = mkdtempSync(join(tmpdir(), "recovery-log-"));
  logFile = join(home.path, "logs/clawweb/clawweb-session-recovery.log");
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); rmSync(home.path, { recursive: true, force: true }); });
function events() {
  return readFileSync(logFile, "utf-8").trimEnd().split("\n").map(line => JSON.parse(line.slice(line.indexOf("{"))));
}

it.each(["CREATE_FAILED", "EXECUTION_FAILED"])("writes %s as a single timestamped file record without a console duplicate", async eventName => {
  const { logRepairEvent } = await import("../logging.js");
  const output = vi.spyOn(console, "info").mockImplementation(() => {});
  vi.useFakeTimers();
  const now = new Date(2026, 9, 30, 21, 4, 5, 6);
  vi.setSystemTime(now);
  withWorkflowBackgroundContext({ runId: "unrelated-run", workflowId: "unrelated-workflow", environment: "prod" }, () => {
    logRepairEvent("session-recovery", "session_recovery_task", eventName, {
      environment: "pre", targetEnvironment: "prod", eventId: "diagnosis-1", botId: "default", count: 1,
    });
  });
  const line = readFileSync(logFile, "utf-8");
  expect(line).toMatch(/^2026-10-30 21:04:05\.006 \[session-recovery\] \{/);
  expect(line.split("\n")).toHaveLength(2);
  expect(events()).toEqual([expect.objectContaining({ job: "session_recovery_task", event: eventName, eventId: "diagnosis-1",
    botId: "default", environment: "pre", targetEnvironment: "prod", count: 1, pid: process.pid, ts: now.toISOString() })]);
  expect(events()[0].instance).toBeTruthy();
  expect(line).not.toContain("unrelated-");
  expect(output).not.toHaveBeenCalled();
});

it("keeps the shared execution monitor on its existing console sink", async () => {
  const { logRepairEvent } = await import("../logging.js");
  const output = vi.spyOn(console, "info").mockImplementation(() => {});
  logRepairEvent("agent-execution-monitor", "agent_execution_monitor", "reconciliation_failed", { taskId: "repair-task" });
  expect(output.mock.calls[0][0]).toMatch(/^\[agent-execution-monitor\] /);
  expect(existsSync(logFile)).toBe(false);
});

it("bounds and redacts error text while excluding exception internals and unknown fields", async () => {
  const { logRepairEvent, repairLogError } = await import("../logging.js");
  const error = Object.assign(new Error("storage failed; password=example\nnext line"), {
    code: "STORAGE_FAILED", sql: "PRIVATE_SQL", params: ["PRIVATE_PARAMETER"],
  });
  logRepairEvent("session-recovery", "session_recovery_task", "CREATE_FAILED", {
    ...repairLogError(error), error, token: "test-token", payload: "PRIVATE_PAYLOAD", count: Number.NaN,
  } as never);
  const line = readFileSync(logFile, "utf-8");
  expect(line).not.toContain("PRIVATE_");
  expect(line).not.toContain("password=example");
  expect(line).not.toContain("test-token");
  expect(events()).toHaveLength(1);
  expect(events()[0]).toMatchObject({ errorCode: "STORAGE_FAILED", reason: "storage failed; password=[REDACTED]\nnext line" });
  expect(events()[0]).not.toHaveProperty("count");
  expect(repairLogError(new Error("x".repeat(4096))).reason!.length).toBeLessThan(1100);
  expect(repairLogError(new TypeError("bad cursor"))).toEqual({ errorCode: "TypeError", reason: "bad cursor" });
});

it("continues at the fixed path after copytruncate and file replacement", async () => {
  const { logRepairEvent } = await import("../logging.js");
  const log = (event: string) => logRepairEvent("session-recovery", "session_recovery_task", event);
  log("before_rotation");
  truncateSync(logFile);
  log("after_copytruncate");
  expect(events().map(event => event.event)).toEqual(["after_copytruncate"]);
  renameSync(logFile, logFile + ".rotated");
  log("after_replacement");
  expect(events().map(event => event.event)).toEqual(["after_replacement"]);
  expect(readFileSync(logFile + ".rotated", "utf-8")).toContain("after_copytruncate");
});

it("reports an unavailable destination once, retries after recovery and never interrupts task execution", async () => {
  const { logRepairEvent } = await import("../logging.js");
  const warning = vi.spyOn(console, "error").mockImplementation(() => {});
  const output = vi.spyOn(console, "info").mockImplementation(() => {});
  writeFileSync(join(home.path, "logs"), "blocks the directory");
  const log = () => logRepairEvent("session-recovery", "session_recovery_task", "EXECUTION_FAILED", { count: 1 });
  expect(log).not.toThrow(); log();
  expect(warning).toHaveBeenCalledTimes(1);
  rmSync(join(home.path, "logs"));
  log();
  expect(events()[0]).toMatchObject({ event: "EXECUTION_FAILED", count: 1 });
  rmSync(logFile); mkdirSync(logFile);
  expect(log).not.toThrow(); log();
  expect(warning).toHaveBeenCalledTimes(2);
  expect(output).not.toHaveBeenCalled();
});
