// @vitest-environment node
import { expect, it, vi } from "vitest";
import express from "express";
import type { Server } from "node:http";
import { BotRuntimeClient, BaasRuntimeProvider } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { ResolvedBaasConfig } from "@avernet/clawweb-shared/server/db";
import { createSessionRecoveryRouter } from "../../../routes/session-recovery.js";
import { SessionRecoveryDeliveryRepository } from "../../../repositories/session-recovery-delivery-repository.js";
import { HttpRecoveryResultReporter } from "../result-reporter.js";
import { SessionRecoveryService } from "../service.js";
import { recoveryDatabase } from "./database.js";
async function listen(app: ReturnType<typeof express>): Promise<{ server: Server; origin: string }> {
  const server = await new Promise<Server>(resolve => { const server = app.listen(0, "127.0.0.1", () => resolve(server)); });
  return { server, origin: `http://127.0.0.1:${(server.address() as { port: number }).port}` };
}
async function close(server: Server) { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
it("runs HTTP trigger → BaaS protocol → durable receipt → failed callback → exact replay without a second message", async () => {
  const db = await recoveryDatabase(), messages: unknown[] = [], results: unknown[] = [];
  const peer = express(); peer.use(express.json());
  peer.post("/openapi/v1/messages", (req, res) => {
    messages.push(req.body); res.json({ code: 0, data: { message_id: "provider-receipt", session_id: "original-session" } });
  });
  peer.post("/api/insight/v1/internal/monitoring/session-recovery-results", (req, res) => {
    expect(req.headers.authorization).toBe("Bearer test-token"); results.push(req.body);
    res.sendStatus(results.length === 1 ? 503 : 204);
  });
  const remote = await listen(peer);
  const runtime = new BotRuntimeClient({ targets: { resolve: async input => ({ ...input, provider: "baas", bindingId: "binding", deviceId: "device", activeEngine: "openclaw" }) },
    providers: { baas: new BaasRuntimeProvider({ iamtoken: "test-iam", environments: { prod: { baseUrl: remote.origin, apiKey: "test-token" } } } as ResolvedBaasConfig) } });
  const service = new SessionRecoveryService({ runtime, store: new SessionRecoveryDeliveryRepository(db), logger: { log: vi.fn() },
    reporter: new HttpRecoveryResultReporter({ baseUrl: remote.origin, token: "test-token" }) });
  const app = express(); app.use("/api/insight/v1", createSessionRecoveryRouter({ service, triggerToken: "test-token" }));
  const host = await listen(app);
  const request = { event_id: "event", tc_fault_label: null, target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" },
    session_key: "agent:main:original", delivery_key: "attempt-1", diagnosis: "𠮷".repeat(8000) };
  const send = (body = request) => fetch(host.origin + "/api/insight/v1/internal/monitoring/session-recoveries", {
    method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer test-token" }, body: JSON.stringify(body),
  });
  try {
    const first = await send(); expect(first.status).toBe(503); await first.arrayBuffer();
    const second = await send(); expect(second.status).toBe(202);
    expect(await second.json()).toEqual({ event_id: "event", delivery_key: "attempt-1", status: "accepted" });
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ bot_id: "default:owner", message_id: "attempt-1", metadata: { session_id: "agent:main:original" } });
    expect(results).toHaveLength(2); expect(results[1]).toEqual(results[0]);
    expect(results[0]).toMatchObject({ event_id: "event", delivery: { status: "accepted", error: null,
      result_payload: { provider_message_id: "provider-receipt", session_id: "original-session" } } });
    const conflict = await send({ ...request, diagnosis: "changed" }); expect(conflict.status).toBe(409); await conflict.arrayBuffer();
    expect(messages).toHaveLength(1);
    expect((await db.query("SELECT callback_delivered FROM insight_session_recovery_delivery"))[0].callback_delivered).toBe(1);
  } finally { await close(host.server); await close(remote.server); await db.close(); }
});
