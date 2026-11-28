// @vitest-environment node
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { format } from "node:util";

const files = vi.hoisted(() => ({ appendFile: vi.fn(), mkdir: vi.fn() }));
vi.mock("node:fs/promises", () => files);
vi.mock("node:os", () => ({ homedir: () => "/home/admin" }));
const logFile = "/home/admin/logs/clawweb/clawweb-internal-api.log";
const loadLogger = () => import("../internal-logger.js");
const records = () => files.appendFile.mock.calls.flatMap(call =>
  String(call[1]).trim().split("\n").filter(Boolean).map(line => JSON.parse(line.slice(line.indexOf("{")))),
);

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-27T00:00:00Z"));
  files.mkdir.mockReset().mockResolvedValue(undefined);
  files.appendFile.mockReset().mockResolvedValue(undefined);
  vi.spyOn(console, "log").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("internal API file log retention integration", () => {
  it("keeps writing the fixed log managed by logrotate instead of creating timestamp backups", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("WRITE", "/runs/id", { status: 200 });
    await flushInternalApiLog();
    apiLog("WRITE", "/runs/id", { status: 200 });
    await flushInternalApiLog();
    expect(files.mkdir).toHaveBeenCalledWith("/home/admin/logs/clawweb", { recursive: true });
    expect(files.appendFile.mock.calls.map(call => call[0])).toEqual([
      "/home/admin/logs/clawweb/clawweb-internal-api.log", "/home/admin/logs/clawweb/clawweb-internal-api.log",
    ]);
    expect(files.appendFile.mock.calls.every(call => call[0] === logFile)).toBe(true);
  });

  it("reports an unavailable destination once and never writes to the root-volume legacy path", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const error = vi.spyOn(console, "error").mockImplementation(() => { throw new Error("stderr unavailable"); });
    const { apiLog, flushInternalApiLog } = await loadLogger();
    files.appendFile.mockRejectedValue(new Error("disk unavailable"));
    apiLog("WRITE", "/runs/id", {}); apiLog("WRITE", "/runs/id", {});
    await flushInternalApiLog();
    expect(console.warn).toHaveBeenCalledTimes(1);
    expect(files.appendFile.mock.calls.every(call => call[0] === "/home/admin/logs/clawweb/clawweb-internal-api.log")).toBe(true);
  });
});

it.each([[undefined, "INFO"], [200, "INFO"], [401, "WARN"], [403, "WARN"], [404, "WARN"], [409, "WARN"], [429, "WARN"], [500, "ERROR"], [503, "ERROR"]])("labels internal API status %s", async (status, level) => {
  const { apiLog, flushInternalApiLog } = await loadLogger();
  apiLog("READ", "/runs/id", { status });
  await flushInternalApiLog();
  expect(String(files.appendFile.mock.calls[0][1])).toContain(` ${level} [internal-api] {`);
});

it("isolates serialization failures from callers", async () => {
  const { apiLog, apiLogBody, flushInternalApiLog } = await loadLogger();
  vi.spyOn(console, "error").mockImplementation(() => {});
  expect(() => apiLog("WRITE", "/runs/id", { value: BigInt(1) })).not.toThrow();
  expect(() => apiLogBody("WRITE", "/runs/id", { value: BigInt(1) })).not.toThrow();
  await flushInternalApiLog();
  expect(String(files.appendFile.mock.calls.at(-1)?.[1])).toContain("[bigint]");
});


