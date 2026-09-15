// @vitest-environment node
import Database from "better-sqlite3";
import { describe, expect, it, vi } from "vitest";
import { runMigrations, SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { sessionAisBaseConfig, sessionAisDeadline, sessionAisParams } from "../session-ais-config.js";
import { reconcileSessionAis, startSessionAisMonitor } from "../session-ais-monitor.js";

async function setup() {
  const sqlite = new Database(":memory:");
  const db = new SqliteDatabase(sqlite);
  await runMigrations(db, "sqlite");
  const repo = new EvolveRepository(db);
  await repo.createTask({ taskId: "SA-1", taskType: "session_analysis", userId: "user", botId: "bot",
    taskName: "AIS test", createdBy: "user", configJson: JSON.stringify({ stepId: "SA-1-AIS", aisBase: {
      snapshotId: 1, packageId: "diagnose", deadlineAt: 10000,
    } }) });
  await repo.createStep({ taskId: "SA-1", stepId: "SA-1-AIS", stepType: "session_ais", stepNo: 1, command: "analysis" });
  await repo.markExternalDispatched("SA-1-AIS", "job", {});
  return { sqlite, repo };
}

describe("session AIS common protocol and recovery", () => {
  it("keeps the explicit skill and arbitrary scenario input, removes old envelope fields", () => {
    expect(sessionAisBaseConfig({ legacySnapshotId: 1, deadlineSeconds: 7200 })).toBeUndefined();
    expect(sessionAisDeadline(90, 500)).toBe(90500);
    const aisBase = sessionAisBaseConfig({ legacySnapshotId: 1, deadlineSeconds: 7200, deployment: { snapshotId: 17, packageId: "clawevolve-ais-diagnose" } }, 0)!;
    const input = { sessionId: "s", question: "why", nested: { preserve: true } };
    const task = JSON.parse(sessionAisParams({ taskId: "t", stepId: "s", attempt: 1,
      clawwebUrl: "https://example.com", aisBase }, "session_analysis", input)["${clawevolve_params}"]);
    expect(task.input).toEqual(input);
    expect(task.runtime).toEqual({ clawwebUrl: "https://example.com", package: { packageId: "clawevolve-ais-diagnose" } });
    expect(Object.keys(task).sort()).toEqual(["attempt", "input", "runtime", "stepId", "taskId", "taskType"]);
  });

  it.each(["failed", "stopped", "success"])("settles remote %s without opening a page", async status => {
    const { sqlite, repo } = await setup();
    try {
      const ais = { getJobStatusDetail: vi.fn().mockResolvedValue({ status, rawStatus: status }), stopExecution: vi.fn() };
      await reconcileSessionAis(repo, ais, 100);
      expect((await repo.findTask("SA-1"))?.status).toBe("failed");
      expect((await repo.findStep("SA-1-AIS"))?.error_code).toBe(status === "success" ? "AIS_RESULT_MISSING" : "AIS_JOB_FAILED");
    } finally { sqlite.close(); }
  });

  it("does not overwrite a successful callback racing with the monitor", async () => {
    const { sqlite, repo } = await setup();
    try {
      const ais = { getJobStatusDetail: vi.fn().mockImplementation(async () => {
        await repo.applySessionAisStatus("SA-1", "SA-1-AIS", { status: "succeeded", output: { success: true } });
        return { status: "failed", rawStatus: "failed" };
      }), stopExecution: vi.fn() };
      await reconcileSessionAis(repo, ais, 100);
      expect((await repo.findTask("SA-1"))?.status).toBe("completed");
      expect(await repo.applySessionAisStatus("SA-1", "SA-1-AIS", { status: "running" })).toBe(false);
    } finally { sqlite.close(); }
  });

  it("ignores query outages until the persisted task deadline", async () => {
    const { sqlite, repo } = await setup();
    try {
      const ais = { getJobStatusDetail: vi.fn().mockRejectedValue(new Error("network")), stopExecution: vi.fn() };
      await reconcileSessionAis(repo, ais, 100);
      expect((await repo.findTask("SA-1"))?.status).toBe("running");
      await reconcileSessionAis(repo, ais, 10001);
      expect((await repo.findStep("SA-1-AIS"))?.error_code).toBe("AIS_DEADLINE_EXCEEDED");
    } finally { sqlite.close(); }
  });
});



describe("Host-owned AIS monitor lifecycle", () => {
  it("starts once and drains the active query before shutdown", async () => {
    vi.useFakeTimers();
    const { sqlite, repo } = await setup();
    let release!: (value: { status: "failed"; rawStatus: string; errorMessage: null }) => void;
    const ais = { getJobStatusDetail: vi.fn(() => new Promise<{ status: "failed"; rawStatus: string; errorMessage: null }>(resolve => { release = resolve; })), stopExecution: vi.fn() };
    const stop = startSessionAisMonitor(repo, ais);
    try {
      expect(startSessionAisMonitor(repo, ais)).toBe(stop);
      await vi.advanceTimersByTimeAsync(90_000);
      expect(ais.getJobStatusDetail).toHaveBeenCalledTimes(1);
      let stopped = false;
      const draining = stop().then(() => { stopped = true; });
      await vi.advanceTimersByTimeAsync(30_000);
      expect(stopped).toBe(false);
      release({ status: "failed", rawStatus: "failed", errorMessage: null });
      await draining;
      expect((await repo.findTask("SA-1"))?.status).toBe("failed");
      await vi.advanceTimersByTimeAsync(60_000);
      expect(ais.getJobStatusDetail).toHaveBeenCalledTimes(1);
    } finally { await stop(); vi.useRealTimers(); sqlite.close(); }
  });
});
