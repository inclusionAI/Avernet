// @vitest-environment node
import express, { Router } from "express";
import { once } from "node:events";
import type { Server } from "node:http";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { ApprovalCardRepository } from "../../repositories/approval-card-repository.js";
import type { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import type { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { OrdinaryRequestError } from "../../services/ordinary-run-request.js";
import { createAutoHealRunRouter } from "../auto-heal-run.js";
import { createInternalApprovalCardsRouter } from "../internal/approval-cards.js";
import { createInternalEventsRouter } from "../internal/events.js";
import { createInternalRunsRouter } from "../internal/runs.js";
import { registerWorkflowListRoutes, workflowsCache } from "../workflows-list.js";
import { createWorkflowsRouter } from "../workflows.js";
import { flushInternalApiLog } from "../internal-logger.js";

const files = vi.hoisted(() => ({ appendFile: vi.fn(), mkdir: vi.fn() }));
vi.mock("node:fs/promises", async importOriginal => ({ ...await importOriginal<typeof import("node:fs/promises")>(), ...files }));
vi.mock("@avernet/workflow/server/routes/internal-logger", async () => import("../internal-logger.js"));
const servers: Server[] = [];
const diagnostics: unknown[] = [];
let summaryWindow = Date.UTC(2026, 10, 27);

beforeEach(() => {
  // Each independent HTTP case starts in a fresh summary window. Fake only
  // Date: Express/network and the sink's batching/drain timers remain real.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(summaryWindow);
  summaryWindow += 1000;
  vi.clearAllMocks();
  files.mkdir.mockReset().mockResolvedValue(undefined);
  files.appendFile.mockReset().mockResolvedValue(undefined);
  diagnostics.length = 0;
  vi.spyOn(console, "log").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve()))));
  await flushInternalApiLog();
  workflowsCache.invalidate();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function listen(router: Router) {
  const app = express();
  app.use(express.json());
  app.use((_req, res, next) => {
    res.once("finish", () => { diagnostics.push(res.locals.taskGuardApiDiagnostic); });
    next();
  });
  app.use(router);
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await once(server, "listening");
  return `http://127.0.0.1:${(server.address() as { port: number }).port}`;
}
const post = async (url: string, body: object, headers: Record<string, string> = {}) => {
  const response = await fetch(url, {
    method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body),
  });
  await flushInternalApiLog();
  return response;
};
function lastFileLog() {
  // A single append may contain both the request preview and result entry.
  const batch = String(files.appendFile.mock.calls.at(-1)![1]);
  const line = batch.trimEnd().split("\n").at(-1)!;
  return { line, entry: JSON.parse(line.slice(line.indexOf("{"))) };
}
function persistenceFailureOrigin() {
  return Object.assign(new Error("database persistence failed"), {
    token: "<private-token-marker>", cookies: ["<private-cookie-marker>"],
    config: { headers: { Authorization: "<private-auth-marker>" } },
  });
}