describe("workflow API severity and exception diagnostics", () => {
  beforeEach(() => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  function lastEntry() {
    const line = String(files.appendFile.mock.calls.at(-1)![1]);
    return JSON.parse(line.slice(line.indexOf("{")));
  }

  it.each([401, 403, 404, 409, 429])("logs business HTTP %s only at WARN", async status => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("READ", "/runs/id", { status, error: "Business rejection" });
    await flushInternalApiLog();
    expect(console.warn).toHaveBeenCalledWith("%s", `[internal-api] READ /runs/id ${status}`);
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
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("WRITE", "/runs", details);
    await flushInternalApiLog();
    expect(vi.mocked(console[method]).mock.calls[0][0]).toBe(level === "ERROR" ? "%s %s" : "%s");
    expect(vi.mocked(console[method]).mock.calls[0][1]).toBe(`[internal-api] WRITE /runs ${status}`);
    expect(String(files.appendFile.mock.calls[0][1])).toContain(` ${level} [internal-api] {`);
    expect(lastEntry()).toMatchObject(details);
  });

  it.each([false, true])("renders path/status format tokens literally with stack=%s", async withStack => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    const path = "/runs/%s/%d/%j/%%";
    const status = "failed %s %d %j %%";
    const exception = new Error("upstream %s %d %j %%");
    exception.stack = "Error: upstream %s %d %j %%\r\n    at origin (%s:%d:%j:%%)\n\ttrailing stack bytes  \n";
    apiLog("WRITE", path, { status }, withStack ? exception : undefined);
    await flushInternalApiLog();
    const summary = `[internal-api] WRITE ${path} ${status}`;
    const args = vi.mocked(console.log).mock.calls[0];
    expect(args).toEqual(withStack ? ["%s %s", summary, exception.stack] : ["%s", summary]);
    expect(format(...args)).toBe(withStack ? `${summary} ${exception.stack}` : summary);
    expect(lastEntry()).toMatchObject({ method: "WRITE", path, status });
    if (withStack) expect(lastEntry()).toMatchObject({ stack: exception.stack, stackSource: "exception" });
    else expect(lastEntry()).not.toHaveProperty("stack");
    expect(console.warn).not.toHaveBeenCalled();
    expect(console.error).not.toHaveBeenCalled();
  });

  it("preserves the caught Error identity, original stack and JSON error message", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    function databaseWriteOrigin() { return new Error("Database write failed"); }
    const exception = databaseWriteOrigin();
    apiLog("WRITE", "/runs", { status: 500, error: exception.message }, exception);
    await flushInternalApiLog();
    expect(console.error).toHaveBeenCalledWith("%s %s", "[internal-api] WRITE /runs 500", exception.stack);
    expect(lastEntry()).toMatchObject({ error: "Database write failed", stack: exception.stack, stackSource: "exception" });
    expect(lastEntry().stack).toContain("databaseWriteOrigin");
    expect(lastEntry().stack).not.toContain("internal-logger.ts");
  });

  it("labels a status-only system failure stack as an observation, never a caught exception", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("WRITE", "/runs", { status: 500, error: "Unconfirmed write" });
    await flushInternalApiLog();
    expect(console.error).toHaveBeenCalledWith("%s %s", "[internal-api] WRITE /runs 500", expect.stringContaining("Workflow API failure observed"));
    expect(lastEntry()).toMatchObject({ error: "Unconfirmed write", stackSource: "observation" });
    expect(lastEntry().stack).toContain("Workflow API failure observed: Unconfirmed write");
  });

  it("keeps non-Error exceptions and explicitly labels their observation stack", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("WRITE", "/runs", { status: 500, error: "untyped failure" }, "untyped failure");
    await flushInternalApiLog();
    expect(console.error).toHaveBeenCalledWith("%s %s", "[internal-api] WRITE /runs 500", expect.stringContaining("Workflow API failure observed: untyped failure"));
    expect(lastEntry()).toMatchObject({ error: "untyped failure", stackSource: "observation" });
    expect(lastEntry().stack).toContain("Workflow API failure observed: untyped failure");
  });

  it.each([undefined, "failure", null, { reason: "failure" }, new Error("original")])("includes a stack in every API ERROR for %j", async exception => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("WRITE", "/runs/id", { status: 500, error: "failure" }, exception);
    await flushInternalApiLog();
    expect(lastEntry().stack).toEqual(expect.stringContaining("Error:"));
    expect(vi.mocked(console.error).mock.calls[0].some(value => typeof value === "string" && value.includes("Error:"))).toBe(true);
  });

  it("labels an Error without its own stack as an observation without dropping that Error", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    const error = new Error("no original stack");
    delete error.stack;
    apiLog("WRITE", "/runs/id", { status: 500, error: error.message }, error);
    await flushInternalApiLog();
    expect(console.error).toHaveBeenCalledWith("%s %s", "[internal-api] WRITE /runs/id 500", expect.stringContaining("Workflow API failure observed: no original stack"));
    expect(lastEntry().stackSource).toBe("observation");
    expect(lastEntry().stack).toContain("Workflow API failure observed: no original stack");
  });

  it("logs only the stack/message whitelist of extended HTTP errors, never their private metadata", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    const exception = Object.assign(new Error("upstream failed"), {
      config: { headers: { Authorization: "<private-auth-marker>", Cookie: "<private-cookie-marker>" } },
      token: "<private-token-marker>", cookies: ["<private-cookie-marker>"],
    });
    apiLog("WRITE", "/runs/id", { status: 500, error: exception.message }, exception);
    await flushInternalApiLog();
    expect(console.error).toHaveBeenCalledWith("%s %s", "[internal-api] WRITE /runs/id 500", exception.stack);
    expect(lastEntry()).toMatchObject({ error: exception.message, stack: exception.stack, stackSource: "exception" });
    expect(JSON.stringify(vi.mocked(console.error).mock.calls)).not.toMatch(/private-|Authorization|Cookie|config|token|cookies/);
    expect(String(files.appendFile.mock.calls[0][1])).not.toMatch(/private-|Authorization|Cookie|config|token|cookies/);
    expect(Object.keys(lastEntry()).sort()).toEqual(["error", "method", "path", "stack", "stackSource", "status", "ts"]);
  });

  it("hands off only the original message/stack/source through the optional response argument", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    const exception = Object.assign(new Error("write failed"), { config: { token: "<private-token-marker>" } });
    const response = { locals: { unrelated: "preserved" }, body: { success: false } };
    apiLog("WRITE", "/runs/id", { httpStatus: 500, error: "Fixed response message" }, exception, response);
    await flushInternalApiLog();
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
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("READ", path, { httpStatus: 404, status: "failed" });
    await flushInternalApiLog();
    expect(console.warn).toHaveBeenCalledWith("%s", `[internal-api] READ ${path} 404`);
    expect(console.error).not.toHaveBeenCalled();
    expect(String(files.appendFile.mock.calls[0][1])).toContain(" WARN [internal-api] ");
  });

  it.each([
    "/reads/knowledge-bases", "/reads/validation-templates", "/api/internal/reads/knowledge-bases",
    "/api/internal/reads/validation-templates", "/reads/unknown", "/unknown", "/api/internal/unknown", "/api/internal",
    "/runs-other/id", "/validate-bot/unknown", "/reads/run-requests/unknown", "/run-requests/unknown",
    "/evolve", "/insight", "/bench", "/repair/v1/tasks",
  ])("preserves non-TaskGuard ERROR and its original JSON shape: %s", async path => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    const response = { locals: {} };
    apiLog("READ", path, { httpStatus: 404, error: "Not found" }, new Error("not found"), response);
    await flushInternalApiLog();
    expect(console.error).toHaveBeenCalledWith("%s", `[internal-api] READ ${path} 404`);
    expect(console.warn).not.toHaveBeenCalled();
    expect(String(files.appendFile.mock.calls[0][1])).toContain(" ERROR [internal-api] ");
    expect(lastEntry()).toEqual({ ts: expect.any(String), method: "READ", path, httpStatus: 404, error: "Not found" });
    expect(response.locals).toEqual({});
  });

  it("bounds persistence failure warnings, recovers, and never discloses raw disk errors", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    files.appendFile.mockRejectedValue(new Error("disk unavailable token=private-marker"));
    for (let i = 0; i < 3; i++) {
      apiLog("WRITE", "/runs/id", {});
      await flushInternalApiLog();
    }
    expect(console.warn).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(vi.mocked(console.warn).mock.calls)).not.toContain("private-marker");
    files.appendFile.mockResolvedValue(undefined);
    apiLog("WRITE", "/runs/id", {}); await flushInternalApiLog();
    expect(files.appendFile.mock.calls.every(call => call[0] === logFile)).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("accepts workflow WARN without downgrading the shared HTTP policy", async () => {
    const { formatHttpLog, httpLogLevel } = await import("@avernet/clawweb-shared/server/services/file-log-format");
    for (const status of [400, 401, 403, 404, 409, 429, 500, 503]) expect(httpLogLevel(status)).toBe("ERROR");
    for (const status of [undefined, "failed", 200, 201, 302]) expect(httpLogLevel(status)).toBe("INFO");
    expect(formatHttpLog(new Date("2026-11-24T01:00:00Z"), "WARN", "internal-api", { status: 404 }))
      .toContain(' WARN [internal-api] {"status":404}');
  });
});

