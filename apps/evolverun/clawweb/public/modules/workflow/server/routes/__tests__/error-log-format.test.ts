// @vitest-environment node
import { describe, expect, it } from "vitest";
import { apiErrorDiagnostic, captureApiError, captureSensitiveApiError, errorStack } from "../error-log-format.js";

describe("safe exception console formatting", () => {
  it("preserves the original stack byte-for-byte without serializing HTTP client metadata", () => {
    const error = Object.assign(new Error("upstream unavailable"), {
      config: { headers: { Authorization: "private-auth-marker", Cookie: "private-cookie-marker" } },
      cookies: ["private-cookie-marker"], token: "private-token-marker",
    });
    const formatted = errorStack(error);
    expect(formatted).toBe(error.stack);
    expect(formatted).not.toMatch(/private-|Authorization|Cookie|cookies|config|token/);
  });

  it("labels an Error without a stack as an observation rather than a caught stack", () => {
    const error = Object.assign(new Error("message only"), { token: "private-token-marker" });
    delete error.stack;
    const formatted = errorStack(error);
    expect(formatted).toContain("Error: Workflow API failure observed: message only\n");
    expect(formatted).toMatch(/\n\s+at /);
    expect(formatted).not.toContain("private-token-marker");
  });

  it("only reads stack/message on non-Error exceptions without inspecting or coercing extensions", () => {
    const error = { stack: "original remote stack", message: "remote failure", token: "private-token-marker",
      toString() { throw new Error("must not coerce HTTP metadata"); } };
    expect(errorStack(error)).toBe("original remote stack");
    const messageOnly = { message: "remote failure", get config() { throw new Error("must not inspect extensions"); },
      [Symbol.toPrimitive]() { throw new Error("must not coerce exception"); } };
    expect(errorStack(messageOnly)).toContain("Error: Workflow API failure observed: remote failure\n");
    expect(errorStack({ token: "private-token-marker" })).toContain("Error: Workflow API failure observed: Unknown error (non-Error exception)\n");
  });

  it.each([null, undefined, 42, true])("returns a fixed diagnostic for non-message exception %j", exception => {
    const formatted = errorStack(exception);
    expect(formatted).toContain("Error: Workflow API failure observed: Unknown error (non-Error exception)\n");
    expect(formatted).toMatch(/\n\s+at /);
  });

  it("gives string exceptions an explicitly observed stack", () => {
    const formatted = errorStack("plain failure");
    expect(formatted).toContain("Error: Workflow API failure observed: plain failure\n");
    expect(formatted).toMatch(/\n\s+at /);
  });

  it("never wraps or normalizes original stack bytes", () => {
    const stack = "CustomError: original failure\r\n    at originalFrame (original.ts:7:3)\r\n";
    expect(errorStack({ stack, message: "original failure" })).toBe(stack);
  });
});


describe("logging-only HTTP diagnostic handoff", () => {
  it("retains the exact caught stack and only the three approved fields", () => {
    const error = Object.assign(new Error("original message"), { config: { headers: { Authorization: "private-auth" } }, token: "private-token" });
    const res = { locals: { unrelated: "unchanged" } };
    expect(captureApiError(res, error)).toBe(error.stack);
    expect(res.locals).toEqual({ unrelated: "unchanged", taskGuardApiDiagnostic: {
      message: error.message, stack: error.stack, stackSource: "exception",
    } });
    expect(JSON.stringify(res.locals)).not.toMatch(/private-|Authorization|config|token/);
  });

  it.each(["plain failure", { message: "plain failure" }])("labels a missing original stack as an observation: %j", error => {
    const diagnostic = apiErrorDiagnostic(error);
    expect(diagnostic).toEqual({ message: "plain failure", stack: expect.stringContaining("Workflow API failure observed: plain failure"), stackSource: "observation" });
  });

  it("offers a fixed observation without accepting sensitive exceptions", () => {
    const res = { locals: {} };
    const stack = captureSensitiveApiError(res);
    expect(res.locals).toEqual({ taskGuardApiDiagnostic: { message: "Protected workflow operation failed", stack, stackSource: "observation" } });
    expect(stack).toContain("Workflow protected operation failure observed");
  });
});