describe("workflow route exception logging without response changes", () => {
  it.each(["/", "/list"])("logs the original workflows-list %s exception and preserves the 500 body", async path => {
    const error = persistenceFailureOrigin();
    const repo = {
      listSummaries: vi.fn().mockRejectedValue(error),
      findPage: vi.fn().mockRejectedValue(error),
    } as unknown as WorkflowSpecRepository;
    const router = Router();
    registerWorkflowListRoutes(router, repo, null, () => 0);
    const base = await listen(router);
    const response = await fetch(`${base}${path}`, { headers: { "x-user-id": "owner-1" } });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "Internal Server Error", message: "database persistence failed" });
    expect(console.error).toHaveBeenCalledWith("[workflows-list] request failed:", error.stack);
    expect(diagnostics).toEqual([{ message: error.message, stack: error.stack, stackSource: "exception" }]);
    expect(JSON.stringify(diagnostics)).not.toMatch(/private-|Authorization|cookies|config|token/);
    expect(error.stack).toContain("persistenceFailureOrigin");
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("preserves a real repository's thrown write error in both API and direct console logs", async () => {
    const error = persistenceFailureOrigin();
    const db = {
      query: vi.fn().mockResolvedValue([]),
      exec: vi.fn().mockRejectedValue(error),
    } as unknown as IDatabase;
    const base = await listen(createInternalApprovalCardsRouter(new ApprovalCardRepository(db)));
    const response = await post(base, { flow_id: "flow-1", node_id: "review", workflow_id: "wf-1", approver_ids: "owner-1" });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ success: false, error: "Internal Server Error", message: error.message });
    expect(db.exec).toHaveBeenCalledOnce();
    expect(diagnostics).toEqual([{ message: error.message, stack: error.stack, stackSource: "exception" }]);
    expect(console.error).toHaveBeenCalledWith("%s %s", "[internal-api] WRITE /approval-cards 500", error.stack);
    expect(console.error).toHaveBeenCalledWith("[approval-cards] insert failed: %s", error.message, expect.any(Object), error.stack);
    expect(lastFileLog().line).toContain(" ERROR [internal-api] ");
    expect(lastFileLog().entry).toMatchObject({ httpStatus: 500, error: error.message, stack: error.stack, stackSource: "exception" });
    expect(lastFileLog().entry.stack).toContain("persistenceFailureOrigin");
    // Both the new API error line and its existing direct companion stay whitelist-only.
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toMatch(/private-|Authorization|cookies|config|token/);
    expect(lastFileLog().line).not.toMatch(/private-|Authorization|cookies|config|token/);
  });

  it("keeps httpStatus-only validation failures at WARN with the original 400 envelope", async () => {
    const repo = new ApprovalCardRepository({} as IDatabase);
    const response = await post(await listen(createInternalApprovalCardsRouter(repo)), { flow_id: "flow-1" });
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ success: false, error: "Bad Request",
      message: "Missing required fields: flow_id, node_id, workflow_id, approver_ids" });
    expect(console.warn).toHaveBeenCalledWith("%s", "[internal-api] WRITE /approval-cards 400");
    expect(console.error).not.toHaveBeenCalled();
    expect(lastFileLog().line).toContain(" WARN [internal-api] ");
  });

  it("distinguishes non-throw persistence rejection diagnostics from caught original exceptions", async () => {
    const base = await listen(createInternalEventsRouter({ insert: async () => false } as never));
    const response = await post(base, { event_id: "event-1", flow_id: "flow-1", workflow_id: "wf-1", event_type: "node.done" });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ success: false, error: "Service Unavailable", message: "Event persistence not confirmed" });
    const diagnostic = vi.mocked(console.error).mock.calls.find(call => call[0] === "%s %s" && call[1] === "[internal-api] WRITE /events 503")?.[2];
    expect(diagnostic).toEqual(expect.stringContaining("Error: Workflow API failure observed: Event persistence not confirmed"));
    expect(lastFileLog().entry).toMatchObject({ error: "Event persistence not confirmed", stack: diagnostic, stackSource: "observation" });
    expect(lastFileLog().entry.stack).toContain("routes/internal/events.ts");
    expect(lastFileLog().entry.stack).not.toContain("persistenceFailureOrigin");
    expect(lastFileLog().entry.stack).toContain("internal-logger.ts");
  });

  it.each([400, 404, 409] as const)("keeps OrdinaryRequestError HTTP %s at WARN", async status => {
    const error = new OrdinaryRequestError(status, "Ordinary request rejected");
    const repo = { ordinaryRequests: { lookup: vi.fn().mockRejectedValue(error) } } as unknown as FlowRunRepository;
    const auth = { authenticate: async () => ({ botId: "bot-1", ownerId: "owner-1" }), authorize: async () => true };
    const base = await listen(createInternalRunsRouter(repo, null, undefined, {}, auth));
    const response = await post(`${base}/request-association/lookup`, { type: "ordinary-run-request.lookup.v1", ordinary_request_key: "key-1" });
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ success: false, error: status === 400 ? "Bad Request" : status === 404 ? "Not Found" : "Conflict" });
    expect(console.warn).toHaveBeenCalledWith("[internal-runs] ordinary request failed:", error.stack);
    expect(console.error).not.toHaveBeenCalled();
  });

  it("logs an unexpected ordinary lookup failure at ERROR with the original stack, keeping its 503 body", async () => {
    const error = persistenceFailureOrigin();
    const repo = { ordinaryRequests: { lookup: vi.fn().mockRejectedValue(error) } } as unknown as FlowRunRepository;
    const auth = { authenticate: async () => ({ botId: "bot-1", ownerId: "owner-1" }), authorize: async () => true };
    const base = await listen(createInternalRunsRouter(repo, null, undefined, {}, auth));
    const response = await post(`${base}/request-association/lookup`, { type: "ordinary-run-request.lookup.v1", ordinary_request_key: "key-1" });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ success: false, error: "Service Unavailable" });
    expect(console.error).toHaveBeenCalledWith("[internal-runs] ordinary request failed:", error.stack);
    expect(error.stack).toContain("persistenceFailureOrigin");
    expect(console.warn).not.toHaveBeenCalled();
  });


  it.each([true, false])("preserves the original parent registration readback stack across both catches (created=%s)", async created => {
    for (const channel of ["info", "debug"] as const) vi.spyOn(console, channel).mockImplementation(() => {});
    const error = persistenceFailureOrigin();
    const relation = { kind: "subworkflow.v1", parent_flow_id: "parent-1", parent_node_id: "node-1", parent_attempt: 1 };
    const repo = { registerParentRelatedRun: vi.fn().mockResolvedValue({ created, relation }),
      findFullByFlowId: vi.fn().mockRejectedValue(error) };
    const router = Router();
    router.use("/api/internal/runs", createInternalRunsRouter(repo as unknown as FlowRunRepository));
    const base = await listen(router);
    const response = await post(`${base}/api/internal/runs`, { flow_id: "child-1", workflow_id: "wf-1", status: "running", parent_relation: relation });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ success: false, error: "Service Unavailable" });
    expect(repo.registerParentRelatedRun).toHaveBeenCalledOnce();
    expect(repo.findFullByFlowId).toHaveBeenCalledExactlyOnceWith("child-1");
    expect(console.error).toHaveBeenCalledWith("[internal-runs] registered run readback failed:", error.stack);
    expect(console.error).toHaveBeenCalledWith("[internal-runs] parent registration failed:", error.stack);
    expect(console.error).toHaveBeenCalledWith("%s %s", "[internal-api] WRITE /runs 503", error.stack);
    expect(vi.mocked(console.error).mock.calls.every(call => call.at(-1) === error.stack)).toBe(true);
    expect(diagnostics).toEqual([{ message: error.message, stack: error.stack, stackSource: "exception" }]);
    expect(lastFileLog().line).toContain(" ERROR [internal-api] ");
    expect(lastFileLog().entry).toMatchObject({ path: "/runs", status: 503, stack: error.stack, stackSource: "exception" });
    expect(lastFileLog().entry.stack).toContain("persistenceFailureOrigin");
    const consoleCalls = ["log", "info", "warn", "error", "debug"].flatMap(channel => vi.mocked(console[channel as "error"]).mock.calls);
    expect(JSON.stringify({ consoleCalls, fileCalls: files.appendFile.mock.calls.map(call => [call[0], String(call[1])]), diagnostics }))
      .not.toMatch(/private-|Authorization|cookies|config|token/);
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("keeps callback failures sanitized in the response while retaining the original console Error", async () => {
    const error = persistenceFailureOrigin();
    const callbacks = { findByWorkflowId: vi.fn().mockRejectedValue(error) };
    const router = Router();
    router.use((req, _res, next) => { req.isAdmin = true; next(); });
    router.use(createWorkflowsRouter(null, null, null, null, null, callbacks as never));
    const base = await listen(router);
    const response = await fetch(`${base}/wf-1/callback-configs`);
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "Internal Server Error", message: "Internal error" });
    expect(console.error).toHaveBeenCalledWith("[workflows] GET callback-configs failed:", error.message, error.stack);
    expect(error.stack).toContain("persistenceFailureOrigin");
  });

  it("keeps accepted-run audit compensation at WARN without private diagnostics and preserves success", async () => {
    const error = persistenceFailureOrigin();
    const flowRunRepo = { findFullByFlowId: async () => ({ workflow_id: "wf-1", origin_bot_id: "bot-1:owner-1",
      credentials_json: null, input_json: null }) } as unknown as FlowRunRepository;
    const botPermRepo = { hasExecutePermission: async () => true, hasEditPermission: async () => false } as unknown as BotWorkflowPermissionRepository;
    const router = createAutoHealRunRouter({ flowRunRepo, botPermRepo,
      sendIntervention: async () => ({ ok: true, messageId: "msg-1", sessionId: "session-1" }),
      onAccepted: async () => { throw error; } });
    const base = await listen(router);
    const response = await post(`${base}/run`, { workflowId: "wf-1", sourceFlowId: "flow-1" }, { "x-user-id": "owner-1" });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true, workflowId: "wf-1", messageId: "msg-1", sessionId: "session-1" });
    expect(console.warn).toHaveBeenCalledWith("[auto-heal] Run accepted but acceptance audit failed");
    expect(JSON.stringify(vi.mocked(console.warn).mock.calls)).not.toMatch(/private-|Authorization|cookies|config|token/);
    expect(console.error).not.toHaveBeenCalled();
  });

  it.each([401, 403, 404])("keeps real auto-heal HTTP %s business rejections out of ERROR", async status => {
    const flowRunRepo = { findFullByFlowId: vi.fn().mockResolvedValue(null) } as unknown as FlowRunRepository;
    const botPermRepo = {
      hasExecutePermission: async () => status !== 403,
      hasEditPermission: async () => false,
    } as unknown as BotWorkflowPermissionRepository;
    const router = createAutoHealRunRouter({ flowRunRepo, botPermRepo, sendIntervention: vi.fn() });
    const base = await listen(router);
    const response = await post(`${base}/run`, { workflowId: "wf-1", sourceFlowId: "flow-1" }, status === 401 ? {} : { "x-user-id": "owner-1" });
    expect(response.status).toBe(status);
    expect(await response.json()).toMatchObject({ ok: false, error: status === 401 ? "Unauthorized" : status === 403 ? "Forbidden" : "Not Found" });
    expect(console.warn).toHaveBeenCalledWith("[auto-heal] run failed:", expect.any(String));
    expect(console.error).not.toHaveBeenCalled();
  });
});
