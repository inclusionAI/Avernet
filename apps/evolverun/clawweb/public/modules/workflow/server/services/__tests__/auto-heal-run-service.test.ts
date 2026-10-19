// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { FlowRunRepository, FlowRunRow } from "../../repositories/flow-run-repository.js";
import { AutoHealRunService, type AutoHealRunDependencies } from "../auto-heal-run-service.js";

const workflowId = "audit-workflow";
const input = { workflowId, sourceFlowId: "source-cron", actorId: "owner-1" };
const receipt = { ok: true, messageId: "accepted-message", sessionId: "new-session" };
const findFullByFlowId = vi.fn();
const hasExecutePermission = vi.fn();
const hasEditPermission = vi.fn();
const sendIntervention = vi.fn();
let run: Partial<FlowRunRow>;
let deps: AutoHealRunDependencies;
let service: AutoHealRunService;

beforeEach(() => {
  vi.resetAllMocks();
  run = {
    flow_id: input.sourceFlowId, workflow_id: workflowId, origin_bot_id: "default:owner-1",
    credentials_json: JSON.stringify({ BOT_TYPE: "service", STAGE: "draft", SECRET: "<must-not-escape>" }),
    params_json: JSON.stringify({ audit: true, nested: { limit: 3 } }),
    input_json: JSON.stringify({ trigger: "cron" }), origin_session_key: "old-cron-key",
    origin_session_id: "old-cron-session", user_id: "different-trigger-user",
  };
  findFullByFlowId.mockImplementation(async () => run);
  hasExecutePermission.mockResolvedValue(true);
  hasEditPermission.mockResolvedValue(false);
  sendIntervention.mockResolvedValue(receipt);
  deps = {
    flowRunRepo: { findFullByFlowId } as unknown as FlowRunRepository,
    botPermRepo: { hasExecutePermission, hasEditPermission } as unknown as BotWorkflowPermissionRepository,
    sendIntervention,
  };
  service = new AutoHealRunService(deps);
});

function sent() {
  const command = sendIntervention.mock.calls.at(-1)![0];
  return { command, payload: JSON.parse(command.message.split("\n").at(-1)) };
}

async function rejectWithoutSending(code: string, request = input) {
  await expect(service.run(request)).rejects.toMatchObject({ code });
  expect(sendIntervention).not.toHaveBeenCalled();
}

