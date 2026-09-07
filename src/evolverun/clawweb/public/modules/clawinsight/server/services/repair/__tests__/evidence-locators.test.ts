import { describe, expect, it } from "vitest";
import { evidenceLocatorsFromText } from "../evidence-locators.js";

describe("evidenceLocatorsFromText", () => {
  it("extracts exact paths and normalizes stack line suffixes", () => {
    expect(evidenceLocatorsFromText(
      "admin 101 node /opt/runtime/server.js --mode worker",
      "at load (/opt/runtime/loader.js:42:7)",
    )).toEqual(["/opt/runtime/server.js", "/opt/runtime/loader.js"]);
  });

  it("does not turn URLs, root, or dot-segment paths into locators", () => {
    expect(evidenceLocatorsFromText(
      "https://example.test/not-a-file / ../secret /opt/../secret",
    )).toEqual([]);
  });
});
