// @vitest-environment node
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

const files = vi.hoisted(() => ({
  appendFileSync: vi.fn(), mkdirSync: vi.fn(), existsSync: vi.fn(() => true),
  statSync: vi.fn(() => ({ size: 100 * 1024 * 1024 })), renameSync: vi.fn(),
}));
vi.mock("node:fs", () => files);
vi.mock("node:os", () => ({ homedir: () => "/home/admin" }));

async function loadLogger() {
  return import("../internal-logger.js");
}

beforeEach(() => { vi.resetModules(); vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); files.appendFileSync.mockReset(); });

describe("internal API file log retention integration", () => {
  it("keeps writing the fixed log managed by logrotate instead of creating timestamp backups", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const { apiLog } = await loadLogger();
    apiLog("WRITE", "/runs/id", { status: 200 });
    apiLog("WRITE", "/runs/id", { status: 200 });
    expect(files.mkdirSync).toHaveBeenCalledWith("/home/admin/logs/clawweb", { recursive: true });
    expect(files.appendFileSync.mock.calls.map(call => call[0])).toEqual([
      "/home/admin/logs/clawweb/clawweb-internal-api.log", "/home/admin/logs/clawweb/clawweb-internal-api.log",
    ]);
    expect(files.renameSync).not.toHaveBeenCalled();
    expect(files.statSync).not.toHaveBeenCalled();
  });

  it("reports an unavailable destination once and never writes to the root-volume legacy path", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => { throw new Error("stderr unavailable"); });
    const { apiLog } = await loadLogger();
    files.appendFileSync.mockImplementation(() => { throw new Error("disk unavailable"); });
    apiLog("WRITE", "/runs/id", {}); apiLog("WRITE", "/runs/id", {});
    expect(error).toHaveBeenCalledTimes(1);
    expect(files.appendFileSync.mock.calls.every(call => call[0] === "/home/admin/logs/clawweb/clawweb-internal-api.log")).toBe(true);
  });
});

it.each([[undefined, "INFO"], [200, "INFO"], [401, "WARN"], [403, "WARN"], [404, "WARN"], [409, "WARN"], [429, "WARN"], [500, "ERROR"], [503, "ERROR"]])("labels internal API status %s", async (status, level) => {
  const { apiLog } = await loadLogger();
  apiLog("READ", "/runs/id", { status });
  expect(files.appendFileSync.mock.calls[0][1]).toContain(` ${level} [internal-api] {`);
});

it("isolates serialization failures from callers", async () => {
  const { apiLog, apiLogBody } = await loadLogger();
  vi.spyOn(console, "error").mockImplementation(() => {});
  expect(() => apiLog("WRITE", "/runs/id", { value: BigInt(1) })).not.toThrow();
  expect(() => apiLogBody("WRITE", "/runs/id", { value: BigInt(1) })).not.toThrow();
  expect(files.appendFileSync.mock.calls.at(-1)?.[1]).toContain("[unserializable body]");
});