describe("internal API asynchronous logging", () => {
  it("batches every structured call to the fixed logrotate path without synchronous filesystem I/O", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    for (let i = 0; i < 50; i++) apiLog("WRITE", "/test", { status: 200, item: i });
    expect(files.mkdir).not.toHaveBeenCalled();
    expect(files.appendFile).not.toHaveBeenCalled();
    await flushInternalApiLog();
    expect(files.mkdir).toHaveBeenCalledExactlyOnceWith("/home/admin/logs/clawweb", { recursive: true });
    expect(files.appendFile).toHaveBeenCalledExactlyOnceWith(logFile, expect.any(Buffer));
    expect(records()).toHaveLength(50);
    expect(records().map(record => record.item)).toEqual(Array.from({ length: 50 }, (_, i) => i));
    expect(records()[0]).toMatchObject({ ts: "2026-11-27T00:00:00.000Z", method: "WRITE", path: "/test", status: 200 });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("limits one summary/second/severity and reports suppressed counts without dropping file entries", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    for (let i = 0; i < 20; i++) apiLog("READ", `/different-${i}`, { status: 200 });
    for (let i = 0; i < 10; i++) apiLog("WRITE", `/runs/failure-${i}`, { status: 503 });
    for (let i = 0; i < 5; i++) apiLog("WRITE", `/runs/warning-${i}`, { status: 409 });
    expect(console.log).toHaveBeenCalledTimes(1);
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(console.warn).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(999);
    apiLog("READ", "/too-soon", { status: 200 });
    expect(console.log).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    apiLog("READ", "/next-window", { status: 200 });
    apiLog("WRITE", "/runs/next-error", { status: 503 });
    apiLog("WRITE", "/runs/next-warning", { status: 409 });
    expect(vi.mocked(console.log).mock.calls[1][1]).toContain("suppressed=20");
    expect(vi.mocked(console.error).mock.calls[1][1]).toContain("suppressed=9");
    expect(vi.mocked(console.warn).mock.calls[1][1]).toContain("suppressed=4");
    await flushInternalApiLog();
    expect(records()).toHaveLength(39);
  });

  it("rate-limits arbitrary string statuses in a single normal bucket, not a dynamic map", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    for (let i = 0; i < 100; i++) apiLog("READ", "/status", { status: `value-${i}` });
    apiLog("READ", "/pending", {});
    expect(console.log).toHaveBeenCalledOnce();
    await flushInternalApiLog();
    expect(records()).toHaveLength(101);
  });

  it("reports append failures at a bounded rate, recovers, and never falls back to the root volume", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    files.appendFile.mockRejectedValue(new Error("disk unavailable"));
    apiLog("WRITE", "/test", {}); apiLog("WRITE", "/test", {});
    await flushInternalApiLog();
    apiLog("WRITE", "/test", {}); await flushInternalApiLog();
    expect(console.warn).toHaveBeenCalledOnce();
    expect(String(vi.mocked(console.warn).mock.calls[0][0])).toContain("writeFailures=1");
    files.appendFile.mockResolvedValue(undefined);
    apiLog("WRITE", "/recovered", { status: 200 }); await flushInternalApiLog();
    expect(files.appendFile.mock.calls.every(call => call[0] === logFile)).toBe(true);
    expect(files.mkdir).toHaveBeenCalledTimes(3);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not block calls during slow I/O and exposes concurrent flush settlement", async () => {
    let resolve!: () => void;
    const slow = new Promise<void>(done => { resolve = done; });
    files.appendFile.mockReturnValueOnce(slow);
    const { apiLog, flushInternalApiLog } = await loadLogger();
    apiLog("WRITE", "/first", {});
    const flushing = flushInternalApiLog();
    await vi.advanceTimersByTimeAsync(0);
    expect(files.appendFile).toHaveBeenCalledOnce();
    expect(() => apiLog("WRITE", "/while-slow", {})).not.toThrow();
    const all = flushInternalApiLog();
    expect(all).not.toBe(flushing);
    resolve();
    await all;
    await flushing;
    expect(records().map(record => record.path)).toEqual(["/first", "/while-slow"]);
  });

  it("still writes structured entries when stdout is broken", async () => {
    vi.mocked(console.log).mockImplementation(() => { throw new Error("stdout unavailable"); });
    const { apiLog, flushInternalApiLog } = await loadLogger();
    expect(() => apiLog("PUT", "/update", { status: 200 })).not.toThrow();
    await flushInternalApiLog();
    expect(records()).toHaveLength(1);
  });

  it("bounds deep/wide bodies before copying/stringifying and never calls getters or toJSON", async () => {
    const { apiLogBody, flushInternalApiLog } = await loadLogger();
    const getter = vi.fn(() => { throw new Error("must not read"); });
    const toJSON = vi.fn(() => { throw new Error("must not serialize"); });
    const body: Record<string, unknown> = { toJSON, password: "not-for-logs", value: "x ".repeat(100_000) };
    Object.defineProperty(body, "getter", { enumerable: true, get: getter });
    body.cycle = body;
    apiLogBody("WRITE", "/body", body, { status: 200, token: "secret-extra" });
    await flushInternalApiLog();
    const [entry] = records();
    expect(entry.bodyPreview.length).toBeLessThanOrEqual(2000);
    expect(JSON.stringify(entry)).not.toContain("not-for-logs");
    expect(JSON.stringify(entry)).not.toContain("secret-extra");
    expect(entry.status).toBe(200);
    expect(getter).not.toHaveBeenCalled();
    expect(toJSON).not.toHaveBeenCalled();
  });

  it("bounds oversized paths/details and serializes cycles and prototype-shaped fields safely", async () => {
    const { apiLog, flushInternalApiLog } = await loadLogger();
    const details = JSON.parse('{"__proto__":{"polluted":true},"diagnostic":"retained"}');
    details.self = details;
    details.bigint = BigInt(123);
    details.values = Array.from({ length: 100_000 }, (_, i) => ({ item: i }));
    apiLog("DELETE", "/" + "a ".repeat(100_000), details);
    await flushInternalApiLog();
    const [entry] = records();
    expect(entry.path.length).toBeLessThanOrEqual(1024);
    expect(entry.self).toBe("[Circular]");
    expect(entry.bigint).toBe("[bigint]");
    expect(entry.diagnostic).toBe("retained");
    expect(JSON.stringify(entry).length).toBeLessThan(10_000);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
});

