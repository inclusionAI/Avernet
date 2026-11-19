import { once } from "node:events";
import Database from "better-sqlite3";
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { DashboardRepository } from "../../repositories/dashboard-repository.js";
import { createDashboardRouter, type DashboardScope } from "../dashboard.js";

const from = 1_000_000;
const to = from + 300;
const viewer: DashboardScope = { kind: "workflows", workflowIds: ["wf-a"] };
let db: SqliteDatabase;
let raw: Database.Database;
let server: ReturnType<express.Application["listen"]> | undefined;

beforeEach(() => {
  raw = new Database(":memory:");
  db = new SqliteDatabase(raw);
  raw.exec(`
    CREATE TABLE flow_runs (
      flow_id TEXT, workflow_id TEXT COLLATE NOCASE, workflow_title TEXT, status TEXT,
      started_at INTEGER, completed_at INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
      node_count INTEGER, user_id TEXT
    );
    CREATE TABLE workflow_repair_release_records (record_kind TEXT, record_id TEXT);
    CREATE TABLE node_executions (
      flow_id TEXT, workflow_id TEXT COLLATE NOCASE, node_id TEXT, node_title TEXT,
      error_text TEXT, status TEXT, started_at INTEGER
    );
    CREATE TABLE workflow_healing_diagnoses (
      flow_id TEXT, workflow_id TEXT COLLATE NOCASE, node_id TEXT, weak_node_id TEXT,
      failure_signature TEXT, failure_mode TEXT, error_text TEXT, gmt_create INTEGER
    );
    CREATE TABLE workflow_healing_suggestions (
      workflow_id TEXT COLLATE NOCASE, status TEXT, verification_status TEXT,
      verification_checked_at INTEGER, gmt_create INTEGER
    );
    CREATE TABLE workflow_healing_outcomes (
      workflow_id TEXT COLLATE NOCASE, action TEXT, succeeded INTEGER, gmt_create INTEGER
    );
  `);
  const run = raw.prepare("INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, 8, ?)");
  const addRun = (flowId: string, workflowId: string, status: string, startedAt: number, duration: number, tokens: number) =>
    run.run(flowId, workflowId, workflowId === "wf-a" ? "Visible" : "Hidden", status, startedAt, startedAt + 80, duration, tokens, flowId);
  addRun("a1", "wf-a", "succeeded", from + 1, 1200, 7);
  addRun("a2", "wf-a", "cancelled", from + 2, 2400, 3);
  addRun("a-prev", "wf-a", "succeeded", from - 60, 600, 6);
  addRun("b1", "wf-b", "failed", from + 2, 30000, 99);
  addRun("b2", "wf-b", "succeeded", from + 3, 60000, 999);
  addRun("b3", "wf-b", "succeeded", from + 4, 80000, 100);
  addRun("b-prev", "wf-b", "failed", from - 60, 2000, 6);
  addRun("case1", "WF-A", "running", from + 8, 100000, 400);
  addRun("trial", "wf-a", "succeeded", from + 9, 80000, 400);
  raw.exec("INSERT INTO workflow_repair_release_records VALUES ('trial_run', 'trial')");

  const diagnosis = raw.prepare("INSERT INTO workflow_healing_diagnoses VALUES (?, ?, ?, NULL, ?, 'timeout', ?, ?)");
  diagnosis.run("a2", "wf-a", "a-node", "timeout", "Visible runtime detail", from + 2);
  diagnosis.run("a1", "wf-a", "a-cache", "cache", "Visible cache detail", from + 3);
  for (const id of ["b1", "b2", "b3"]) {
    diagnosis.run(id, "wf-b", "b-node", "timeout", "Hidden runtime detail", from + 2);
  }
  diagnosis.run("trial", "wf-a", "a-trial", "trial", "Trial runtime detail", from + 9);

  const node = raw.prepare("INSERT INTO node_executions VALUES (?, ?, ?, ?, ?, 'failed', ?)");
  node.run("a2", "wf-a", "a-node", "Visible node", "Visible runtime detail", from + 2);
  for (const id of ["b1", "b2", "b3"]) {
    node.run(id, "wf-b", "b-node", "Hidden node", "Hidden runtime detail", from + 2);
  }
  const suggestion = raw.prepare("INSERT INTO workflow_healing_suggestions VALUES (?, ?, ?, ?, ?)");
  suggestion.run("wf-a", "applied_unverified", "not_started", null, from + 2);
  suggestion.run("wf-a", "applied", "verified", from + 2, from + 2);
  suggestion.run("wf-b", "applied", "verified", from + 2, from + 2);
  suggestion.run("wf-b", "failed", "recurrence_detected", from + 2, from - 600);
  const outcome = raw.prepare("INSERT INTO workflow_healing_outcomes VALUES (?, 'suggestion_apply', ?, ?)");
  outcome.run("wf-a", 1, from + 2);
  outcome.run("wf-b", 0, from + 2);
  outcome.run(null, 0, from + 2);
});

