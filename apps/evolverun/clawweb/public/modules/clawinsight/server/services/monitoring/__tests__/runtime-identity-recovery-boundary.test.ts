/** Consumer contract only. This mock represents the OTHER developer's future parser.
 * Real-parser rejection in this checkout is covered separately; this is NOT live integration.
 */
import { beforeEach, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { DIAGNOSIS_V3_VERSION } from "../contracts.js";
import { parseDiagnosis } from "../validation.js";
import { createMonitoringRecoveryTrigger } from "../session-recovery-trigger.js";
import { SessionRecoveryError } from "../../repair/session-recovery/errors.js";
import { runtimeIdentity } from "./runtime-identity-fixture.js";

const boundary = vi.hoisted(() => ({ parse: vi.fn((input: unknown) => input) }));
vi.mock("../../repair/session-recovery/validation.js", () => ({ parseRequest: boundary.parse }));
const original = JSON.parse(readFileSync(join(process.cwd(), "server/fixtures/monitoring/alert.json"), "utf8"));
const event = parseDiagnosis({ ...original, botId: "default", engine: "OC", env: "prod", handlerName: "NOT_COMPLETED",
  schemaVersion: DIAGNOSIS_V3_VERSION, runtimeIdentity }, original.eventId);
function setup() {
  const service = { create: vi.fn().mockResolvedValue({ status: "accepted" }) };
  const config = { findByKey: vi.fn().mockResolvedValue({ enabled: 1, config_json: JSON.stringify({
    allowed_bots: [{ bot_id: event.botId, entity_id: event.entityId, env: event.env }],
  }) }) };
  return { service, trigger: createMonitoringRecoveryTrigger({ service, config }) };
}
beforeEach(() => { vi.restoreAllMocks(); boundary.parse.mockClear(); });

it("delivers the complete object through the parser and retains the trusted actor on retries", async () => {
  const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
  const { service, trigger } = setup();
  service.create.mockRejectedValueOnce(new Error("temporary"));
  await expect(trigger.trigger(event)).rejects.toMatchObject({ code: "NOT_READY" });
  await trigger.trigger(event);
  expect(boundary.parse).toHaveBeenCalledTimes(2);
  const request = boundary.parse.mock.calls[0][0];
  expect(request).toHaveProperty("target", { bot_id: "default", entity_id: runtimeIdentity.OWNER_ID,
    env: "prod", engine: "OC", runtime_identity: runtimeIdentity });
  expect(service.create).toHaveBeenCalledWith(request, { userId: event.entityId, isAdmin: false });
  expect(service.create.mock.calls[0]).toEqual(service.create.mock.calls[1]);
  expect(JSON.stringify(error.mock.calls)).not.toContain(runtimeIdentity.TOKEN);
});

it.each([409, 422])("does not delete identity or retry another target after recovery returns %s", async status => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const { service, trigger } = setup();
  service.create.mockRejectedValue(new SessionRecoveryError(status, "rejected", "synthetic rejection"));
  await expect(trigger.trigger(event)).rejects.toMatchObject({ code: status === 409 ? "EVENT_CONFLICT" : "INVALID_EVENT" });
  expect(service.create).toHaveBeenCalledTimes(1);
  expect(boundary.parse.mock.calls[0][0]).toHaveProperty("target.runtime_identity", runtimeIdentity);
});
