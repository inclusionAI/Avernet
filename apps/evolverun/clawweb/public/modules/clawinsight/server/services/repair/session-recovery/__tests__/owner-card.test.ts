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
  expect(sender.sendCard).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ outTrackId: "sr-owner:pre:SR-one", templateId: "template-pre", callbackRouteKey: "route-pre", recipientUserId: "123", cardParamMap: expect.objectContaining({ request_id: "r1", task_id: "SR-one", instructions: "请补权益规则正文与图1", control_plane: "pre" }) }));
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
