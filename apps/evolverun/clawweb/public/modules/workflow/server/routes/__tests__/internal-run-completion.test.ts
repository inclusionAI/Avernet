// @vitest-environment node
import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import Database from "better-sqlite3";
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { createInternalRunsRouter } from "../internal/runs.js";

// Keep test requests out of the host's on-disk API logs; use the real router and database.
vi.mock("../internal-logger.js", () => ({ apiLog: vi.fn(), apiLogBody: vi.fn() }));

const finalMetadata = {
  status: "succeeded",
  result_json: JSON.stringify({ nodeId: "finish", output: "complete", currentPhase: "not-the-phase" }),
  input_json: JSON.stringify({ task: "completion regression" }),
  total_duration_ms: 3456,
  total_token_usage: 987,
  succeeded_count: 3,
  failed_count: 0,
  completed_at: 200,
};

let raw: Database.Database;
let db: SqliteDatabase;
let repo: FlowRunRepository;
let server: Server;
let base: string;
let onTerminal: ReturnType<typeof vi.fn>;

beforeEach(async () => {
  raw = new Database(":memory:");
  db = new SqliteDatabase(raw);
  repo = new FlowRunRepository(db);
  raw.exec(`
    CREATE TABLE flow_runs (
      id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE NOT NULL, workflow_id TEXT NOT NULL,
      workflow_title TEXT, status TEXT NOT NULL, retry_epoch INTEGER DEFAULT 0,
      current_phase TEXT, result_json TEXT, input_json TEXT, params_json TEXT, state_json TEXT,
      node_count INTEGER DEFAULT 4, succeeded_count INTEGER DEFAULT 0, failed_count INTEGER DEFAULT 0,
      total_duration_ms INTEGER, total_token_usage INTEGER, started_at INTEGER, completed_at INTEGER,
      triggered_by TEXT, identity_key TEXT, credentials_json TEXT,
      origin_session_key TEXT, origin_session_id TEXT, origin_bot_id TEXT, user_id TEXT,
      plugin_version TEXT, engine TEXT, workflow_version INTEGER, workflow_deploy_number INTEGER,
      evolution_analysis_status TEXT, gmt_create INTEGER, gmt_modified INTEGER
    );
    CREATE TABLE node_executions (flow_id TEXT, node_id TEXT, status TEXT);
    INSERT INTO flow_runs (
      flow_id, workflow_id, workflow_title, status, current_phase, result_json, input_json,
      state_json, started_at, origin_bot_id, workflow_version, workflow_deploy_number,
      gmt_create, gmt_modified
    ) VALUES (
      'flow', 'completion-workflow', 'Completion workflow', 'running', 'P4',
      '{"currentPhase":"from-result"}', '{"current_phase":"from-input"}',
      '{"currentPhase":"from-state"}', 100, 'fixture-bot', 2, 5, 100, 100
    );
    INSERT INTO node_executions VALUES
      ('flow', 'one', 'succeeded'), ('flow', 'two', 'succeeded'),
      ('flow', 'three', 'skipped'), ('flow', 'four', 'failed'),
      ('other-flow', 'other', 'succeeded');
  `);
  onTerminal = vi.fn(async () => {});
  const app = express();
  app.use(express.json());
  app.use("/api/internal/runs", createInternalRunsRouter(repo, undefined, undefined, { onTerminal }));
  server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/internal/runs`;
});

afterEach(async () => {
  vi.restoreAllMocks();
  try {
    if (server?.listening) {
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }
  } finally {
    if (db) await db.close();
  }
});

function complete(payload: Record<string, unknown>, flowId = "flow") {
  return fetch(`${base}/${flowId}/completion`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function readRun() {
  const response = await fetch(`${base}/flow`);
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.success).toBe(true);
  return body.data;
}

function snapshot() {
  return raw.prepare("SELECT * FROM flow_runs WHERE flow_id = 'flow'").get();
}

function rejectCompletionWrites() {
  raw.exec(`CREATE TRIGGER reject_completion BEFORE UPDATE ON flow_runs
    BEGIN SELECT RAISE(ABORT, 'completion persistence failed'); END;`);
}

describe("PUT /api/internal/runs/:flowId/completion with SQLite", () => {
  it.each(["P5", "P7", "final-custom-phase", "", " P7 "])("round-trips the explicit final phase %j and all completion metadata", async (phase) => {
    const response = await complete({ ...finalMetadata, current_phase: phase });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
    const stored = await readRun();
    expect(stored).toMatchObject({
      ...finalMetadata,
      current_phase: phase,
      flow_id: "flow",
      workflow_id: "completion-workflow",
      workflow_title: "Completion workflow",
      node_count: 4,
      started_at: 100,
      origin_bot_id: "fixture-bot",
      workflow_version: 2,
      workflow_deploy_number: 5,
      gmt_create: 100,
      gmt_modified: expect.any(Number),
    });
    expect(await new FlowRunRepository(db).findFullByFlowId("flow")).toEqual(stored);
  });

  it.each([null, 0, 2])("preserves phase for a legacy completion that omits it (stored epoch %j)", async (epoch) => {
    raw.prepare("UPDATE flow_runs SET retry_epoch = ? WHERE flow_id = 'flow'").run(epoch);
    const response = await complete(finalMetadata);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
    expect(await readRun()).toMatchObject({ ...finalMetadata, current_phase: "P4", retry_epoch: epoch });
  });

  it("clears the phase only for an explicit null", async () => {
    const response = await complete({ ...finalMetadata, current_phase: null });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
    expect(await readRun()).toMatchObject({ ...finalMetadata, current_phase: null });
  });

  it.each([0, 7, true, false, [], ["P5"], {}, { phase: "P5" }])("rejects current_phase=%j before reaching any write", async (phase) => {
    const before = snapshot();
    const update = vi.spyOn(repo, "updateCompletionOutcome");
    const write = vi.spyOn(db, "exec");
    const response = await complete({ ...finalMetadata, current_phase: phase });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({
      success: false, error: "Bad Request", message: "current_phase must be a string or null",
    });
    expect(update).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(before);
  });

  it("returns 500 on an actual SQLite write failure without changing the run", async () => {
    const before = snapshot();
    rejectCompletionWrites();
    const response = await complete({ ...finalMetadata, current_phase: "P7" });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({
      success: false, error: "Internal Server Error", message: "completion persistence failed",
    });
    expect(snapshot()).toEqual(before);
  });

  it.each([0, 1, 3])("returns 409 for conflicting epoch %i without changing phase or status", async (epoch) => {
    raw.exec("UPDATE flow_runs SET retry_epoch = 2 WHERE flow_id = 'flow'");
    const before = snapshot();
    const response = await complete({ ...finalMetadata, current_phase: "P7", retry_epoch: epoch });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ success: false, error: "Conflict" });
    expect(snapshot()).toEqual(before);
  });

  it.each([
    ["succeeded", 0, 200], ["succeeded", 2, 200],
    ["cancelled", 0, 200], ["cancelled", 2, 200],
    ["canceled", 0, 200], ["canceled", 2, 200],
    ["failed", 2, 200],
  ] as const)("retains the matching-epoch terminal guard for %s at epoch %i", async (status, epoch, completedAt) => {
    raw.prepare("UPDATE flow_runs SET status = ?, retry_epoch = ?, completed_at = ? WHERE flow_id = 'flow'")
      .run(status, epoch, completedAt);
    const before = snapshot();
    const response = await complete({ ...finalMetadata, current_phase: "P7", retry_epoch: epoch });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ success: false, error: "Conflict" });
    expect(snapshot()).toEqual(before);
  });

  it.each([[null, 0], [0, 0], [2, 2]] as const)("writes the phase with a matching epoch (%j, %i)", async (stored, sent) => {
    raw.prepare("UPDATE flow_runs SET retry_epoch = ? WHERE flow_id = 'flow'").run(stored);
    const response = await complete({ ...finalMetadata, current_phase: "P5", retry_epoch: sent });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
    expect(await readRun()).toMatchObject({ ...finalMetadata, current_phase: "P5", retry_epoch: stored });
  });

  it("still accepts matching-epoch failure details before the failure is completed", async () => {
    raw.exec("UPDATE flow_runs SET status = 'failed', retry_epoch = 2 WHERE flow_id = 'flow'");
    const response = await complete({ ...finalMetadata, status: "failed", current_phase: "P5", retry_epoch: 2 });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
    expect(await readRun()).toMatchObject({ ...finalMetadata, status: "failed", current_phase: "P5", retry_epoch: 2 });
  });

  it("preserves omitted JSON fields and reconciles counts without inferring phase from JSON", async () => {
    const response = await complete({ status: "succeeded", completed_at: 200 });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
    expect(await readRun()).toMatchObject({
      status: "succeeded", current_phase: "P4", succeeded_count: 3, failed_count: 1,
      result_json: '{"currentPhase":"from-result"}', input_json: '{"current_phase":"from-input"}',
      state_json: '{"currentPhase":"from-state"}', completed_at: 200,
    });
  });

  it("keeps retry_epoch validation ahead of writes", async () => {
    const before = snapshot();
    const write = vi.spyOn(db, "exec");
    const response = await complete({ ...finalMetadata, current_phase: "P7", retry_epoch: -1 });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "Bad Request" });
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(before);
  });

  it("returns 404 for a missing run without changing other runs", async () => {
    const before = snapshot();
    const response = await complete({ ...finalMetadata, current_phase: "P7" }, "missing");
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({ success: false, error: "Not Found" });
    expect(snapshot()).toEqual(before);
  });
});

describe("FlowRunRepository.updateCompletion phase persistence", () => {
  const completion = { status: "succeeded", completedAt: 200, succeededCount: 3, failedCount: 0 };

  it.each(["P5", "P7", "final-custom-phase", "", " P7 "])("persists an explicit phase %j for direct callers", async (phase) => {
    expect(await repo.updateCompletion("flow", { ...completion, currentPhase: phase })).toBe(true);
    expect(await new FlowRunRepository(db).findFullByFlowId("flow")).toMatchObject({
      status: "succeeded", current_phase: phase, completed_at: 200,
    });
  });

  it.each([{}, { currentPhase: undefined }])("preserves phase for an omitted/undefined direct-call field %j", async (phase) => {
    expect(await repo.updateCompletion("flow", { ...completion, ...phase })).toBe(true);
    expect(await repo.findFullByFlowId("flow")).toMatchObject({ status: "succeeded", current_phase: "P4" });
  });

  it("persists an explicit null for direct callers", async () => {
    expect(await repo.updateCompletion("flow", { ...completion, currentPhase: null })).toBe(true);
    expect(await repo.findFullByFlowId("flow")).toMatchObject({ status: "succeeded", current_phase: null });
  });

  it("propagates real SQLite persistence errors to direct callers", async () => {
    const before = snapshot();
    rejectCompletionWrites();
    await expect(repo.updateCompletion("flow", { ...completion, currentPhase: "P7" }))
      .rejects.toThrow("completion persistence failed");
    expect(snapshot()).toEqual(before);
  });
});

const tick = () => new Promise<void>(resolve => setImmediate(resolve));
const timeoutResult = JSON.stringify({
  status: "failed", message: JSON.stringify({ reason: "flow_timeout", timeoutSecs: 60, ranForSecs: 61 }),
});

function seedTimeout(budget: unknown = 1) {
  vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_000);
  raw.prepare("UPDATE flow_runs SET started_at = ?, state_json = ?, retry_epoch = 2 WHERE flow_id = 'flow'")
    .run(1_800_000_000 - 61, JSON.stringify({ effectiveFlowTimeoutMinutes: budget,
      workflowSnapshot: { flowTimeoutMinutes: 1 } }));
}

const timeoutCompletion = { ...finalMetadata, status: "failed", result_json: timeoutResult,
  total_duration_ms: 61_000, retry_epoch: 2 };

describe("completion phase respects current settlement guards", () => {
  it.each([undefined, 0, 2])("settles plain failure once and handles its phase supplement at epoch %j", async epoch => {
    raw.prepare("UPDATE flow_runs SET retry_epoch = ? WHERE flow_id = 'flow'").run(epoch ?? 2);
    const first = { ...finalMetadata, status: "failed", current_phase: "P5", retry_epoch: epoch };
    expect((await complete(first)).status).toBe(200);
    await tick();
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith({ flowId: "flow", status: "failed" });
    expect(await readRun()).toMatchObject({ ...finalMetadata, status: "failed", current_phase: "P5" });
    const settled = snapshot();
    const supplement = await complete({ ...first, current_phase: "P7" });
    expect(supplement.status).toBe(epoch === 2 ? 409 : 200);
    await tick();
    if (epoch === 2) expect(snapshot()).toEqual(settled);
    else expect(await readRun()).toMatchObject({ status: "failed", current_phase: "P7" });
    expect(onTerminal).toHaveBeenCalledTimes(1);
  });

  it.each(["succeeded", "cancelled", "canceled", " SUCCEEDED ", "\tcancelled\n", "\u2003canceled\u2003"])(
    "cannot use a plain failure phase patch to overwrite terminal %j", async status => {
      raw.prepare("UPDATE flow_runs SET status = ?, completed_at = 200 WHERE flow_id = 'flow'").run(status);
      const before = snapshot();
      expect((await complete({ ...finalMetadata, status: "failed", current_phase: "P7" })).status).toBe(409);
      await tick();
      expect(snapshot()).toEqual(before);
      expect(onTerminal).not.toHaveBeenCalled();
    });

  it.each(["cancelled", "canceled"])("keeps legacy success phase writes out of %s runs", async status => {
    raw.prepare("UPDATE flow_runs SET status = ?, completed_at = 200 WHERE flow_id = 'flow'").run(status);
    const before = snapshot();
    expect((await complete({ ...finalMetadata, current_phase: "P7" })).status).toBe(409);
    expect(snapshot()).toEqual(before);
  });

  it.each([undefined, null, "P7"])("applies phase %j only on the first accepted timeout, not its duplicate", async phase => {
    seedTimeout();
    expect((await complete({ ...timeoutCompletion, current_phase: phase })).status).toBe(200);
    await tick();
    expect(await readRun()).toMatchObject({ status: "failed", current_phase: phase === undefined ? "P4" : phase,
      completed_at: 1_800_000_000, retry_epoch: 2, result_json: timeoutResult });
    const settled = snapshot();
    expect((await complete({ ...timeoutCompletion, current_phase: "duplicate-must-not-write" })).status).toBe(200);
    await tick();
    expect(snapshot()).toEqual(settled);
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith({ flowId: "flow", status: "failed" });
  });

  it.each([0, null, 2])("does not change phase or notify for invalid/mismatched saved timeout budget %j", async budget => {
    seedTimeout(budget);
    const before = snapshot();
    expect((await complete({ ...timeoutCompletion, current_phase: "P7" })).status).toBe(409);
    await tick();
    expect(snapshot()).toEqual(before);
    expect(onTerminal).not.toHaveBeenCalled();
  });

  it("loses a timeout snapshot race without writing phase or notifying", async () => {
    seedTimeout();
    const query = db.query.bind(db);
    let raced = false;
    vi.spyOn(db, "query").mockImplementation(async (sql, params) => {
      const rows = await query(sql, params);
      if (!raced && /SELECT status, started_at, state_json/.test(sql)) {
        raced = true;
        raw.exec("UPDATE flow_runs SET started_at = started_at + 1 WHERE flow_id = 'flow'");
      }
      return rows;
    });
    expect((await complete({ ...timeoutCompletion, current_phase: "P7" })).status).toBe(409);
    await tick();
    expect(raced).toBe(true);
    expect(await readRun()).toMatchObject({ status: "running", current_phase: "P4", completed_at: null });
    expect(onTerminal).not.toHaveBeenCalled();
  });

  it("keeps accepted final phase and closeout counts stable against late node events", async () => {
    expect((await complete({ ...finalMetadata, current_phase: "P7", retry_epoch: 0 })).status).toBe(200);
    const settled = snapshot();
    expect(await repo.incrementNodeCount("flow", "succeeded_count")).toBe(false);
    expect(await repo.incrementNodeCount("flow", "failed_count")).toBe(false);
    expect(snapshot()).toEqual(settled);
  });
});
