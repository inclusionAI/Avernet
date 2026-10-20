import type { DingTalkNotificationSender } from "../../notifications/dingtalk-sender.js";
import type { RecoveryTaskNotifier } from "./task-service.js";
import { redactPersistableText } from "@avernet/clawweb-shared/server/services/redaction";
import type { RecoveryActionNotifier } from "./contracts.js";
import type { RecoveryStoredTask, RecoveryTaskStore, RecoveryTimelineEvent } from "./task-contracts.js";
import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "../common/task-control-plane.js";

export class SessionRecoveryNotifier implements RecoveryTaskNotifier {
  constructor(private readonly sender: Pick<DingTalkNotificationSender, "sendNotification"> | null | undefined,
    private readonly publicBaseUrl: string) {}

  async notify(input: Parameters<RecoveryTaskNotifier["notify"]>[0]): Promise<void> {
    if (!this.sender) throw new Error("Notification unavailable");
    const title = input.kind === "plan" ? "会话自愈方案待确认" : "会话自愈需要人工处理";
    const url = new URL("/insight", this.publicBaseUrl);
    url.searchParams.set("module", "monitoring"); url.searchParams.set("recoveryTaskId", input.taskId);
    await this.sender.sendNotification({ recipientUserId: input.ownerId, title,
      text: `${input.summary}\n\n[查看完整方案、证据及任务时间线](${url.href})` });
  }
}

export const SESSION_RECOVERY_NOTIFICATIONS_KEY = "session_recovery_notifications";
type GroupNotification = Parameters<RecoveryActionNotifier["after"]>[0];
type WebhookConfig = { webhook: string; timeout_seconds: number };

function webhookConfig(raw: string): WebhookConfig {
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).some(key => !["webhook", "timeout_seconds"].includes(key))
    || typeof value.webhook !== "string" || new URL(value.webhook).protocol !== "https:"
    || !Number.isSafeInteger(value.timeout_seconds) || value.timeout_seconds < 1 || value.timeout_seconds > 60) {
    throw new Error("Invalid recovery notification configuration");
  }
  return value;
}

function clip(value: string, limit: number): string {
  const text = redactPersistableText(value, limit + 100);
  if (text.length <= limit) return text;
  const prefix = text.slice(0, limit);
  const boundary = prefix.lastIndexOf("\n");
  return `${prefix.slice(0, boundary > limit / 2 ? boundary : limit).trimEnd()}…`;
}

function notificationContent(task: RecoveryStoredTask, event: RecoveryTimelineEvent, kind: GroupNotification["kind"], baseUrl: string) {
  let title: string;
  const sections: string[] = [];
  const section = (label: string, value: string, limit = 700) => {
    if (value.trim()) sections.push(`**${label}**\n\n${clip(value, limit)}`);
  };
  if (kind === "message") {
    const request = event.payload.request as Record<string, unknown> | undefined;
    const status = event.payload.status;
    title = status === "failed" ? "恢复消息发送失败" : status === "unknown" ? "恢复消息投递未确认" : "已提交恢复消息";
    section("处理目的", event.summary, 300);
    section("恢复消息", String(request?.message ?? ""), 800);
    section("当前结果", status === "failed" ? "发送调用失败，详见任务中的实际回执。"
      : status === "unknown" ? "发送结果未知，需核对原会话；不会因此重新执行业务动作。"
      : "消息调用已完成，触达和恢复效果仍需结合原会话验证。", 300);
  } else if (kind === "plan") {
    const plan = event.payload.plan as RecoveryStoredTask["plan"];
    title = "已形成修复方案";
    section("方案", plan?.summary ?? event.summary);
    section("主要动作", plan?.actions.map(action => `- ${clip(action.summary, 180)}`).slice(0, 5).join("\n") ?? "");
    section("需要配合", plan?.owner_cooperation.map(item => `- ${clip(item.request, 180)}`).slice(0, 5).join("\n") ?? "");
    section("当前状态", task.level === "debug" ? "仅分析，未实际干预。"
      : task.status === "waiting_owner" ? "等待 Owner 完成配合事项并确认方案。" : "按任务已有授权继续处理。", 200);
  } else if (kind === "execution") {
    title = event.payload.success === true ? "修复方案执行完成" : "修复方案执行未完成";
    section("执行结果", event.summary, 1000);
    if (event.payload.success === true) section("后续处理", "继续核对原任务的执行效果，方案执行完成不代表业务已恢复。", 200);
  } else {
    title = task.status === "completed" ? "自愈任务已完成" : task.status === "failed" ? "自愈任务执行失败" : "自愈任务已停止";
    section("处理结论", event.summary || task.judgment?.summary || "", 1000);
    const effects = { recovered: "原任务已恢复", not_recovered: "原任务未恢复", unknown: "恢复效果尚未确认", not_intervened: "未实际干预" };
    section("恢复效果", effects[task.judgment?.effect ?? "unknown"], 200);
  }
  const url = new URL("/insight", baseUrl);
  url.searchParams.set("module", "monitoring"); url.searchParams.set("recoveryTaskId", task.taskId);
  const context = `**Bot**：${clip(task.request.target.bot_id, 150)}\n\n**问题**：${clip(task.request.diagnosis, 450)}`;
  return { title: `会话自愈｜${title}`, text: `## ${title}\n\n${clip([context, ...sections].join("\n\n"), 3000)}\n\n[查看完整任务与处理记录](${url.href})` };
}

