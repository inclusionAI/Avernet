import { expect, it, vi } from "vitest";
import { RecoverySessionReader } from "../session-reader.js";
const target = { environment: "prod" as const, ownerId: "owner", botId: "bot", provider: "teclaw", bindingId: "1", deviceId: "device", activeEngine: "teclaw" };
it("reads TE with the existing unpadded URL-safe session codec and never uses Shell", async () => {
  const key = "agent:main:default:original:user:owner", marker = "[会话自愈：send-1]\n";
  const request = vi.fn().mockResolvedValue({ status: 200, body: { success: true, data: [
    { id: "quoted", role: "assistant", content: marker + "引用" }, { id: "actual", role: "user", content: marker + "继续" },
    { id: "reply", role: "assistant", content: "原动作成功" },
  ] } });
  const executeShell = vi.fn();
  const result = await new RecoverySessionReader({ request, executeShell }).read({ target, sessionKey: key, deliveryKey: "send-1", offset: 4 });
  expect(result).toMatchObject({ delivery_confirmed: true, delivery: { message_id: "actual", offset: 5 } });
  expect(request.mock.calls[0][0].path).toBe(`/api/sessions/${Buffer.from(key).toString("base64url")}/messages?engine=teclaw&limit=100&offset=4`);
  expect(executeShell).not.toHaveBeenCalled();
});
it("does not confirm delivery from a quoted marker or infer failure from missing messages", async () => {
  const request = vi.fn().mockResolvedValue({ status: 200, body: { success: true, data: [{ role: "assistant", content: "[会话自愈：send-1]\n引用" }] } });
  const result = await new RecoverySessionReader({ request, executeShell: vi.fn() }).read({ target, sessionKey: "original", deliveryKey: "send-1" });
  expect(result).toMatchObject({ delivery_confirmed: false, missing_message_is_failure: false, coverage: "api_window" });
});

it("preserves the real TE session 404 instead of disguising it as a service outage", async () => {
  const request = vi.fn().mockResolvedValue({ status: 404, body: { detail: "Session not found" } });
  await expect(new RecoverySessionReader({ request, executeShell: vi.fn() }).read({ target, sessionKey: "missing", deliveryKey: "send" }))
    .rejects.toMatchObject({ status: 404, code: "original_session_not_found", message: expect.stringContaining("Session not found") });
});
