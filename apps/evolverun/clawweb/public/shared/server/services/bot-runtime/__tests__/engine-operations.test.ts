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
  expect(await tool.execute(target, { method: "POST", path: "/api/v1/file/remove", body: { target_path: "/workspace/obsolete.txt" } }, true)).toMatchObject({ status: "unknown" });
  expect(await tool.execute(target, { method: "POST", path: "/api/v1/file/read", body: { file_path: "/workspace/file" } }, false)).toMatchObject({ status: "failed" });
  expect(request).toHaveBeenCalledTimes(2);
});
it("rejects TE Shell before contacting the provider", async () => {
  const executeShell = vi.fn();
  const runtime = new BotRuntimeClient({ providers: { teclaw: { executeShell, request: vi.fn(), sendMessage: vi.fn() } } });
  await expect(runtime.executeShell({ target, command: "cat file" })).rejects.toMatchObject({ code: "unsupported_engine_shell" });
  expect(executeShell).not.toHaveBeenCalled();
});
it.each([
  ["read", { path: "/workspace/file" }],
  ["list", { path: "/workspace" }],
  ["read", { file_path: "/home/admin/.teclaw/workspace/file" }],
  ["list", { dir_path: "/tmp/ais-analysis" }],
  ["read", { file_path: "/workspace-other/file" }],
])("rejects an invalid TE file %s contract before dispatch", async (operation, body) => {
  const request = vi.fn();
  await expect(new BotEngineOperations({ request }).execute(target,
    { method: "POST", path: `/api/v1/file/${operation}`, body }, false)).rejects.toMatchObject({
      status: 422, code: "invalid_engine_file_read",
    });
  expect(request).not.toHaveBeenCalled();
});
it.each(["/workspace/file", "/identity/SOUL.md", "/config/teclaw.json"])("preserves the supported TE logical path %s", async file_path => {
  const request = vi.fn().mockResolvedValue({ status: 200, body: "contents" });
  const operation = { method: "POST" as const, path: "/api/v1/file/read", body: { file_path } };
  await new BotEngineOperations({ request }).execute(target, operation, false);
  expect(request).toHaveBeenCalledWith(expect.objectContaining(operation));
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

it.each(["openclaw", "claude_code"])("does not advertise or dispatch unimplemented MCP lifecycle for %s", async activeEngine => {
  expect(BotEngineOperations.capabilities(activeEngine).writes.join(" ")).not.toMatch(/mcp\/\{code\}\/start/);
  const request = vi.fn();
  await expect(new BotEngineOperations({ request }).execute({ ...target, activeEngine }, { method: "POST", path: "/api/mcp/server/start" }, true)).rejects.toMatchObject({ status: 403 });
  expect(request).not.toHaveBeenCalled();
});
it("preserves bounded cron query parameters and uses the TE enable endpoint", async () => {
  const request = vi.fn().mockResolvedValue({ status: 200, body: { success: true } });
  const tool = new BotEngineOperations({ request });
  await tool.execute(target, { method: "GET", path: "/api/v1/bot/cron/job/runs", query: { limit: 40 } }, false);
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ path: "/api/v1/bot/cron/job/runs?limit=40" }));
  await tool.execute(target, { method: "POST", path: "/api/v1/bot/cron/job/run", query: { force: true } }, true);
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ path: "/api/v1/bot/cron/job/run?force=true" }));
  await tool.execute(target, { method: "POST", path: "/api/v1/bot/cron/job/disable" }, true);
  await expect(tool.execute(target, { method: "PUT", path: "/api/v1/bot/cron/job", body: { enabled: false } }, true)).rejects.toMatchObject({ code: "invalid_cron_body" });
  await expect(tool.execute(target, { method: "POST", path: "/api/v1/bot/cron", body: { schedule: { kind: "cron" }, payload: {} } }, true)).rejects.toMatchObject({ code: "invalid_cron_body" });
  await expect(tool.execute(target, { method: "GET", path: "/api/v1/file/read", query: { limit: 40 } }, false)).rejects.toThrow();
});

it.each(["openclaw", "claude_code", "teclaw"])("keeps MCP/Skill reads and Cron writes while denying managed mutations for %s", async activeEngine => {
  const request = vi.fn().mockResolvedValue({ status: 200, body: { success: true } });
  const tool = new BotEngineOperations({ request }), te = activeEngine === "teclaw";
  const prefix = te ? "/api/v1" : "/api";
  const current = { ...target, activeEngine };
  expect(BotEngineOperations.capabilities(activeEngine).writes.join(" ")).not.toMatch(/mcp|skills|skill-package|bot\/apply/);
  for (const file_path of ["/workspace/skills/review/SKILL.md", te ? "/config/teclaw.json" : "/home/admin/.mcporter/mcporter.json"]) {
    await expect(tool.execute(current, { method: "POST", path: prefix + "/file/read", body: { file_path } }, false)).resolves.toMatchObject({ status: "success" });
  }
  await tool.execute(current, { method: "POST", path: prefix + "/file/list", body: { dir_path: "/workspace/skills" } }, false);
  if (!te) {
    await tool.execute(current, { method: "GET", path: "/api/mcp" }, false);
    await tool.execute(current, { method: "GET", path: "/api/mcp/server" }, false);
  }
  const cron = prefix + (te ? "/bot" : "") + "/cron";
  for (const operation of [
    { method: "GET", path: cron }, { method: "POST", path: cron, body: { name: "daily", schedule: "0 8 * * *", command: "report" } },
    { method: "PUT", path: cron + "/job", body: te ? { name: "updated" } : { enabled: false } },
    { method: "DELETE", path: cron + "/job" }, { method: "POST", path: cron + "/job/run" },
  ] as const) await tool.execute(current, operation, operation.method !== "GET");
  request.mockClear();
  for (const operation of [
    { method: "POST", path: "/api/mcp" }, { method: "PUT", path: "/api/mcp/server" }, { method: "DELETE", path: "/api/mcp/server" },
    { method: "POST", path: "/api/skills/local/apply" }, { method: "POST", path: "/api/skills/mappings/apply" },
    { method: "POST", path: "/api/v1/file/skill-package" }, { method: "POST", path: "/api/v1/bot/apply" },
    { method: "GET", path: "/api/engine/capabilities" },
  ] as const) await expect(tool.execute(current, operation, operation.method !== "GET")).rejects.toMatchObject({ status: 403 });
  for (const target_path of ["/workspace/skills/review/SKILL.md", "/home/admin/.mcporter/mcporter.json", "/config/teclaw.json", "/home/admin/.openclaw/cron/jobs.json"]) {
    await expect(tool.execute(current, { method: "POST", path: prefix + "/file/remove", body: { target_path } }, true)).rejects.toMatchObject({ code: "managed_runtime_file" });
    await expect(tool.execute(current, { method: "POST", path: prefix + "/file/upload", upload: { targetPath: target_path, content: "replacement" } }, true)).rejects.toMatchObject({ code: "managed_runtime_file" });
  }
  expect(request).not.toHaveBeenCalled();
  await expect(tool.execute(current, { method: "POST", path: prefix + "/file/upload", upload: { targetPath: "/workspace/memory.md", content: "remember" } }, true)).resolves.toMatchObject({ status: "success" });
});
