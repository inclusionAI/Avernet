import type { DingTalkNotificationSender } from "../../notifications/dingtalk-sender.js";
import type { RecoveryTaskNotifier } from "./task-service.js";
import { redactPersistableText } from "@avernet/clawweb-shared/server/services/redaction";
import { fromMarkdown } from "mdast-util-from-markdown";
import type { RecoveryActionNotifier } from "./contracts.js";
import type { RecoveryStoredTask, RecoveryTaskStore, RecoveryTimelineEvent } from "./task-contracts.js";
import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "../common/task-control-plane.js";

export const SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY = "session_recovery_owner_notifications";
type NotificationConfigStore = { findByKey(key: string): Promise<{ enabled: number; config_json: string } | null> };

export class SessionRecoveryNotifier implements RecoveryTaskNotifier {
  constructor(private readonly sender: Pick<DingTalkNotificationSender, "sendNotification"> | null | undefined,
    private readonly publicBaseUrl: string, private readonly config: NotificationConfigStore) {}

  async notify(input: Parameters<RecoveryTaskNotifier["notify"]>[0]): Promise<boolean> {
    const row = await this.config.findByKey(SESSION_RECOVERY_OWNER_NOTIFICATIONS_KEY);
    if (!row || row.enabled === 0) return false;
    if (row.enabled !== 1) throw new Error("Invalid recovery owner notification state");
    if (!this.sender) throw new Error("Notification unavailable");
    const title = input.kind === "plan" ? "会话自愈方案待确认" : "会话自愈需要人工处理";
    const url = new URL("/insight", this.publicBaseUrl);
    url.searchParams.set("module", "monitoring"); url.searchParams.set("recoveryTaskId", input.taskId);
    await this.sender.sendNotification({ recipientUserId: input.ownerId, title,
      text: `${input.summary}${input.kind === "result" ? "\n\n本次自愈已结束，后续请自行处理。" : ""}\n\n[${input.kind === "plan" ? "查看完整方案、证据及任务时间线" : "查看处理指引、证据及任务时间线"}](${url.href})` });
    return true;
  }
}

export const SESSION_RECOVERY_NOTIFICATIONS_KEY = "session_recovery_notifications";
type GroupNotification = Parameters<RecoveryActionNotifier["after"]>[0];
type BotWebhook = { bot_id: string; entity_id: string; webhook: string };
type WebhookConfig = { webhook: string; timeout_seconds: number; bot_webhooks?: BotWebhook[] };

function webhookConfig(raw: string): WebhookConfig {
  const value = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)
    || Object.keys(value).some(key => !["webhook", "timeout_seconds", "bot_webhooks"].includes(key))
    || typeof value.webhook !== "string" || new URL(value.webhook).protocol !== "https:"
    || !Number.isSafeInteger(value.timeout_seconds) || value.timeout_seconds < 1 || value.timeout_seconds > 60) {
    throw new Error("Invalid recovery notification configuration");
  }
  if (value.bot_webhooks !== undefined) {
    if (!Array.isArray(value.bot_webhooks)) throw new Error("Invalid recovery Bot notification configuration");
    const targets = new Set<string>();
    for (const item of value.bot_webhooks) {
      if (!item || typeof item !== "object" || Array.isArray(item)
        || Object.keys(item).some(key => !["bot_id", "entity_id", "webhook"].includes(key))
        || typeof item.bot_id !== "string" || !item.bot_id.trim() || item.bot_id !== item.bot_id.trim()
        || typeof item.entity_id !== "string" || !item.entity_id.trim() || item.entity_id !== item.entity_id.trim()
        || typeof item.webhook !== "string" || new URL(item.webhook).protocol !== "https:") {
        throw new Error("Invalid recovery Bot notification configuration");
      }
      const target = JSON.stringify([item.bot_id, item.entity_id]);
      if (targets.has(target)) throw new Error("Duplicate recovery Bot notification target");
      targets.add(target);
    }
  }
  return value;
}