afterEach(async () => {
  if (server) {
    await new Promise<void>((resolve) => server!.close(() => resolve()));
    server = undefined;
  }
  await db?.close();
});

async function start(getScope: () => DashboardScope) {
  const app = express();
  // Explicit resolver mode must not derive privileges from this marker.
  app.use((req, _res, next) => { req.isAdmin = true; next(); });
  app.use("/api/dashboard", createDashboardRouter(new DashboardRepository(db), null, db, async () => getScope()));
  server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("dashboard fixture did not bind");
  return async (path: string, query = "", range = { from, to }) => {
    const params = `from=${range.from}&to=${range.to}${query ? `&${query}` : ""}`;
    const response = await fetch(`http://127.0.0.1:${address.port}/api/dashboard/${path}?${params}`);
    expect(response.status).toBe(200);
    return response.json();
  };
}

describe("workflow-scoped Dashboard aggregation", () => {
  it("does not change admin KPIs, comparison, global ranking, or existing error examples", async () => {
    const read = await start(() => ({ kind: "all" }));
    expect(await read("overview")).toMatchObject({
      totalRuns: 6, successRate: 3 / 5, prevSuccessRate: 1 / 2, machineDurationP50: 60_000,
      evolution: { diagnosisCount: 5, suggestionCount: 3, applicationAttemptCount: 3, verifiedCount: 2 },
    });
    expect(await read("top-workflows", "limit=1")).toMatchObject({ workflows: [{ workflowId: "wf-b", runCount: 3 }] });
    expect(await read("failure-hotspots", "limit=1")).toMatchObject({
      hotspots: [{ workflowTitle: "Hidden", count: 3, exampleError: "Hidden runtime detail" }],
    });
  });

  it("scopes overview's current and preceding interval, DAU and evolution", async () => {
    const read = await start(() => viewer);
    expect(await read("overview", "workflowId=wf-b")).toMatchObject({
      workflowCount: 1, totalRuns: 2, statusDistribution: { succeeded: 1, cancelled: 1 },
      successRate: 1 / 2, prevSuccessRate: 1, completionSuccessRate: 1 / 2, prevCompletionSuccessRate: 1,
      avgDurationMs: 1200, machineDurationP50: 1200, machineDurationP95: 1200, prevMachineDurationP50: 600,
      durationSampleCount: 1, dau: 3, wau: 3, totalTokenUsage: 10,
      evolution: {
        available: true, verificationAvailable: true, diagnosisCount: 2, diagnosedRunCount: 2,
        issueClusterCount: 2, suggestionCount: 2, applicationAttemptCount: 1, applicationSuccessRate: 1,
        appliedUnverifiedCount: 1, verifiedCount: 1, recurrenceDetectedCount: 0,
      },
    });
    // Comparison requests are ordinary reads of a second range.
    expect(await read("overview", "", { from: from - 300, to: from })).toMatchObject({
      workflowCount: 1, totalRuns: 1, successRate: 1, machineDurationP50: 600,
    });
  });

  it("restricts both daily sources, duration distribution, health, rank and trend before aggregation", async () => {
    const read = await start(() => viewer);
    expect(await read("daily-trend")).toMatchObject({
      dates: [{ runCount: 2, tokenUsage: 10, completionSuccessRate: 1 / 2, machineDurationP50: 1200 }],
    });
    const distribution = await read("duration-distribution");
    expect(distribution.buckets.map((b: { count: number }) => b.count)).toEqual([0, 2, 0, 0, 0]);
    expect(await read("top-workflows", "limit=1")).toMatchObject({ workflows: [{ workflowId: "wf-a", runCount: 2 }] });
    expect(await read("workflow-health")).toMatchObject({
      workflows: [{ workflowId: "wf-a", runCount: 2, completionSuccessRate: 1 / 2, machineDurationP50: 1200 }],
    });
    expect(await read("metric-trend", "metric=runs")).toMatchObject({ available: true, points: [{ value: 2 }] });
    expect(await read("metric-trend", "metric=machineP50")).toMatchObject({ available: true, points: [{ value: 1200 }] });
  });

  it("scopes diagnosis counts and titles before hotspot limit and excludes raw error examples", async () => {
    const read = await start(() => viewer);
    const { hotspots } = await read("failure-hotspots", "limit=2");
    expect(hotspots).toEqual(expect.arrayContaining([
      { nodeLabel: "a-node", workflowTitle: "Visible", sceneName: "", count: 1, sharePct: 50, exampleError: "" },
      { nodeLabel: "a-cache", workflowTitle: "Visible", sceneName: "", count: 1, sharePct: 50, exampleError: "" },
    ]));
    expect(hotspots).toHaveLength(2);
  });

  it("falls back to scoped node data if diagnoses exist only for forbidden workflows", async () => {
    raw.exec("DELETE FROM workflow_healing_diagnoses WHERE workflow_id = 'wf-a'");
    const read = await start(() => viewer);
    expect(await read("failure-hotspots", "limit=1")).toMatchObject({
      hotspots: [{ nodeLabel: "Visible node", workflowTitle: "Visible", count: 1, sharePct: 100, exampleError: "" }],
    });
  });

  it("never falls back to unscoped data on a query failure", async () => {
    const realQuery = db.query.bind(db);
    const query = vi.spyOn(db, "query").mockImplementation((sql, params) => {
      if (sql.includes("COLLATE BINARY IN")) return Promise.reject(new Error("scoped read unavailable"));
      return realQuery(sql, params);
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    try {
      const read = await start(() => viewer);
      expect(await read("failure-hotspots")).toMatchObject({ hotspots: [] });
      expect(query).toHaveBeenCalledTimes(2);
      expect(query.mock.calls.every(([sql]) => sql.includes("COLLATE BINARY IN"))).toBe(true);
    } finally {
      warn.mockRestore();
      query.mockRestore();
    }
  });

  it("does not reuse a previous request's grants or admin access", async () => {
    let scope: DashboardScope = { kind: "all" };
    const read = await start(() => scope);
    expect(await read("top-workflows", "limit=1")).toMatchObject({ workflows: [{ workflowId: "wf-b" }] });
    scope = viewer;
    expect(await read("top-workflows", "limit=1")).toMatchObject({ workflows: [{ workflowId: "wf-a" }] });
    scope = { kind: "workflows", workflowIds: [] };
    expect(await read("top-workflows", "limit=1")).toMatchObject({ workflows: [] });
  });

  it("returns the existing empty shapes for no grants rather than global data", async () => {
    const read = await start(() => ({ kind: "workflows", workflowIds: [] }));
    expect(await read("overview")).toMatchObject({
      workflowCount: 0, totalRuns: 0, prevSuccessRate: null, statusDistribution: {},
      machineDurationP50: null, dau: 0, wau: 0,
      evolution: { diagnosisCount: 0, suggestionCount: 0, applicationAttemptCount: 0, verifiedCount: 0 },
    });
    const distribution = await read("duration-distribution");
    expect(distribution.buckets.map((b: { count: number }) => b.count)).toEqual([0, 0, 0, 0, 0]);
    for (const [path, key] of [
      ["daily-trend", "dates"], ["top-workflows", "workflows"], ["failure-hotspots", "hotspots"],
      ["workflow-health", "workflows"], ["metric-trend", "points"],
    ]) expect(await read(path)).toMatchObject({ [key]: [] });
    for (const path of ["release-efficiency", "release-quality-trend", "workflow-release-stats"]) {
      expect(await read(path)).toMatchObject({ available: false });
    }
  });
});
