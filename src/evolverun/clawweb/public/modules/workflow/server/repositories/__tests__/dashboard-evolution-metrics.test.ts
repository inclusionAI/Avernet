import { afterEach, beforeEach, describe, expect, it } from "vitest";
import Database from "better-sqlite3";

import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { DashboardRepository } from "../dashboard-repository.js";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";

describe("DashboardRepository evolution metrics", () => {
  let db: SqliteDatabase;
  let dashboardRepo: DashboardRepository;
  let evolveRepo: EvolveRepository;

  beforeEach(async () => {
    db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    dashboardRepo = new DashboardRepository(db);
    evolveRepo = new EvolveRepository(db);
  });

  afterEach(async () => {
    await db.close();
  });

  it("reports evolution operations without calling application success verified", async () => {
    const suggestion = await evolveRepo.createSuggestion({
      workflowId: "wf-1",
      failureSignature: "timeout · cli-script · node-1",
      nodeId: "node-1",
    });
    await evolveRepo.updateSuggestionStatus(suggestion.id, "applying", { actor: "owner-1", action: "applying" });
    await evolveRepo.markSuggestionAppliedUnverified(suggestion.id, { actor: "owner-1" });
    const now = Math.floor(Date.now() / 1000);

    const metrics = await dashboardRepo.getEvolutionMetrics(now - 60, now + 60);

    expect(metrics.available).toBe(true);
    expect(metrics.suggestionCount).toBe(1);
    expect(metrics.appliedUnverifiedCount).toBe(1);
    expect(metrics.verifiedCount).toBe(0);
    expect(metrics.applicationSuccessRate).toBeNull();
  });

  it("keeps base evolution counts when verification columns are not available yet", async () => {
    const legacyDb = {
      dbType: "zdas",
      dialect: { epochToDb: (value: number) => value },
      query: async (sql: string) => {
        if (sql.includes("COUNT(DISTINCT flow_id)")) return [{ cnt: "2" }];
        if (sql.includes("issue_clusters")) return [{ cnt: "7" }];
        if (sql.includes("workflow_healing_diagnoses")) return [{ cnt: "19" }];
        if (sql.includes("workflow_healing_outcomes")) return [{ total: "1", succeeded: "1" }];
        if (sql.includes("verification_status")) throw new Error("Unknown column 'verification_status'");
        if (sql.includes("workflow_healing_suggestions")) return [{ cnt: "7" }];
        return [];
      },
    } as unknown as IDatabase;

    const metrics = await new DashboardRepository(legacyDb).getEvolutionMetrics(1, 2);

    expect(metrics).toMatchObject({
      available: true,
      verificationAvailable: false,
      diagnosisCount: 19,
      diagnosedRunCount: 2,
      issueClusterCount: 7,
      suggestionCount: 7,
      applicationAttemptCount: 1,
      applicationSucceededCount: 1,
    });
    expect(metrics.appliedUnverifiedCount).toBeNull();
    expect(metrics.verifiedCount).toBeNull();
  });

  it("normalizes ZDAS aggregate strings before calculating daily rates", async () => {
    const zdasDb = {
      dbType: "zdas",
      dialect: {},
      query: async (sql: string) => {
        if (sql.includes("GROUP BY")) {
          return [{
            day: "2026-08-31",
            run_count: "2",
            succeeded_count: "2",
            failed_count: "0",
            avg_duration_ms: "1200",
            token_usage: "42",
            lr_s: "2",
            lr_t: "2",
          }];
        }
        return [{ day: "2026-08-31", d: "1000" }, { day: "2026-08-31", d: "1400" }];
      },
    } as unknown as IDatabase;

    const points = await new DashboardRepository(zdasDb).getDailyTrend(1, 2);

    expect(points[0]).toMatchObject({
      runCount: 2,
      succeededCount: 2,
      failedCount: 0,
      avgDurationMs: 1200,
      tokenUsage: 42,
      completionSuccessRate: 1,
      machineDurationP50: 1200,
    });
  });
});