type MarkdownNode = { type: string; value?: string; children?: MarkdownNode[] };
function plainText(node: MarkdownNode): string {
  // Full commands, raw HTML and images belong in the linked task, not in a short notification.
  if (["code", "html", "image", "imageReference"].includes(node.type)) return "";
  if (typeof node.value === "string") return node.value;
  const separator = ["root", "blockquote", "list", "listItem"].includes(node.type) ? " " : "";
  return node.children?.map(plainText).join(separator) ?? "";
}
function escapeMarkdown(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/[\\`*_[\]{}()#+.!|~-]/g, "\\$&");
}
function clip(value: string, limit: number): string {
  // Redact the complete, already bounded source before presentation trimming. A byte slice can split UTF-8.
  const text = plainText(fromMarkdown(redactPersistableText(value, Number.MAX_SAFE_INTEGER))).replace(/\s+/g, " ").trim();
  if (!text) return value.trim() ? "详细内容请查看任务页。" : "";
  if (Array.from(text).length <= limit) return escapeMarkdown(text);
  let prefix = "";
  for (const { segment } of new Intl.Segmenter("zh", { granularity: "sentence" }).segment(text)) {
    if (Array.from(prefix + segment).length > limit) break;
    prefix += segment;
  }
  return prefix.trim() ? `${escapeMarkdown(prefix.trim())}（完整内容见任务页）` : "内容较长，请查看完整任务。";
}

function notificationContent(task: RecoveryStoredTask, event: RecoveryTimelineEvent, kind: GroupNotification["kind"], baseUrl: string) {
  let title: string;
  const sections: string[] = [];
  const section = (label: string, value: string, limit = 240) => {
    const text = clip(value, limit);
    if (text) sections.push(`**【${label}】**\n\n${text}`);
  };
  const list = (label: string, values: string[]) => {
    if (values.length) sections.push(`**【${label}】**\n\n${values.slice(0, 5).map(value => `- ${clip(value, 120)}`).join("\n")}`
      + (values.length > 5 ? "\n\n其余事项见任务页。" : ""));
  };
  const intervention = () => {
    const purposes = task.timeline.flatMap(item => {
      const request = item.payload.request as Record<string, unknown> | undefined;
      const result = item.payload.result as Record<string, unknown> | undefined;
      if (item.kind !== "operation" || result?.dry_run === true || !request
        || typeof request.message !== "string" && typeof request.actionId !== "string") return [];
      const action = typeof request.actionId === "string" ? task.plan?.actions.find(value => value.actionId === request.actionId) : null;
      return [action?.summary ?? (typeof request.purpose === "string" ? request.purpose : item.summary)];
    });
    const unique = [...new Set(purposes.filter(value => value.trim()))];
    if (unique.length > 1) list("本次干预", unique);
    else section("本次干预", unique[0] ?? (task.level === "debug" ? "仅分析，未实际干预。" : "未记录可确认的干预动作，详情见任务页。"), 180);
  };
  if (kind === "message") {
    const status = event.payload.status;
    title = status === "failed" ? "恢复消息发送失败" : status === "unknown" ? "恢复消息投递未确认" : "已提交恢复消息";
    section("本次干预", event.summary, 180);
    section("当前结果", status === "failed" ? "发送调用失败，详见任务中的实际回执。"
      : status === "unknown" ? "发送结果未知，需核对原会话；不会因此重新执行业务动作。"
      : "恢复消息已提交，触达和恢复效果尚待验证。");
  } else if (kind === "plan") {
    const plan = event.payload.plan as RecoveryStoredTask["plan"];
    title = "已形成修复方案";
    section("方案", plan?.summary ?? event.summary);
    list("主要动作", plan?.actions.map(action => action.summary) ?? []);
    list("需要配合", plan?.owner_cooperation.map(item => item.request) ?? []);
    section("当前状态", task.level === "debug" ? "仅分析，未实际干预。"
      : task.status === "waiting_owner" ? "等待 Owner 完成配合事项并确认方案。" : "按任务已有授权继续处理。", 200);
  } else if (kind === "execution") {
    title = event.payload.success === true ? "修复方案执行完成" : "修复方案执行未完成";
    section("执行结果", event.summary);
    if (event.payload.success === true) section("后续处理", "继续核对原任务的执行效果，方案执行完成不代表业务已恢复。", 200);
  } else {
    title = task.completionReason === "target_unavailable" ? "目标不可用，自愈已结束" : task.completionReason === "user_continued" ? "用户已接手，自愈已让路" : task.status === "completed" ? "任务已完成" : task.status === "failed" ? task.phase === "verify" && task.judgment?.effect === "not_recovered" ? "自愈处理结束" : "任务执行失败" : "任务已停止";
    section("任务总结", task.final_summary ?? "摘要暂不可用，请查看完整任务与处理记录。", 2000);
    if (!task.final_summary) intervention();
    const delivery = task.judgment?.delivery === "confirmed" ? "**消息已触达**：恢复指引已确认出现在原会话中。"
      : "**消息触达未确认**：尚无证据确认恢复指引进入原会话。";
    const response = { acted: "**Bot 已采取行动**：已采纳指引并实际执行。", replied: "**Bot 已回复**：尚未确认实际采取恢复行动。",
      none: "**尚未观察到 Bot 响应**。", unknown: "**Bot 响应无法判断**。" };
    if (task.completionReason !== "user_continued") sections.push(`**【已验证的情况】**\n\n- ${delivery}\n- ${response[task.judgment?.response ?? "unknown"]}`);
    const effects = { recovered: "原任务已恢复", not_recovered: "原任务未恢复", unknown: "恢复效果尚未确认", not_intervened: "未实际干预" };
    const effect = task.judgment?.effect ?? "unknown";
    sections.push(`**【恢复结论】**\n\n${effect === "recovered" ? `<font color="#16803C">${effects[effect]}</font>` : effects[effect]}`);
    if (task.completionReason !== undefined || task.status === "failed" || task.status === "canceled") section("结束说明", event.summary);
  }
  const url = new URL("/insight", baseUrl);
  url.searchParams.set("module", "monitoring"); url.searchParams.set("recoveryTaskId", task.taskId);
  const context = `**Bot**：${clip(task.request.target.bot_id, 150)}`;
  return { title: `会话自愈｜${title}`, text: `## 会话自愈｜${title}\n\n${[context, ...sections].join("\n\n")}\n\n[查看完整任务与处理记录](${url.href})` };
}

/** One optional group channel; no task state, approvals or Agent output depend on delivery. */
export class SessionRecoveryGroupNotifier implements RecoveryActionNotifier {
  constructor(private readonly deps: {
    controlPlaneEnvironment: AgentControlPlaneEnvironment;
    tasks: Pick<RecoveryTaskStore, "find" | "appendNotification">;
    config: NotificationConfigStore;
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
    // Reuse the complete tool audit; uncertain delivery must not be treated as no intervention.
    if (input.kind === "result" && task.interventionStatus === "none") return;
    const target = task.request.target;
    const webhook = config.bot_webhooks?.find(item => item.bot_id === target.bot_id && item.entity_id === target.entity_id)?.webhook ?? config.webhook;
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
      await this.deps.sender.send({ webhook, timeoutSeconds: config.timeout_seconds,
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
