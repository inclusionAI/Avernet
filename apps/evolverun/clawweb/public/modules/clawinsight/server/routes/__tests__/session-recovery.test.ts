// @vitest-environment node
import express from "express";
import { expect, it, vi } from "vitest";
import { createSessionRecoveryRouter } from "../session-recovery.js";

it("logs a redacted creation failure while keeping the HTTP response generic", async () => {
  const fixtureValue = ["recovery", "fixture", "private", "credential", "123456"].join("-");
  const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
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
    const record = JSON.parse(log.mock.calls[0][1]);
    expect(record).toMatchObject({ method: "POST", path: "/internal/monitoring/session-recoveries" });
    expect(record.error).toContain("OSS PutObject failed");
    expect(record.error).not.toContain(fixtureValue);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); log.mockRestore(); }
});

it("counts an unavailable recovery service at the creation entry point", async () => {
  const output = vi.spyOn(console, "info").mockImplementation(() => {});
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
    const events = output.mock.calls.map(([line]) => JSON.parse(String(line).slice("[workflow-background] ".length)));
    expect(events.filter(event => event.event === "CREATE_FAILED")).toEqual([expect.objectContaining({ errorCode: "recovery_not_ready", count: 1 })]);
  } finally {
    server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));
    output.mockRestore(); error.mockRestore();
  }
});
