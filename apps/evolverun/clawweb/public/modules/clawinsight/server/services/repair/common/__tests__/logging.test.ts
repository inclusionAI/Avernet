import { afterEach, expect, it, vi } from "vitest";
import { withWorkflowBackgroundContext } from "@avernet/clawweb-shared/server/services/workflow-background-log";
import { logRepairEvent, repairLogError } from "../logging.js";

afterEach(() => vi.restoreAllMocks());

it("keeps recovery correlation and counters separate from an enclosing workflow context", () => {
  const output = vi.spyOn(console, "info").mockImplementation(() => {});
  withWorkflowBackgroundContext({ runId: "unrelated-run", workflowId: "unrelated-workflow", environment: "prod" }, () => {
    logRepairEvent("session-recovery", "session_recovery_task", "CREATE_FAILED", {
      environment: "pre", targetEnvironment: "prod", eventId: "diagnosis-1", botId: "default", count: 1,
    });
  });
  const line = String(output.mock.calls[0][0]);
  expect(line).toMatch(/^\[session-recovery\] /);
  const event = JSON.parse(line.slice("[session-recovery] ".length));
  expect(event).toMatchObject({ job: "session_recovery_task", event: "CREATE_FAILED", eventId: "diagnosis-1",
    botId: "default", environment: "pre", targetEnvironment: "prod", count: 1, pid: process.pid });
  expect(event.instance).toBeTruthy();
  expect(Number.isFinite(Date.parse(event.ts))).toBe(true);
  expect(line).not.toContain("unrelated-");
});

it("identifies the shared execution monitor without labelling all Repair tasks as recovery", () => {
  const output = vi.spyOn(console, "info").mockImplementation(() => {});
  logRepairEvent("agent-execution-monitor", "agent_execution_monitor", "reconciliation_failed", { taskId: "repair-task" });
  expect(output.mock.calls[0][0]).toMatch(/^\[agent-execution-monitor\] /);
});

it("bounds and redacts error text while excluding exception internals and unknown fields", () => {
  const output = vi.spyOn(console, "info").mockImplementation(() => {});
  const error = Object.assign(new Error("storage failed; password=example"), {
    code: "STORAGE_FAILED", sql: "PRIVATE_SQL", params: ["PRIVATE_PARAMETER"],
  });
  logRepairEvent("session-recovery", "session_recovery_task", "CREATE_FAILED", {
    ...repairLogError(error), error, token: "test-token", payload: "PRIVATE_PAYLOAD", count: Number.NaN,
  } as never);
  const line = String(output.mock.calls[0][0]);
  expect(line).not.toContain("PRIVATE_");
  expect(line).not.toContain("password=example");
  expect(line).not.toContain("test-token");
  expect(JSON.parse(line.slice("[session-recovery] ".length))).toMatchObject({
    errorCode: "STORAGE_FAILED", reason: "storage failed; password=[REDACTED]",
  });
  expect(JSON.parse(line.slice("[session-recovery] ".length))).not.toHaveProperty("count");
  expect(repairLogError(new Error("x".repeat(4096))).reason!.length).toBeLessThan(1100);
  expect(repairLogError(new TypeError("bad cursor"))).toEqual({ errorCode: "TypeError", reason: "bad cursor" });
});

it("does not interrupt business execution when the console sink fails", () => {
  vi.spyOn(console, "info").mockImplementation(() => { throw new Error("sink unavailable"); });
  expect(() => logRepairEvent("session-recovery", "session_recovery_task", "EXECUTION_FAILED", { count: 1 })).not.toThrow();
});
