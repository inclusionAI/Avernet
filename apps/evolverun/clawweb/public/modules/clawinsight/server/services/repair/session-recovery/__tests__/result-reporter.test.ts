import { createServer } from "node:http";
import { afterEach, expect, it, vi } from "vitest";
import { HttpRecoveryResultReporter } from "../result-reporter.js";
import type { RecoveryResult } from "../contracts.js";
const result: RecoveryResult = { event_id: "evt-test", delivery: { delivery_key: "key", status: "accepted", result_text: "accepted", result_payload: { provider_message_id: "message" }, started_at_ms: 1, finished_at_ms: 2, error: null } };
afterEach(() => vi.unstubAllGlobals());
it("posts the exact v2 result to the monitoring route", async () => {
  const requests: { url: string | undefined; authorization: string | undefined; body: unknown }[] = [];
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = []; for await (const chunk of request) chunks.push(Buffer.from(chunk));
    requests.push({ url: request.url, authorization: request.headers.authorization, body: JSON.parse(Buffer.concat(chunks).toString()) });
    response.writeHead(204); response.end();
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const reporter = new HttpRecoveryResultReporter({ baseUrl: "http://127.0.0.1:" + (server.address() as { port: number }).port, token: "test-token" });
    await reporter.report(result);
    expect(requests).toEqual([{ url: "/api/insight/v1/internal/monitoring/session-recovery-results", authorization: "Bearer test-token", body: result }]);
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
it.each([401, 409, 500])("preserves callback failure (%s) without retry or response-body disclosure", async status => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("private upstream details", { status })); vi.stubGlobal("fetch", fetchMock);
  const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test", token: "test-token" });
  await expect(reporter.report(result)).rejects.toMatchObject({ status: 503, code: "recovery_result_unavailable" });
  expect(fetchMock).toHaveBeenCalledTimes(1); expect(fetchMock.mock.lastCall![1].redirect).toBe("error");
});
it("turns network failure into a sanitized callback error and does not retry", async () => {
  const fetchMock = vi.fn().mockRejectedValue(Error("IAM_TOKEN=private")); vi.stubGlobal("fetch", fetchMock);
  const reporter = new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test", token: "test-token" });
  await expect(reporter.report(result)).rejects.toMatchObject({ status: 503 }); expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("rejects arbitrary callback paths, insecure origins and missing credentials", () => {
  for (const baseUrl of ["http://remote.example.test", "https://monitor.example.test/other", "https://user:pass@monitor.example.test", "https://monitor.example.test#fragment"]) {
    expect(() => new HttpRecoveryResultReporter({ baseUrl, token: "test-token" })).toThrow();
  }
  expect(() => new HttpRecoveryResultReporter({ baseUrl: "https://monitor.example.test", token: "" })).toThrow();
});
