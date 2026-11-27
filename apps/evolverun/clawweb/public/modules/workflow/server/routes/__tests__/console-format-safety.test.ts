// @vitest-environment node
import express, { type Router } from "express";
import { once } from "node:events";
import type { Server } from "node:http";
import { format, inspect } from "node:util";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createDashboardRouter } from "../dashboard.js";
import { createInternalApprovalCardsRouter } from "../internal/approval-cards.js";
import { createInternalDeployHistoryRouter } from "../internal/deploy-history.js";
import { createInternalRunsRouter } from "../internal/runs.js";
import { createRunBaasRouter } from "../run-baas-routes.js";
import { createRunsRouter } from "../runs.js";
import { createWorkflowsRouter } from "../workflows.js";

const baas = vi.hoisted(() => ({ sendIntervention: vi.fn() }));
vi.mock("@avernet/clawevolve/server/services/baas-intervention", () => baas);
// Isolate direct route diagnostics from the separately tested console/file logger.
vi.mock("../internal-logger.js", () => ({ apiLog: vi.fn(), apiLogBody: vi.fn() }));

const servers: Server[] = [];
const id = "id-%s-%d-%j-%%";
const message = "failure-%s-%d-%j-%%";

beforeEach(() => {
  vi.clearAllMocks();
  for (const channel of ["log", "info", "warn", "error"] as const) vi.spyOn(console, channel).mockImplementation(() => {});
  baas.sendIntervention.mockResolvedValue({ ok: true, messageId: "message-1", sessionId: "session-new" });
});
afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve()))));
  vi.restoreAllMocks();
});

async function listen(router: Router, admin = false) {
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => { req.isAdmin = admin; next(); });
  app.use(router);
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await once(server, "listening");
  return `http://127.0.0.1:${(server.address() as { port: number }).port}`;
}
function send(url: string, method: string, body: object) {
  return fetch(url, { method, headers: { "content-type": "application/json", "x-user-id": "owner-1" }, body: JSON.stringify(body) });
}
function originalFailure(text = message) {
  return Object.assign(new Error(text), {
    stack: `Error: ${text}\r\n    at originalFailure (%s-%d-%j-%%.ts:7:3)\r\n`,
    config: { headers: { Authorization: "<private-auth-marker>" } },
    token: "<private-token-marker>",
  });
}
function expectDiagnostic(channel: "warn" | "error", literal: string, data: unknown[], error: Error, renderedMessage: string) {
  const calls = vi.mocked(console[channel]).mock.calls.filter(call => call[0] === literal);
  expect(calls).toHaveLength(1);
  const call = calls[0]!;
  expect(call).toEqual([literal, ...data, error.stack]);
  // Exercise Node's actual substitution rules, not just the spy's argument list.
  expect(format(...call)).toBe(`${renderedMessage} ${error.stack}`);
  expect(format(...call)).not.toMatch(/private-|Authorization|config|token/);
}
const deployBody = { packId: "pack-1", workflowId: id, deployNumber: 1, version: 1, action: "deploy", specJson: "{}" };

