import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { digestPlanSource } from "@avernet/clawweb-shared/server/contracts/plan-source";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { freezeDiagnosePlanSource, validateFrozenDiagnosePlanSource } from "../../services/evolve/plan-business.js";
import { createEvolveRouter } from "../evolve.js";

const producer = { taskId: "EV-SOURCE-REVIEW", stepId: "STEP-SOURCE-REVIEW", userId: "user-1", botId: "bot-1" };
function output(empty = false) {
  return { diagnosis: { summary: "诊断", issues: [] }, cases: {
    total: empty ? 0 : 1, goodCount: 0, badCount: empty ? 0 : 1,
    items: empty ? [] : [{ caseId: "case-1", type: "bad", summary: "诊断案例" }],
  } };
}
function source() {
  return {
    schema_version: "plan-source/v2", generated_at: "2026-09-11T00:00:00Z",
    source: { type: "diagnose", id: `diagnose:${producer.taskId}`, producer: "clawevolve-diagnose", bot_id: producer.botId, version: "2" },
    problem: { title: "实际来源", user_guidance: null },
    cases: [{ case_id: "case-1", case_type: "bad", session_id: "session-1", query: "日报", evidence: { observed: "范围内证据" } }],
    analysis: { case_distribution: { bad: 1 }, root_cause_clusters: [] }, planning_hints: {}, extensions: {},
  };
}

