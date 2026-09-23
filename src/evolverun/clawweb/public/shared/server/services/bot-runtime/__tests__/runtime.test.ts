import { createServer } from "node:http";
import { mkdtemp, mkdir, writeFile, appendFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { WebSocketServer } from "ws";
import { afterEach, expect, it, vi } from "vitest";
import { BotRuntimeClient, ArcaRuntimeProvider, BaasRuntimeProvider, LocalRuntimeProvider, type ResolvedBotTarget } from "../index.js";
import { DirectArcaConnectionProvider } from "../internal/arca-command-transport.js";
const target: ResolvedBotTarget = { environment: "pre", ownerId: "owner", botId: "default", provider: "arca", bindingId: "12", deviceId: "device", sandboxId: "sandbox", arcaInstanceId: "sandbox@0" };
const message = { target, sessionKey: "agent:main:test", deliveryKey: "key", message: "Please continue" };
afterEach(() => vi.unstubAllGlobals());
it("routes only to the selected provider and rejects a resolver identity mismatch", async () => {
  const arca = { executeShell: vi.fn(), request: vi.fn(), sendMessage: vi.fn() };
  const baas = { executeShell: vi.fn(), request: vi.fn(), sendMessage: vi.fn() };
  const resolve = vi.fn().mockResolvedValue(target);
  const runtime = new BotRuntimeClient({ targets: { resolve }, providers: { arca, baas } });
  const identity = { environment: target.environment, ownerId: target.ownerId, botId: target.botId };
  await runtime.sendMessage({ ...message, target: identity });
  expect(arca.sendMessage).toHaveBeenCalledTimes(1);
  expect(baas.sendMessage).not.toHaveBeenCalled();
  resolve.mockResolvedValue({ ...target, ownerId: "other" });
  await expect(runtime.sendMessage({ ...message, target: identity })).rejects.toMatchObject({ status: 409 });
  expect(arca.sendMessage).toHaveBeenCalledTimes(1);
  await expect(runtime.executeShell({ target: { ...target, provider: "__proto__" }, command: "true" })).rejects.toMatchObject({ code: "unsupported_runtime_provider" });
});
it("isolates stable message identities by owner, environment and session, without retrying", async () => {
  const sendMessage = vi.fn().mockResolvedValue({ status: "unknown" });
  const runtime = new BotRuntimeClient({ providers: { arca: { sendMessage, executeShell: vi.fn(), request: vi.fn() } } });
  await runtime.sendMessage(message); await runtime.sendMessage(message);
  expect(sendMessage.mock.calls[0][0].deliveryId).toBe(sendMessage.mock.calls[1][0].deliveryId);
  for (const input of [{ ...message, sessionKey: "agent:main:other" }, { ...message, target: { ...target, ownerId: "other" } }, { ...message, target: { ...target, environment: "prod" as const } }]) await runtime.sendMessage(input);
  expect(new Set(sendMessage.mock.calls.map(([input]) => input.deliveryId)).size).toBe(4);
  expect(sendMessage).toHaveBeenCalledTimes(5);
});
it("rejects invalid timeout, HTTP target and incompatible Engine before provider calls", async () => {
  const provider = { executeShell: vi.fn(), request: vi.fn(), sendMessage: vi.fn() };
  const runtime = new BotRuntimeClient({ providers: { arca: provider } });
  await expect(runtime.executeShell({ target, command: "true", timeoutMs: -1 })).rejects.toMatchObject({ status: 422 });
  for (const path of ["//outside.test/", "/\\outside.test", "/test\nheader"]) await expect(runtime.request({ target, method: "GET", path })).rejects.toMatchObject({ status: 422 });
  await expect(runtime.sendMessage({ ...message, target: { ...target, activeEngine: "other" } })).rejects.toMatchObject({ code: "unsupported_engine" });
  await expect(runtime.sendMessage({ ...message, target: { ...target, activeEngine: "" } })).rejects.toMatchObject({ code: "unsupported_engine" });
  expect(provider.executeShell).not.toHaveBeenCalled(); expect(provider.request).not.toHaveBeenCalled(); expect(provider.sendMessage).not.toHaveBeenCalled();
});
it("legacy Arca HTTP signs the requested port and never uses BaaS", async () => {
  const connection = new DirectArcaConnectionProvider({ getSecretValue: async () => "test-secret" });
  const fetchMock = vi.fn().mockResolvedValue(new Response("not found", { status: 404 })); vi.stubGlobal("fetch", fetchMock);
  const runtime = new BotRuntimeClient({ providers: { arca: new ArcaRuntimeProvider({ connectionProvider: connection, proxyBaseUrls: { pre: "https://proxy.example.test" } }) } });
  expect(await runtime.request({ target, port: 12345, path: "/health", method: "GET" })).toEqual({ status: 404, body: "not found" });
  const [url, options] = fetchMock.mock.calls[0];
  expect(url).toBe("https://proxy.example.test/proxypass/ARCA_sandbox@0:12345/health");
  const payload = JSON.parse(Buffer.from(options.headers["x-proxypass-token"].split(".")[1], "base64url").toString());
  expect(payload.target).toBe("ARCA_sandbox@0:12345"); expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("legacy Arca refuses a changed instance before sending HTTP", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  const provider = new ArcaRuntimeProvider({ connectionProvider: { getConnection: async () => ({ target: "ARCA_other:20003", token: "a.b.c" }) }, proxyBaseUrls: { pre: "https://proxy.example.test" } });
  await expect(provider.request({ target, method: "POST", path: "/api/example" })).rejects.toMatchObject({ status: 409 });
  expect(fetchMock).not.toHaveBeenCalled();
});
it("BaaS HTTP negotiates its connection and preserves an empty response", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, data: { http_url: "https://proxy.example.test/proxypass/target/api/test", token: "test-token" } }))).mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  const provider = new BaasRuntimeProvider({ commandTenant: "test", iamtoken: "test-iam", environments: { pre: { baseUrl: "https://baas.example.test", apiKey: "test-key" } } } as any);
  expect(await provider.request({ target: { ...target, provider: "baas" }, method: "POST", path: "/api/test", body: { value: 1 } })).toEqual({ status: 204, body: "" });
  expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/bots/device/http-info?");
  expect(fetchMock.mock.calls[1][1].headers["x-proxypass-token"]).toBe("test-token");
  expect(fetchMock.mock.calls[1][1].headers.Authorization).toBeUndefined();
});
it("local provider actually executes shell, calls HTTP and sends through Engine with transcript evidence", async () => {
  const home = await mkdtemp(join(tmpdir(), "bot-runtime-"));
  const stateDirectory = join(home, "state"), directory = join(stateDirectory, "agents/main/sessions");
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, "sessions.json"), JSON.stringify({ "agent:main:test": { sessionId: "original" } }));
  await writeFile(join(directory, "original.jsonl"), "{}\n");
  const http = createServer((request, response) => { response.writeHead(200, { "Content-Type": "application/json" }); response.end(JSON.stringify({ path: request.url })); });
  const ws = new WebSocketServer({ server: http });
  let sends = 0;
  ws.on("connection", socket => socket.on("message", async raw => {
    const frame = JSON.parse(String(raw));
    if (frame.method === "chat.send") { sends++; await appendFile(join(directory, "original.jsonl"), JSON.stringify({ type: "message", id: "message-1", timestamp: "2026-09-24T00:00:00Z", message: { role: "user", content: [{ type: "text", text: frame.params.message }] } }) + "\n"); }
    socket.send(JSON.stringify({ type: "res", id: frame.id, ok: true, payload: frame.method === "chat.send" ? { accepted: true, recovery: "process_local_v1", runId: "run-1" } : {} }));
  }));
  await new Promise<void>(resolve => http.listen(0, "127.0.0.1", resolve));
  const engineOrigin = "http://127.0.0.1:" + (http.address() as { port: number }).port;
  const local = new LocalRuntimeProvider(async () => ({ cwd: home, home, stateDirectory, engineOrigin, engineIdentity: "test-identity" }));
  const runtime = new BotRuntimeClient({ providers: { local } });
  const localTarget = { ...target, environment: "dev" as const, provider: "local" };
  try {
    const result = await runtime.executeShell({ target: localTarget, command: 'printf "%s" "$OPENCLAW_STATE_DIR"' });
    expect(result).toMatchObject({ status: "success", exitCode: 0, stdout: stateDirectory });
    expect(await runtime.executeShell({ target: localTarget, command: "sleep 5", timeoutMs: 50 })).toMatchObject({ status: "unknown" });
    expect(await runtime.request({ target: localTarget, path: "/health", method: "GET" })).toEqual({ status: 200, body: { path: "/health" } });
    const receipt = await runtime.sendMessage({ ...message, target: localTarget });
    expect(receipt).toMatchObject({ status: "accepted", messageId: "message-1", sessionId: "original", locationStatus: "confirmed" });
    expect(await runtime.sendMessage({ ...message, target: localTarget })).toEqual(receipt); expect(sends).toBe(1);
  } finally {
    for (const client of ws.clients) client.terminate();
    await new Promise<void>(resolve => ws.close(() => resolve()));
    http.closeAllConnections(); await new Promise<void>(resolve => http.close(() => resolve())); await rm(home, { recursive: true, force: true });
  }
}, 20000);

it("Arca service success codes are opaque and do not override the successful shell result", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ success: true, code: "13-200000", data: {
    status: "completed", exit_code: 0, outputs: [{ output_type: "stdout", text: "raw command output" }] } })));
  vi.stubGlobal("fetch", fetchMock);
  const provider = new ArcaRuntimeProvider({ connectionProvider: new DirectArcaConnectionProvider({ getSecretValue: async () => "test-key" }), proxyBaseUrls: { pre: "https://proxy.example.test" } });
  expect(await provider.executeShell({ target, command: "true" })).toMatchObject({ status: "success", exitCode: 0, stdout: "raw command output" });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