const capabilityCases = [
  ["raw evidence", "/api/insight/v1/evidence-access/FAKE_CAPABILITY"],
  ["encoded evidence", "?next=%2Fapi%2Finsight%2Fv1%2Fevidence-access%2FFAKE_CAPABILITY"],
  ["double encoded evidence", "?next=%252Fapi%252Finsight%252Fv1%252Fevidence-access%252FFAKE_CAPABILITY"],
  ["mixed case evidence", "/api/insight/v1/EvIdEnCe-AcCeSs/FAKE_CAPABILITY"],
  ["raw signature", "?Signature=FAKE_SIGNATURE"],
  ["encoded signature", "%3FSignature%3DFAKE_SIGNATURE"],
  ["double encoded signature", "%253fSiGnAtUrE%253dFAKE_SIGNATURE"],
  ["encoded signature value", "%3fX-AmZ-SiGnAtUrE%3d%46%41%4b%45_SIGNATURE"],
  ["deeply encoded", "%25252525253FSignature%25252525253DFAKE_SIGNATURE"],
  ["clipped evidence", "/api/insight/v1/evidence-access/FAKE_CAPABILITY" + "x".repeat(30_000)],
  ["clipped signature", "%3FSignature%3DFAKE_SIGNATURE" + "x".repeat(30_000)],
  ["long diagnostic prefix", "safe ".repeat(300) + "%3FSignature%3DFAKE_SIGNATURE" + "x".repeat(30_000)],
] as const;

