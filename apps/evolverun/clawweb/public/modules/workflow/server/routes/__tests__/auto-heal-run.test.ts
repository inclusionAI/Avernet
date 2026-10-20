// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Request, Response, Router } from "express";
import type { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createAutoHealRunRouter, type AutoHealRunRouterDependencies } from "../auto-heal-run.js";
import { createAutoHealRouter, type AutoHealRouteDependencies } from "../auto-heal.js";

const fallbackSender = vi.hoisted(() => vi.fn());
vi.mock("@avernet/clawevolve/server/services/baas-intervention", () => ({ sendIntervention: fallbackSender }));
const body = { workflowId: "audit-workflow", sourceFlowId: "source-cron" };
const receipt = { ok: true, messageId: "accepted-message", sessionId: "new-session" };
let deps: AutoHealRunRouterDependencies;
const findFullByFlowId = vi.fn();
const hasExecutePermission = vi.fn();
const hasEditPermission = vi.fn();
const sendIntervention = vi.fn();
const onAccepted = vi.fn();
const findByWorkflowId = vi.fn();

// Exercise the actual Express router without opening sockets or making HTTP requests.
function request(router: Router, payload: unknown = body, headers: Record<string, string> = { "x-user-id": "owner-1" },
  extra: Partial<Request> = {}): Promise<{ status: number; body: Record<string, unknown> }> {
  return new Promise((resolve, reject) => {
    const get = (key: string) => headers[key.toLowerCase()];
    const req = { method: "POST", url: "/run", headers, body: payload, get, header: get, ...extra } as Request;
    let status = 200;
    const res = {
      status(code: number) { status = code; return this; },
      json(data: Record<string, unknown>) { resolve({ status, body: data }); return this; },
    } as Response;
    router(req, res, (error?: unknown) => error ? reject(error) : resolve({ status: 404, body: {} }));
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  findFullByFlowId.mockResolvedValue({ flow_id: "source-cron", workflow_id: body.workflowId,
    origin_bot_id: "default:owner-1",
    credentials_json: '{"BOT_TYPE":"service","STAGE":"draft","SECRET":"not-public"}',
    params_json: '{"audit":true}', input_json: '{"trigger":"cron"}' });
  hasExecutePermission.mockResolvedValue(true);
  hasEditPermission.mockResolvedValue(false);
  sendIntervention.mockResolvedValue(receipt);
  fallbackSender.mockResolvedValue(receipt);
  onAccepted.mockResolvedValue(undefined);
  // A cron-triggered run records no command, so the binding supplies it.
  findByWorkflowId.mockResolvedValue([{ command: body.workflowId }]);
  deps = { flowRunRepo: { findFullByFlowId } as unknown as FlowRunRepository,
    botPermRepo: { hasExecutePermission, hasEditPermission } as unknown as BotWorkflowPermissionRepository,
    sendIntervention, onAccepted, facadeRepo: { findByWorkflowId } as never };
});
afterEach(() => vi.restoreAllMocks());

async function failure(expectedStatus: number, payload: unknown = body, headers?: Record<string, string>) {
  const result = await request(createAutoHealRunRouter(deps), payload, headers);
  expect(result).toMatchObject({ status: expectedStatus, body: { ok: false, error: expect.any(String), message: expect.any(String) } });
  expect(result.body).not.toHaveProperty("flowId");
  expect(JSON.stringify(result.body)).not.toMatch(/not-public|SECRET|credentials_json/);
  expect(sendIntervention).not.toHaveBeenCalled();
  expect(onAccepted).not.toHaveBeenCalled();
  return result;
}

describe("POST /api/auto-heal/run", () => {
  it("requires sourceFlowId (400), never defaulting to the diagnosis bot", async () => {
    await failure(400, { workflowId: body.workflowId });
  });

  it.each([null, [], "bad-body", {}, { ...body, sourceFlowId: " " }, { ...body, sourceFlowId: 7 },
    { ...body, workflowId: [] }, { ...body, workflowId: "" }, { ...body, params: null },
    { ...body, params: [] }, { ...body, params: "bad-params" }])("validates the JSON body (%j)", async payload => {
    await failure(400, payload);
  });

  it("requires real actor identity, not the body actor or unknown fallback", async () => {
    await failure(401, { ...body, actorId: "owner-1", userId: "owner-1", isAdmin: true }, {});
    expect(findFullByFlowId).not.toHaveBeenCalled();
  });

  it("requires identity even when the request has an admin flag", async () => {
    const result = await request(createAutoHealRunRouter(deps), body, {}, { isAdmin: true });
    expect(result.status).toBe(401);
    expect(sendIntervention).not.toHaveBeenCalled();
  });

  // Built at runtime so no credential-shaped literal appears in source, while
  // the cookie stays a real header.payload.signature IAM token for the parser.
  const iamToken = ["header", Buffer.from(JSON.stringify({ sno: "owner-1" })).toString("base64url"), "signature"].join(".");
  const signedIamCookie = ["IAM_TOKEN", iamToken].join("=");

  it.each([
    { "x-user-id": "owner-1" }, { "x-staff-id": "owner-1" }, { staff_id: "owner-1" },
    { cookie: "staff_id=owner-1" },
    { cookie: signedIamCookie },
  ])("reuses resolveWorkflowActorId identity sources (%j)", async headers => {
    expect(await request(createAutoHealRunRouter(deps), body, headers)).toEqual({ status: 200,
      body: { ok: true, workflowId: body.workflowId, messageId: receipt.messageId, sessionId: receipt.sessionId } });
    expect(hasExecutePermission).toHaveBeenCalledWith(body.workflowId, "owner-1");
  });

  it("reports missing source as 404", async () => { findFullByFlowId.mockResolvedValue(null); await failure(404); });
  it("reports a workflow mismatch as 400", async () => {
    findFullByFlowId.mockResolvedValue({ workflow_id: "other-workflow" }); await failure(400);
  });
  it("reports missing origin as 422", async () => {
    findFullByFlowId.mockResolvedValue({ workflow_id: body.workflowId, origin_bot_id: null }); await failure(422);
  });
  it("rejects invalid STAGE without exposing it", async () => {
    findFullByFlowId.mockResolvedValue({ workflow_id: body.workflowId, origin_bot_id: "default:owner-1",
      credentials_json: '{"BOT_TYPE":"service","STAGE":"not-public"}' }); await failure(422);
  });
  // The trigger is the bound slash command, so stored params no longer gate a re-run.
  it.each(["broken-json", "null", "[]", "1"])("ignores corrupt stored params (%s)", async params => {
    findFullByFlowId.mockResolvedValue({ workflow_id: body.workflowId, origin_bot_id: "default:owner-1",
      credentials_json: '{"STAGE":"draft"}', params_json: params });
    expect(await request(createAutoHealRunRouter(deps))).toMatchObject({ status: 200, body: { ok: true } });
  });
  it("fails closed without permission service", async () => { deps.botPermRepo = null; await failure(403); });
  it("fails closed without run storage", async () => { deps.flowRunRepo = null; await failure(503); });
  it("denies view-only permission", async () => { hasExecutePermission.mockResolvedValue(false); await failure(403); });
  it("does not impersonate another owner from sourceFlowId even if the run is visible", async () => {
    await failure(403, { ...body, actorId: "owner-1", isAdmin: true }, { "x-user-id": "stranger" });
    expect(hasExecutePermission).toHaveBeenCalledWith(body.workflowId, "stranger");
  });

  it("does not accept client overrides of Bot, stage, or cron session", async () => {
    await request(createAutoHealRunRouter(deps), { ...body, botId: "diagnosis:owner", lifecycleStage: "online",
      sessionId: "cron-session", credentials: { STAGE: "online" }, params: {} });
    expect(sendIntervention).toHaveBeenCalledWith(expect.objectContaining({ botId: "default:owner-1",
      lifecycleStage: "draft", sessionId: null }));
    // The trigger is resolved server-side, not taken from a client-supplied
    // payload. This run records no message, so it uses the documented
    // /workflow run form rather than a bare command that would print help.
    expect(sendIntervention.mock.calls[0][0].message.split("\n").at(-1))
      .toBe(`/workflow run ${body.workflowId}`);
  });

  it.each([false, true])("preserves detailed BaaS failure, tokenExpired=%s, without retry", async tokenExpired => {
    const message = "BaaS HTTP 400 BotBindingNotFoundError: binding absent (trace_id=trace-123)";
    sendIntervention.mockResolvedValue({ ok: false, error: message, tokenExpired });
    expect(await request(createAutoHealRunRouter(deps))).toMatchObject({ status: tokenExpired ? 401 : 502,
      body: { ok: false, message, ...(tokenExpired ? { tokenExpired: true } : {}) } });
    expect(sendIntervention).toHaveBeenCalledOnce();
    expect(onAccepted).not.toHaveBeenCalled();
  });

  it("rejects unconfirmed receipts without auditing success", async () => {
    sendIntervention.mockResolvedValue({ ok: true });
    expect(await request(createAutoHealRunRouter(deps))).toMatchObject({ status: 502, body: { ok: false } });
    expect(onAccepted).not.toHaveBeenCalled();
  });

  it("calls the acceptance audit once with the resolved actor and request params", async () => {
    const params = { replacement: true };
    await request(createAutoHealRunRouter(deps), { ...body, params, actorId: "spoofed" });
    expect(onAccepted).toHaveBeenCalledExactlyOnceWith(
      { ok: true, workflowId: body.workflowId, messageId: receipt.messageId, sessionId: receipt.sessionId },
      { ...body, actorId: "owner-1", params },
    );
    expect(sendIntervention.mock.invocationCallOrder[0]).toBeLessThan(onAccepted.mock.invocationCallOrder[0]);
  });

  it("does not report a delivered message as failed when best-effort audit throws", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    onAccepted.mockRejectedValue(new Error("audit unavailable: not-public"));
    expect(await request(createAutoHealRunRouter(deps))).toMatchObject({ status: 200, body: { ok: true } });
    expect(sendIntervention).toHaveBeenCalledOnce();
    expect(onAccepted).toHaveBeenCalledOnce();
    expect(warn).toHaveBeenCalled();
    expect(JSON.stringify(warn.mock.calls)).not.toContain("not-public");
  });

  it("exports only POST /run, leaving legacy APIs to the parent router", async () => {
    const router = createAutoHealRunRouter(deps);
    expect(await request(router, body, {}, { method: "GET" })).toMatchObject({ status: 404 });
    expect(await request(router, body, {}, { url: "/deploy" })).toMatchObject({ status: 404 });
    expect(sendIntervention).not.toHaveBeenCalled();
  });

  it("wires the same run router in the public parent, while keeping flowRunRepo optional", async () => {
    const publicDeps = { flowRunRepo: deps.flowRunRepo, botPermRepo: deps.botPermRepo,
      sendIntervention, facadeRepo: deps.facadeRepo } as unknown as AutoHealRouteDependencies;
    expect(await request(createAutoHealRouter(publicDeps))).toMatchObject({ status: 200, body: { ok: true } });
    expect(fallbackSender).not.toHaveBeenCalled();
    delete publicDeps.flowRunRepo;
    expect(await request(createAutoHealRouter(publicDeps))).toMatchObject({ status: 503 });
  });

  it("selects the existing sender only in the public routing adapter", async () => {
    const publicDeps = { flowRunRepo: deps.flowRunRepo, botPermRepo: deps.botPermRepo,
      facadeRepo: deps.facadeRepo } as unknown as AutoHealRouteDependencies;
    expect(await request(createAutoHealRouter(publicDeps))).toMatchObject({ status: 200, body: { ok: true } });
    expect(fallbackSender).toHaveBeenCalledWith(expect.objectContaining({ botId: "default:owner-1", lifecycleStage: "draft" }));
  });
});
