// @vitest-environment node
import { expect, it } from "vitest";
import { parseBotRuntimeIdentity } from "../runtime-identity.js";

const service = { BOT_TYPE: "service", STAGE: "online", CLIENT_ID: "DEVICE-" + "a".repeat(32), VERSION: "V16" };

it.each([{}, { STAGE: "" }])("does not require a release stage for a personal Bot: %j", fields => {
  expect(parseBotRuntimeIdentity({ BOT_TYPE: "personal", CLIENT_ID: service.CLIENT_ID, VERSION: "V1", ...fields })).toBeUndefined();
});

it.each([{}, { STAGE: "" }, { STAGE: "invalid" }])("still rejects missing or invalid service stages: %j", fields => {
  expect(() => parseBotRuntimeIdentity({ BOT_TYPE: "service", ...fields })).toThrow();
});

it.each([{ STAGE: "" }, { CLIENT_ID: "BOT-wrong" }, { VERSION: "16" }, { stage: "verify" }])("preserves service locator checks: %j", fields => {
  expect(() => parseBotRuntimeIdentity({ ...service, ...fields })).toThrow();
});

it("preserves normalized locators and does not treat unspecified Bot types as personal", () => {
  const identity = { stage: "online", deviceUuid: service.CLIENT_ID, version: "V16" };
  expect(parseBotRuntimeIdentity(identity)).toEqual(identity);
  expect(parseBotRuntimeIdentity(service)).toEqual(identity);
  expect(() => parseBotRuntimeIdentity({ STAGE: "" })).toThrow();
  expect(parseBotRuntimeIdentity(undefined)).toBeUndefined();
});
