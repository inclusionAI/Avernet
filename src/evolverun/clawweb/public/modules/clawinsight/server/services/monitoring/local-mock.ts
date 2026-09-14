/** Loopback-only sender: use the production HTTP contract without contacting AIStudio. */
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { CHECK_VERSION } from "./contracts.js";
const port = Number(process.env.MONITORING_LOCAL_PORT ?? "3101");
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid local port");
const base = `http://127.0.0.1:${port}/api/insight/v1`;
const batch = randomUUID();
const now = Date.now();
const fixtures = ["alert", "pass", "unresolved"].map(name => JSON.parse(readFileSync(new URL(`../../fixtures/monitoring/${name}.json`, import.meta.url), "utf8")));
async function send(path: string, body: Record<string, unknown>) {
  const response = await fetch(`${base}/internal/monitoring/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json",
      ...(path === "diagnosis-events" ? { "Idempotency-Key": String(body.eventId) } : {}) },
    body: JSON.stringify(body), signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error(`Local reporting failed: HTTP ${response.status} ${await response.text()}`);
  return response.json();
}
let first: Record<string, unknown> | undefined;
for (let i = 0; i < 30; i++) {
  const source = fixtures[i % fixtures.length];
  const event = { ...source, eventId: `mock-${batch}-${i}`, diagnosisId: `mock-${batch}-${i}`,
    occurredAt: source.occurredAt === null ? null : new Date(now - i * 60000).toISOString(), diagnosedAt: new Date(now).toISOString() };
  first ??= event;
  const ack = await send("diagnosis-events", event);
  if (ack.stored !== true || ack.duplicate !== false) throw new Error("Unexpected initial diagnosis ACK");
}
const duplicate = await send("diagnosis-events", first!);
if (duplicate.stored !== true || duplicate.duplicate !== true) throw new Error("Duplicate ACK not confirmed");
for (const [botId, engine] of [["mock-bot-te", "TE"], ["mock-bot-oc", "OC"]]) {
  await send("bot-checks", { schemaVersion: CHECK_VERSION, botId, engine, checkedAt: new Date(now).toISOString(),
    lastSuccessfulCheckAt: new Date(now).toISOString(), status: "HEALTHY" });
}
const response = await fetch(`${base}/monitoring/bots/mock-bot-te/diagnoses?page=2&pageSize=10`, { signal: AbortSignal.timeout(10000) });
if (!response.ok) throw new Error(`Local query failed: HTTP ${response.status}`);
const page = await response.json();
if (page.items.length !== 10 || page.total < 20) throw new Error("Expected second page of TE diagnoses");
console.info(`[monitoring] added 30 diagnoses, confirmed duplicate ACK, reported 2 BotChecks; TE total=${page.total}, page2=${page.items.length}`);
