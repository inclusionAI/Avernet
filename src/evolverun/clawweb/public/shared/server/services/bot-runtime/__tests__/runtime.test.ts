import { afterEach, expect, it, vi } from "vitest";
import { BotRuntimeClient, BaasRuntimeProvider, type ResolvedBotTarget } from "../index.js";
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
it("BaaS HTTP negotiates its connection and preserves an empty response", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ code: 0, data: { http_url: "https://proxy.example.test/proxypass/target/api/test", token: "test-token" } }))).mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  const provider = new BaasRuntimeProvider({ commandTenant: "test", iamtoken: "test-iam", environments: { pre: { baseUrl: "https://baas.example.test", apiKey: "test-key" } } } as any);
  expect(await provider.request({ target: { ...target, provider: "baas" }, method: "POST", path: "/api/test", body: { value: 1 } })).toEqual({ status: 204, body: "" });
  expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/bots/device/http-info?");
  expect(fetchMock.mock.calls[1][1].headers["x-proxypass-token"]).toBe("test-token");
  expect(fetchMock.mock.calls[1][1].headers.Authorization).toBeUndefined();
});