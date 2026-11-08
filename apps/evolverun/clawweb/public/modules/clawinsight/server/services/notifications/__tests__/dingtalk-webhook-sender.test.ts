// @vitest-environment node
import { expect, it, vi } from "vitest";
import { DingTalkWebhookSender } from "../dingtalk-webhook-sender.js";

const input = { webhook: "https://robot.invalid/send?access_token=example", timeoutSeconds: 10, title: "自愈进展", text: "## 已形成方案\n\n查看任务详情" };
it("posts Markdown directly to the configured group without mentions or application authentication", async () => {
  const request = vi.fn().mockResolvedValue(Response.json({ errcode: 0, errmsg: "ok" }));
  await new DingTalkWebhookSender(request).send(input);
  expect(request).toHaveBeenCalledOnce();
  const [url, options] = request.mock.calls[0];
  expect(url).toBe(input.webhook);
  expect(options).toMatchObject({ method: "POST", redirect: "error", headers: { "Content-Type": "application/json" } });
  expect(options.signal).toBeInstanceOf(AbortSignal);
  expect(JSON.parse(options.body)).toEqual({ msgtype: "markdown", markdown: { title: input.title, text: input.text } });
});
it.each([
  new Response("offline", { status: 503 }),
  Response.json({ errcode: 310000, errmsg: "rejected" }),
  Response.json({}),
])("does not treat an HTTP or robot rejection as successful delivery", async response => {
  await expect(new DingTalkWebhookSender(vi.fn().mockResolvedValue(response)).send(input)).rejects.toThrow();
});
it("propagates transport failures without another delivery attempt", async () => {
  const request = vi.fn().mockRejectedValue(new Error("timeout"));
  await expect(new DingTalkWebhookSender(request).send(input)).rejects.toThrow("timeout");
  expect(request).toHaveBeenCalledOnce();
});
