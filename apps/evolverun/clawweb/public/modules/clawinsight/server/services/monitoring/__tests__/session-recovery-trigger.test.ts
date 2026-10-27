import { beforeEach, describe, expect, it, vi } from "vitest";
import { DIAGNOSIS_VERSION, DIAGNOSIS_V3_VERSION, DIAGNOSIS_V4_VERSION, type DiagnosisEvent } from "../contracts.js";
import { createMonitoringRecoveryTrigger, SESSION_RECOVERY_ALLOWLIST_KEY } from "../session-recovery-trigger.js";
import { SessionRecoveryError } from "../../repair/session-recovery/errors.js";

import { runtimeIdentity } from "./runtime-identity-fixture.js";
const bot = { bot_id: "default", entity_id: "001234", env: "prod" };
const event: DiagnosisEvent = {
  schemaVersion: DIAGNOSIS_V4_VERSION, runtimeIdentity: { ...runtimeIdentity, OWNER_ID: bot.entity_id }, eventId: "event-1", diagnosisId: "event-1", botId: "default",
  entityId: "001234", env: "prod", engine: "OC", sessionKey: "agent:main:session:original",
  sessionId: "physical-session", traceId: null, occurredAt: null, diagnosedAt: "2026-10-15T00:00:00.000Z",
  decision: "ALERT", tcFaultLabel: "TC.MCP.PERMISSION", confidence: 0.2,
  businessProblemCategory: null, businessProblemSubtype: null,
  businessDiagnosis: "用户需要读取文档，但尚未取得正文。", systemDiagnosis: "文档访问被拒绝。",
  handlerName: "NOT_COMPLETED", humanIntervention: false,
};
function setup() {
  const config = { findByKey: vi.fn().mockResolvedValue({ enabled: 1, config_json: JSON.stringify({ allowed_bots: [bot] }) }) };
  const service = { create: vi.fn().mockResolvedValue({ event_id: event.eventId, delivery_key: "receipt", status: "accepted" }) };
  return { config, service, trigger: createMonitoringRecoveryTrigger({ config, service }) };
}
beforeEach(() => { vi.restoreAllMocks(); });

describe("monitoring automatic recovery", () => {
  it.each(["OC", "TE"] as const)("maps %s diagnostics to the existing approval flow without inferring a location", async engine => {
    const { config, service, trigger } = setup();
    await trigger.trigger({ ...event, engine });
    expect(config.findByKey).toHaveBeenCalledWith(SESSION_RECOVERY_ALLOWLIST_KEY);
    const [request, actor] = service.create.mock.calls[0];
    expect(request).toEqual({
      event_id: event.eventId, tc_fault_label: event.tcFaultLabel, target: { ...bot, engine, ...(engine === "OC" ? { runtime_identity: {
        stage: "online", deviceUuid: runtimeIdentity.CLIENT_ID, version: "V16",
      } } : {}) },
      session_key: event.sessionKey, session_id: event.sessionId, delivery_key: expect.any(String),
      diagnosis: `业务诊断：${event.businessDiagnosis}\n系统诊断：${event.systemDiagnosis}`,
    });
    expect(actor).toEqual({ userId: "001234", isAdmin: false });
    expect(request).not.toHaveProperty("error_location");
    expect(request).not.toHaveProperty("execution_level");
  });

  it.each([
    { decision: "PASS" }, { decision: "UNRESOLVED" },
    { handlerName: "COMPLETED" }, { handlerName: null }, { humanIntervention: true },
    { tcFaultLabel: null }, { schemaVersion: DIAGNOSIS_VERSION }, { schemaVersion: DIAGNOSIS_V3_VERSION },
  ])("does not trigger for an ineligible diagnostic %j", async patch => {
    const { config, service, trigger } = setup();
    await trigger.trigger({ ...event, ...patch } as DiagnosisEvent);
    expect(config.findByKey).not.toHaveBeenCalled();
    expect(service.create).not.toHaveBeenCalled();
  });

  it("requires the exact Bot, Owner and environment and reloads the saved configuration", async () => {
    const { config, service, trigger } = setup();
    for (const patch of [{ botId: "another" }, { entityId: "1234" }, { env: "pre" }]) {
      await trigger.trigger({ ...event, ...patch });
    }
    expect(service.create).not.toHaveBeenCalled();
    await trigger.trigger(event);
    config.findByKey.mockResolvedValueOnce({ enabled: 0, config_json: "invalid disabled content" });
    await trigger.trigger(event);
    config.findByKey.mockResolvedValueOnce(null);
    await trigger.trigger(event);
    config.findByKey.mockResolvedValueOnce({ enabled: 1, config_json: '{"allowed_bots":[]}' });
    await trigger.trigger(event);
    expect(service.create).toHaveBeenCalledTimes(1);
    expect(config.findByKey).toHaveBeenCalledTimes(7);
  });

  it.each([
    "invalid", "null", '{"allowed_bots":true}', '{"allowed_bots":[],"enabled":true}',
    JSON.stringify({ allowed_bots: [{ ...bot, env: "*" }] }),
    JSON.stringify({ allowed_bots: [{ ...bot, entity_id: "001234 " }] }),
    JSON.stringify({ allowed_bots: [bot, bot] }),
  ])("fails closed on invalid enabled configuration: %s", async config_json => {
    const { config, service, trigger } = setup();
    config.findByKey.mockResolvedValue({ enabled: 1, config_json });
    await expect(trigger.trigger(event)).rejects.toMatchObject({ code: "NOT_READY" });
    expect(service.create).not.toHaveBeenCalled();
  });

  it("preserves retryable configuration and task storage errors instead of acknowledging them", async () => {
    const { config, service, trigger } = setup();
    config.findByKey.mockRejectedValueOnce(new Error("configuration DB unavailable"));
    await expect(trigger.trigger(event)).rejects.toMatchObject({ code: "NOT_READY" });
    service.create.mockRejectedValueOnce(new Error("task storage unavailable"));
    await expect(trigger.trigger(event)).rejects.toMatchObject({ code: "NOT_READY" });
    await trigger.trigger(event);
    expect(service.create).toHaveBeenCalledTimes(2);
    expect(service.create.mock.calls[0]).toEqual(service.create.mock.calls[1]);
  });

  it("does not retry an explicitly rejected recovery request as a temporary storage failure", async () => {
    const { service, trigger } = setup();
    service.create.mockRejectedValueOnce(new SessionRecoveryError(409, "conflict", "request changed"));
    await expect(trigger.trigger(event)).rejects.toMatchObject({ code: "EVENT_CONFLICT" });
  });

  it("does not substitute a physical Session ID or Trace ID when the real session key is missing", async () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { service, trigger } = setup();
    await trigger.trigger({ ...event, sessionKey: null, traceId: "trace" });
    expect(service.create).not.toHaveBeenCalled();
    expect(warning).toHaveBeenCalledWith("[monitoring] recovery skipped", expect.objectContaining({ eventId: event.eventId, reason: "invalid_recovery_input" }));
  });

  it("preserves v4 diagnostics without a physical Session ID", async () => {
    const { service, trigger } = setup();
    await trigger.trigger({ ...event, sessionId: null });
    expect(service.create.mock.calls[0][0]).not.toHaveProperty("session_id");
  });

  it("returns a retryable error only for allowed targets when the recovery service is unavailable", async () => {
    const output = vi.spyOn(console, "info").mockImplementation(() => {});
    const { config } = setup();
    const trigger = createMonitoringRecoveryTrigger({ config, service: null });
    await trigger.trigger({ ...event, entityId: "another-owner" });
    await expect(trigger.trigger(event)).rejects.toMatchObject({ code: "NOT_READY" });
    const events = output.mock.calls.map(([line]) => JSON.parse(String(line).slice("[workflow-background] ".length)));
    expect(events.filter(event => event.event === "CREATE_FAILED")).toEqual([expect.objectContaining({
      eventId: event.eventId, botId: "default", errorCode: "recovery_not_ready", count: 1 })]);
  });
});


