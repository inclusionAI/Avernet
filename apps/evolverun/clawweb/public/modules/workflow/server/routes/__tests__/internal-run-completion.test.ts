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
vi.mock("../internal-logger.js", () => ({
  apiLog: vi.fn(), apiLogBody: vi.fn(), RUN_COMPLETION_LOG_PATH: "/runs/:flowId/completion",
}));

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

  it.each([null, 0])("preserves phase for an epoch-zero completion that omits it (stored epoch %j)", async (epoch) => {
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

  it.each([undefined, 0, 1, 3])("returns 409 for omitted/conflicting epoch %j without changing phase or status", async (epoch) => {
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
    ["failed", 0, 200], ["failed", 2, 200],
    ["completed", 0, null],
  ] as const)("rejects a phase-changing terminal completion for %s at epoch %i", async (status, epoch, completedAt) => {
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

  it.each([undefined, 0, 2])("does not backfill a preexisting failed row with no completion timestamp (epoch %j)", async epoch => {
    raw.prepare("UPDATE flow_runs SET status = 'failed', retry_epoch = ? WHERE flow_id = 'flow'").run(epoch ?? 0);
    const before = snapshot();
    const write = vi.spyOn(db, "exec");
    const response = await complete({ ...finalMetadata, status: "failed", current_phase: "P5", retry_epoch: epoch });
    expect(response.status).toBe(409);
    await tick();
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(before);
    expect(onTerminal).not.toHaveBeenCalled();
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

  it.each([-1, 0.5, Number.MAX_SAFE_INTEGER + 1, "2", null, false])("rejects malformed retry_epoch %j before writes", async epoch => {
    const before = snapshot();
    const write = vi.spyOn(db, "exec");
    const response = await complete({ ...finalMetadata, current_phase: "P7", retry_epoch: epoch });
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "Bad Request" });
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(before);
  });

  it.each([undefined, null, "P7"])("rejects an epochless positive-attempt phase intent %j without writing", async phase => {
    raw.exec("UPDATE flow_runs SET retry_epoch = 2 WHERE flow_id = 'flow'");
    const before = snapshot();
    const write = vi.spyOn(db, "exec");
    expect((await complete({ ...finalMetadata, current_phase: phase })).status).toBe(409);
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(before);
  });

  it.each([undefined, 0, 2])("allows only epoch zero when the schema truly lacks retry_epoch (sent %j)", async epoch => {
    raw.exec("ALTER TABLE flow_runs DROP COLUMN retry_epoch");
    const before = snapshot();
    const response = await complete({ ...finalMetadata, current_phase: "P7", retry_epoch: epoch });
    expect(response.status).toBe(epoch === 2 ? 409 : 200);
    if (epoch === 2) expect(snapshot()).toEqual(before);
    else expect(await readRun()).toMatchObject({ ...finalMetadata, current_phase: "P7" });
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

  it.each([undefined, 0, 1, 3])("rejects direct positive-attempt phase writes with omitted/wrong epoch %j", async retryEpoch => {
    raw.exec("UPDATE flow_runs SET retry_epoch = 2 WHERE flow_id = 'flow'");
    const before = snapshot();
    expect(await repo.updateCompletionOutcome("flow", { ...completion, currentPhase: "P7", retryEpoch })).toBe("rejected");
    expect(snapshot()).toEqual(before);
  });

  it("returns a direct duplicate without UPDATE and rejects a different phase", async () => {
    const intent = { ...completion, currentPhase: "P7", retryEpoch: 0 };
    expect(await repo.updateCompletionOutcome("flow", intent)).toBe("updated");
    const settled = snapshot();
    rejectCompletionWrites();
    const write = vi.spyOn(db, "exec");
    expect(await repo.updateCompletionOutcome("flow", intent)).toBe("duplicate");
    expect(await repo.updateCompletion("flow", { ...intent, currentPhase: undefined })).toBe(true);
    expect(await repo.updateCompletionOutcome("flow", { ...intent, currentPhase: null })).toBe("rejected");
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(settled);
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

function seedTimeout() {
  vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_000);
  raw.prepare("UPDATE flow_runs SET started_at = ?, retry_epoch = 2 WHERE flow_id = 'flow'")
    .run(1_800_000_000 - 61);
}

const timeoutCompletion = { ...finalMetadata, status: "failed", result_json: timeoutResult,
  total_duration_ms: 61_000, retry_epoch: 2 };

describe("completion phase respects current settlement guards", () => {
  it.each([undefined, 0, 2])("acknowledges failed replays read-only and rejects phase changes at epoch %j", async epoch => {
    raw.prepare("UPDATE flow_runs SET retry_epoch = ? WHERE flow_id = 'flow'").run(epoch ?? 0);
    const first = { ...finalMetadata, status: "failed", current_phase: "P5", retry_epoch: epoch };
    expect((await complete(first)).status).toBe(200);
    await tick();
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith({ flowId: "flow", status: "failed" });
    expect(await readRun()).toMatchObject({ ...finalMetadata, status: "failed", current_phase: "P5" });
    const settled = snapshot();
    rejectCompletionWrites();
    const write = vi.spyOn(db, "exec");
    expect((await complete(first)).status).toBe(200);
    expect((await complete({ ...first, current_phase: undefined })).status).toBe(200);
    expect((await complete({ ...first, current_phase: "P7" })).status).toBe(409);
    expect((await complete({ ...first, current_phase: null })).status).toBe(409);
    await tick();
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(settled);
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

  it.each([undefined, null, "P7"])("persists timeout phase %j once; only identical/omitted phase can replay", async phase => {
    seedTimeout();
    const first = { ...timeoutCompletion, current_phase: phase };
    expect((await complete(first)).status).toBe(200);
    await tick();
    expect(await readRun()).toMatchObject({ status: "failed", current_phase: phase === undefined ? "P4" : phase,
      completed_at: 1_800_000_000, retry_epoch: 2, result_json: timeoutResult });
    const settled = snapshot();
    rejectCompletionWrites();
    const write = vi.spyOn(db, "exec");
    expect((await complete({ ...first, completed_at: 201 })).status).toBe(200);
    expect((await complete({ ...first, current_phase: undefined })).status).toBe(200);
    expect((await complete({ ...first, current_phase: "different-phase" })).status).toBe(409);
    await tick();
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(settled);
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith({ flowId: "flow", status: "failed" });
  });

  it.each(["succeeded", "failed"])("cannot backfill a missing terminal phase after %s closeout", async status => {
    raw.exec("UPDATE flow_runs SET current_phase = NULL WHERE flow_id = 'flow'");
    const first = { ...finalMetadata, status };
    expect((await complete(first)).status).toBe(200);
    await tick();
    expect(await readRun()).toMatchObject({ status, current_phase: null });
    const settled = snapshot();
    rejectCompletionWrites();
    expect((await complete({ ...first, current_phase: null })).status).toBe(200);
    expect((await complete({ ...first, current_phase: "P7" })).status).toBe(409);
    await tick();
    expect(snapshot()).toEqual(settled);
    expect(onTerminal).toHaveBeenCalledTimes(status === "failed" ? 1 : 0);
  });

  it.each([null, "P7", ""])("requires byte-identical explicit phase %j on a succeeded replay", async phase => {
    const first = { ...finalMetadata, current_phase: phase };
    expect((await complete(first)).status).toBe(200);
    const settled = snapshot();
    rejectCompletionWrites();
    const write = vi.spyOn(db, "exec");
    expect((await complete(first)).status).toBe(200);
    expect((await complete({ ...first, current_phase: undefined })).status).toBe(200);
    const different = phase === null ? "P7" : phase === "" ? null : " P7 ";
    expect((await complete({ ...first, current_phase: different })).status).toBe(409);
    expect(write).not.toHaveBeenCalled();
    expect(snapshot()).toEqual(settled);
  });

  it.each(["current_phase", "retry_epoch", "state_json"])("loses a real snapshot race on %s without overwriting phase or notifying", async field => {
    seedTimeout();
    const query = db.query.bind(db);
    let raced = false;
    let concurrent: unknown;
    vi.spyOn(db, "query").mockImplementation(async (sql, params) => {
      const rows = await query(sql, params);
      if (!raced && /SELECT status, started_at, state_json/.test(sql)) {
        raced = true;
        if (field === "current_phase") raw.exec("UPDATE flow_runs SET current_phase = 'concurrent-phase' WHERE flow_id = 'flow'");
        else if (field === "retry_epoch") raw.exec("UPDATE flow_runs SET retry_epoch = 3 WHERE flow_id = 'flow'");
        else raw.exec("UPDATE flow_runs SET state_json = '{}' WHERE flow_id = 'flow'");
        concurrent = snapshot();
      }
      return rows;
    });
    expect((await complete({ ...timeoutCompletion, current_phase: "P7" })).status).toBe(409);
    await tick();
    expect(raced).toBe(true);
    expect(snapshot()).toEqual(concurrent);
    expect(await readRun()).toMatchObject({ status: "running", completed_at: null });
    expect(onTerminal).not.toHaveBeenCalled();
  });

  it.each(["succeeded", "failed"])("does not recount nodes or change phase on a %s replay with omitted fields", async status => {
    const first = { status, completed_at: 200, current_phase: "P7", retry_epoch: 0 };
    expect((await complete(first)).status).toBe(200);
    await tick();
    expect(await readRun()).toMatchObject({ status, current_phase: "P7", succeeded_count: 3, failed_count: 1 });
    const settled = snapshot();
    raw.exec("INSERT INTO node_executions VALUES ('flow', 'late-success', 'succeeded'), ('flow', 'late-failure', 'failed')");
    rejectCompletionWrites();
    expect((await complete({ ...first, current_phase: undefined })).status).toBe(200);
    await tick();
    expect(snapshot()).toEqual(settled);
    expect(onTerminal).toHaveBeenCalledTimes(status === "failed" ? 1 : 0);
  });

  it("keeps accepted final phase and closeout counts stable against late node events", async () => {
    expect((await complete({ ...finalMetadata, current_phase: "P7", retry_epoch: 0 })).status).toBe(200);
    const settled = snapshot();
    expect(await repo.incrementNodeCount("flow", "succeeded_count")).toBe(false);
    expect(await repo.incrementNodeCount("flow", "failed_count")).toBe(false);
    expect(snapshot()).toEqual(settled);
  });
});
