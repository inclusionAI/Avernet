import { createServer } from "node:http";
import { afterEach, expect, it, vi } from "vitest";
import { HttpRecoveryResultReporter } from "../result-reporter.js";
import type { RecoveryResult } from "../contracts.js";
const result: RecoveryResult = { event_id: "evt-test", delivery: { delivery_key: "key", status: "accepted", result_text: "accepted", result_payload: { provider_message_id: "message" }, started_at_ms: 1, finished_at_ms: 2, error: null } };
afterEach(() => vi.unstubAllGlobals());
it("posts the exact v2 result to the monitoring route without an independent token", async () => {
  const requests: { url: string | undefined; authorization: string | undefined; body: unknown }[] = [];
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
    requests.push({ url: request.url, authorization: request.headers.authorization, body: JSON.parse(Buffer.concat(chunks).toString()) });
    response.setHeader("Content-Type", "application/json"); response.end(JSON.stringify({ accepted: true, eventId: result.event_id }));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const reporter = new HttpRecoveryResultReporter({ baseUrl: "http://127.0.0.1:" + (server.address() as { port: number }).port });
    await reporter.report(result);
    expect(requests).toEqual([{ url: "/api/insight/v1/internal/monitoring/session-recovery-results", authorization: undefined, body: result }]);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
it.each([404, 409, 500])("preserves callback failure (%s) without retry or response-body disclosure", async status => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("private upstream details", { status })); vi.stubGlobal("fetch", fetchMock);
  const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test" });
  await expect(reporter.report(result)).rejects.toMatchObject({ status: 503, code: "recovery_result_unavailable", logFields: { httpStatus: status, failureKind: "http" } });
  expect(fetchMock).toHaveBeenCalledTimes(1); expect(fetchMock.mock.lastCall![1].redirect).toBe("error");
});
it("turns network failure into a sanitized callback error and does not retry", async () => {
  const fetchMock = vi.fn().mockRejectedValue(Error("IAM_TOKEN=private")); vi.stubGlobal("fetch", fetchMock);
  const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test" });
  await expect(reporter.report(result)).rejects.toMatchObject({ status: 503 }); expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("rejects arbitrary callback paths and insecure origins", () => {
  for (const baseUrl of ["http://remote.example.test", "https://monitor.example.test/other", "https://user:pass@monitor.example.test", "https://monitor.example.test#fragment"]) {
    expect(() => new HttpRecoveryResultReporter({ baseUrl })).toThrow();
  }
});

it("preserves the safe monitoring code and HTTP status without logging provider content", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "MONITORING_RESULT_TARGET_NOT_FOUND", message: "PRIVATE_BODY" } }), { status: 404 })));
  const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test" });
  const error = await reporter.report(result).catch(error => error);
  expect(error.logFields).toEqual({ errorCode: "RECOVERY_RESULT_HTTP_REJECTED", failureKind: "http", httpStatus: 404,
    upstreamCode: "MONITORING_RESULT_TARGET_NOT_FOUND", reason: "结果回传被 HTTP 服务拒绝" });
  expect(JSON.stringify(error)).not.toContain("PRIVATE_BODY");
});
it.each([
  [new DOMException("PRIVATE_TIMEOUT", "TimeoutError"), "timeout", undefined],
  [new TypeError("PRIVATE_NETWORK", { cause: Object.assign(new Error("PRIVATE_CAUSE"), { code: "ECONNRESET" }) }), "network", "ECONNRESET"],
])("distinguishes transport failures without disclosing raw exceptions", async (failure, failureKind, transportCode) => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(failure));
  const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test" });
  const error = await reporter.report(result).catch(error => error);
  expect(error.logFields).toMatchObject({ failureKind, ...(transportCode ? { transportCode } : {}) });
  expect(JSON.stringify(error)).not.toContain("PRIVATE_");
});
it.each(["<html>login required</html>", '{"accepted":false}', '{"accepted":true,"eventId":"different"}', "x".repeat(10000)])(
  "rejects unconfirmed 2xx responses instead of reporting success", async body => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body)));
    const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test" });
    await expect(reporter.report(result)).rejects.toMatchObject({ logFields: { failureKind: "invalid_ack", httpStatus: 200 } });
  });
it("accepts a duplicate acknowledgment only for the exact task and result version", async () => {
  const taskResult = { ...result, task_id: "task", result_version: 7 } as import("../task-contracts.js").RecoveryTaskResult;
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true, eventId: result.event_id, taskId: "task", resultVersion: 7, duplicate: true })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ accepted: true, eventId: result.event_id, taskId: "task", resultVersion: 6 })));
  vi.stubGlobal("fetch", fetchMock);
  const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test" });
  await expect(reporter.report(taskResult)).resolves.toBeUndefined();
  await expect(reporter.report(taskResult)).rejects.toMatchObject({ logFields: { failureKind: "invalid_ack" } });
});

it("retains an already received HTTP status if the response body times out", async () => {
  const server = createServer((_request, response) => { response.writeHead(404, { "Content-Type": "application/json" }); response.write('{"error":'); });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const reporter = new HttpRecoveryResultReporter({ baseUrl: "http://127.0.0.1:" + (server.address() as { port: number }).port, timeoutMs: 1000 });
    await expect(reporter.report(result)).rejects.toMatchObject({ logFields: { failureKind: "timeout", httpStatus: 404 } });
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
