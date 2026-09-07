import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { WorkflowEvolutionRepository } from "../../repositories/workflow-evolution-repository.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;
const dispatch = vi.fn();
const cancelExecution = vi.fn();

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  await db.exec("ALTER TABLE workflow_healing_suggestions ADD COLUMN proposal_json TEXT");
  await db.exec("ALTER TABLE workflow_healing_suggestions ADD COLUMN proposal_digest TEXT");
  await db.exec("ALTER TABLE workflow_healing_suggestions ADD COLUMN apply_task_id TEXT");
  await db.exec(`CREATE TABLE workflow_run_evidence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, payload_digest TEXT NOT NULL,
    flow_id TEXT NOT NULL, workflow_id TEXT NOT NULL, node_id TEXT, event_type TEXT NOT NULL,
    producer TEXT NOT NULL, event_seq INTEGER NOT NULL, occurred_at_ms INTEGER NOT NULL, payload_json TEXT NOT NULL,
    gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0
  )`);
  await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL UNIQUE, request_key TEXT NOT NULL UNIQUE,
    scope_type TEXT NOT NULL, scope_json TEXT NOT NULL, flow_id TEXT, workflow_id TEXT, status TEXT NOT NULL,
    evidence_status TEXT, evidence_snapshot_ref TEXT, evidence_snapshot_digest TEXT, evidence_manifest_json TEXT,
    task_id TEXT, step_id TEXT, analysis_version TEXT NOT NULL, result_json TEXT, result_digest TEXT,
    diagnosis_count INTEGER NOT NULL DEFAULT 0, error_code TEXT, requested_by TEXT, requested_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER, state_version INTEGER NOT NULL DEFAULT 0, gmt_create INTEGER, gmt_modified INTEGER
  )`);
  repo = new EvolveRepository(db);
  dispatch.mockReset();
  cancelExecution.mockReset();
  const app = express();
  app.use(express.json());
  const createSignedUrl = vi.fn();
  createSignedUrl.mockResolvedValue("https://oss.example.test/signed");
  app.use("/api/evolve", createEvolveRouter(repo, {
    dispatch, cancelExecution,
    artifactUrlStore: { createSignedUrl },
  }));
  const startedServer = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  server = startedServer;
  baseUrl = `http://127.0.0.1:${(startedServer.address() as { port: number }).port}`;
});

afterEach(async () => {
  const activeServer = server;
  server = null;
  if (activeServer) await new Promise<void>((resolve) => activeServer.close(() => resolve()));
  await db.close();
});