describe("Plan Source registration boundary", () => {
  let db: SqliteDatabase;
  let repo: EvolveRepository;
  let server: ReturnType<express.Application["listen"]>;
  let baseUrl: string;
  const dispatch = vi.fn();

  beforeEach(async () => {
    db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    repo = new EvolveRepository(db);
    dispatch.mockReset().mockResolvedValue({ runId: "test-run", sessionId: "test-session" });
    const app = express();
    app.use(express.json());
    app.use("/api/evolve", createEvolveRouter(repo, { dispatch }));
    server = await new Promise((resolve) => { const instance = app.listen(0, () => resolve(instance)); });
    baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
    await repo.createTask({ taskId: producer.taskId, taskType: "diagnose", userId: producer.userId,
      botId: producer.botId, taskName: "来源审阅", configJson: "{}", createdBy: producer.userId });
    await repo.createStep({ stepId: producer.stepId, taskId: producer.taskId, stepType: "diagnose", stepNo: 1, command: "/clawevolve-diagnose" });
  });
  afterEach(async () => {
    vi.restoreAllMocks();
    if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
    await db.close();
  });
  async function report(body: Record<string, unknown>) {
    const response = await fetch(`${baseUrl}/api/evolve/internal/tasks/${producer.taskId}/steps/${producer.stepId}/report`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  }
  async function stored() { return JSON.parse((await repo.findStep(producer.stepId))!.output_json!); }

  it.each(["id", "bot_id", "producer", "type", "owner_user_id", "bot_owner_user_id"])("rejects source identity mismatch: %s", async (key) => {
    const value = source();
    Object.assign(value.source, { [key]: "foreign-identity" });
    expect((await report({ status: "succeeded", output: output(), planSource: value })).status).toBe(422);
    expect((await repo.findStep(producer.stepId))!.output_json).toBeNull();
    expect(dispatch).not.toHaveBeenCalled();
  });

  it.each(["id", "type", "duplicate"])("rejects Source/output case mismatch: %s", async (kind) => {
    const value = source();
    if (kind === "id") value.cases[0].case_id = "foreign-case";
    if (kind === "type") value.cases[0].case_type = "good";
    if (kind === "duplicate") value.cases.push({ ...value.cases[0] });
    expect((await report({ status: "succeeded", output: output(), planSource: value })).status).toBe(422);
    expect((await repo.findStep(producer.stepId))!.output_json).toBeNull();
  });

  it("keeps the frozen descriptor when a legacy replay omits optional Source", async () => {
    expect((await report({ status: "succeeded", output: output(), planSource: source() })).status).toBe(200);
    const first = (await stored()).planSource;
    const dispatchCount = dispatch.mock.calls.length;
    expect((await report({ status: "succeeded", output: output() })).status).toBe(200);
    expect((await stored()).planSource).toEqual(first);
    expect(dispatch).toHaveBeenCalledTimes(dispatchCount);
  });

  it("keeps legacy no_cases successful without Source and forbids historical backfill", async () => {
    expect((await report({ status: "succeeded", output: output(true) })).status).toBe(200);
    expect(await stored()).toEqual(output(true));
    expect((await report({ status: "succeeded", output: output(), planSource: source() })).status).toBe(409);
    expect(await stored()).toEqual(output(true));
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("rejects explicit empty Source and reserved output descriptor before persistence", async () => {
    const empty = source(); empty.cases = [];
    expect((await report({ status: "succeeded", output: output(true), planSource: empty })).status).toBe(422);
    const frozen = freezeDiagnosePlanSource(source(), producer, output());
    expect((await report({ status: "succeeded", output: { ...output(), planSource: frozen } })).status).toBe(422);
    expect((await repo.findStep(producer.stepId))!.output_json).toBeNull();
  });

  it("uses shared digest, clones raw content, and rejects descriptor tampering", () => {
    const raw = source();
    const frozen = freezeDiagnosePlanSource(raw, producer, output());
    expect(frozen.digest).toBe(digestPlanSource(frozen.delivery.content));
    raw.problem.title = "not frozen";
    expect(frozen.delivery.content.problem.title).toBe("实际来源");
    expect(validateFrozenDiagnosePlanSource(frozen, producer, output())).toEqual(frozen);
    expect(() => validateFrozenDiagnosePlanSource({ ...frozen, digest: "sha256:bad" }, producer, output())).toThrow();
    expect(() => validateFrozenDiagnosePlanSource({ ...frozen, producer: { ...producer, userId: "foreign" } }, producer, output())).toThrow();
  });

  it("rejects a changed Source on a terminal report even when output is omitted", async () => {
    await report({ status: "succeeded", output: output(), planSource: source() });
    const changed = source(); changed.problem.title = "different Source";
    const replay = await report({ status: "succeeded", planSource: changed });
    expect(replay.status).toBe(422);
    expect(replay.body.error).toContain("output");
  });

  async function raceReports(bodies: Record<string, unknown>[]) {
    // Hold two reads of the non-terminal row to model independent DB requests.
    // The actual validation, writes and transactions still run unchanged.
    const findStep = repo.findStep.bind(repo);
    let arrivals = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const timer = setTimeout(release, 1000);
    vi.spyOn(repo, "findStep").mockImplementation(async (id) => {
      const row = await findStep(id);
      if (id === producer.stepId && arrivals < 2) {
        arrivals += 1;
        if (arrivals === 2) release();
        await gate;
      }
      return row;
    });
    try {
      return await Promise.all(bodies.map(report));
    } finally { clearTimeout(timer); }
  }

  it("atomically freezes one Source when two first reports race", async () => {
    const first = source();
    const second = source(); second.problem.title = "other concurrent source";
    const replies = await raceReports([
      { status: "succeeded", output: output(), planSource: first },
      { status: "succeeded", output: output(), planSource: second },
    ]);
    expect(replies.map((reply) => reply.status).sort()).toEqual([200, 409]);
    expect((await stored()).planSource.delivery.content).toEqual(replies[0].status === 200 ? first : second);
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("acknowledges identical racing reports without a second downstream dispatch", async () => {
    const body = { status: "succeeded", output: output(), planSource: source() };
    const replies = await raceReports([body, body]);
    expect(replies.map((reply) => reply.status)).toEqual([200, 200]);
    expect(replies.filter((reply) => reply.body.duplicate)).toHaveLength(1);
    expect((await stored()).planSource.delivery.content).toEqual(source());
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it.each([false, true])("does not lose or backfill Source when a legacy report races (legacy first=%s)", async (legacyFirst) => {
    const scoped = { status: "succeeded", output: output(), planSource: source() };
    const legacy = { status: "succeeded", output: output() };
    const bodies = legacyFirst ? [legacy, scoped] : [scoped, legacy];
    const replies = await raceReports(bodies);
    expect(replies.map((reply) => reply.status).sort()).toEqual([200, 409]);
    const winner = bodies[replies.findIndex((reply) => reply.status === 200)];
    const persisted = await stored();
    if ("planSource" in winner) expect(persisted.planSource.delivery.content).toEqual(source());
    else expect(persisted).not.toHaveProperty("planSource");
    expect(dispatch).toHaveBeenCalledTimes(1);
  });

  it("repository rejects stale completion/revision snapshots without mutating the winner", async () => {
    const initial = (await repo.findStep(producer.stepId))!;
    const frozen = freezeDiagnosePlanSource(source(), producer, output());
    const accepted = { ...output(), planSource: frozen };
    expect(await repo.updateStepStatus(producer.stepId, {
      status: "succeeded", output: accepted, expectedStep: initial,
    })).toBe(true);
    expect(await repo.updateStepStatus(producer.stepId, {
      status: "succeeded", output: output(), expectedStep: initial,
    })).toBe(false);
    const snapshot = (await repo.findStep(producer.stepId))!;
    const revised = { ...accepted, diagnosis: { summary: "updated diagnosis", issues: [] } };
    expect(await repo.reviseSucceededStep(producer.stepId, { output: revised, expectedStep: snapshot })).toBe(true);
    expect(await repo.reviseSucceededStep(producer.stepId, { output: accepted, expectedStep: snapshot })).toBe(false);
    expect(await stored()).toEqual(revised);
  });
});
