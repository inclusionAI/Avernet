import { afterEach, describe, expect, it, vi } from "vitest";
import { DIAGNOSIS_V4_VERSION } from "../contracts.js";
import { parseDiagnosis } from "../validation.js";
import { buildRecoveryTarget, createMonitoringRecoveryTrigger } from "../session-recovery-trigger.js";
import { runtimeIdentity } from "./runtime-identity-fixture.js";

const deviceUuid = `DEVICE-${"b".repeat(32)}`;
const sessionNasPath = `/nas/prod/prod_staff_different-entity_openclaw_default_${deviceUuid}/.openclaw/agents/main/sessions/physical-session.jsonl`;
const event = {
  schemaVersion: DIAGNOSIS_V4_VERSION, eventId: "nas-instance", diagnosisId: "nas-instance",
  botId: "default", entityId: runtimeIdentity.OWNER_ID, env: "prod", engine: "OC",
  sessionKey: "agent:main:session:original", sessionId: "physical-session", traceId: null,
  occurredAt: null, diagnosedAt: "2026-10-27T00:00:00Z", decision: "ALERT",
  tcFaultLabel: "TC.MCP.PERMISSION", confidence: 0.2, businessProblemCategory: null,
  businessProblemSubtype: null, systemDiagnosis: "Tool failed", businessDiagnosis: "Task unfinished",
  handlerName: "NOT_COMPLETED", humanIntervention: false, runtimeIdentity,
};
const parse = (patch = {}) => parseDiagnosis({ ...event, ...patch }, event.eventId);
afterEach(() => vi.restoreAllMocks());

describe("online service session NAS instance correction", () => {
  it("accepts the optional path without changing credentials or requiring it on existing reports", () => {
    expect(parse({ sessionNasPath })).toHaveProperty("sessionNasPath", sessionNasPath);
    expect(parse({ sessionNasPath }).runtimeIdentity).toEqual(runtimeIdentity);
    expect(parse()).not.toHaveProperty("sessionNasPath");
    expect(parse({ sessionNasPath: null })).not.toHaveProperty("sessionNasPath");
  });

  it.each([42, {}, "", "relative/session.jsonl", "/nas/../session.jsonl", "/nas/./session.jsonl", "/nas/\u0000session.jsonl", "/" + "x".repeat(4096)])(
    "rejects an invalid path hint without echoing it: %j", sessionNasPath => {
      expect(() => parse({ sessionNasPath })).toThrow();
    },
  );

  it("copies only mismatched CLIENT_ID and warns without logging credentials", () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    const parsed = parse({ sessionNasPath });
    const target = buildRecoveryTarget(parsed)!;
    expect(target.runtime_identity).toEqual({ ...runtimeIdentity, CLIENT_ID: deviceUuid });
    expect(target.runtime_identity).not.toBe(parsed.runtimeIdentity);
    expect(parsed.runtimeIdentity).toEqual(runtimeIdentity);
    expect(warning).toHaveBeenCalledExactlyOnceWith("[monitoring] recovery instance corrected", {
      eventId: event.eventId, botId: event.botId, previousClientId: runtimeIdentity.CLIENT_ID, deviceUuid,
    });
    expect(JSON.stringify(warning.mock.calls)).not.toContain(runtimeIdentity.TOKEN);
    expect(JSON.stringify(warning.mock.calls)).not.toContain("EXTENSION");
    expect(JSON.stringify(warning.mock.calls)).not.toContain(sessionNasPath);
  });

  it("does not copy or warn when the IDs already agree", () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    const parsed = parse({ sessionNasPath, runtimeIdentity: { ...runtimeIdentity, CLIENT_ID: deviceUuid } });
    expect(buildRecoveryTarget(parsed)!.runtime_identity).toBe(parsed.runtimeIdentity);
    expect(warning).not.toHaveBeenCalled();
  });

  it.each([
    { BOT_TYPE: "personal" }, { STAGE: "draft" }, { STAGE: "verify" },
  ])("keeps other Bot types and stages unchanged: %j", patch => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    const parsed = parse({ sessionNasPath, runtimeIdentity: { ...runtimeIdentity, ...patch } });
    expect(buildRecoveryTarget(parsed)!.runtime_identity).toBe(parsed.runtimeIdentity);
    expect(warning).not.toHaveBeenCalled();
  });

  it("keeps TE and reports without a path on the existing route", () => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(buildRecoveryTarget(parse({ sessionNasPath, engine: "TE", traceId: "trace" }))).not.toHaveProperty("runtime_identity");
    const parsed = parse();
    expect(buildRecoveryTarget(parsed)!.runtime_identity).toBe(parsed.runtimeIdentity);
    expect(warning).not.toHaveBeenCalled();
  });

  it.each([
    sessionNasPath.replace("_default_", "_another-bot_"),
    sessionNasPath.replace("_different-entity_", "_another-entity_"),
    sessionNasPath.replace("_staff_", "_team_"),
    sessionNasPath.replace("/prod/", "/pre/"),
    sessionNasPath.replace("physical-session.jsonl", "another-session.jsonl"),
    sessionNasPath.replace("/agents/main/", "/agents/other/"),
    sessionNasPath.replace(`_${deviceUuid}`, ""),
    sessionNasPath.replace(deviceUuid, "DEVICE-invalid"),
    sessionNasPath + "/nested.jsonl",
  ])("does not use a path outside the diagnosed session namespace: %s", sessionNasPath => {
    const warning = vi.spyOn(console, "warn").mockImplementation(() => {});
    const parsed = parse({ sessionNasPath });
    expect(buildRecoveryTarget(parsed)!.runtime_identity).toBe(parsed.runtimeIdentity);
    expect(warning).not.toHaveBeenCalled();
  });

  it.each(["pre", "prod"])("uses lifecycle stage online independently of the %s environment", env => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const path = sessionNasPath.replaceAll("prod", env);
    expect(buildRecoveryTarget(parse({ env, sessionNasPath: path }))!.runtime_identity.CLIENT_ID).toBe(deviceUuid);
  });

  it("feeds the corrected locator to the existing parser and preserves retry inputs and actor", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
    const service = { create: vi.fn().mockRejectedValueOnce(new Error("storage unavailable")).mockResolvedValue({ status: "accepted" }) };
    const config = { findByKey: vi.fn().mockResolvedValue({ enabled: 1, config_json: JSON.stringify({
      allowed_bots: [{ bot_id: event.botId, entity_id: event.entityId, env: event.env }],
    }) }) };
    const trigger = createMonitoringRecoveryTrigger({ config, service });
    const parsed = parse({ sessionNasPath });
    await expect(trigger.trigger(parsed)).rejects.toMatchObject({ code: "NOT_READY" });
    await trigger.trigger(parsed);
    expect(service.create.mock.calls[0]).toEqual(service.create.mock.calls[1]);
    const [request, actor] = service.create.mock.calls[1];
    expect(request.target.runtime_identity).toEqual({ stage: "online", version: "V16", deviceUuid });
    expect(request).not.toHaveProperty("sessionNasPath");
    expect(actor).toEqual({ userId: event.entityId, isAdmin: false });
    expect(parsed.runtimeIdentity).toEqual(runtimeIdentity);
  });
});
