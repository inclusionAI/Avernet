import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DIAGNOSIS_V3_VERSION, DIAGNOSIS_VERSION } from "../contracts.js";
import { parseDiagnosis, parseRuntimeIdentity } from "../validation.js";
import { buildRecoveryTarget } from "../session-recovery-trigger.js";
import { runtimeIdentity } from "./runtime-identity-fixture.js";

const old = JSON.parse(readFileSync(join(process.cwd(), "server/fixtures/monitoring/alert.json"), "utf8"));
const event = { ...old, botId: "default", engine: "OC", schemaVersion: DIAGNOSIS_V3_VERSION, runtimeIdentity };

describe("runtime identity v3 wire contract", () => {
  it("preserves the complete object and version without changing the old event", () => {
    const parsed = parseDiagnosis(event, event.eventId);
    expect(parsed.runtimeIdentity).toEqual(runtimeIdentity);
    expect(parsed.runtimeIdentity).not.toBe(runtimeIdentity);
    expect(parsed.schemaVersion).toBe(DIAGNOSIS_V3_VERSION);
    expect(parseDiagnosis(old, old.eventId).schemaVersion).toBe(DIAGNOSIS_VERSION);
    expect(parseDiagnosis(old, old.eventId)).not.toHaveProperty("runtimeIdentity");
    expect(parseDiagnosis({ ...event, runtimeIdentity: null }, event.eventId).runtimeIdentity).toBeNull();
  });
  it.each([undefined, "{}", [], {}, { A: 123 }, { "": "value" }, { A: "\uD800" }])("rejects invalid wire values without echoing credentials: %j", runtimeIdentity => {
    expect(() => parseDiagnosis({ ...event, runtimeIdentity }, event.eventId)).toThrow();
  });
  it("requires a v3 key, forbids it for v2, and keeps exact top-level keys", () => {
    const { runtimeIdentity: _, ...missing } = event;
    for (const input of [missing, { ...old, runtimeIdentity }, { ...event, unexpected: true }]) {
      expect(() => parseDiagnosis(input, event.eventId)).toThrow();
    }
  });
  it("bounds UTF-8 JSON bytes and handles special keys as inert data", () => {
    expect(() => parseRuntimeIdentity({ A: "中".repeat(11000) })).toThrow();
    const special = JSON.parse('{"__proto__":"literal","constructor":"value","ADMINS":""}');
    const parsed = parseRuntimeIdentity(special);
    expect(parsed).toEqual(special);
    expect(Object.prototype.hasOwnProperty.call(parsed, "__proto__")).toBe(true);
    expect(parseRuntimeIdentity({ z: "last", a: "first" })).toEqual({ a: "first", z: "last" });
    expect(JSON.stringify(parseRuntimeIdentity({ z: "last", a: "first" }))).toBe('{"a":"first","z":"last"}');
  });
  it("maps owner rather than entity, without business validation or cropping extensions", () => {
    const parsed = parseDiagnosis(event, event.eventId);
    expect(buildRecoveryTarget(parsed)).toEqual({
      bot_id: "default", entity_id: runtimeIdentity.OWNER_ID, env: event.env, engine: "OC", runtime_identity: runtimeIdentity,
    });
    expect(parsed.entityId).toBe(old.entityId);
    expect(buildRecoveryTarget(parseDiagnosis({ ...event, runtimeIdentity: { ...runtimeIdentity, STAGE: "draft", VERSION: "" } }, event.eventId)))
      .toHaveProperty("runtime_identity.VERSION", "");
  });
  it.each([null, { ...runtimeIdentity, BOT_ID: "other" }, { ...runtimeIdentity, OWNER_ID: "" }, { STAGE: "online" }])("skips new OC identities without a reliable target", runtimeIdentity => {
    expect(buildRecoveryTarget(parseDiagnosis({ ...event, runtimeIdentity }, event.eventId))).toBeNull();
  });
  it("keeps v2 and TE on their old path", () => {
    for (const input of [old, { ...event, engine: "TE" }]) {
      expect(buildRecoveryTarget(parseDiagnosis(input, input.eventId))).toEqual({
        bot_id: input.botId, entity_id: input.entityId, env: input.env, engine: input.engine,
      });
    }
  });
});