/** One optional group channel; no task state, approvals or Agent output depend on delivery. */
export class SessionRecoveryGroupNotifier implements RecoveryActionNotifier {
  constructor(private readonly deps: {
    controlPlaneEnvironment: AgentControlPlaneEnvironment;
    tasks: Pick<RecoveryTaskStore, "find" | "appendNotification">;
    config: { findByKey(key: string): Promise<{ enabled: number; config_json: string } | null> };
    sender: { send(input: { webhook: string; timeoutSeconds: number; title: string; text: string }): Promise<void> };
    publicBaseUrl: string;
  }) {}

  after(input: GroupNotification): void {
    setImmediate(() => {
      void this.notify(input).catch(() => console.error("[session-recovery] group notification unavailable", input.taskId, input.kind));
    });
  }
  private async notify(input: GroupNotification): Promise<void> {
    const row = await this.deps.config.findByKey(SESSION_RECOVERY_NOTIFICATIONS_KEY);
    if (!row || row.enabled === 0) return;
    if (row.enabled !== 1) throw new Error("Invalid recovery notification state");
    const config = webhookConfig(row.config_json);
    const task = await this.deps.tasks.find(input.taskId);
    if (!task || resolveTaskControlPlaneEnvironment(task) !== this.deps.controlPlaneEnvironment) return;
    const source = task.timeline.find(event => event.event_id === input.eventId);
    if (!source) return;
    const id = `notification-${input.kind}-${input.eventId}`;
    const phase = input.kind === "plan" ? "plan" : input.kind === "message" ? "observe" : input.kind === "execution" ? "apply" : task.phase;
    const payload = { channel: "group_webhook", kind: input.kind, source_event_id: input.eventId, phase };
    const claimed = await this.deps.tasks.appendNotification(input.taskId, {
      event_id: id, at_ms: Date.now(), kind: "notification", actor: "service",
      summary: "正在发送自愈群通知", payload: { ...payload, state: "sending" },
    });
    if (!claimed) return;
    let sent = false;
    try {
      await this.deps.sender.send({ webhook: config.webhook, timeoutSeconds: config.timeout_seconds,
        ...notificationContent(task, source, input.kind, this.deps.publicBaseUrl) });
      sent = true;
    } catch {
      console.error("[session-recovery] group notification not confirmed", input.taskId, input.kind);
    }
    await this.deps.tasks.appendNotification(input.taskId, {
      event_id: `${id}-result`, at_ms: Date.now(), kind: "notification", actor: "service",
      summary: sent ? "自愈群通知已发送" : "自愈群通知发送未确认，原任务继续处理",
      payload: { ...payload, state: sent ? "sent" : "failed", sent },
    });
  }
}
