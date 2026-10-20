import { expect, it, vi } from "vitest";
import { SessionRecoveryGroupNotifier, SESSION_RECOVERY_NOTIFICATIONS_KEY } from "../notification.js";
import type { RecoveryStoredTask, RecoveryTimelineEvent } from "../task-contracts.js";

function setup() {
  const task = { taskId: "SR-notification", version: 7, status: "running", phase: "assess", level: "approval", controlPlaneEnvironment: "pre",
    request: { diagnosis: "读取失败，需要核对实际调用", target: { bot_id: "bot", entity_id: "owner" } },
    judgment: null, timeline: [{ event_id: "call-1", kind: "operation", at_ms: 1, actor: "agent", summary: "纠正调用方式",
      payload: { status: "succeeded", request: { message: "请使用已配置的工具继续原任务" } } }] } as RecoveryStoredTask;
  const events: RecoveryTimelineEvent[] = [];
  const tasks = { find: vi.fn(async () => structuredClone({ ...task, timeline: [...task.timeline, ...events] })),
    appendNotification: vi.fn(async (_id, event: RecoveryTimelineEvent) => {
      if (events.some(item => item.event_id === event.event_id)) return false;
      events.push(event); return true;
    }) };
  const config = { findByKey: vi.fn().mockResolvedValue({ enabled: 1, config_json: JSON.stringify({ webhook: "https://robot.invalid/send", timeout_seconds: 10 }) }) };
  const sender = { send: vi.fn().mockResolvedValue(undefined) };
  const deps = { controlPlaneEnvironment: "pre" as const, tasks, config, sender, publicBaseUrl: "https://service.invalid" };
  return { task, tasks, events, config, sender, deps, notifier: new SessionRecoveryGroupNotifier(deps) };
}
const message = { taskId: "SR-notification", eventId: "call-1", kind: "message" as const };

it("delivers asynchronously once across repeated callbacks and separate notifier instances", async () => {
  const { notifier, deps, sender, task, events, config } = setup();
  notifier.after(message); notifier.after(message); new SessionRecoveryGroupNotifier(deps).after(message);
  expect(sender.send).not.toHaveBeenCalled();
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send).toHaveBeenCalledOnce();
  expect(config.findByKey).toHaveBeenCalledWith(SESSION_RECOVERY_NOTIFICATIONS_KEY);
  expect(sender.send.mock.calls[0][0]).toMatchObject({ timeoutSeconds: 10, title: "会话自愈｜已提交恢复消息" });
  expect(sender.send.mock.calls[0][0].text).toContain("触达和恢复效果仍需");
  expect(sender.send.mock.calls[0][0].text).toContain("recoveryTaskId=SR-notification");
  expect(task.version).toBe(7); expect(task.status).toBe("running");
  expect(JSON.stringify(events)).not.toContain("https://robot.invalid");
});
it.each([null, { enabled: 0, config_json: "{}" }])("leaves notifications disabled when configuration is absent or disabled", async row => {
  const { notifier, config, sender, tasks } = setup(); config.findByKey.mockResolvedValue(row);
  notifier.after(message);
  await new Promise(resolve => setImmediate(resolve));
  expect(sender.send).not.toHaveBeenCalled(); expect(tasks.find).not.toHaveBeenCalled(); expect(tasks.appendNotification).not.toHaveBeenCalled();
});
it("isolates unavailable config tables and sender failures from the task", async () => {
  const { notifier, config, sender, task, events } = setup();
  config.findByKey.mockRejectedValueOnce(new Error("no such table"));
  expect(() => notifier.after(message)).not.toThrow();
  await new Promise(resolve => setImmediate(resolve));
  expect(sender.send).not.toHaveBeenCalled();
  sender.send.mockRejectedValueOnce(new Error("transport offline"));
  notifier.after(message);
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === false)).toBe(true));
  expect(task).toMatchObject({ version: 7, status: "running", phase: "assess" });
});
it("does not send another control plane's notification", async () => {
  const { notifier, task, sender, tasks } = setup(); task.controlPlaneEnvironment = "prod";
  notifier.after(message); await new Promise(resolve => setImmediate(resolve));
  expect(tasks.appendNotification).not.toHaveBeenCalled(); expect(sender.send).not.toHaveBeenCalled();
});
it("formats an automatic plan without Owner cooperation and bounds long content while retaining the task link", async () => {
  const { notifier, task, sender, events } = setup();
  task.level = "automatic";
  task.timeline = [{ event_id: "plan-1", kind: "plan", at_ms: 2, actor: "agent", summary: "修正错误的配置",
    payload: { plan: { summary: "现场依据\n".repeat(2000), actions: [{ summary: "修正配置" }], owner_cooperation: [] } } }];
  notifier.after({ ...message, eventId: "plan-1", kind: "plan" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const sent = sender.send.mock.calls[0][0];
  expect(sent.title).toContain("已形成修复方案"); expect(sent.text).toContain("主要动作");
  expect(sent.text).not.toContain("需要配合"); expect(sent.text).toContain("[查看完整任务与处理记录]");
  expect(Buffer.byteLength(sent.text)).toBeLessThan(12000);
});