describe("evolve knowledge endpoints", () => {
  it("normalizes legacy applied suggestions to applied-unverified for clients", async () => {
    const suggestion = await repo.createSuggestion({
      workflowId: "wf-legacy",
      failureSignature: "timeout · fetch-data",
      failureMode: "timeout",
      fixKind: "adjust-timeout",
      fixSpec: "increase timeout",
    });
    await db.exec("UPDATE workflow_healing_suggestions SET status = ? WHERE id = ?", ["applied", suggestion.id]);

    const response = await fetch(`${baseUrl}/api/evolve/suggestions?workflowId=wf-legacy`);
    expect(response.status).toBe(200);
    const body = await response.json() as { suggestions: Array<{ status: string }> };

    expect(body.suggestions[0]?.status).toBe("applied_unverified");
  });

  it("keeps application success separate from effectiveness verification", async () => {
    const suggestion = await repo.createSuggestion({
      workflowId: "wf-observe",
      nodeId: "fetch-orders",
      weakNodeId: "fetch-orders",
      failureSignature: "timeout · cli-script · fetch-orders",
      failureMode: "timeout",
      fixKind: "adjust-timeout",
      fixSpec: "increase timeout",
    });

    await expect(repo.markSuggestionAppliedUnverified(suggestion.id, {
      actor: "owner-1",
      note: "Bot updated the workflow",
    })).rejects.toThrow("只能记录已采纳或应用中的建议");
    await repo.updateSuggestionStatus(suggestion.id, "applying", { actor: "owner-1", action: "applying" });
    const applied = await repo.markSuggestionAppliedUnverified(suggestion.id, {
      actor: "owner-1",
      note: "Bot updated the workflow",
    });

    expect(applied?.status).toBe("applied_unverified");
    expect(applied?.verification_status).toBe("observing");
    expect(applied?.applied_at).toBeTruthy();

    await repo.createDiagnosis({
      diagnosisId: "DG-RECURRENCE",
      flowId: "flow-after-apply",
      workflowId: "wf-observe",
      runId: "flow-after-apply",
      nodeId: "fetch-orders",
      weakNodeId: "fetch-orders",
      failureSignature: "timeout · cli-script · fetch-orders",
      failureMode: "timeout",
      executorType: "cli-script",
    });

    const observed = await repo.findSuggestionById(suggestion.id);
    expect(observed?.status).toBe("applied_unverified");
    expect(observed?.verification_status).toBe("recurrence_detected");
    expect(observed?.recurrence_count).toBe(1);
    expect(observed?.last_recurrence_at).toBeTruthy();

    const verifyRes = await fetch(`${baseUrl}/api/evolve/suggestions/${suggestion.id}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ action: "verify", note: "Owner confirmed the business result" }),
    });
    expect(verifyRes.status).toBe(200);
    const verified = await repo.findSuggestionById(suggestion.id);
    expect(verified?.status).toBe("verified");
    expect(verified?.verification_status).toBe("verified");

    const lessons = await repo.listLessons({ workflowId: "wf-observe" });
    expect(lessons.total).toBe(0);
  });

  it("creates and lists a lesson", async () => {
    const createRes = await fetch(`${baseUrl}/api/evolve/lessons`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        workflowId: "wf-1",
        nodeId: "node-1",
        failureSignature: "timeout · cli-script · tvm-process-single",
        failureMode: "timeout",
        executorType: "cli-script",
        fixKind: "node_patch",
        fixSpec: "前置分块节点 + 超时调至 600s",
        status: "verified",
        confidence: 86,
      }),
    });
    expect(createRes.status).toBe(201);
    const created = (await createRes.json()) as { lesson: { lesson_id: string } };
    expect(created.lesson.lesson_id).toMatch(/^LS-/);

    const listRes = await fetch(`${baseUrl}/api/evolve/lessons?workflowId=wf-1`);
    expect(listRes.status).toBe(200);
    const list = (await listRes.json()) as { lessons: Array<{ lesson_id: string; failure_mode: string }>; total: number };
    expect(list.total).toBe(1);
    expect(list.lessons[0].failure_mode).toBe("timeout");
  });

  it("updates a lesson status", async () => {
    const createRes = await fetch(`${baseUrl}/api/evolve/lessons`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        failureSignature: "sig",
        failureMode: "mode",
        fixKind: "prompt_patch",
        fixSpec: "spec",
        status: "draft",
      }),
    });
    const { lesson } = (await createRes.json()) as { lesson: { lesson_id: string } };

    const patchRes = await fetch(`${baseUrl}/api/evolve/lessons/${lesson.lesson_id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "published" }),
    });
    expect(patchRes.status).toBe(200);
    const updated = (await patchRes.json()) as { lesson: { status: string } };
    expect(updated.lesson.status).toBe("published");
  });

  it("records a lesson outcome and updates stats", async () => {
    const createRes = await fetch(`${baseUrl}/api/evolve/lessons`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        failureSignature: "sig",
        failureMode: "mode",
        fixKind: "prompt_patch",
        fixSpec: "spec",
      }),
    });
    const { lesson } = (await createRes.json()) as { lesson: { lesson_id: string } };

    const outcomeRes = await fetch(`${baseUrl}/api/evolve/lessons/${lesson.lesson_id}/outcomes`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ action: "apply", applied: true, succeeded: true, verdict: "rescued" }),
    });
    expect(outcomeRes.status).toBe(200);
    const result = (await outcomeRes.json()) as { lesson: { hit_count: number; rescued_count: number } };
    expect(result.lesson.hit_count).toBe(1);
    expect(result.lesson.rescued_count).toBe(1);
  });

  it("creates and promotes a diagnosis to a lesson", async () => {
    const createRes = await fetch(`${baseUrl}/api/evolve/run-diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        flowId: "flow-1",
        workflowId: "wf-1",
        runId: "run-1",
        nodeId: "node-1",
        failureSignature: "output-contract · embedded-agent · risk-decision",
        failureMode: "output-contract",
        executorType: "embedded-agent",
        weakNodeId: "risk-decision",
        suggestedFixKind: "prompt_patch",
        errorText: "模型输出缺少 risk_level 字段",
      }),
    });
    expect(createRes.status).toBe(201);
    const created = (await createRes.json()) as { diagnosis: { diagnosis_id: string } };

    const listRes = await fetch(`${baseUrl}/api/evolve/diagnoses?workflowId=wf-1`);
    expect(listRes.status).toBe(200);
    const list = (await listRes.json()) as { diagnoses: Array<{ diagnosis_id: string }>; total: number };
    expect(list.total).toBe(1);
    expect(list.diagnoses[0]?.diagnosis_id).toBe(created.diagnosis.diagnosis_id);

    const promoteRes = await fetch(`${baseUrl}/api/evolve/diagnoses/${created.diagnosis.diagnosis_id}/promote`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        fixSpec: "prompt 末尾追加 JSON schema 示例",
        status: "draft",
      }),
    });
    expect(promoteRes.status).toBe(201);
    const promoted = (await promoteRes.json()) as { lesson: { lesson_id: string; fix_spec: string } };
    expect(promoted.lesson.lesson_id).toMatch(/^LS-/);
    expect(promoted.lesson.fix_spec).toContain("JSON schema");
  });

  it("keeps the same diagnosis id from different flows as separate occurrences", async () => {
    const analysisRepo = new WorkflowEvolutionRepository(db);
    const resultFor = (analysisId: string, flowId: string) => ({
      schemaVersion: "workflow-evolution-analysis/v1",
      analysisId,
      facts: ["report output violates its contract"],
      inferences: ["the output contract type is stale"],
      unknowns: [],
      diagnoses: [{
        diagnosisId: "DG-REPORT-OUTPUT-CONTRACT",
        flowIds: [flowId],
        nodeId: "report",
        failureSignature: "output-contract · embedded-agent · report",
        failureMode: "output-contract",
        severity: "high",
        reasoning: "report returned an object while the contract expected a string",
        evidenceEventIds: [],
      }],
    });

    for (const [index, flowId] of ["flow-1", "flow-2"].entries()) {
      const analysisId = `AN-${index + 1}`;
      await analysisRepo.createAnalysisRun({
        analysisId,
        requestKey: String(index + 1).repeat(64),
        scopeType: "single_run",
        scope: { flowIds: [flowId] },
        flowId,
        workflowId: "wf-repeated",
        analysisVersion: "workflow-evolution/v1",
        requestedAtMs: 1000 + index,
      });
      await analysisRepo.completeAnalysisRun(analysisId, resultFor(analysisId, flowId), 2000 + index);
    }

    const response = await fetch(`${baseUrl}/api/evolve/diagnoses?workflowId=wf-repeated`);
    expect(response.status).toBe(200);
    const body = await response.json() as { diagnoses: Array<{ flow_id: string }>; total: number };

    expect(body.total).toBe(2);
    expect(body.diagnoses.map((diagnosis) => diagnosis.flow_id).sort()).toEqual(["flow-1", "flow-2"]);

    const instanceResponse = await fetch(
      `${baseUrl}/api/evolve/diagnoses?workflowId=wf-repeated&flowId=flow-2&analysisId=AN-2`,
    );
    expect(instanceResponse.status).toBe(200);
    const instance = await instanceResponse.json() as {
      diagnoses: Array<{ analysis_id?: string; flow_ids?: string[] }>;
      total: number;
    };
    expect(instance).toMatchObject({
      total: 1,
      diagnoses: [{ analysis_id: "AN-2", flow_ids: ["flow-2"] }],
    });
  });

  it("keeps legacy POST /api/evolve/diagnoses as task creation", async () => {
    const res = await fetch(`${baseUrl}/api/evolve/diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        taskName: "legacy diagnose task",
        userId: "user-1",
        botId: "bot-1",
        apiKey: "secret",
        model: "GLM-5.1",
        diagnoseIntent: "test",
        maxSessions: 1,
      }),
    });
    expect(res.status).toBeGreaterThanOrEqual(200);
  });

  it("records a suggestion action while preserving the suggestion audit trail", async () => {
    // 1. create a diagnosis
    const createDiagnosisRes = await fetch(`${baseUrl}/api/evolve/run-diagnoses`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        flowId: "flow-1",
        workflowId: "wf-1",
        runId: "run-1",
        nodeId: "node-1",
        failureSignature: "timeout · cli-script · node-1",
        failureMode: "timeout",
        executorType: "cli-script",
        weakNodeId: "node-1",
        suggestedFixKind: "adjust-timeout",
        errorText: "execution timed out",
      }),
    });
    expect(createDiagnosisRes.status).toBe(201);

    const suggestion = await repo.createSuggestion({
      workflowId: "wf-1",
      nodeId: "node-1",
      weakNodeId: "node-1",
      failureSignature: "timeout · cli-script · node-1",
      failureMode: "timeout",
      fixKind: "adjust-timeout",
      fixSpec: "increase timeout",
      impactRunIds: ["flow-1"],
    });

    // 2. fetch suggestions: should include the signature
    const suggestionsRes1 = await fetch(`${baseUrl}/api/evolve/suggestions?workflowId=wf-1`);
    expect(suggestionsRes1.status).toBe(200);
    const suggestions1 = (await suggestionsRes1.json()) as { suggestions: Array<{ id: string; signature: string }>; total: number };
    expect(suggestions1.total).toBe(1);
    expect(suggestions1.suggestions[0].signature).toBe("timeout · cli-script · node-1");

    // 3. record a rejected action
    const actionRes = await fetch(`${baseUrl}/api/evolve/suggestions/${suggestion.id}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        action: "reject",
        note: "not worth fixing now",
      }),
    });
    expect(actionRes.status).toBe(200);
    const actionBody = (await actionRes.json()) as { action: { signature: string; action: string } };
    expect(actionBody.action.signature).toBe("timeout · cli-script · node-1");
    expect(actionBody.action.action).toBe("rejected");

    // 4. fetch suggestions again: processed suggestions remain visible for audit
    const suggestionsRes2 = await fetch(`${baseUrl}/api/evolve/suggestions?workflowId=wf-1`);
    expect(suggestionsRes2.status).toBe(200);
    const suggestions2 = (await suggestionsRes2.json()) as { suggestions: Array<{ id: string; signature: string }>; total: number };
    expect(suggestions2.total).toBe(1);
    expect(suggestions2.suggestions[0]).toEqual(expect.objectContaining({ status: "rejected" }));
  });

  it("returns 501 for removed offline analyze endpoints", async () => {
    const analyzeRes = await fetch(`${baseUrl}/api/evolve/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workflowId: "wf-1", lookbackDays: 30 }),
    });
    expect(analyzeRes.status).toBe(501);

    const flowRes = await fetch(`${baseUrl}/api/evolve/diagnoses/analyze-flow`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flowId: "flow-1", workflowId: "wf-1" }),
    });
    expect(flowRes.status).toBe(501);
  });
});
