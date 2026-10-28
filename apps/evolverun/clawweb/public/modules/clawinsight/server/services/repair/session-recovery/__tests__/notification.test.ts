import { expect, it, vi } from "vitest";
import { SessionRecoveryGroupNotifier, SessionRecoveryNotifier, SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY, SESSION_RECOVERY_NOTIFICATIONS_KEY } from "../notification.js";
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

it.each([
  ["completed", "recovered", undefined],
  ["completed", "unknown", undefined],
  ["failed", "unknown", undefined],
  ["canceled", "unknown", undefined],
  ["completed", "unknown", "user_continued"],
  ["completed", "unknown", "owner_handoff"],
] as const)("omits terminal group notices without intervention: %s/%s/%s", async (status, effect, completionReason) => {
  const { notifier, task, tasks, sender, events } = setup();
  task.status = status; task.completionReason = completionReason; task.interventionStatus = "none";
  task.judgment = { delivery: "unconfirmed", response: "none", effect, summary: "仅调查，未实际干预", evidence_ids: [] };
  task.timeline = [{ event_id: "result-1", kind: "observation", at_ms: 2, actor: "agent", summary: task.judgment.summary, payload: {} }];
  const original = structuredClone(task);
  notifier.after({ ...message, eventId: "result-1", kind: "result" });
  await new Promise(resolve => setImmediate(resolve));
  expect(tasks.find).toHaveBeenCalledWith(task.taskId);
  expect(sender.send).not.toHaveBeenCalled(); expect(tasks.appendNotification).not.toHaveBeenCalled();
  expect(events).toEqual([]); expect(task).toEqual(original);
});

it.each(["performed", "unknown", undefined] as const)("retains terminal notices for performed or uncertain intervention: %s", async interventionStatus => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.interventionStatus = interventionStatus;
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send).toHaveBeenCalledOnce();
  expect(sender.send.mock.calls[0][0].title).toBe("会话自愈｜任务已完成");
});

it.each(["message", "plan", "execution"] as const)("retains %s notices before any confirmed intervention", async kind => {
  const { notifier, task, sender, events } = setup();
  task.interventionStatus = "none";
  if (kind === "plan") {
    task.status = "waiting_owner";
    task.timeline[0].payload.plan = { summary: "需要用户开通权限", actions: [], owner_cooperation: [{ request: "申请目标文档的读取权限" }] };
  }
  notifier.after({ ...message, kind });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send).toHaveBeenCalledOnce();
  if (kind === "plan") expect(sender.send.mock.calls[0][0].text).toContain("申请目标文档的读取权限");
});