// Each ID, message and original stack deliberately contains all four format tokens.
describe("route console format strings treat request-derived values as data", () => {
  it.each([409, 500])("preserves deploy-history HTTP %s and literal ID/message/stack", async status => {
    const error = originalFailure(status === 409 ? `Duplicate ${message}` : message);
    const insert = vi.fn().mockRejectedValue(error);
    const base = await listen(createInternalDeployHistoryRouter({ insert } as never, null));
    const response = await send(base, "POST", deployBody);
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ error: status === 409 ? "Conflict" : "Internal Server Error", message: error.message });
    expect(insert).toHaveBeenCalledOnce();
    expectDiagnostic(status === 409 ? "warn" : "error", "[deploy-history] Insert failed for %s: %s", [id, error.message], error,
      `[deploy-history] Insert failed for ${id}: ${error.message}`);
    expect(console[status === 409 ? "error" : "warn"]).not.toHaveBeenCalled();
  });

  it.each([409, 500])("preserves HTTP %s object-ID coercion without logging request properties", async status => {
    const objectId = { secret: "<private-request-id-marker>", nested: { token: "<private-nested-request-marker>" } };
    const error = originalFailure(status === 409 ? `Duplicate ${message}` : message);
    const insert = vi.fn().mockRejectedValue(error);
    const base = await listen(createInternalDeployHistoryRouter({ insert } as never, null));
    const response = await send(base, "POST", { ...deployBody, workflowId: objectId });
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ error: status === 409 ? "Conflict" : "Internal Server Error", message: error.message });
    // Logging-only coercion must not normalize or validate the repository input.
    expect(insert).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ workflowId: objectId }));
    expectDiagnostic(status === 409 ? "warn" : "error", "[deploy-history] Insert failed for %s: %s", ["[object Object]", error.message], error,
      `[deploy-history] Insert failed for [object Object]: ${error.message}`);
    expect(JSON.stringify([...vi.mocked(console.warn).mock.calls, ...vi.mocked(console.error).mock.calls]))
      .not.toMatch(/private-|secret|nested|token/);
    expect(console[status === 409 ? "error" : "warn"]).not.toHaveBeenCalled();
  });

  it("keeps updateVersion failure nonfatal without interpreting its message", async () => {
    const error = originalFailure();
    const insert = vi.fn().mockResolvedValue(undefined);
    const updateVersion = vi.fn().mockRejectedValue(error);
    const base = await listen(createInternalDeployHistoryRouter({ insert } as never, { updateVersion } as never));
    const response = await send(base, "POST", deployBody);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, version: 1 });
    expect(updateVersion).toHaveBeenCalledExactlyOnceWith(id, 1);
    expectDiagnostic("warn", "[deploy-history] updateVersion failed: %s", [error.message], error,
      `[deploy-history] updateVersion failed: ${error.message}`);
    expect(console.error).not.toHaveBeenCalled();
  });

  it("preserves an asynchronous terminal observer's original stack and successful completion", async () => {
    const error = originalFailure();
    const updateCompletionOutcome = vi.fn().mockResolvedValue("updated");
    const onTerminal = vi.fn().mockRejectedValue(error);
    const base = await listen(createInternalRunsRouter({ updateCompletionOutcome } as never, null, undefined, { onTerminal }));
    const response = await send(`${base}/${encodeURIComponent(id)}/completion`, "PUT", { status: "failed" });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true, data: true });
    expect(updateCompletionOutcome).toHaveBeenCalledExactlyOnceWith(id, {
      status: "failed", totalDurationMs: null, totalTokenUsage: null,
      completedAt: expect.any(Number), retryEpoch: undefined,
    });
    await vi.waitFor(() => expect(console.warn).toHaveBeenCalledWith(
      "[internal-runs] terminal observer failed for %s: %s", id, error.message, error.stack));
    expect(onTerminal).toHaveBeenCalledExactlyOnceWith({ flowId: id, status: "failed" });
    expectDiagnostic("warn", "[internal-runs] terminal observer failed for %s: %s", [id, error.message], error,
      `[internal-runs] terminal observer failed for ${id}: ${error.message}`);
    expect(console.error).not.toHaveBeenCalled();
  });

  it.each(["intervene", "rerun", "chat"] as const)("preserves successful BaaS %s despite diagnostic failures", async action => {
    const sessionError = originalFailure(`session ${message}`);
    const eventError = originalFailure(`event ${message}`);
    const run = { workflow_id: "workflow-1", status: "failed", origin_bot_id: "bot-1:owner-1",
      origin_session_key: "session-key", origin_session_id: null, credentials_json: null,
      input_json: JSON.stringify({ command: "/workflow run workflow-1" }) };
    const flowRepo = { findByFlowId: vi.fn().mockResolvedValue(run), findFullByFlowId: vi.fn().mockResolvedValue(run),
      updateSessionInfo: vi.fn().mockRejectedValue(sessionError) };
    const insert = vi.fn().mockRejectedValue(eventError);
    const base = await listen(createRunBaasRouter(flowRepo as never, { insert } as never, null));
    const response = await send(`${base}/${encodeURIComponent(id)}/${action}`, "POST", action === "intervene" ? { action: "retry" } : { message });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(action === "rerun"
      ? { ok: true, flowId: id, newFlowId: "message-1", sessionId: "session-new" }
      : { ok: true, flowId: id, ...(action === "intervene" ? { action: "retry" } : {}), messageId: "message-1", sessionId: "session-new" });
    expect(baas.sendIntervention).toHaveBeenCalledOnce();
    expect(insert).toHaveBeenCalledOnce();
    if (action !== "rerun") {
      expect(flowRepo.updateSessionInfo).toHaveBeenCalledExactlyOnceWith(id, { originSessionId: "session-new" });
      expectDiagnostic("warn", "[runs] failed to persist session_id for %s:", [id], sessionError,
        `[runs] failed to persist session_id for ${id}:`);
    }
    const event = action === "intervene" ? "intervention" : action;
    expectDiagnostic("warn", `[runs] failed to record ${event} event for %s:`, [id], eventError,
      `[runs] failed to record ${event} event for ${id}:`);
    expect(console.warn).toHaveBeenCalledTimes(action === "rerun" ? 1 : 2);
    expect(console.error).not.toHaveBeenCalled();
  });

  it("keeps abort cleanup and audit warnings literal, preserving the successful abort", async () => {
    const cleanupError = originalFailure(`cleanup ${message}`);
    const eventError = originalFailure(`event ${message}`);
    const flowRepo = { findByFlowId: vi.fn().mockResolvedValue({ workflow_id: "workflow-1", status: "running" }),
      abort: vi.fn().mockResolvedValue(true) };
    const nodes = { reconcileStaleRunning: vi.fn().mockResolvedValue(2) };
    const events = { insert: vi.fn().mockRejectedValue(eventError) };
    const controls = { releaseAllSlotsForFlowByFlowId: vi.fn().mockRejectedValue(cleanupError) };
    const base = await listen(createRunsRouter(flowRepo as never, nodes as never, events as never, null, null, controls as never));
    const response = await send(`${base}/${encodeURIComponent(id)}/abort`, "POST", { reason: message });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, flowId: id, status: "cancelled", reconciledNodes: 2 });
    expect(flowRepo.abort).toHaveBeenCalledExactlyOnceWith(id, message);
    expect(events.insert).toHaveBeenCalledOnce();
    expectDiagnostic("warn", "[runs] failed to release flow-control for aborted flow %s:", [id], cleanupError,
      `[runs] failed to release flow-control for aborted flow ${id}:`);
    expectDiagnostic("warn", "[runs] failed to record abort event for %s:", [id], eventError,
      `[runs] failed to record abort event for ${id}:`);
    expect(console.error).not.toHaveBeenCalled();
  });

  it("keeps admin deletion successful when flow-control cleanup fails", async () => {
    const error = originalFailure();
    const flowRepo = { findByFlowId: vi.fn().mockResolvedValue({ workflow_id: "workflow-1" }), deleteByFlowId: vi.fn().mockResolvedValue(true) };
    const nodes = { deleteByFlowId: vi.fn().mockResolvedValue(2) };
    const events = { deleteByFlowId: vi.fn().mockResolvedValue(3) };
    const controls = { releaseAllSlotsForFlowByFlowId: vi.fn().mockRejectedValue(error) };
    const base = await listen(createRunsRouter(flowRepo as never, nodes as never, events as never, null, null, controls as never), true);
    const response = await fetch(`${base}/${encodeURIComponent(id)}`, { method: "DELETE" });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ deleted: true, details: { nodes: 2, events: 3, metrics: 0, alerts: 0 } });
    expect(flowRepo.deleteByFlowId).toHaveBeenCalledExactlyOnceWith(id);
    expectDiagnostic("warn", "[runs] failed to release flow-control for flow %s:", [id], error,
      `[runs] failed to release flow-control for flow ${id}:`);
    expect(console.error).not.toHaveBeenCalled();
  });

  it("keeps workflow save successful with literal permission/history messages and original stacks", async () => {
    const permissionError = originalFailure(`permission ${message}`);
    const historyError = originalFailure(`history ${message}`);
    const spec = { id, nodes: [{ id: "start", executor: { type: "agent" } }] };
    const repo = { existsByWorkflowId: vi.fn().mockResolvedValue(false), upsert: vi.fn().mockResolvedValue({ spec_json: JSON.stringify(spec) }) };
    const permissions = { upsert: vi.fn().mockRejectedValue(permissionError) };
    const history = { getLatestVersion: vi.fn().mockRejectedValue(historyError) };
    const base = await listen(createWorkflowsRouter(repo as never, null, permissions as never, null, history as never));
    const response = await send(`${base}/save`, "POST", { workflowId: id, spec });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(spec);
    expect(repo.upsert).toHaveBeenCalledOnce();
    expectDiagnostic("error", "[clawweb] Failed to write bot permission for workflow %s: %s", [id, permissionError.message], permissionError,
      `[clawweb] Failed to write bot permission for workflow ${id}: ${permissionError.message}`);
    expectDiagnostic("error", "[clawweb] Failed to write deploy_history for workflow %s: %s", [id, historyError.message], historyError,
      `[clawweb] Failed to write deploy_history for workflow ${id}: ${historyError.message}`);
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("preserves dashboard fallback counts and the failed query's literal message/stack", async () => {
    const error = originalFailure();
    const query = vi.fn(() => { throw error; });
    const base = await listen(createDashboardRouter(null, null, { query } as never), true);
    const response = await fetch(`${base}/subsystem-summary`);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ approval: { pending: 0, approved: 0, rejected: 0, other: 0 },
      alerts: { unacknowledged: 0, critical: 0, warning: 0, info: 0 }, scheduler: { enabled: 0, disabled: 0 },
      flowControl: { activeSlots: 0, queuedItems: 0 } });
    expectDiagnostic("warn", "[dashboard] subsystem-summary failed: %s", [error.message], error,
      `[dashboard] subsystem-summary failed: ${error.message}`);
    expect(console.error).not.toHaveBeenCalled();
  });

  it("retains approval-card response, exact metadata whitelist, literal message and original stack", async () => {
    const error = Object.assign(originalFailure(), { code: "DB_FAILURE", sqlMessage: message });
    const repo = { findPendingByFlowNode: vi.fn().mockResolvedValue(null), create: vi.fn().mockRejectedValue(error) };
    const base = await listen(createInternalApprovalCardsRouter(repo as never));
    const response = await send(base, "POST", { flow_id: id, node_id: id, workflow_id: id, approver_ids: "owner-1" });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ success: false, error: "Internal Server Error", message: error.message, code: error.code, detail: error.sqlMessage });
    const metadata = { code: error.code, sqlMessage: error.sqlMessage, flow_id: id, node_id: id, workflow_id: id };
    expectDiagnostic("error", "[approval-cards] insert failed: %s", [error.message, metadata], error,
      `[approval-cards] insert failed: ${error.message} ${inspect(metadata)}`);
    expect(console.warn).not.toHaveBeenCalled();
  });
});
