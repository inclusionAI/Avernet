import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { WorkflowEvolutionRepository } from "../../repositories/workflow-evolution-repository.js";
import { createEvolveRouter } from "../evolve.js";
import { IssueAggregationRepository } from '../../repositories/issue-aggregation-repository.js';
import { createInternalEvolveRouter } from '../internal/evolve.js';
import { createEvolveKnowledgeRouter } from '../evolve-knowledge.js';

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
  app.use('/internal', createInternalEvolveRouter({ db, evolveRepo: repo }));
  app.use('/restricted', createEvolveKnowledgeRouter(db, repo, {
    getViewByIdsForOwner: async (owner: string) => ({ viewableIds: new Set(owner === 'owner' ? ['wf'] : []) }),
  } as never));
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
  vi.restoreAllMocks();
  const activeServer = server;
  server = null;
  if (activeServer) await new Promise<void>((resolve) => activeServer.close(() => resolve()));
  await db.close();
});

describe("evolve knowledge endpoints", () => {
  it.each([false, true])('refreshes removed run-set sources with partial findings=%s', async (partial) => {
    const insert = async (id: string, flows: string[], findings: string[], time: number) => {
      const result = { schemaVersion: 'workflow-evolution-analysis/v1', analysisId: id, facts: [], inferences: [], unknowns: [],
        diagnoses: findings.map(flow => ({ diagnosisId: flow, flowIds: [flow], nodeId: 'fetch', failureSignature: 'timeout · cli · fetch',
          failureMode: 'timeout', severity: 'high', reasoning: 'network delay', evidenceEventIds: [] })) };
      await db.exec(`INSERT INTO workflow_evolution_analysis_runs
        (analysis_id, request_key, scope_type, scope_json, workflow_id, status, analysis_version, result_json, requested_at_ms, completed_at_ms)
        VALUES (?, ?, 'run_set', ?, 'wf', 'completed', 'v1', ?, ?, ?)`,
      [id, id, JSON.stringify({ flowIds: flows }), JSON.stringify(result), time, time]);
    };
    await insert('old', ['run-a', 'run-b'], ['run-a', 'run-b'], 1);
    const aggregates = new IssueAggregationRepository(db);
    const [old] = await aggregates.prepare('old');
    await aggregates.complete('old', old.id, { summary: 'Old conclusion', unknowns: [], causes: [{
      title: 'Network', conclusion: 'Slow upstream', certainty: 'hypothesis', sourceIds: old.input.sources.map(s => s.sourceId),
    }] });
    await insert('replacement', ['run-a', 'run-c'], partial ? ['run-c'] : [], 2);
    // A partial result uses a different signature, so it cannot accidentally refresh the removed cause.
    if (partial) await db.exec("UPDATE workflow_evolution_analysis_runs SET result_json = replace(result_json, 'timeout · cli · fetch', 'timeout · approval · review') WHERE analysis_id = 'replacement'");
    const jobs = await aggregates.prepare('replacement');
    const refreshed = jobs.find(job => job.input.signature === 'timeout · cli · fetch');
    expect(refreshed?.input.flowIds).toEqual(['run-b']);
    expect(refreshed?.input.sources.map(s => s.flowId)).toEqual(['run-b']);
    expect(jobs).toHaveLength(partial ? 2 : 1);
    expect(await aggregates.prepare('replacement')).toEqual([]);
  });
  it('retains competing cause proposals without overwriting a single executable suggestion', async () => {
    const analyses = new WorkflowEvolutionRepository(db);
    await analyses.createAnalysisRun({ analysisId: 'multi', requestKey: 'multi', workflowId: 'wf', flowId: 'run', scopeType: 'single_run', scope: { flowIds: ['run'] }, analysisVersion: 'v1', requestedAtMs: 1 });
    await analyses.completeAnalysisRun('multi', { schemaVersion: 'workflow-evolution-analysis/v1', analysisId: 'multi', facts: [], inferences: [], unknowns: [],
      diagnoses: [100, 200].map(value => ({ diagnosisId: `d${value}`, flowIds: ['run'], nodeId: 'fetch', failureSignature: 'timeout', failureMode: 'timeout', severity: 'high', reasoning: String(value), evidenceEventIds: [],
        proposal: { schemaVersion: 'workflow-patch/v1', workflowId: 'wf', baseSpecDigest: 'a'.repeat(64), summary: `timeout ${value}`,
          operations: [{ op: 'replace', nodeId: 'fetch', path: '/executor/timeoutMs', value }] } })) }, 2);
    expect((await db.query('SELECT * FROM workflow_healing_suggestions'))).toHaveLength(0);
    const groups = await new IssueAggregationRepository(db).list('wf');
    expect(groups[0].sources.map(s => s.proposal?.summary)).toEqual(expect.arrayContaining(['timeout 100', 'timeout 200']));
    const response = await fetch(`${baseUrl}/api/evolve/issue-groups?workflowId=wf`);
    expect(response.status).toBe(200);
    expect((await response.json() as { groups: unknown[] }).groups).toHaveLength(1);
    expect((await fetch(`${baseUrl}/api/evolve/issue-groups`)).status).toBe(400);
  });
  it('freezes latest-run aggregation input, caches completed results and rejects stale or invented references', async () => {
    const insert = async (id: string, run: string, time: number, findings = true) => {
      const result = { schemaVersion: 'workflow-evolution-analysis/v1', analysisId: id, facts: [], inferences: [], unknowns: [],
        diagnoses: findings ? [{ diagnosisId: 'd', flowIds: [run], nodeId: 'fetch', failureSignature: 'timeout · cli · fetch',
          failureMode: 'timeout', severity: 'high', reasoning: 'network delay', evidenceEventIds: [] }] : [] };
      await db.exec(`INSERT INTO workflow_evolution_analysis_runs
        (analysis_id, request_key, scope_type, scope_json, flow_id, workflow_id, status, analysis_version, result_json, requested_at_ms, completed_at_ms)
        VALUES (?, ?, 'single_run', '{}', ?, 'wf', 'completed', 'v1', ?, ?, ?)`, [id, id, run, JSON.stringify(result), time, time]);
    };
    await insert('a', 'run-a', 1);
    await insert('b', 'run-b', 2);
    const aggregates = new IssueAggregationRepository(db);
    const jobs = await aggregates.prepare('b');
    expect(jobs).toHaveLength(1);
    expect(jobs[0].input.flowIds).toEqual(['run-a', 'run-b']);
    expect((await fetch(`${baseUrl}/restricted/issue-groups?workflowId=wf`)).status).toBe(401);
    expect((await fetch(`${baseUrl}/restricted/issue-groups?workflowId=wf`, { headers: { 'X-User-Id': 'other' } })).status).toBe(403);
    expect((await fetch(`${baseUrl}/restricted/issue-groups?workflowId=wf`, { headers: { 'X-User-Id': 'owner' } })).status).toBe(200);
    await db.exec("UPDATE workflow_evolution_analysis_runs SET task_id = 'task', step_id = 'step' WHERE analysis_id = 'b'");
    vi.spyOn(repo, 'findTask').mockResolvedValue({ bot_id: 'assigned' } as never);
    vi.spyOn(repo, 'findStep').mockResolvedValue({ task_id: 'task', step_type: 'run_analysis' } as never);
    const post = (path: string, body: unknown) => fetch(`${baseUrl}/internal${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    expect((await post('/analysis-runs/b/aggregations', { botId: 'wrong' })).status).toBe(403);
    expect((await post('/analysis-runs/b/aggregations', { botId: 'assigned' })).status).toBe(200);
    const summary = { summary: 'Network delays', causes: [{ title: 'Network', conclusion: 'Slow upstream', certainty: 'hypothesis',
      sourceIds: jobs[0].input.sources.map(s => s.sourceId) }], unknowns: [] };
    await expect(aggregates.complete('b', jobs[0].id, { ...summary, causes: [{ ...summary.causes[0], sourceIds: ['invented'] }] })).rejects.toThrow();
    expect((await post(`/analysis-runs/b/aggregations/${jobs[0].id}`, { botId: 'wrong', result: summary })).status).toBe(403);
    expect((await post(`/analysis-runs/b/aggregations/${jobs[0].id}`, { botId: 'assigned', result: summary })).status).toBe(200);
    expect((await aggregates.list('wf'))[0].summary?.summary).toBe('Network delays');
    expect(await aggregates.prepare('b')).toEqual([]);
    await insert('c', 'run-a', 3, false);
    const updated = await aggregates.list('wf');
    expect(updated[0].flowIds).toEqual(['run-b']);
    expect(updated[0].stale).toBe(true);
    expect((await aggregates.list('other'))).toEqual([]);
    await expect(aggregates.complete('c', jobs[0].id, summary)).rejects.toThrow();
    const retry = await aggregates.prepare('c');
    expect(retry).toHaveLength(1);
    expect(await aggregates.prepare('c')).toEqual([]);
    await db.exec('UPDATE workflow_evolution_analysis_runs SET requested_at_ms = 1 WHERE analysis_id = ?', [retry[0].id]);
    const recovered = await aggregates.prepare('c');
    expect(recovered).toHaveLength(1);
    expect(recovered[0].id).not.toBe(retry[0].id);
    await expect(aggregates.complete('c', retry[0].id, summary)).rejects.toThrow();
  });
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