it.each(["message", "plan", "execution", "result"] as const)("routes %s to the exact Bot and Owner override only", async kind => {
  const { notifier, config, sender, task, events } = setup();
  task.request.target.bot_id = "default";
  config.findByKey.mockResolvedValue({ enabled: 1, config_json: JSON.stringify({ webhook: "https://business.invalid/send", timeout_seconds: 10,
    bot_webhooks: [{ bot_id: "default", entity_id: "another-owner", webhook: "https://another.invalid/send" },
      { bot_id: "default", entity_id: "owner", webhook: "https://test.invalid/send" }] }) });
  notifier.after({ ...message, kind });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send).toHaveBeenCalledOnce();
  expect(sender.send.mock.calls[0][0]).toMatchObject({ webhook: "https://test.invalid/send", timeoutSeconds: 10 });
  expect(JSON.stringify(events)).not.toMatch(/https:\/\/(test|business|another)\.invalid/);
});
it.each([["bot", "another-owner"], ["another-bot", "owner"]])("retains the default group for unmatched target %s/%s", async (bot_id, entity_id) => {
  const { notifier, config, sender, events } = setup();
  config.findByKey.mockResolvedValue({ enabled: 1, config_json: JSON.stringify({ webhook: "https://business.invalid/send", timeout_seconds: 10,
    bot_webhooks: [{ bot_id, entity_id, webhook: "https://test.invalid/send" }] }) });
  notifier.after(message);
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send).toHaveBeenCalledOnce();
  expect(sender.send.mock.calls[0][0].webhook).toBe("https://business.invalid/send");
});
it.each([
  [{ bot_id: "bot", webhook: "https://test.invalid/send" }],
  [{ bot_id: "bot", entity_id: "owner", webhook: "http://test.invalid/send" }],
  [{ bot_id: "bot", entity_id: "owner", webhook: "https://test.invalid/send" },
    { bot_id: "bot", entity_id: "owner", webhook: "https://another.invalid/send" }],
])("isolates invalid or ambiguous Bot routes without sending to the default group", async (...routes) => {
  const { notifier, config, sender, tasks, task } = setup();
  config.findByKey.mockResolvedValue({ enabled: 1, config_json: JSON.stringify({ webhook: "https://business.invalid/send", timeout_seconds: 10, bot_webhooks: routes }) });
  notifier.after(message); await new Promise(resolve => setImmediate(resolve));
  expect(sender.send).not.toHaveBeenCalled(); expect(tasks.appendNotification).not.toHaveBeenCalled();
  expect(task).toMatchObject({ version: 7, status: "running", phase: "assess" });
});
it("does not leak a failed Bot-specific notification into the default business group", async () => {
  const { notifier, config, sender, events } = setup();
  config.findByKey.mockResolvedValue({ enabled: 1, config_json: JSON.stringify({ webhook: "https://business.invalid/send", timeout_seconds: 10,
    bot_webhooks: [{ bot_id: "bot", entity_id: "owner", webhook: "https://test.invalid/send" }] }) });
  sender.send.mockRejectedValue(new Error("transport offline"));
  notifier.after(message);
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === false)).toBe(true));
  expect(sender.send).toHaveBeenCalledOnce();
  expect(sender.send.mock.calls[0][0].webhook).toBe("https://test.invalid/send");
});