describe("source-aware auto-heal run service", () => {
  it("starts a cron workflow without input.command on its origin draft Bot", async () => {
    expect(await service.run(input)).toEqual({ ok: true, workflowId, flowId: receipt.messageId, sessionId: receipt.sessionId });
    expect(findFullByFlowId).toHaveBeenCalledWith("source-cron");
    expect(hasExecutePermission).toHaveBeenCalledWith(workflowId, "owner-1");
    expect(hasEditPermission).toHaveBeenCalledWith(workflowId, "owner-1");
    expect(sendIntervention).toHaveBeenCalledOnce();
    const { command, payload } = sent();
    expect(command).toEqual({ botId: "default:owner-1", lifecycleStage: "draft", sessionId: null,
      sessionKey: expect.stringMatching(/^auto-heal-run:audit-workflow:/), message: expect.any(String) });
    expect(payload).toEqual({ action: "run_workflow", workflowId, params: { audit: true, nested: { limit: 3 } },
      triggeredBy: "clawweb-dashboard", userId: "owner-1", userName: "owner-1", source: "auto-heal" });
    expect(JSON.stringify(command)).not.toMatch(/must-not-escape|old-cron|SECRET|credentials/);
  });

  it.each(["draft", "verify", "online"])("propagates a service Bot's %s stage explicitly", async stage => {
    run.credentials_json = JSON.stringify({ BOT_TYPE: "service", STAGE: stage });
    await service.run(input);
    expect(sent().command.lifecycleStage).toBe(stage);
  });

  it.each(['{"BOT_TYPE":"service"}', '{"BOT_TYPE":"service","OTHER":"private"}'])
  ("defaults a service Bot's missing STAGE to online (%s)", async credentials => {
    run.credentials_json = credentials;
    await service.run(input);
    expect(sent().command.lifecycleStage).toBe("online");
  });

  // bot_options.lifecycle_stage is a service-Bot option. A personal Bot has no
  // stage, so the field must be absent rather than defaulted.
  it.each([null, "", "{}", '{"OTHER":"private"}', '{"BOT_TYPE":"personal","STAGE":"draft"}',
    '{"BOT_TYPE":"application","STAGE":"verify"}'])
  ("omits lifecycle_stage for a non-service Bot (%s)", async credentials => {
    run.credentials_json = credentials;
    await service.run(input);
    // Undefined, so the BaaS adapter omits bot_options entirely.
    expect(sent().command.lifecycleStage).toBeUndefined();
  });

  it.each(["{invalid-private", "null", "[]", '{"BOT_TYPE":"service","STAGE":null}', '{"BOT_TYPE":"service","STAGE":""}',
    '{"BOT_TYPE":"service","STAGE":"production-private"}', '{"BOT_TYPE":"service","STAGE":123}',
    '{"BOT_TYPE":"service","STAGE":{}}'])
  ("fails invalid service Bot stage metadata safely (%s)", async credentials => {
    run.credentials_json = credentials;
    await expect(service.run(input)).rejects.toMatchObject({ code: "invalid_source", message: expect.stringContaining("STAGE") });
    expect(sendIntervention).not.toHaveBeenCalled();
  });

  it.each([null, ""])("uses empty params only for absent defaults (%s)", async params => {
    run.params_json = params;
    await service.run(input);
    expect(sent().payload.params).toEqual({});
  });

  it.each(["broken-json", "null", "[]", "1", '\"text\"'])("rejects invalid stored defaults (%s)", async params => {
    run.params_json = params;
    await rejectWithoutSending("invalid_source");
  });

  it("allows explicit params to replace corrupt stored defaults", async () => {
    run.params_json = "corrupt-defaults";
    await service.run({ ...input, params: {} });
    expect(sent().payload.params).toEqual({});
  });

  it.each([{}, { replacement: "only" }])("uses explicit params as replacement, not merge (%j)", async params => {
    await service.run({ ...input, params });
    expect(sent().payload.params).toEqual(params);
  });

  it("uses distinct sessions even when two requests start at the same time", async () => {
    await Promise.all([service.run(input), service.run(input)]);
    const keys = sendIntervention.mock.calls.map(([command]) => command.sessionKey);
    expect(new Set(keys).size).toBe(2);
  });

  it("rejects a missing source", async () => { findFullByFlowId.mockResolvedValue(null); await rejectWithoutSending("not_found"); });
  it("rejects a workflow mismatch", async () => { run.workflow_id = "another-workflow"; await rejectWithoutSending("bad_request"); });
  it.each([null, "", "default", "default:", ":owner-1", "default:other:owner-1", "*:owner-1", "default:*", "default: owner-1"])
  ("rejects absent or ambiguous Bot ownership (%s)", async origin => {
    run.origin_bot_id = origin;
    await rejectWithoutSending("invalid_source");
  });

  it("does not turn visibility or workflow access into another Bot owner's send authority", async () => {
    run.origin_bot_id = "default:victim";
    run.user_id = input.actorId;
    hasEditPermission.mockResolvedValue(true);
    await rejectWithoutSending("forbidden");
  });

  it("allows an explicitly authenticated admin, not an inferred owner", async () => {
    run.origin_bot_id = "default:original-owner";
    hasExecutePermission.mockResolvedValue(false);
    await service.run({ ...input, actorId: "admin", isAdmin: true });
    expect(sent().command.botId).toBe("default:original-owner");
    expect(sent().payload.userId).toBe("admin");
  });

  it("accepts edit as well as execute permission", async () => {
    hasExecutePermission.mockResolvedValue(false);
    hasEditPermission.mockResolvedValue(true);
    await service.run(input);
    expect(sendIntervention).toHaveBeenCalledOnce();
  });

  it("denies view-only actors before fetching credentials", async () => {
    hasExecutePermission.mockResolvedValue(false);
    await rejectWithoutSending("forbidden");
    expect(findFullByFlowId).not.toHaveBeenCalled();
  });

  it("fails closed without the permission repository, including for admins", async () => {
    service = new AutoHealRunService({ ...deps, botPermRepo: null });
    await rejectWithoutSending("forbidden");
    await expect(service.run({ ...input, isAdmin: true })).rejects.toMatchObject({ code: "forbidden" });
    expect(sendIntervention).not.toHaveBeenCalled();
  });

  it("fails closed without the flow repository", async () => {
    service = new AutoHealRunService({ ...deps, flowRunRepo: null });
    await rejectWithoutSending("unavailable");
  });

  it("does not bypass failed permission lookups", async () => {
    hasExecutePermission.mockRejectedValue(new Error("permission lookup failed"));
    await expect(service.run(input)).rejects.toThrow("permission lookup failed");
    expect(sendIntervention).not.toHaveBeenCalled();
  });

  it("retains the detailed BaaS failure and never retries it", async () => {
    const error = "BaaS HTTP 400 BotBindingNotFoundError: binding absent (trace_id=trace-123)";
    sendIntervention.mockResolvedValue({ ok: false, error });
    await expect(service.run(input)).rejects.toMatchObject({ code: "delivery_failed", message: error });
    expect(sendIntervention).toHaveBeenCalledOnce();
  });

  it.each([null, { ok: true }, { ok: true, messageId: "" }, { ok: true, messageId: "   " },
    { ok: true, messageId: 7 }, { ok: false, messageId: "not-accepted" }])
  ("does not report an unconfirmed receipt as success (%j)", async result => {
    sendIntervention.mockResolvedValue(result);
    await expect(service.run(input)).rejects.toMatchObject({ code: "delivery_failed" });
    expect(sendIntervention).toHaveBeenCalledOnce();
  });

  it("does not invent a session ID when only message acceptance is confirmed", async () => {
    sendIntervention.mockResolvedValue({ ok: true, messageId: "accepted" });
    expect(await service.run(input)).toEqual({ ok: true, workflowId, flowId: "accepted", sessionId: null });
  });
});
