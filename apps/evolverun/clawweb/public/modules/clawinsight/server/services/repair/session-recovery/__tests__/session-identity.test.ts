import { expect, it } from "vitest";
import { parseRequest } from "../validation.js";

const request = { event_id: "identity-fixture", tc_fault_label: null, delivery_key: "delivery", diagnosis: "Original operation failed",
  target: { bot_id: "default", entity_id: "owner", env: "prod", engine: "OC" }, session_key: "agent:main:cron:daily:run:original" };
it("accepts and retains the upstream physical Session ID without requiring it from legacy callers", () => {
  expect(parseRequest({ ...request, session_id: "original" })).toMatchObject({ session_id: "original" });
  expect(parseRequest(request)).not.toHaveProperty("session_id");
});
it.each(["another-run", "../outside", "bad\nvalue"])("rejects conflicting or unsafe physical Session IDs: %j", session_id => {
  expect(() => parseRequest({ ...request, session_id })).toThrow();
});