describe("workflow API severity and exception diagnostics", () => {
  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  function lastEntry() {
    const line = files.appendFileSync.mock.calls.at(-1)![1] as string;
    return JSON.parse(line.slice(line.indexOf("{")));
  }

  it.each([401, 403, 404, 409, 429])("logs business HTTP %s only at WARN", async status => {
    const { apiLog } = await loadLogger();
    apiLog("READ", "/runs/id", { status, error: "Business rejection" });
    expect(console.warn).toHaveBeenCalledWith(`[internal-api] READ /runs/id ${status}`);
    expect(console.error).not.toHaveBeenCalled();
    expect(console.log).not.toHaveBeenCalled();
    expect(lastEntry()).toMatchObject({ status, error: "Business rejection" });
    expect(lastEntry()).not.toHaveProperty("stack");
  });

  it.each([
    [{ httpStatus: 403, status: 500 }, "WARN", "warn", 403],
    [{ httpStatus: 500, status: 200 }, "ERROR", "error", 500],
    [{ httpStatus: 201, status: "failed", runStatus: "failed" }, "INFO", "log", 201],
    [{ status: "failed" }, "INFO", "log", "failed"],
    [{}, "INFO", "log", "pending"],
  ] as const)("prefers httpStatus over domain status: %j", async (details, level, method, status) => {
    const { apiLog } = await loadLogger();
    apiLog("WRITE", "/runs", details);
    expect(vi.mocked(console[method]).mock.calls[0][0]).toBe(`[internal-api] WRITE /runs ${status}`);
    expect(files.appendFileSync.mock.calls[0][1]).toContain(` ${level} [internal-api] {`);
    expect(lastEntry()).toMatchObject(details);
  });

  it("preserves the caught Error identity, original stack and JSON error message", async () => {
    const { apiLog } = await loadLogger();
    function databaseWriteOrigin() { return new Error("Database write failed"); }
    const exception = databaseWriteOrigin();
    apiLog("WRITE", "/runs", { status: 500, error: exception.message }, exception);
    expect(console.error).toHaveBeenCalledWith("[internal-api] WRITE /runs 500", exception.stack);
    expect(lastEntry()).toMatchObject({ error: "Database write failed", stack: exception.stack, stackSource: "exception" });
    expect(lastEntry().stack).toContain("databaseWriteOrigin");
    expect(lastEntry().stack).not.toContain("internal-logger.ts");
  });

  it("labels a status-only system failure stack as an observation, never a caught exception", async () => {
    const { apiLog } = await loadLogger();
    apiLog("WRITE", "/runs", { status: 500, error: "Unconfirmed write" });
    expect(console.error).toHaveBeenCalledWith("[internal-api] WRITE /runs 500", expect.stringContaining("Workflow API failure observed"));
    expect(lastEntry()).toMatchObject({ error: "Unconfirmed write", stackSource: "observation" });
    expect(lastEntry().stack).toContain("Workflow API failure observed: Unconfirmed write");
  });

  it("keeps non-Error exceptions and explicitly labels their observation stack", async () => {
    const { apiLog } = await loadLogger();
    apiLog("WRITE", "/runs", { status: 500, error: "untyped failure" }, "untyped failure");
    expect(console.error).toHaveBeenCalledWith("[internal-api] WRITE /runs 500", expect.stringContaining("Workflow API failure observed: untyped failure"));
    expect(lastEntry()).toMatchObject({ error: "untyped failure", stackSource: "observation" });
    expect(lastEntry().stack).toContain("Workflow API failure observed: untyped failure");
  });

  it.each([undefined, "failure", null, { reason: "failure" }, new Error("original")])("includes a stack in every API ERROR for %j", async exception => {
    const { apiLog } = await loadLogger();
    apiLog("WRITE", "/runs/id", { status: 500, error: "failure" }, exception);
    expect(lastEntry().stack).toEqual(expect.stringContaining("Error:"));
    expect(vi.mocked(console.error).mock.calls[0].some(value => typeof value === "string" && value.includes("Error:"))).toBe(true);
  });

  it("labels an Error without its own stack as an observation without dropping that Error", async () => {
    const { apiLog } = await loadLogger();
    const error = new Error("no original stack");
    delete error.stack;
    apiLog("WRITE", "/runs/id", { status: 500, error: error.message }, error);
    expect(console.error).toHaveBeenCalledWith("[internal-api] WRITE /runs/id 500", expect.stringContaining("Workflow API failure observed: no original stack"));
    expect(lastEntry().stackSource).toBe("observation");
    expect(lastEntry().stack).toContain("Workflow API failure observed: no original stack");
  });

  it("logs only the stack/message whitelist of extended HTTP errors, never their private metadata", async () => {
    const { apiLog } = await loadLogger();
    const exception = Object.assign(new Error("upstream failed"), {
      config: { headers: { Authorization: "private-auth-marker", Cookie: "private-cookie-marker" } },
      token: "private-token-marker", cookies: ["private-cookie-marker"],
    });
    apiLog("WRITE", "/runs/id", { status: 500, error: exception.message }, exception);
    expect(console.error).toHaveBeenCalledWith("[internal-api] WRITE /runs/id 500", exception.stack);
    expect(lastEntry()).toMatchObject({ error: exception.message, stack: exception.stack, stackSource: "exception" });
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toMatch(/private-|Authorization|Cookie|config|token|cookies/);
    expect(files.appendFileSync.mock.calls[0][1]).not.toMatch(/private-|Authorization|Cookie|config|token|cookies/);
    expect(Object.keys(lastEntry()).sort()).toEqual(["error", "method", "path", "stack", "stackSource", "status", "ts"]);
  });

  it("hands off only the original message/stack/source through the optional response argument", async () => {
    const { apiLog } = await loadLogger();
    const exception = Object.assign(new Error("write failed"), { config: { token: "private-token-marker" } });
    const response = { locals: { unrelated: "preserved" }, body: { success: false } };
    apiLog("WRITE", "/runs/id", { httpStatus: 500, error: "Fixed response message" }, exception, response);
    expect(response.locals).toEqual({ unrelated: "preserved", taskGuardApiDiagnostic: {
      message: exception.message, stack: exception.stack, stackSource: "exception",
    } });
    expect(response.body).toEqual({ success: false });
    expect(lastEntry()).toMatchObject({ error: "Fixed response message", stack: exception.stack });
    expect(JSON.stringify(response.locals)).not.toContain("private-token-marker");
  });

  it.each([
    "/runs/id", "/node-executions/id", "/events", "/metrics", "/facades/id", "/scheduled-triggers/id",
    "/webhook-triggers/id", "/webhook-events/id", "/deploy-history", "/hallucination-checks", "/run-logs/id",
    "/alerts/id", "/bot-workflow-permissions/upsert", "/bot-workflow-permissions/list", "/flow-control/id",
    "/langfuse-ingestion", "/task-guard/status", "/workflow-repair-publication", "/node-step-traces/batch",
    "/execution-step-logs", "/http-callback-logs", "/retry-requests/claim", "/flow-run-links", "/run-reads/logs",
    "/reads/workflow-specs/id", "/reads/notification-configs", "/reads/facade-bindings", "/reads/http-callback-configs",
    "/reads/http-callback-logs", "/validate-bot/?check=1", "/reads/run-requests/", "/run-requests/failures?check=1",
    "/api/internal/runs/id", "/api/internal/reads/workflow-specs/id",
  ])("keeps the confirmed TaskGuard family at WARN: %s", async path => {
    const { apiLog } = await loadLogger();
    apiLog("READ", path, { httpStatus: 404, status: "failed" });
    expect(console.warn).toHaveBeenCalledWith(`[internal-api] READ ${path} 404`);
    expect(console.error).not.toHaveBeenCalled();
    expect(files.appendFileSync.mock.calls[0][1]).toContain(" WARN [internal-api] ");
  });

  it.each([
    "/reads/knowledge-bases", "/reads/validation-templates", "/api/internal/reads/knowledge-bases",
    "/api/internal/reads/validation-templates", "/reads/unknown", "/unknown", "/api/internal/unknown", "/api/internal",
    "/runs-other/id", "/validate-bot/unknown", "/reads/run-requests/unknown", "/run-requests/unknown",
    "/evolve", "/insight", "/bench", "/repair/v1/tasks",
  ])("preserves non-TaskGuard ERROR and its original JSON shape: %s", async path => {
    const { apiLog } = await loadLogger();
    const response = { locals: {} };
    apiLog("READ", path, { httpStatus: 404, error: "Not found" }, new Error("not found"), response);
    expect(console.error).toHaveBeenCalledWith(`[internal-api] READ ${path} 404`);
    expect(console.warn).not.toHaveBeenCalled();
    expect(files.appendFileSync.mock.calls[0][1]).toContain(" ERROR [internal-api] ");
    expect(lastEntry()).toEqual({ ts: expect.any(String), method: "READ", path, httpStatus: 404, error: "Not found" });
    expect(response.locals).toEqual({});
  });

  it("keeps the original log persistence failure and re-arms after recovery", async () => {
    const { apiLog } = await loadLogger();
    function logWriteOrigin() { return new Error("disk unavailable"); }
    const exception = logWriteOrigin();
    files.appendFileSync.mockImplementationOnce(() => { throw exception; });
    apiLog("WRITE", "/runs/id", {});
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining("Cannot write internal API log"), exception.stack);
    expect(exception.stack).toContain("logWriteOrigin");
    apiLog("WRITE", "/runs/id", {});
    files.appendFileSync.mockImplementationOnce(() => { throw exception; });
    apiLog("WRITE", "/runs/id", {});
    expect(console.error).toHaveBeenCalledTimes(2);
  });

  it("accepts workflow WARN without downgrading the shared HTTP policy", async () => {
    const { formatHttpLog, httpLogLevel } = await import("@avernet/clawweb-shared/server/services/file-log-format");
    for (const status of [400, 401, 403, 404, 409, 429, 500, 503]) expect(httpLogLevel(status)).toBe("ERROR");
    for (const status of [undefined, "failed", 200, 201, 302]) expect(httpLogLevel(status)).toBe("INFO");
    expect(formatHttpLog(new Date("2026-11-24T01:00:00Z"), "WARN", "internal-api", { status: 404 }))
      .toContain(' WARN [internal-api] {"status":404}');
  });
});