it.each(capabilityCases)("redacts %s in actual apiLog/apiLogBody console and file output", async (_label, capability) => {
  const { apiLog, apiLogBody, flushInternalApiLog } = await loadLogger();
  const path = "/runs?next=" + capability;
  const error = new Error("safe diagnostic");
  error.stack = "Error: capability " + capability;
  apiLog("WRITE", path, { status: 500, diagnostic: capability }, error);
  await vi.advanceTimersByTimeAsync(1000);
  apiLogBody("WRITE", path, { url: capability }, { status: 500 });
  await vi.advanceTimersByTimeAsync(1000);
  apiLogBody("WRITE", path, capability, { status: 500 });
  await flushInternalApiLog();
  const fileOutput = files.appendFile.mock.calls.map(call => String(call[1])).join("");
  const consoleOutput = [console.log, console.warn, console.error].flatMap(fn => vi.mocked(fn).mock.calls)
    .map(args => format(...args)).join("\n");
  expect(records()).toHaveLength(3);
  for (const output of [fileOutput, consoleOutput]) {
    expect(output).not.toMatch(/FAKE|%46%41%4b%45/i);
    expect(output).toMatch(/REDACTED|truncated/);
  }
});

// Exercise both public logger entry points, including direct calls with a concrete path.
it.each([["runs", "completion"], ["node-executions", "reconcile-stale-running"]])(
  "protects %s before path sampling, projection, preview or diagnostic capture", async (family, action) => {
    const { apiLog, apiLogBody, flushInternalApiLog } = await loadLogger();
    const path = `/api/internal/${family}/${"private-id".repeat(200)}/${action}/?key=private-query`;
    const read = vi.fn((): never => { throw new Error("unexpected inspection"); });
    const opaque = new Proxy({}, { ownKeys: read, get: read, getOwnPropertyDescriptor: read });
    const details = new Proxy({ httpStatus: 500, status: "private-status", error: opaque, headers: opaque,
      path: "private-path", response: opaque, body: opaque }, { ownKeys: read, get: read });
    const response = { locals: { unrelated: true } };
    apiLog("PUT", path, details, opaque, response);
    apiLogBody("PUT", path, opaque, { status: "private-status", headers: opaque });
    await flushInternalApiLog();
    expect(read).not.toHaveBeenCalled();
    const fixed = { ts: expect.any(String), method: "PUT", path: `/${family}/:flowId/${action}` };
    expect(records()).toEqual([{ ...fixed, status: 500, stackSource: "observation",
      stack: "Workflow closeout failure observed (exception withheld)" }, fixed]);
    expect(response.locals).toMatchObject({ unrelated: true, taskGuardApiDiagnostic: { stackSource: "observation" } });
    expect(JSON.stringify([response.locals, vi.mocked(console.error).mock.calls, vi.mocked(console.log).mock.calls])).not.toContain("private-");
  });

it.each([["READ", "/runs/id/completion"], ["PUT", "/runs/id/completion-info"],
  ["PUT", "/node-executions/id/completion"], ["PUT", "/runs/id/completion/other"]] as const)(
  "retains ordinary logging for adjacent %s %s", async (method, path) => {
    const { apiLogBody, flushInternalApiLog } = await loadLogger();
    apiLogBody(method, path, { fixture: "ok" });
    await flushInternalApiLog();
    expect(records()[0]).toMatchObject({ path, bodyPreview: '{"fixture":"ok"}' });
  });

it("preserves workflow WARN severity even if an ambiguous encoded query hides the whole path", async () => {
  const { apiLog, flushInternalApiLog } = await loadLogger();
  apiLog("READ", "/api/internal/runs/id?next=private%2526data", { httpStatus: 409 });
  await flushInternalApiLog();
  expect(records()[0]).toMatchObject({ path: "[REDACTED_ENCODED_TEXT]", httpStatus: 409 });
  expect(console.warn).toHaveBeenCalledOnce();
  expect(console.error).not.toHaveBeenCalled();
});