it("delivers asynchronously once across repeated callbacks and separate notifier instances", async () => {
  const { notifier, deps, sender, task, events, config } = setup();
  notifier.after(message); notifier.after(message); new SessionRecoveryGroupNotifier(deps).after(message);
  expect(sender.send).not.toHaveBeenCalled();
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send).toHaveBeenCalledOnce();
  expect(config.findByKey).toHaveBeenCalledWith(SESSION_RECOVERY_NOTIFICATIONS_KEY);
  expect(sender.send.mock.calls[0][0]).toMatchObject({ timeoutSeconds: 10, title: "会话自愈｜已提交恢复消息" });
  expect(sender.send.mock.calls[0][0].text).toContain("触达和恢复效果尚待验证");
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

it("keeps nested Markdown, commands and long diagnosis out of the message notification", async () => {
  const { notifier, task, sender, events } = setup();
  task.request.diagnosis = "中文诊断😀".repeat(2000);
  task.timeline[0].summary = "**纠正调用方式**，请参考[工具说明](https://docs.invalid)和 `mcporter`。";
  (task.timeline[0].payload.request as { message: string }).message = "### 恢复步骤\n```bash\n" + "mcporter call ".repeat(1000);
  notifier.after(message);
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const text = sender.send.mock.calls[0][0].text;
  expect(text).toContain("**【本次干预】**\n\n纠正调用方式，请参考工具说明和 mcporter。");
  expect(text).toContain("**【当前结果】**");
  expect(text).not.toMatch(/\[TRUNCATED\]|\uFFFD|```|### 恢复步骤|mcporter call|中文诊断/);
  expect(text).not.toContain("https://docs.invalid");
});

it.each([
  ["completed", "recovered", "任务已完成", "<font color=\"#16803C\">原任务已恢复</font>"],
  ["completed", "unknown", "任务已完成", "恢复效果尚未确认"],
  ["failed", "not_recovered", "任务执行失败", "原任务未恢复"],
  ["canceled", "unknown", "任务已停止", "恢复效果尚未确认"],
] as const)("uses structured verification fields for %s/%s instead of the long judgment", async (status, effect, title, conclusion) => {
  const { notifier, task, sender, events } = setup();
  task.status = status;
  task.judgment = { delivery: "confirmed", response: "acted", effect, summary: "不应复制的长篇判断\n```bash\n".repeat(1000), evidence_ids: [] };
  task.timeline.push({ event_id: "result-1", kind: status === "canceled" ? "termination" : "observation", actor: "service", at_ms: 2,
    summary: status === "completed" ? task.judgment.summary : "**停止或失败原因**：现场已记录。", payload: {} });
  notifier.after({ ...message, eventId: "result-1", kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const sent = sender.send.mock.calls[0][0];
  expect(sent.title).toBe(`会话自愈｜${title}`);
  expect(sent.text).toContain("**【本次干预】**\n\n纠正调用方式");
  expect(sent.text).toContain("**消息已触达**：恢复指引已确认出现在原会话中。");
  expect(sent.text).toContain("**Bot 已采取行动**：已采纳指引并实际执行。");
  expect(sent.text).toContain(`**【恢复结论】**\n\n${conclusion}`);
  expect(sent.text).not.toMatch(/不应复制|\[TRUNCATED\]|\uFFFD|```/);
  if (effect !== "recovered") expect(sent.text).not.toContain("<font");
  if (status !== "completed") expect(sent.text).toContain("停止或失败原因：现场已记录。");
});

it("does not turn missing delivery, a reply or an ended task into confirmed recovery", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed";
  task.judgment = { delivery: "unconfirmed", response: "replied", effect: "unknown", summary: "只有回复", evidence_ids: [] };
  task.timeline[0].payload.status = "failed";
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const text = sender.send.mock.calls[0][0].text;
  expect(text).toContain("消息触达未确认"); expect(text).toContain("Bot 已回复");
  expect(text).toContain("恢复效果尚未确认"); expect(text).not.toContain("<font");
});

it.each([
  ["recovered", "原任务已恢复"],
  ["not_recovered", "原任务未恢复"],
  ["unknown", "恢复效果尚未确认"],
] as const)("shows the saved Owner handoff details without changing effect %s", async (effect, conclusion) => {
  const { notifier, task, sender, events } = setup();
  const handoff = "## 转交原因\n\n原任务的外部上报缺少可独立核对的回执。\n\n"
    + "已核查本轮执行记录，保留现有结果并避免重复业务操作。".repeat(18)
    + "\n\n## Owner 处理事项\n\n核查 eventKey start-fixture-key、end-fixture-key 对应的服务端记录；核对 worker.py 的实际位置。";
  task.status = "completed"; task.completionReason = "owner_handoff"; task.interventionStatus = "performed";
  task.final_summary = "原任务已经核查，具体后续事项在转交说明中。";
  task.judgment = { delivery: "confirmed", response: "acted", effect, summary: handoff, evidence_ids: [] };
  task.timeline.push({ event_id: "handoff", kind: "termination", at_ms: 2, actor: "agent", summary: handoff,
    payload: { completion_reason: "owner_handoff" } });
  task.timeline.push({ event_id: "released", kind: "execution", at_ms: 3, actor: "service",
    summary: "本次执行结束，已保存任务上下文", payload: {} });
  // A queued legacy callback may still refer to the release event; the saved handoff remains authoritative.
  notifier.after({ ...message, eventId: "released", kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const sent = sender.send.mock.calls[0][0];
  expect(sent.title).toBe("会话自愈｜已转交 Owner，自愈已结束");
  expect(sent.text).toContain("**【转交原因与 Owner 处理事项】**");
  expect(sent.text).toContain("外部上报缺少可独立核对的回执");
  expect(sent.text).toContain("start\\-fixture\\-key"); expect(sent.text).toContain("end\\-fixture\\-key");
  expect(sent.text).toContain("worker\\.py");
  expect(sent.text).toContain(conclusion);
  expect(sent.text).toContain("本次自愈已结束，后续由 Owner 处理。");
  expect(sent.text).not.toContain("本次执行结束，已保存任务上下文");
  expect(sent.text).not.toContain("等待 Owner");
  expect(task.judgment.effect).toBe(effect);
});

it.each([
  ["message", "已提交恢复消息"], ["plan", "已形成修复方案"], ["execution", "修复方案执行未完成"],
] as const)("keeps a delayed %s notification unchanged after Owner handoff", async (kind, title) => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.completionReason = "owner_handoff";
  notifier.after({ ...message, kind });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const sent = sender.send.mock.calls[0][0];
  expect(sent.title).toBe(`会话自愈｜${title}`);
  expect(sent.text).not.toContain("转交原因与 Owner 处理事项");
});

it("uses the saved task summary when a legacy handoff has no detailed judgment", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.completionReason = "owner_handoff";
  task.final_summary = "请 Owner 核查 start-fixture-key 对应的上报记录。";
  task.timeline[0].summary = "本次执行结束，已保存任务上下文";
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const text = sender.send.mock.calls[0][0].text;
  expect(text).toContain("start\\-fixture\\-key");
  expect(text).not.toContain("本次执行结束，已保存任务上下文");
});

it("prefers the saved handoff event over a generic legacy task summary", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.completionReason = "owner_handoff";
  task.final_summary = "原任务已经核查，具体后续事项在转交说明中。";
  task.timeline.push({ event_id: "handoff", kind: "termination", at_ms: 2, actor: "agent",
    summary: "缺少外部回执，请 Owner 核查 start-fixture-key 对应的服务端记录。",
    payload: { completion_reason: "owner_handoff" } });
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send.mock.calls[0][0].text).toContain("start\\-fixture\\-key");
});

it.each([
  ["Chinese", "请核对原始执行回执。".repeat(700), "任务摘要。".repeat(400)],
  ["emoji", "😀。".repeat(2000), "任务摘要。".repeat(400)],
  ["escaped characters", "&&&&。".repeat(1600), "&&&&。".repeat(400)],
])("bounds long Owner details with %s while retaining the task link", async (_name, summary, finalSummary) => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.completionReason = "owner_handoff";
  task.final_summary = finalSummary;
  task.judgment = { delivery: "confirmed", response: "acted", effect: "unknown", summary, evidence_ids: [] };
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const text = sender.send.mock.calls[0][0].text;
  expect(Buffer.byteLength(text)).toBeLessThan(20000);
  expect(text).toContain("完整内容见任务页");
  expect(text).toContain("[查看完整任务与处理记录]");
});

it("shows the business summary of an audited plan action without claiming unexecuted actions", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed";
  task.plan = { summary: "清理错误配置", actions: [{ actionId: "a1", summary: "删除错误定时任务" }, { actionId: "a2", summary: "未执行的另一动作" }] } as RecoveryStoredTask["plan"];
  task.timeline[0].payload.request = { actionId: "a1", purpose: "调用底层接口执行命令" };
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const text = sender.send.mock.calls[0][0].text;
  expect(text).toContain("删除错误定时任务");
  expect(text).not.toContain("未执行的另一动作"); expect(text).not.toContain("调用底层接口执行命令");
});

it("omits an oversized command as a whole and escapes injected formatting in plan values", async () => {
  const { notifier, task, sender, events } = setup();
  const purpose = "mcporter_call_".repeat(400);
  task.timeline = [{ event_id: "plan-1", kind: "plan", at_ms: 2, actor: "agent", summary: "修复配置", payload: {
    plan: { summary: "## 修复说明\n\n**修复** `配置`，参考[文档](https://docs.invalid)。\n\n```bash\n" + purpose + "\n```",
      actions: [{ summary: purpose }], owner_cooperation: [{ request: "确认 *权限* <font color=\"red\">后</font>继续。" }] } } }];
  notifier.after({ ...message, eventId: "plan-1", kind: "plan" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const text = sender.send.mock.calls[0][0].text;
  expect(text).toContain("修复说明 修复 配置，参考文档。");
  expect(text).toContain("内容较长，请查看完整任务");
  expect(text).toContain("确认 权限 后继续。");
  expect(text).not.toMatch(/mcporter_call_|```|<font|\[TRUNCATED\]|\uFFFD/);
  expect(text).toContain("**【当前状态】**");
});

it("shortens Chinese and emoji only at sentence boundaries and treats inline Markdown as literal text", async () => {
  const { notifier, task, sender, events } = setup();
  task.timeline[0].summary = "已确认目标😀。" + "下一句中文😀".repeat(80);
  notifier.after(message);
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const text = sender.send.mock.calls[0][0].text;
  expect(text).toContain("已确认目标😀。（完整内容见任务页）");
  expect(text).not.toMatch(/下一句|\[TRUNCATED\]|\uFFFD/);
  const other = setup();
  other.task.timeline[0].summary = "说明：`**不要加粗** [不要变链接](https://docs.invalid)`。";
  other.notifier.after(message);
  await vi.waitFor(() => expect(other.events.some(event => event.payload.sent === true)).toBe(true));
  expect(other.sender.send.mock.calls[0][0].text).toContain("\\*\\*不要加粗\\*\\* \\[不要变链接\\]\\(https://docs\\.invalid\\)");
});

it("checks the independent Owner notification switch on each send", async () => {
  const config = { findByKey: vi.fn<() => Promise<{ enabled: number; config_json: string } | null>>().mockResolvedValue(null) };
  const sender = { sendNotification: vi.fn().mockResolvedValue(undefined) };
  const notifier = new SessionRecoveryNotifier(sender, "https://service.invalid", config);
  const input = { ownerId: "owner", taskId: "SR-owner", kind: "plan" as const, summary: "需要确认方案" };
  expect(await notifier.notify(input)).toBe(false);
  config.findByKey.mockResolvedValue({ enabled: 0, config_json: "{}" });
  expect(await notifier.notify(input)).toBe(false);
  expect(sender.sendNotification).not.toHaveBeenCalled();
  config.findByKey.mockResolvedValue({ enabled: 1, config_json: "{}" });
  expect(await notifier.notify(input)).toBe(true);
  expect(config.findByKey).toHaveBeenLastCalledWith(SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY);
  expect(sender.sendNotification).toHaveBeenCalledOnce();
  expect(sender.sendNotification.mock.calls[0][0]).toMatchObject({ recipientUserId: "owner", title: "会话自愈方案待确认" });
  config.findByKey.mockResolvedValue({ enabled: 0, config_json: "{}" });
  expect(await notifier.notify(input)).toBe(false);
  expect(sender.sendNotification).toHaveBeenCalledOnce();
});


it("notifies user handoff after intervention without claiming business recovery", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.completionReason = "user_continued"; task.interventionStatus = "performed";
  task.judgment = { delivery: "unconfirmed", response: "unknown", effect: "unknown", summary: "用户已继续对话，本次自愈已让路", evidence_ids: [] };
  task.timeline = [{ event_id: "handoff", at_ms: 1, kind: "termination", actor: "agent", summary: task.judgment.summary, payload: { completion_reason: "user_continued" } }];
  notifier.after({ taskId: task.taskId, eventId: "handoff", kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send.mock.calls[0][0]).toMatchObject({ title: "会话自愈｜用户已接手，自愈已让路" });
  expect(sender.send.mock.calls[0][0].text).toContain("恢复效果尚未确认");
  expect(sender.send.mock.calls[0][0].text).not.toContain("原任务已恢复");
  expect(sender.send.mock.calls[0][0].text).not.toContain("任务执行失败");
});


it("reuses the direct Owner DingTalk channel for ended permission cases without an approval link label", async () => {
  const sender = { sendNotification: vi.fn().mockResolvedValue(undefined) };
  const config = { findByKey: vi.fn().mockResolvedValue({ enabled: 1, config_json: "{}" }) };
  const notifier = new SessionRecoveryNotifier(sender, "https://service.invalid", config);
  await expect(notifier.notify({ ownerId: "owner-fixture", taskId: "SR-permission", kind: "result", summary: "请在能力集中申请 MCP 服务权限。" })).resolves.toBe(true);
  expect(sender.sendNotification).toHaveBeenCalledExactlyOnceWith({ recipientUserId: "owner-fixture", title: "会话自愈需要人工处理",
    text: expect.stringContaining("查看处理指引、证据及任务时间线") });
  expect(sender.sendNotification.mock.calls[0][0].text).toContain("请在能力集中申请 MCP 服务权限");
  expect(sender.sendNotification.mock.calls[0][0].text).not.toContain("查看完整方案");
  expect(sender.sendNotification.mock.calls[0][0].text).toContain("本次自愈已结束，后续请自行处理。");
});

it("notifies the Owner of nonblocking suggestions after recovery without requesting approval or repair", async () => {
  const sender = { sendNotification: vi.fn().mockResolvedValue(undefined) };
  const config = { findByKey: vi.fn().mockResolvedValue({ enabled: 1, config_json: "{}" }) };
  const notifier = new SessionRecoveryNotifier(sender, "https://service.invalid", config);
  await notifier.notify({ ownerId: "owner", taskId: "SR-suggestion", kind: "result", effect: "recovered", summary: "原任务已完成；建议在 Skill 中补充参数示例。" });
  expect(sender.sendNotification).toHaveBeenCalledExactlyOnceWith({ recipientUserId: "owner", title: "会话自愈已恢复，有后续建议",
    text: expect.stringContaining("原任务已完成") });
  const text = sender.sendNotification.mock.calls[0][0].text;
  expect(text).toContain("本次自愈已结束，后续建议可按需处理。");
  expect(text).not.toMatch(/等待|确认方案|需要人工处理/);
});

it("uses the saved overall summary in final notices while preserving verified facts", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.interventionStatus = "performed";
  task.final_summary = "原任务需要读取资料。核实调用参数后发送纠正指引，Bot已成功读取并继续原任务。";
  task.judgment = { delivery: "confirmed", response: "acted", effect: "recovered", summary: "很长的过程分析不代替整体摘要", evidence_ids: [] };
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  const sent = sender.send.mock.calls[0][0];
  expect(sent.text).toContain(task.final_summary);
  expect(sent.text).toContain("消息已触达"); expect(sent.text).toContain("原任务已恢复");
  expect(sent.text).not.toContain(task.judgment.summary);
});
it("links to full results when a legacy or failed summary is unavailable", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "completed"; task.interventionStatus = "performed";
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send.mock.calls[0][0].text).toContain("摘要暂不可用");
  expect(sender.send.mock.calls[0][0].text).toContain("查看完整任务与处理记录");
});
it("describes a failed business verification as unrecovered rather than an execution error", async () => {
  const { notifier, task, sender, events } = setup();
  task.status = "failed"; task.phase = "verify"; task.interventionStatus = "performed";
  task.judgment = { delivery: "confirmed", response: "acted", effect: "not_recovered", summary: "原任务仍受阻", evidence_ids: [] };
  task.final_summary = "已执行完整方案，但原任务的后续步骤仍受阻，需继续处理。";
  notifier.after({ ...message, kind: "result" });
  await vi.waitFor(() => expect(events.some(event => event.payload.sent === true)).toBe(true));
  expect(sender.send.mock.calls[0][0].title).not.toContain("执行失败");
  expect(sender.send.mock.calls[0][0].text).toContain("原任务未恢复");
});
