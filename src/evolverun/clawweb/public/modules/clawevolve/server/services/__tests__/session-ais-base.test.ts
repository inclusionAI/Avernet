// @vitest-environment node
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { runMigrations, SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { sessionAisBaseConfig, sessionAisDeadline, sessionAisParams } from "../session-ais-config.js";

describe("session analysis AIS base contract", () => {
  it("freezes the selected Snapshot and explicit Skill in the compact envelope", () => {
    expect(sessionAisBaseConfig({ legacySnapshotId: 1, deadlineSeconds: 7200 })).toBeUndefined();
    expect(sessionAisDeadline(90, 500)).toBe(90_500);
    const aisBase = sessionAisBaseConfig({ legacySnapshotId: 1, deadlineSeconds: 7200,
      deployment: { snapshotId: 17, packageId: "clawevolve-ais-diagnose" } }, 0)!;
    const input = { sessionId: "s", question: "why", nested: { preserve: true } };
    const task = JSON.parse(sessionAisParams({ taskId: "t", stepId: "s", attempt: 1,
      clawwebUrl: "https://example.com", aisBase }, "session_analysis", input)["${clawevolve_params}"]);
    expect(task.input).toEqual(input);
    expect(task.runtime).toEqual({ clawwebUrl: "https://example.com",
      package: { packageId: "clawevolve-ais-diagnose" } });
    expect(Object.keys(task).sort()).toEqual(["attempt", "input", "runtime", "stepId", "taskId", "taskType"]);
  });

  it("recovers only session_analysis and settles its current attempt atomically", async () => {
    const sqlite = new Database(":memory:");
    const db = new SqliteDatabase(sqlite);
    await runMigrations(db, "sqlite");
    const repo = new EvolveRepository(db);
    try {
      for (const [taskId, taskType] of [["SA-1", "session_analysis"], ["SE-1", "session_export"]] as const) {
        const stepId = `${taskId}-AIS`;
        await repo.createTask({ taskId, taskType, userId: "user", botId: "bot", taskName: taskId,
          createdBy: "user", configJson: JSON.stringify({ stepId, aisBase: {
            snapshotId: 1, packageId: "diagnose", deadlineAt: 10_000,
          } }) });
        await repo.createStep({ taskId, stepId, stepType: "session_ais", stepNo: 1, command: "analysis" });
        await repo.markExternalDispatched(stepId, `job-${taskId}`, {});
      }
      expect((await repo.listActiveSessionAisTasks(0, 100)).map(task => task.task_id)).toEqual(["SA-1"]);
      await expect(repo.applySessionAisStatus("SE-1", "SE-1-AIS", { status: "failed" })).resolves.toBe(false);
      await expect(repo.applySessionAisStatus("SA-1", "SA-1-AIS", {
        status: "succeeded", output: { success: true },
      })).resolves.toBe(true);
      expect((await repo.findTask("SA-1"))?.status).toBe("completed");
      await expect(repo.applySessionAisStatus("SA-1", "SA-1-AIS", { status: "failed" })).resolves.toBe(false);
    } finally {
      sqlite.close();
    }
  });
});