it("uses the real recovery parser to normalize v4 locators without forwarding credentials", async () => {
  const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const { service, trigger } = setup();
  await trigger.trigger({ ...event, schemaVersion: DIAGNOSIS_V4_VERSION,
    runtimeIdentity: { ...runtimeIdentity, OWNER_ID: event.entityId } });
  expect(service.create).toHaveBeenCalledTimes(1);
  const [request, actor] = service.create.mock.calls[0];
  expect(request.target).toEqual({ ...bot, engine: "OC", runtime_identity: {
    stage: "online", deviceUuid: runtimeIdentity.CLIENT_ID, version: "V16",
  } });
  expect(actor).toEqual({ userId: event.entityId, isAdmin: false });
  expect(request.target.runtime_identity).not.toHaveProperty("TOKEN");
  expect(request.target.runtime_identity).not.toHaveProperty("EXTENSION");
  expect(warning).not.toHaveBeenCalled();
});

it("does not downgrade missing or mismatched v4 identity to old recovery", async () => {
  const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const { service, trigger } = setup();
  for (const identity of [null, { ...runtimeIdentity, BOT_ID: "wrong" }, { ...runtimeIdentity, OWNER_ID: "" }]) {
    await trigger.trigger({ ...event, schemaVersion: DIAGNOSIS_V4_VERSION, runtimeIdentity: identity });
  }
  expect(service.create).not.toHaveBeenCalled();
  expect(warning).toHaveBeenCalledTimes(3);
});

it.each([{}, { STAGE: "" }])("creates recovery for a v4 personal Bot without a service stage: %j", async fields => {
  const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const { service, trigger } = setup();
  const { STAGE: _stage, ...identity } = runtimeIdentity;
  await trigger.trigger({ ...event, schemaVersion: DIAGNOSIS_V4_VERSION,
    runtimeIdentity: { ...identity, BOT_TYPE: "personal", OWNER_ID: event.entityId, ...fields } });
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(service.create.mock.calls[0][0].target).toEqual({ ...bot, engine: "OC" });
  expect(warning).not.toHaveBeenCalled();
});

// Cron/session interpretation belongs to the unmodified recovery parser.
it("preserves the physical Cron session ID and defers inconsistent locators to recovery validation", async () => {
  const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const { service, trigger } = setup();
  const cron = { ...event, sessionKey: "agent:main:cron:daily-job:run:cron-run-1", sessionId: "cron-run-1" };
  await trigger.trigger(cron);
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(service.create.mock.calls[0][0]).toMatchObject({ session_key: cron.sessionKey, session_id: cron.sessionId });
  await trigger.trigger({ ...cron, sessionId: "different-run" });
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(warning).toHaveBeenCalledWith("[monitoring] recovery skipped", expect.objectContaining({ reason: "invalid_recovery_input" }));
});
