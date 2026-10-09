import express from "express";
import { once } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createDashboardRouter } from "../dashboard.js";
import type { DashboardRepository, OverviewRow } from "../../repositories/dashboard-repository.js";

let server: ReturnType<express.Application["listen"]> | null;
let baseUrl = "";

function createRepo(): DashboardRepository {
  const overview: OverviewRow = {
    workflowCount: 2,
    totalRuns: 8,
    succeededCount: 6,
    failedCount: 2,
    runningCount: 0,
    terminalCount: 8,
    nonTerminalCount: 0,
    avgDurationMs: 1200,
    totalTokenUsage: 10,
    statusDistribution: { succeeded: 6, failed: 2 },
    prevSucceededCount: 5,
    prevFailedCount: 1,
    completionSuccessRate: 0.75,
    prevCompletionSuccessRate: 0.7,
    machineDurationP50: 1000,
    machineDurationP95: 2000,
    prevMachineDurationP50: 1100,
    durationSampleCount: 6,
    dau: 2,
    wau: 4,
    prevDau: 1,
    releasedWorkflowCount: 1,
    newReleasedThisWeek: 1,
    monthlyReleasedCount: 1,
    windowReleasedCount: 1,
  };
  return {
    getOverview: vi.fn().mockResolvedValue(overview),
    getEvolutionMetrics: vi.fn().mockResolvedValue({
      available: true,
      diagnosisCount: 2,
      suggestionCount: 1,
      applicationAttemptCount: 0,
      applicationSucceededCount: 0,
      applicationFailedCount: 0,
      applicationSuccessRate: null,
      appliedUnverifiedCount: 0,
      recurrenceDetectedCount: 0,
      verifiedCount: 0,
    }),
  } as unknown as DashboardRepository;
}

async function startRouter(isAdmin: boolean, repo = createRepo()) {
  const app = express();
  app.use((req, _res, next) => {
    req.isAdmin = isAdmin;
    next();
  });
  app.use("/api/dashboard", createDashboardRouter(repo, null, null));
  const instance = app.listen(0, "127.0.0.1");
  await once(instance, "listening");
  server = instance;
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("test server did not bind a TCP port");
  baseUrl = `http://127.0.0.1:${address.port}`;
  return repo;
}

beforeEach(() => {
  server = null;
  baseUrl = "";
});

afterEach(async () => {
  const activeServer = server;
  server = null;
  if (activeServer) await new Promise<void>((resolve) => activeServer.close(() => resolve()));
});

describe("dashboard admin boundary", () => {
  it("rejects a non-admin before reading global dashboard data", async () => {
    const repo = await startRouter(false);

    const response = await fetch(`${baseUrl}/api/dashboard/overview`);

    expect(response.status).toBe(403);
    expect(await response.json()).toMatchObject({ code: "FORBIDDEN" });
    expect(repo.getOverview).not.toHaveBeenCalled();
  });

  it("allows an admin to read the global dashboard", async () => {
    const repo = await startRouter(true);

    const response = await fetch(`${baseUrl}/api/dashboard/overview`);

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      workflowCount: 2,
      totalRuns: 8,
      terminalCount: 8,
      nonTerminalCount: 0,
      durationSampleCount: 6,
    });
    expect(repo.getOverview).toHaveBeenCalledOnce();
  });
});
