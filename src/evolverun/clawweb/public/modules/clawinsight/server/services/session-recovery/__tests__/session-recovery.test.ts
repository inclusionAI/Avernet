// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BotRuntimeClient } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryService } from "../service.js";
import { SessionRecoveryDeliveryRepository } from "../../../repositories/session-recovery-delivery-repository.js";
import { recoveryDatabase } from "./database.js";
import { parseRequest } from "../validation.js";
import { recoveryAdvice } from "../handbook.js";
const sender = { send: vi.fn() }, targets = { resolve: vi.fn() }, logger = { log: vi.fn() }, reporter = { report: vi.fn() };
const request = { event_id: "event-1", tc_fault_label: "TC.MCP.PERMISSION", diagnosis: "MCP access denied",
  target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" }, session_key: "agent:main:dashboard:test", delivery_key: "upstream-key" };
const actor = { userId: "owner", isAdmin: false };
const target = { environment: "prod", ownerId: "owner", botId: "default", bindingId: "binding", provider: "arca", deviceId: "device", activeEngine: "openclaw" };
const runtime = new BotRuntimeClient({ targets, providers: { arca: { sendMessage: sender.send, executeShell: vi.fn(), request: vi.fn() } } });
let service: SessionRecoveryService;
let db: Awaited<ReturnType<typeof recoveryDatabase>>;
afterEach(async () => { await db?.close(); });
beforeEach(async () => {
  db = await recoveryDatabase();
  vi.resetAllMocks(); targets.resolve.mockResolvedValue(target);
  sender.send.mockResolvedValue({ status: "accepted", sessionId: "actual-session", messageId: "actual-message", locationStatus: "confirmed" });
  reporter.report.mockResolvedValue(undefined);
  service = new SessionRecoveryService({ runtime, logger, reporter, store: new SessionRecoveryDeliveryRepository(db), now: () => 1000 });
});
describe("session recovery v2", () => {
  it("returns v2 acknowledgment and reports only the defined result envelope", async () => {
    expect(await service.create(request, actor)).toEqual({ event_id: "event-1", delivery_key: "upstream-key", status: "accepted" });
    expect(sender.send).toHaveBeenCalledTimes(1);
    expect(reporter.report).toHaveBeenCalledWith({ event_id: "event-1", delivery: {
      delivery_key: "upstream-key", status: "accepted", result_text: "建议消息已被投递通道接受；尚未判断原任务是否恢复。",
      result_payload: { session_id: "actual-session", message_id: "actual-message" }, started_at_ms: 1000, finished_at_ms: 1000, error: null,
    } });
    expect(sender.send.mock.lastCall![0]).toMatchObject({ sessionKey: request.session_key, deliveryKey: request.delivery_key, expectedEngine: "openclaw" });
    expect(logger.log).toHaveBeenCalledWith({ event: "session_recovery_delivery", ...reporter.report.mock.lastCall![0] });
    expect(JSON.stringify(reporter.report.mock.calls)).not.toContain(request.diagnosis);
    expect(reporter.report.mock.lastCall![0].delivery).not.toHaveProperty("message");
  });
  it("accepts an explicit null fault label and uses generic handbook advice", async () => {
    await service.create({ ...request, tc_fault_label: null }, actor);
    expect(sender.send.mock.lastCall![0].message).toBe(recoveryAdvice(null, request.delivery_key).message);
    expect(sender.send.mock.lastCall![0].message).not.toContain("mcporter");
  });
  it.each(["failed", "unknown"])("reports %s with a sanitized error and no automatic send retry", async status => {
    sender.send.mockResolvedValue({ status, error: "IAM_TOKEN=do-not-log", private_field: "must-not-forward" });
    expect(await service.create(request, actor)).toMatchObject({ status: "accepted" });
    expect(sender.send).toHaveBeenCalledTimes(1);
    const result = reporter.report.mock.lastCall![0];
    expect(result.delivery).toMatchObject({ status, result_text: "", result_payload: {}, error: expect.any(String) });
    expect(JSON.stringify(result)).not.toContain("do-not-log"); expect(JSON.stringify(result)).not.toContain("private_field");
  });
  it("reports an unknown thrown transport outcome without leaking the exception", async () => {
    sender.send.mockRejectedValue(Error("Bearer private-credential"));
    await service.create(request, actor);
    expect(reporter.report.mock.lastCall![0].delivery.status).toBe("unknown");
    expect(JSON.stringify(logger.log.mock.calls)).not.toContain("private-credential");
    expect(sender.send).toHaveBeenCalledTimes(1);
  });
  it("rejects unauthorized requests and invalid target snapshots before any send", async () => {
    await expect(service.create(request, { userId: "other", isAdmin: false })).rejects.toMatchObject({ status: 403 });
    targets.resolve.mockResolvedValue({ ...target, ownerId: "other" });
    await expect(service.create(request, actor)).rejects.toMatchObject({ status: 422 });
    targets.resolve.mockResolvedValue({ ...target, activeEngine: "teclaw" });
    await expect(service.create({ ...request, delivery_key: "engine-mismatch" }, actor)).rejects.toMatchObject({ status: 422 });
    expect(sender.send).not.toHaveBeenCalled(); expect(reporter.report).not.toHaveBeenCalled();
  });
  it.each([undefined, "", "../bad", "new\nline", "bad]marker", null, "x".repeat(129)])("requires a valid delivery_key %j", delivery_key => {
    expect(() => parseRequest({ ...request, delivery_key })).toThrow();
  });
  it("requires label presence, engine and diagnosis; rejects the old schema and extra fields", () => {
    for (const patch of [{ tc_fault_label: undefined }, { diagnosis: undefined }, { target: { ...request.target, engine: undefined } },
      { diagnosisId: "old" }, { session_id: "wrong-locator" }, { diagnosis: "Authorization: Bearer do-not-store-this" }]) {
      expect(() => parseRequest({ ...request, ...patch })).toThrow();
    }
    expect(() => parseRequest({ ...request, target: { ...request.target, provider: "arca" } })).toThrow();
    expect(() => parseRequest({ ...request, diagnosis: "界".repeat(8000) })).not.toThrow();
    expect(() => parseRequest({ ...request, diagnosis: "界".repeat(8001) })).toThrow();
  });
});
it("HTTP trigger authenticates and responds with the exact v2 schema", async () => {
  const { default: express } = await import("express");
  const { createSessionRecoveryRouter } = await import("../../../routes/session-recovery.js");
  const app = express(); app.use(createSessionRecoveryRouter({ service, triggerToken: "test-service-token" }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const s = app.listen(0, "127.0.0.1", () => resolve(s)); });
  const origin = "http://127.0.0.1:" + (server.address() as { port: number }).port;
  const path = "/internal/monitoring/session-recoveries";
  try {
    expect((await fetch(origin + path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(request) })).status).toBe(401);
    const response = await fetch(origin + path, { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer test-service-token" }, body: JSON.stringify(request) });
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({ event_id: "event-1", delivery_key: "upstream-key", status: "accepted" });
    expect(reporter.report).toHaveBeenCalledTimes(1);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
