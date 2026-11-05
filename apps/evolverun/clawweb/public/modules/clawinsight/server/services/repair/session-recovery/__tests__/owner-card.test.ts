import { expect, it, vi } from "vitest";
import { SessionRecoveryNotifier } from "../notification.js";
import type { RecoveryStoredTask } from "../task-contracts.js";
const task: RecoveryStoredTask = { schemaVersion: "session-recovery-task/v1", taskId: "SR-one", request: { event_id: "e", delivery_key: "d", session_key: "original", diagnosis: "缺正文", tc_fault_label: null,
  target: { bot_id: "bot", entity_id: "123", env: "prod", engine: "OC" } }, level: "approval", status: "waiting_owner", phase: "assess", version: 2,
  controlPlaneEnvironment: "pre", observationDeadlineAt: null, ownerDeadlineAt: null, plan: null, judgment: null, evidence: [], timeline: [], pending_results: [],
  ownerRequest: { requestId: "r1", revision: 1, status: "pending", reason: "materials", summary: "缺权益规则正文", instructions: "请补权益规则正文与图1", evidence_ids: [], resumePhase: "assess", createdAtMs: 1 } };
it("selects the card route by CW environment even when the Bot is production, and updates one card", async () => {
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const config = { findByKey: vi.fn().mockResolvedValue({ enabled: 1, config_json: JSON.stringify({ card: { pre: { template_id: "template-pre", callback_route_key: "route-pre" }, prod: { template_id: "template-prod", callback_route_key: "route-prod" } } }) }) };
  const notifier = new SessionRecoveryNotifier(sender, "https://pre.invalid", config, "pre");
  expect(await notifier.notifyOwnerRequest({ task, updateOnly: false })).toBe(true);
  expect(sender.sendCard).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ outTrackId: "sr-owner:pre:SR-one", templateId: "template-pre", callbackRouteKey: "route-pre", recipientUserId: "123", cardParamMap: expect.objectContaining({ request_id: "r1", task_id: "SR-one", session_key: "original", confirm_label: "已补齐材料，继续任务", confirm_status: "normal", confirm_enabled: "true", config: '{"autoLayout":true}', instructions: "请补权益规则正文与图1", control_plane: "pre" }) }));
  await notifier.notifyOwnerRequest({ task: { ...task, ownerRequest: { ...task.ownerRequest!, requestId: "r2", revision: 2, instructions: "仍缺图1" } }, updateOnly: true });
  expect(sender.sendCard).toHaveBeenCalledOnce();
  expect(sender.updateCard).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ outTrackId: "sr-owner:pre:SR-one", cardParamMap: expect.objectContaining({ request_id: "r2", instructions: "仍缺图1" }) }));
});
it("does not claim card delivery when disabled, not configured for the control plane, or rejected by the sender", async () => {
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn().mockRejectedValue(new Error("unknown")), updateCard: vi.fn() };
  const row = { enabled: 0, config_json: "{}" };
  const notifier = new SessionRecoveryNotifier(sender, "https://pre.invalid", { findByKey: async () => row }, "pre");
  expect(await notifier.notifyOwnerRequest({ task, updateOnly: false })).toBe(false);
  row.enabled = 1;
  expect(await notifier.notifyOwnerRequest({ task, updateOnly: false })).toBe(false);
  row.config_json = JSON.stringify({ card: { pre: { template_id: "template", callback_route_key: "route" } } });
  await expect(notifier.notifyOwnerRequest({ task, updateOnly: false })).rejects.toThrow("unknown");
});

it.each([
  ["permission", "已获取权限，继续任务"],
  ["materials", "已补齐材料，继续任务"],
] as const)("updates the single %s button after Owner feedback", async (reason, label) => {
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const config = { findByKey: async () => ({ enabled: 1, config_json: JSON.stringify({ card: { pre: { template_id: "template", callback_route_key: "route" } } }) }) };
  const notifier = new SessionRecoveryNotifier(sender, "https://pre.invalid", config, "pre");
  const pending: RecoveryStoredTask = { ...task, ownerRequest: { ...task.ownerRequest!, reason } };
  await notifier.notifyOwnerRequest({ task: pending, updateOnly: false });
  const initial = sender.sendCard.mock.calls[0][0];
  expect(initial.cardParamMap).toMatchObject({ confirm_label: label, confirm_status: "normal", confirm_enabled: "true", session_key: "original" });
  expect(Object.keys(initial.cardParamMap)).toHaveLength(14);
  expect(Object.values(initial.cardParamMap).every(value => typeof value === "string")).toBe(true);
  expect(JSON.parse(initial.cardParamMap.config)).toEqual({ autoLayout: true });

  await notifier.notifyOwnerRequest({ task: { ...pending, status: "running", ownerRequest: { ...pending.ownerRequest!, status: "confirmed" } }, updateOnly: true });
  expect(sender.updateCard).toHaveBeenCalledExactlyOnceWith({
    outTrackId: initial.outTrackId,
    cardParamMap: { ...initial.cardParamMap, confirm_enabled: "false", confirm_status: "disabled", status: "已收到处理反馈，正在核验" },
  });
  expect(sender.sendCard).toHaveBeenCalledOnce();
});
it.each(["completed", "failed", "canceled"] as const)("disables an outstanding button when the Task is %s", async status => {
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, "https://pre.invalid", {
    findByKey: async () => ({ enabled: 1, config_json: JSON.stringify({ card: { pre: { template_id: "template", callback_route_key: "route" } } }) }),
  }, "pre");
  await notifier.notifyOwnerRequest({ task: { ...task, status }, updateOnly: true });
  expect(sender.updateCard).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ cardParamMap: expect.objectContaining({ confirm_status: "disabled", confirm_enabled: "false" }) }));
  expect(sender.sendCard).not.toHaveBeenCalled();
});
it("clips the original logical session key by UTF-8 bytes without changing the stored request", async () => {
  const sender = { sendNotification: vi.fn(), sendCard: vi.fn(), updateCard: vi.fn() };
  const notifier = new SessionRecoveryNotifier(sender, "https://pre.invalid", {
    findByKey: async () => ({ enabled: 1, config_json: JSON.stringify({ card: { pre: { template_id: "template", callback_route_key: "route" } } }) }),
  }, "pre");
  const sessionKey = `agent:main:session:${"会话😀".repeat(200)}`;
  const longTask = { ...task, request: { ...task.request, session_key: sessionKey, session_id: "physical-session" } };
  await notifier.notifyOwnerRequest({ task: longTask, updateOnly: false });
  const displayed = sender.sendCard.mock.calls[0][0].cardParamMap.session_key;
  expect(displayed).toMatch(/^agent:main:session:/);
  expect(displayed).toContain("（完整事项见任务页）");
  expect(Buffer.byteLength(displayed)).toBeLessThanOrEqual(1000);
  expect(Buffer.from(displayed).toString("utf8")).toBe(displayed);
  expect(displayed).not.toContain("physical-session");
  expect(longTask.request.session_key).toBe(sessionKey);
});
