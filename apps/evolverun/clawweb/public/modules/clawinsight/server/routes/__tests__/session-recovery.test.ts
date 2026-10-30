// @vitest-environment node
import express from "express";
import { expect, it, vi } from "vitest";
import { createSessionRecoveryRouter } from "../session-recovery.js";
import { logRepairEvent } from "../../services/repair/common/logging.js";
vi.mock("../../services/repair/common/logging.js", async importOriginal => ({
  ...await importOriginal<typeof import("../../services/repair/common/logging.js")>(), logRepairEvent: vi.fn(),
}));

it("logs a redacted creation failure while keeping the HTTP response generic", async () => {
  const fixtureValue = ["recovery", "fixture", "private", "credential", "123456"].join("-");
  const log = vi.mocked(logRepairEvent).mockClear();
  const service = { create: vi.fn().mockRejectedValue(new Error(`OSS PutObject failed: Authorization: Bearer ${fixtureValue}`)) };
  const app = express(); app.use(createSessionRecoveryRouter({ service: service as never }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  try {
    const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/internal/monitoring/session-recoveries`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ event_id: "event-fixture" }),
    });
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.error).toBe("recovery_unavailable");
    expect(JSON.stringify(body)).not.toContain("OSS PutObject");
    expect(log).toHaveBeenCalledOnce();
    const record = log.mock.calls[0][3]!;
    expect(record).toMatchObject({ operation: "POST /internal/monitoring/session-recoveries" });
    expect(record.reason).toContain("OSS PutObject failed");
    expect(record.reason).not.toContain(fixtureValue);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); log.mockClear(); }
});

it("counts an unavailable recovery service at the creation entry point", async () => {
  const output = vi.mocked(logRepairEvent).mockClear();
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const app = express(); app.use(createSessionRecoveryRouter());
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  try {
    const request = { event_id: "event-fixture", tc_fault_label: null, delivery_key: "delivery-1", diagnosis: "任务受阻",
      target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:session:original" };
    const response = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/internal/monitoring/session-recoveries`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
    });
    expect(response.status).toBe(503);
    for (const body of [{}, { ...request, execution_level: "debug" }, { ...request, execution_level: "invalid" }]) {
      const rejected = await fetch(`http://127.0.0.1:${(server.address() as { port: number }).port}/internal/monitoring/session-recoveries`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      expect(rejected.status).toBe(503); // Keep the existing unavailable-service HTTP contract.
    }
    const events = output.mock.calls.map(([_scope, job, event, fields]) => ({ job, event, ...fields }));
    expect(events.filter(event => event.event === "CREATE_FAILED")).toEqual([expect.objectContaining({ errorCode: "recovery_not_ready", count: 1 })]);
  } finally {
    server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));
    output.mockClear(); error.mockRestore();
  }
});
