// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Request, Response } from "express";
import { createServiceTokenMiddleware, type ServiceTokenMiddleware } from "../service-token.js";

type RunResult = { status: number; body: Record<string, unknown> | undefined; passed: boolean };

function run(middleware: ServiceTokenMiddleware, headers: Record<string, string>): Promise<RunResult> {
  return new Promise((resolve) => {
    const req = { headers } as Request;
    let status = 200;
    const res = {
      status(code: number) { status = code; return this; },
      json(data: Record<string, unknown>) {
        resolve({ status, body: data, passed: false });
        return this;
      },
    } as unknown as Response;
    const next = () => resolve({ status, body: undefined, passed: true });
    middleware(req, res, next);
  });
}

beforeEach(() => {
  delete process.env.CLAWWEB_SERVICE_AUTH_TOKEN;
});

afterEach(() => {
  vi.restoreAllMocks();
  delete process.env.CLAWWEB_SERVICE_AUTH_TOKEN;
});

describe("createServiceTokenMiddleware", () => {
  it("rejects every request with 403 when no token is configured in a non-dev environment", async () => {
    const mw = createServiceTokenMiddleware({ isDev: false });
    const result = await run(mw, { authorization: "Bearer anything" });
    expect(result).toMatchObject({ status: 403, passed: false });
    expect(result.body).toMatchObject({ error: "Forbidden" });
  });

  it("passes through when no token is configured in dev", async () => {
    const mw = createServiceTokenMiddleware({ isDev: true });
    const result = await run(mw, {});
    expect(result.passed).toBe(true);
  });

  it("accepts a matching Authorization Bearer token", async () => {
    const token = "a".repeat(32);
    const mw = createServiceTokenMiddleware({ token, isDev: false });
    const result = await run(mw, { authorization: `Bearer ${token}` });
    expect(result.passed).toBe(true);
  });

  it("accepts a matching X-Service-Token header", async () => {
    const token = "b".repeat(32);
    const mw = createServiceTokenMiddleware({ token, isDev: false });
    const result = await run(mw, { "x-service-token": token });
    expect(result.passed).toBe(true);
  });

  it("rejects a wrong Bearer token with 401", async () => {
    const mw = createServiceTokenMiddleware({ token: "c".repeat(32), isDev: false });
    const result = await run(mw, { authorization: "Bearer wrong-token-value" });
    expect(result).toMatchObject({ status: 401, passed: false });
  });

  it("rejects a request with no credentials with the same 401", async () => {
    const mw = createServiceTokenMiddleware({ token: "d".repeat(32), isDev: false });
    const result = await run(mw, {});
    expect(result).toMatchObject({ status: 401, passed: false });
    expect(result.body).toMatchObject({ error: "Unauthorized", message: "Authentication required" });
  });

  it("never leaks the configured token in error responses or logs", async () => {
    // Built via concatenation so no credential-like literal ever lands in source.
    const token = ["leak", "check", "t".repeat(32)].join("-");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const mw = createServiceTokenMiddleware({ token, isDev: false });
    const result = await run(mw, { authorization: "Bearer nope" });
    expect(result.status).toBe(401);
    const serialized = JSON.stringify(result.body) + JSON.stringify(warn.mock.calls);
    expect(serialized).not.toContain(token);
    expect(serialized).not.toContain("leak-check");
  });

  it("falls back to the CLAWWEB_SERVICE_AUTH_TOKEN environment variable", async () => {
    const token = "e".repeat(32);
    process.env.CLAWWEB_SERVICE_AUTH_TOKEN = token;
    const mw = createServiceTokenMiddleware({ isDev: false });
    expect((await run(mw, { authorization: `Bearer ${token}` })).passed).toBe(true);
    expect((await run(mw, { authorization: "Bearer other" })).passed).toBe(false);
  });

  it("an explicit token wins over the environment variable", async () => {
    process.env.CLAWWEB_SERVICE_AUTH_TOKEN = "f".repeat(32);
    const mw = createServiceTokenMiddleware({ token: "g".repeat(32), isDev: false });
    expect((await run(mw, { "x-service-token": "g".repeat(32) })).passed).toBe(true);
    expect((await run(mw, { "x-service-token": "f".repeat(32) })).passed).toBe(false);
  });

  it("warns (without printing the token) when the token is shorter than 32 bytes", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    createServiceTokenMiddleware({ token: "short-token", isDev: false });
    expect(warn).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(warn.mock.calls)).not.toContain("short-token");
  });
});