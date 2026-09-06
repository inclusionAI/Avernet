import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { BotWorkflowPermissionRepository } from "../../repositories/bot-workflow-permission-repository.js";
import { createEvolveRouter } from "../evolve.js";
import { createInternalTaskGuardRouter } from "../internal/task-guard.js";
import { digestCanonicalJson } from "../../services/evolution/contracts.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;
const dispatch = vi.fn();

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db);
  await db.exec("ALTER TABLE workflow_healing_suggestions ADD COLUMN proposal_json TEXT");
  await db.exec("ALTER TABLE workflow_healing_suggestions ADD COLUMN proposal_digest TEXT");
  await db.exec("ALTER TABLE workflow_healing_suggestions ADD COLUMN apply_task_id TEXT");
  await db.exec(`CREATE TABLE workflow_run_evidence_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, payload_digest TEXT NOT NULL,
    flow_id TEXT NOT NULL, workflow_id TEXT NOT NULL, node_id TEXT, event_type TEXT NOT NULL,
    producer TEXT NOT NULL, event_seq INTEGER NOT NULL, occurred_at_ms INTEGER NOT NULL,
    payload_json TEXT NOT NULL, gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0
  )`);
  await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL UNIQUE, request_key TEXT NOT NULL UNIQUE,
    scope_type TEXT NOT NULL, scope_json TEXT NOT NULL, flow_id TEXT, workflow_id TEXT, status TEXT NOT NULL,
    evidence_status TEXT, evidence_snapshot_ref TEXT, evidence_snapshot_digest TEXT, evidence_manifest_json TEXT,
    task_id TEXT, step_id TEXT, analysis_version TEXT NOT NULL, result_json TEXT, result_digest TEXT,
    diagnosis_count INTEGER NOT NULL DEFAULT 0, error_code TEXT, requested_by TEXT, requested_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER, state_version INTEGER NOT NULL DEFAULT 0, gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0
  )`);
  const permissionRepo = new BotWorkflowPermissionRepository(db);
  dispatch.mockReset();
  dispatch.mockResolvedValue({ runId: "run-batch", sessionId: "session-batch" });

  await db.exec(`CREATE TABLE IF NOT EXISTS ac_entity_device_binding (
    id INTEGER PRIMARY KEY, device_provider TEXT, device_id TEXT, device_props TEXT, status TEXT, env TEXT
  )`);
  await db.exec(`CREATE TABLE IF NOT EXISTS ac_bots (
    id INTEGER PRIMARY KEY, bot_id TEXT NOT NULL, bot_name TEXT, owner_id TEXT, entity_id TEXT,
    is_delete INTEGER NOT NULL DEFAULT 0, active_engine TEXT, bot_type TEXT, status TEXT,
    binding_id INTEGER, env TEXT
  )`);
  await db.exec(
    "INSERT INTO ac_entity_device_binding (id, device_provider, device_id, status, env) VALUES (1, 'arca', 'ARCA-bot-1', 'active', 'dev')",
  );
  await db.exec(
    `INSERT INTO ac_bots
      (id, bot_id, bot_name, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
     VALUES (1, 'bot-1', '修复 Bot', 'owner-1', 'owner-1', 0, 'openclaw', 'personal', 'active', 1, 'dev')`,
  );
  await permissionRepo.upsert({
    bot_id: "bot-1",
    bot_owner_id: "owner-1",
    workflow_id: "wf-1",
    can_view: 1,
    can_execute: 1,
    can_edit: 1,
  });

  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(repo, {
    db,
    dispatch,
    cancelExecution: vi.fn(),
    botWorkflowPermissionRepo: permissionRepo,
    artifactUrlStore: { createSignedUrl: vi.fn().mockResolvedValue("https://oss.example.test/signed") },
  }));
  app.use("/api/internal/task-guard", createInternalTaskGuardRouter(repo));
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

describe("suggestion batch application", () => {
  it("lists only bots with an explicit bot-level edit grant", async () => {
    await db.exec(
      `INSERT INTO ac_bots
        (id, bot_id, bot_name, owner_id, entity_id, is_delete, active_engine, bot_type, status, binding_id, env)
       VALUES (2, 'bot-owner-only', 'Owner 权限 Bot', 'owner-1', 'owner-1', 0, 'openclaw', 'personal', 'active', 1, 'dev')`,
    );
    await db.exec(
      `INSERT INTO bot_workflow_permissions
        (bot_id, bot_owner_id, workflow_id, env, can_view, can_execute, can_edit, gmt_create, gmt_modified)
       VALUES (NULL, 'owner-1', 'wf-1', 'dev', 1, 1, 1, 1, 1)`,
    );
    const suggestion = await repo.createSuggestion({
      workflowId: "wf-1",
      nodeId: "report",
      failureSignature: "output-contract:report",
      failureMode: "output-contract",
      fixKind: "workflow_patch",
      fixSpec: "修复输出契约",
    });

    const response = await fetch(`${baseUrl}/api/evolve/suggestions/${suggestion.id}/eligible-bots`, {
      headers: { "X-User-Id": "owner-1" },
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      bots: [expect.objectContaining({ botId: "bot-1", accessType: "granted" })],
    });
  });

  it("returns the requested run analysis with only its cited evidence", async () => {
    const flowId = "flow-analysis-result";
    const analysisId = "AN-RESULT-1";
    const result = {
      schemaVersion: "workflow-evolution-analysis/v1",
      analysisId,
      facts: ["report 输出为 object"],
      inferences: ["输出契约与实际结构不一致"],
      unknowns: [],
      diagnoses: [{
        diagnosisId: "DG-RESULT-1",
        flowIds: [flowId],
        nodeId: "report",
        failureSignature: "output-contract:report",
        failureMode: "output-contract",
        severity: "high",
        reasoning: "契约要求 string，但实际返回 object",
        evidenceEventIds: ["EV-RESULT-1", "EV-MISSING"],
      }],
    };
    await db.exec(
      "INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, evolution_analysis_status) VALUES (?, ?, ?, ?, ?)",
      [flowId, "wf-1", "failed", Date.now(), "completed"],
    );
    await db.exec(
      `INSERT INTO workflow_run_evidence_events
       (event_id, payload_digest, flow_id, workflow_id, node_id, event_type, producer, event_seq, occurred_at_ms, payload_json)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ["EV-RESULT-1", digestCanonicalJson({ message: "expected string, actual object", secret: "do-not-expose" }),
        flowId, "wf-1", "report", "node.output_contract_failed", "controller", 1, 1234,
        JSON.stringify({ message: "expected string, actual object", secret: "do-not-expose" })],
    );
    await db.exec(
      `INSERT INTO workflow_evolution_analysis_runs
       (analysis_id, request_key, scope_type, scope_json, flow_id, workflow_id, status, evidence_status,
        evidence_manifest_json, analysis_version, result_json, result_digest, diagnosis_count,
        requested_at_ms, completed_at_ms, state_version)
       VALUES (?, ?, 'single_run', ?, ?, ?, 'completed', 'partial', ?, ?, ?, ?, 1, ?, ?, 1)`,
      [analysisId, "request-result-1", JSON.stringify({ flowIds: [flowId] }), flowId, "wf-1",
        JSON.stringify({ schemaVersion: "workflow-evidence-manifest/v1", capturedAtMs: 1234, flows: [] }),
        "workflow-evolution/v1", JSON.stringify(result), digestCanonicalJson(result), 1234, 2345],
    );

    const response = await fetch(
      `${baseUrl}/api/evolve/runs/${flowId}/analysis-result?analysisId=${analysisId}`,
      { headers: { "X-User-Id": "owner-1" } },
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      analysis: {
        analysisId,
        flowId,
        status: "completed",
        evidenceStatus: "partial",
        facts: ["report 输出为 object"],
        diagnoses: [{
          diagnosisId: "DG-RESULT-1",
          sourceEvidence: [
            expect.objectContaining({ eventId: "EV-RESULT-1", summary: "message: expected string, actual object" }),
            expect.objectContaining({ eventId: "EV-MISSING", missing: true }),
          ],
        }],
      },
    });
    expect(JSON.stringify(await (await fetch(
      `${baseUrl}/api/evolve/runs/${flowId}/analysis-result?analysisId=${analysisId}`,
      { headers: { "X-User-Id": "owner-1" } },
    )).json())).not.toContain("do-not-expose");

    const mismatch = await fetch(
      `${baseUrl}/api/evolve/runs/another-flow/analysis-result?analysisId=${analysisId}`,
      { headers: { "X-User-Id": "owner-1" } },
    );
    expect(mismatch.status).toBe(404);
  });

  it("dispatches the exact analysis id and does not ask the Bot to post a second report", async () => {
    await db.exec(
      "INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, evolution_analysis_status) VALUES (?, ?, ?, ?, ?)",
      ["flow-exact", "wf-1", "failed", Date.now(), null],
    );

    const response = await fetch(`${baseUrl}/api/evolve/runs/flow-exact/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ botId: "bot-1", botEnv: "dev" }),
    });

    expect(response.status).toBe(200);
    const body = await response.json() as { analysisId: string };
    expect(body.analysisId).toMatch(/^AN-/);
    const command = String(dispatch.mock.calls[0]?.[0]?.command ?? "");
    expect(command).toContain(`analyze flow-exact --analysis-id ${body.analysisId}`);
    expect(command).not.toContain("reportUrl");
    expect(command).not.toContain("/report");
  });

  it("records IAM dispatch failures on the V2 analysis run without waiting for a Bot callback", async () => {
    await db.exec(
      "INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, evolution_analysis_status) VALUES (?, ?, ?, ?, ?)",
      ["flow-iam", "wf-1", "failed", Date.now(), null],
    );
    dispatch.mockRejectedValueOnce(new Error("IAM token expired"));

    const response = await fetch(`${baseUrl}/api/evolve/runs/flow-iam/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ botId: "bot-1", botEnv: "dev" }),
    });

    expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({ error: "消息派发失败", message: "IAM token expired" });
    const analysis = (await db.query<{ status: string; error_code: string; task_id: string; step_id: string }>(
      "SELECT status, error_code, task_id, step_id FROM workflow_evolution_analysis_runs WHERE flow_id = ?",
      ["flow-iam"],
    ))[0];
    expect(analysis).toMatchObject({ status: "failed", error_code: "DISPATCH_FAILED" });
    const task = (await db.query<{ config_json: string }>("SELECT config_json FROM ce_tasks WHERE task_id = ?", [analysis.task_id]))[0];
    expect(JSON.parse(task.config_json)).toMatchObject({ flowId: "flow-iam", analysisId: expect.stringMatching(/^AN-/) });
  });

  it("dispatches typed proposals to the selected Bot and tracks application progress", async () => {
    const spec = {
      id: "wf-1", version: "1", title: "Workflow",
      nodes: [{ id: "fetch-data", title: "Fetch", executor: { type: "embedded-agent", prompt: "old" } }],
    };
    const proposal = {
      schemaVersion: "workflow-patch/v1", workflowId: "wf-1", baseSpecDigest: digestCanonicalJson(spec),
      summary: "update prompt",
      operations: [{ op: "replace", nodeId: "fetch-data", path: "/executor/prompt", value: "new" }],
    };
    await db.exec(
      "INSERT INTO workflow_specs (workflow_id, spec_json, title) VALUES (?, ?, ?)",
      ["wf-1", JSON.stringify(spec), "Workflow"],
    );
    const suggestion = await repo.createSuggestion({
      workflowId: "wf-1",
      nodeId: "fetch-data",
      failureSignature: "timeout:fetch-data",
      failureMode: "timeout",
      fixKind: "workflow_patch",
      fixSpec: "update prompt",
    });
    await db.exec(
      "UPDATE workflow_healing_suggestions SET proposal_json = ?, proposal_digest = ? WHERE id = ?",
      [JSON.stringify(proposal), digestCanonicalJson(proposal), suggestion.id],
    );

    const response = await fetch(`${baseUrl}/api/evolve/suggestions/${suggestion.id}/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ botId: "bot-1", botEnv: "dev" }),
    });

    expect(response.status).toBe(200);
    const applyBody = await response.json() as { taskId: string; stepId: string; status: string; suggestionIds: string[] };
    expect(applyBody).toMatchObject({ status: "running", suggestionIds: [String(suggestion.id)] });
    expect(dispatch).toHaveBeenCalledTimes(1);
    const command = String(dispatch.mock.calls[0]?.[0]?.command ?? "");
    expect(command).toMatch(/^\[clawmind-task-guard-apply:v1\]\n/);
    const envelope = JSON.parse(command.slice(command.indexOf("{") )) as {
      command: string;
      spec: string;
      proposal?: Record<string, unknown>;
      deploy: boolean;
      taskContext: { taskId: string; stepId: string; claimToken: string };
    };
    expect(envelope).toMatchObject({
      command: "apply-suggestion wf-1",
      spec: "update prompt",
      proposal,
      deploy: true,
      taskContext: { taskId: applyBody.taskId, stepId: applyBody.stepId },
    });
    expect(envelope.taskContext.claimToken).toMatch(/^[A-Za-z0-9_-]{32,}$/);
    expect(command).not.toContain("Report URL");
    expect(command).not.toContain("suggestion-progress");
    const stored = (await db.query<{ spec_json: string }>(
      "SELECT spec_json FROM workflow_specs WHERE workflow_id = ?",
      ["wf-1"],
    ))[0];
    expect(JSON.parse(stored.spec_json).nodes[0].executor.prompt).toBe("old");
    expect((await repo.findSuggestionById(suggestion.id))?.status).toBe("applying");
    expect(await repo.listSuggestionApplyTasks([String(suggestion.id)])).toEqual([
      expect.objectContaining({
        suggestionId: String(suggestion.id),
        botId: "bot-1",
        botEnv: "dev",
        status: "dispatched",
        progress: expect.objectContaining({ phase: "task_received" }),
      }),
    ]);

    const claimResponse = await fetch(
      `${baseUrl}/api/internal/task-guard/suggestion-applications/${applyBody.taskId}/steps/${applyBody.stepId}/claim`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ botId: "bot-1", claimToken: envelope.taskContext.claimToken }),
      },
    );
    expect(claimResponse.status).toBe(200);
    expect(await claimResponse.json()).toMatchObject({
      ok: true,
      input: {
        workflowId: "wf-1",
        spec: "update prompt",
        proposal,
        deploy: true,
      },
    });

    const duplicateClaim = await fetch(
      `${baseUrl}/api/internal/task-guard/suggestion-applications/${applyBody.taskId}/steps/${applyBody.stepId}/claim`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ botId: "bot-1", claimToken: envelope.taskContext.claimToken }),
      },
    );
    expect(duplicateClaim.status).toBe(409);

    const progressResponse = await fetch(
      `${baseUrl}/api/internal/task-guard/suggestion-applications/${applyBody.taskId}/steps/${applyBody.stepId}/progress`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          botId: "bot-1",
          claimToken: envelope.taskContext.claimToken,
          phase: "editing_workflow",
        }),
      },
    );
    expect(progressResponse.status).toBe(200);
    expect(await repo.listSuggestionApplyTasks([String(suggestion.id)])).toEqual([
      expect.objectContaining({
        status: "running",
        progress: expect.objectContaining({
          phase: "editing_workflow",
          message: "正在修改 Workflow",
        }),
      }),
    ]);

    const newerProposal = {
      ...proposal,
      summary: "newer update prompt",
      operations: [{ op: "replace", nodeId: "fetch-data", path: "/executor/prompt", value: "newer" }],
    };
    await db.exec(
      "UPDATE workflow_healing_suggestions SET proposal_json = ?, proposal_digest = ?, status = 'pending' WHERE id = ?",
      [JSON.stringify(newerProposal), digestCanonicalJson(newerProposal), suggestion.id],
    );

    const managedReport = await fetch(
      `${baseUrl}/api/internal/task-guard/suggestion-applications/${applyBody.taskId}/steps/${applyBody.stepId}/report`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          botId: "bot-1",
          claimToken: envelope.taskContext.claimToken,
          status: "succeeded",
          summary: "已修改并部署",
          output: { deployResult: { deployed: true, workflowId: "wf-1" } },
        }),
      },
    );
    expect(managedReport.status).toBe(200);
    expect((await repo.findSuggestionById(suggestion.id))?.status).toBe("pending");
    expect(await managedReport.json()).toMatchObject({ supersededSuggestionIds: [String(suggestion.id)] });
  });

  it("keeps an edited application spec on the new attempt and does not mutate the suggestion", async () => {
    const proposal = {
      schemaVersion: "workflow-patch/v1", workflowId: "wf-1", baseSpecDigest: "a".repeat(64),
      summary: "原始修复说明",
      operations: [{ op: "replace", nodeId: "report", path: "/executor/prompt", value: "new" }],
    };
    const suggestion = await repo.createSuggestion({
      workflowId: "wf-1",
      nodeId: "report",
      failureSignature: "output-contract:report",
      failureMode: "output-contract",
      fixKind: "workflow_patch",
      fixSpec: "原始修复说明",
    });
    await db.exec(
      "UPDATE workflow_healing_suggestions SET proposal_json = ?, proposal_digest = ? WHERE id = ?",
      [JSON.stringify(proposal), digestCanonicalJson(proposal), suggestion.id],
    );

    const first = await fetch(`${baseUrl}/api/evolve/suggestions/apply-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({
        suggestionIds: [String(suggestion.id)],
        botId: "bot-1",
        botEnv: "dev",
        applicationSpec: "只调整 report 输出契约，保留已有 fallback",
      }),
    });
    expect(first.status).toBe(200);
    const firstBody = await first.json() as { taskId: string };
    const envelope = JSON.parse(String(dispatch.mock.calls[0]?.[0]?.command).slice(
      String(dispatch.mock.calls[0]?.[0]?.command).indexOf("{"),
    )) as { spec: string; proposal?: unknown };
    expect(envelope.spec).toBe("只调整 report 输出契约，保留已有 fallback");
    expect(envelope.proposal).toBeUndefined();
    expect((await repo.findSuggestionById(suggestion.id))?.fix_spec).toBe("原始修复说明");
    expect(await repo.listSuggestionApplyTasks([String(suggestion.id)])).toEqual([
      expect.objectContaining({ applicationSpec: "只调整 report 输出契约，保留已有 fallback" }),
    ]);

    const duplicate = await fetch(`${baseUrl}/api/evolve/suggestions/${suggestion.id}/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ botId: "bot-1", botEnv: "dev" }),
    });
    expect(duplicate.status).toBe(409);
    expect(dispatch).toHaveBeenCalledTimes(1);

    await db.exec("UPDATE ce_steps SET status = 'failed' WHERE task_id = ?", [firstBody.taskId]);
    await db.exec("UPDATE workflow_healing_suggestions SET status = 'failed' WHERE id = ?", [suggestion.id]);
    const retry = await fetch(`${baseUrl}/api/evolve/suggestions/${suggestion.id}/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ botId: "bot-1", botEnv: "dev", applicationSpec: "重试时的新要求" }),
    });
    expect(retry.status).toBe(200);
    expect((await retry.json() as { taskId: string }).taskId).not.toBe(firstBody.taskId);
    expect(dispatch).toHaveBeenCalledTimes(2);
  });

  it("applies pending suggestions in one Bot task and keeps per-suggestion status", async () => {
    const first = await repo.createSuggestion({
      workflowId: "wf-1",
      nodeId: "fetch-data",
      failureSignature: "timeout:fetch-data",
      failureMode: "timeout",
      fixKind: "adjust-timeout",
      fixSpec: "将超时阈值调整为 90 秒",
    });
    const second = await repo.createSuggestion({
      workflowId: "wf-1",
      nodeId: "write-report",
      failureSignature: "retry:write-report",
      failureMode: "repetitive-retry",
      fixKind: "prompt_patch",
      fixSpec: "避免无差别重复写入",
    });

    const response = await fetch(`${baseUrl}/api/evolve/suggestions/apply-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ suggestionIds: [String(first.id), String(second.id)], botId: "bot-1", botEnv: "dev" }),
    });
    expect(response.status).toBe(200);
    const body = await response.json() as { taskId: string; stepId: string; suggestionIds: string[] };

    expect(body.suggestionIds).toEqual([String(first.id), String(second.id)]);
    expect(dispatch).toHaveBeenCalledTimes(1);
    const command = String(dispatch.mock.calls[0]?.[0]?.command ?? "");
    const envelope = JSON.parse(command.slice(command.indexOf("{"))) as {
      spec: string;
      proposal?: { schemaVersion?: string; proposals?: unknown[] };
    };
    expect(envelope.spec).toContain("将超时阈值调整为 90 秒");
    expect(envelope.spec).toContain("避免无差别重复写入");
    expect(envelope.proposal).toBeUndefined();
    expect((await repo.findSuggestionById(first.id))?.status).toBe("applying");
    expect((await repo.findSuggestionById(second.id))?.status).toBe("applying");

    const tasks = await repo.listSuggestionApplyTasks([String(first.id), String(second.id)]);
    expect(tasks).toEqual(expect.arrayContaining([
      expect.objectContaining({ suggestionId: String(first.id), taskId: body.taskId, botId: "bot-1", botEnv: "dev", status: "dispatched" }),
      expect.objectContaining({ suggestionId: String(second.id), taskId: body.taskId, botId: "bot-1", botEnv: "dev", status: "dispatched" }),
    ]));

    const report = await fetch(`${baseUrl}/api/evolve/internal/tasks/${body.taskId}/steps/${body.stepId}/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: "succeeded",
        summary: "两条建议已合并修改并部署",
        output: { deployResult: { deployed: true, workflowId: "wf-1" } },
      }),
    });

    expect(report.status).toBe(200);
    expect((await repo.findSuggestionById(first.id))?.status).toBe("applied_unverified");
    expect((await repo.findSuggestionById(second.id))?.status).toBe("applied_unverified");
  });

  it("records Bot dispatch failures so the page can show a terminal error", async () => {
    const suggestion = await repo.createSuggestion({
      workflowId: "wf-1",
      nodeId: "fetch-data",
      failureSignature: "timeout:dispatch",
      failureMode: "timeout",
      fixKind: "adjust-timeout",
      fixSpec: "将超时阈值调整为 90 秒",
    });
    dispatch.mockRejectedValueOnce(new Error("Bot dispatch unavailable"));

    const response = await fetch(`${baseUrl}/api/evolve/suggestions/${suggestion.id}/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": "owner-1" },
      body: JSON.stringify({ botId: "bot-1", botEnv: "dev" }),
    });

    expect(response.status).toBe(500);
    expect((await repo.findSuggestionById(suggestion.id))?.status).toBe("failed");
    expect(await repo.listSuggestionApplyTasks([String(suggestion.id)])).toEqual([
      expect.objectContaining({
        suggestionId: String(suggestion.id),
        status: "failed",
        errorMessage: "Bot dispatch unavailable",
      }),
    ]);
  });
});
