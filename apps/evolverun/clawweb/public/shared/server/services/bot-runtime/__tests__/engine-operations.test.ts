import { expect, it, vi } from "vitest";
import { BotEngineOperations } from "../engine-operations.js";
import { BotRuntimeClient } from "../bot-runtime-client.js";
import { runtimeHttpBody } from "../internal/http-body.js";
const target = { environment: "prod" as const, ownerId: "owner", botId: "bot", provider: "teclaw", bindingId: "1", deviceId: "device", activeEngine: "teclaw" };
it("uses the frozen BaaS target for POST file reads without granting writes", async () => {
  const request = vi.fn().mockResolvedValue({ status: 200, body: "contents" });
  const tool = new BotEngineOperations({ request });
  expect(await tool.execute(target, { method: "POST", path: "/api/v1/file/read", body: { file_path: "/config/teclaw.json" } }, false)).toMatchObject({ status: "success" });
  expect(request.mock.calls[0][0].target).toBe(target);
  await expect(tool.execute(target, { method: "POST", path: "/api/v1/file/remove", body: { target_path: "/workspace/file" } }, false)).rejects.toMatchObject({ status: 403 });
  expect(request).toHaveBeenCalledTimes(1);
});
it.each(["/api/engine/restart", "/api/v1/file/../bot/apply", "/api/v1/bot/apply?bot_id=other", "https://other/api/file/read"])("rejects unsupported TE path %s", async path => {
  const request = vi.fn();
  await expect(new BotEngineOperations({ request }).execute(target, { method: "POST", path }, true)).rejects.toThrow();
  expect(request).not.toHaveBeenCalled();
});
it("reports unknown writes and explicit business failures without retrying", async () => {
  const request = vi.fn().mockResolvedValueOnce({ status: 504, body: "timeout" }).mockResolvedValueOnce({ status: 200, body: { success: false } });
  const tool = new BotEngineOperations({ request });
  expect(await tool.execute(target, { method: "POST", path: "/api/v1/file/remove" }, true)).toMatchObject({ status: "unknown" });
  expect(await tool.execute(target, { method: "POST", path: "/api/v1/file/read" }, false)).toMatchObject({ status: "failed" });
  expect(request).toHaveBeenCalledTimes(2);
});
it("rejects TE Shell before contacting the provider", async () => {
  const executeShell = vi.fn();
  const runtime = new BotRuntimeClient({ providers: { teclaw: { executeShell, request: vi.fn(), sendMessage: vi.fn() } } });
  await expect(runtime.executeShell({ target, command: "cat file" })).rejects.toMatchObject({ code: "unsupported_engine_shell" });
  expect(executeShell).not.toHaveBeenCalled();
});
it("uses existing multipart file and target_path contract without JSON content-type", async () => {
  const headers = { "Content-Type": "application/json", "x-proxypass-token": "opaque" };
  const form = runtimeHttpBody({ target, method: "POST", path: "/api/v1/file/upload", upload: { targetPath: "/workspace/memory.md", content: "content" } }, headers) as FormData;
  expect(form.get("target_path")).toBe("/workspace/memory.md");
  expect(await (form.get("file") as File).text()).toBe("content");
  expect(headers).toEqual({ "x-proxypass-token": "opaque" });
});
it.each([['POST', '/api/mcp/call-tool'], ['DELETE', '/api/mcp/server/start'], ['PUT', '/api/cron/id/run']])("rejects undeclared write method/path %s %s", async (method, path) => {
  const request = vi.fn();
  await expect(new BotEngineOperations({ request }).execute({ ...target, activeEngine: "openclaw" }, { method: method as 'POST', path }, true)).rejects.toThrow();
  expect(request).not.toHaveBeenCalled();
});
