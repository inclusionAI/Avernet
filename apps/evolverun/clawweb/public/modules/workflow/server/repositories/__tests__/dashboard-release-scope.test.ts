import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SqliteDatabase, type IDatabase } from "@avernet/clawweb-shared/server/db";
import { DashboardRepository } from "../dashboard-repository.js";

const from = 1_000_000;
const to = from + 1_000;
let db: SqliteDatabase;
let admin: DashboardRepository;
let viewer: DashboardRepository;

beforeEach(() => {
  const raw = new Database(":memory:");
  db = new SqliteDatabase(raw);
  // Exercise full SQL and bindings locally, substituting only the MySQL-specific
  // timestamp and comparison expressions for the fixture's integer timestamps.
  raw.function("FROM_UNIXTIME", (ts) => ts);
  raw.function("UNIX_TIMESTAMP", (ts) => ts);
  raw.function("DATE_FORMAT", (ts, _format) => new Date(Number(ts) * 1_000).toISOString().slice(0, 10));
  const mysqlFixture: IDatabase = {
    dbType: "mysql",
    dialect: db.dialect,
    query: (sql, params) => db.query(sql.replace(/CAST\(([\w.]+) AS BINARY\)/g, "$1 COLLATE BINARY"), params),
    exec: db.exec.bind(db),
    transaction: db.transaction.bind(db),
    close: db.close.bind(db),
  };
  admin = new DashboardRepository(mysqlFixture);
  viewer = admin.forWorkflows(["wf-visible"]);

  raw.exec(`
    CREATE TABLE workflow_specs (workflow_id TEXT COLLATE NOCASE, title TEXT, gmt_create INTEGER);
    CREATE TABLE workflow_deploy_history (workflow_id TEXT COLLATE NOCASE, action TEXT, gmt_create INTEGER);
    CREATE TABLE flow_runs (
      workflow_id TEXT COLLATE NOCASE, flow_id TEXT, status TEXT, started_at INTEGER, workflow_title TEXT,
      total_duration_ms INTEGER, completed_at INTEGER, total_token_usage INTEGER, node_count INTEGER, user_id TEXT
    );
    CREATE TABLE workflow_repair_release_records (record_kind TEXT, record_id TEXT);
  `);
  const spec = raw.prepare("INSERT INTO workflow_specs VALUES (?, ?, ?)");
  spec.run("wf-visible", "Visible flow", from - 10);
  spec.run("wf-hidden", "Hidden flow", from - 20);
  spec.run("WF-VISIBLE", "Case variant", from - 30);

  const history = raw.prepare("INSERT INTO workflow_deploy_history VALUES (?, ?, ?)");
  history.run("wf-visible", "edit", from - 10);
  history.run("wf-visible", "deploy", from + 1);
  history.run("WF-VISIBLE", "edit", from);
  history.run("WF-VISIBLE", "rollback", from + 4);
  // Disallowed deploys sort ahead of the sole allowed deploy in both release queries.
  for (let i = 0; i < 305; i++) history.run("wf-hidden", "deploy", from + 5 + i);

  const run = raw.prepare("INSERT INTO flow_runs VALUES (?, ?, ?, ?, '', 100, ?, 0, 8, 'fixture-user')");
  run.run("wf-visible", "visible-run", "succeeded", from + 2, from + 3);
  run.run("WF-VISIBLE", "other-run", "failed", from + 2, from + 3);
  run.run("wf-hidden", "hidden-run", "failed", from + 2, from + 3);
});

afterEach(async () => { await db.close(); });

describe("dashboard release scopes", () => {
  it("filters deployment rows before ordering/limiting and scopes dependent lookups", async () => {
    expect(await viewer.getReleaseEfficiency(from, to)).toMatchObject({
      available: true, releaseCount: 1, rollbackCount: 0, successRate: 1, avgReleaseDurationMs: 11_000,
      releases: [{ workflowId: "wf-visible", workflowTitle: "Visible flow", firstRuns: 1, rolledBack: false }],
    });
    expect(await viewer.getReleaseQualityTrend(from, to, "day")).toMatchObject({
      available: true, points: [{ releaseCount: 1, rollbackCount: 0, successRate: 1 }],
    });
    expect(await viewer.getMetricTrend("deploys", "day", from, to)).toMatchObject({
      available: true, points: [{ value: 1 }],
    });
    expect(await viewer.getMetricTrend("releaseSuccessRate", "day", from, to)).toMatchObject({
      available: true, points: [{ value: 1 }],
    });
    expect(await admin.getReleaseQualityTrend(from, to, "day")).toMatchObject({
      available: true, points: [{ releaseCount: 300, successRate: 0 }],
    });
  });

  it("scopes spec-backed stats, release-derived overview and health, and empty grants", async () => {
    expect(await viewer.getWorkflowReleaseStats(from, to)).toMatchObject({
      available: true, workflows: [{ workflowId: "wf-visible", deployCount: 1, rollbackCount: 0, devCycleMs: 11_000, firstRunSuccessRate: 1 }],
    });
    expect(await viewer.getOverview(from, to)).toMatchObject({
      releasedWorkflowCount: 1, newReleasedThisWeek: 1, monthlyReleasedCount: 1, windowReleasedCount: 1,
    });
    expect(await viewer.getWorkflowHealth(from, to)).toMatchObject({
      available: true, workflows: [{ workflowId: "wf-visible", released: true }],
    });
    const empty = admin.forWorkflows([]);
    expect(await empty.getReleaseEfficiency(from, to)).toMatchObject({ available: true, releaseCount: 0, releases: [] });
    expect(await empty.getReleaseQualityTrend(from, to, "day")).toMatchObject({ available: true, points: [] });
    expect(await empty.getWorkflowReleaseStats(from, to)).toMatchObject({ available: true, workflows: [] });
  });
});
