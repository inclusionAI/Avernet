/**
 * CLI command handler for `/workflow webhook` subcommands.
 *
 * Provides register, list, unregister, update, and test operations
 * for webhook triggers.
 */
import type { WebhookTrigger } from "./types.js";
import type { WebhookTriggerRepository } from "../db/repositories/webhook-trigger-repository.js";

// ── Types ──

export type WebhookCommand =
  | { subcommand: "register"; triggerId?: string; workflowId: string; packId?: string; secret?: string; mapping?: string; allowedIps?: string; description?: string }
  | { subcommand: "list"; workflowId?: string }
  | { subcommand: "unregister"; triggerId: string }
  | { subcommand: "update"; triggerId: string; workflowId?: string; packId?: string; secret?: string; mapping?: string; allowedIps?: string; disabled?: boolean; description?: string }
  | { subcommand: "test"; triggerId: string; body?: string }
  | { subcommand: "help" };

export type WebhookCommandDeps = {
  triggerStore: WebhookTriggerRepository | null;
  webhookEnabled: boolean;
  /** Callback to check if a workflow exists. */
  workflowExists: (workflowId: string) => boolean;
  /** Callback to check if a pack exists. */
  packExists: (packId: string) => boolean;
  /** API base URL for constructing webhook URLs. */
  apiBaseUrl: string;
};

// ── Usage ──

const WEBHOOK_USAGE = [
  "用法: /workflow webhook <subcommand> [options]",
  "",
  "子命令:",
  "  register    注册 Webhook 触发器",
  "  list        列出触发器",
  "  unregister  删除触发器",
  "  update      更新触发器配置",
  "  test        测试 Payload 映射（不触发工作流）",
  "  help        显示帮助",
  "",
  "示例:",
  "  /workflow webhook register --workflow my-wf",
  '  /workflow webhook register --workflow my-wf --secret my-secret --mapping \'{"topic":"$.body.topic"}\'',
  "  /workflow webhook register --workflow my-wf --allowed-ips '10.0.0.0/8,192.168.1.0/24'",
  "  /workflow webhook list",
  "  /workflow webhook list --workflow my-wf",
  "  /workflow webhook unregister trg_abc12345",
  "  /workflow webhook update trg_abc12345 --secret new-secret",
  "  /workflow webhook update trg_abc12345 --disabled",
  '  /workflow webhook test trg_abc12345 --body \'{"topic":"alert"}\'',
].join("\n");

// ── Command Parsing ──

export function parseWebhookArgs(parts: string[]): WebhookCommand {
  const subcommand = parts[0]?.toLowerCase();

  switch (subcommand) {
    case "register":
      return parseWebhookRegister(parts.slice(1));
    case "list":
      return parseWebhookList(parts.slice(1));
    case "unregister":
      return parseWebhookUnregister(parts.slice(1));
    case "update":
      return parseWebhookUpdate(parts.slice(1));
    case "test":
      return parseWebhookTest(parts.slice(1));
    case "help":
    case undefined:
    case "":
      return { subcommand: "help" };
    default:
      throw new Error(`未知子命令: ${subcommand}\n\n${WEBHOOK_USAGE}`);
  }
}

function parseWebhookRegister(parts: string[]): WebhookCommand {
  const params = parseNamedParams(parts);

  const workflowId = params.workflow;
  if (!workflowId) throw new Error("--workflow 是必填项");

  const triggerId = params["trigger-id"] ?? undefined;
  const packId = params.pack ?? undefined;
  const secret = params.secret ?? undefined;
  const mapping = params.mapping ?? undefined;
  const allowedIps = params["allowed-ips"] ?? undefined;
  const description = params.description ?? undefined;

  // Validate triggerId format if provided
  if (triggerId && !/^[a-zA-Z0-9_-]{1,64}$/.test(triggerId)) {
    throw new Error("--trigger-id 格式无效，仅允许字母、数字、下划线、连字符，最多64字符");
  }

  return {
    subcommand: "register",
    triggerId,
    workflowId,
    packId,
    secret,
    mapping,
    allowedIps,
    description,
  };
}

function parseWebhookList(parts: string[]): WebhookCommand {
  const params = parseNamedParams(parts);
  return {
    subcommand: "list",
    ...(params.workflow ? { workflowId: params.workflow } : {}),
  };
}

function parseWebhookUnregister(parts: string[]): WebhookCommand {
  const triggerId = parts[0];
  if (!triggerId) throw new Error("用法: /workflow webhook unregister <triggerId>");
  return { subcommand: "unregister", triggerId };
}

function parseWebhookUpdate(parts: string[]): WebhookCommand {
  const triggerId = parts[0];
  if (!triggerId || triggerId.startsWith("--")) throw new Error("用法: /workflow webhook update <triggerId> [options]");

  const params = parseNamedParams(parts.slice(1));

  return {
    subcommand: "update",
    triggerId,
    workflowId: params.workflow ?? undefined,
    packId: params.pack ?? undefined,
    secret: params.secret ?? undefined,
    mapping: params.mapping ?? undefined,
    allowedIps: params["allowed-ips"] ?? undefined,
    disabled: params.disabled === "true" || params.disabled === "",
    description: params.description ?? undefined,
  };
}

function parseWebhookTest(parts: string[]): WebhookCommand {
  const triggerId = parts[0];
  if (!triggerId) throw new Error("用法: /workflow webhook test <triggerId> [--body '<json>']");

  const params = parseNamedParams(parts.slice(1));

  return {
    subcommand: "test",
    triggerId,
    body: params.body ?? undefined,
  };
}

function parseNamedParams(parts: string[]): Record<string, string> {
  const params: Record<string, string> = {};
  let i = 0;
  while (i < parts.length) {
    if (parts[i].startsWith("--")) {
      const key = parts[i].substring(2);
      const value = parts[i + 1];
      if (!value || value.startsWith("--")) {
        // Flag-style option (e.g., --disabled)
        params[key] = "";
        i += 1;
      } else {
        params[key] = value;
        i += 2;
      }
    } else {
      i++;
    }
  }
  return params;
}

// ── Command Execution ──

export async function handleWebhookCommand(
  cmd: WebhookCommand,
  deps: WebhookCommandDeps,
): Promise<string> {
  if (!deps.webhookEnabled) {
    return "Webhook 未启用。请在 application.yaml 中设置 webhook.enabled: true 或设置 WEBHOOK_ENABLED=true";
  }

  if (!deps.triggerStore) {
    return "Webhook 不可用，数据库错误";
  }

  switch (cmd.subcommand) {
    case "register":
      return handleWebhookRegister(cmd, deps);
    case "list":
      return handleWebhookList(cmd, deps);
    case "unregister":
      return handleWebhookUnregister(cmd, deps);
    case "update":
      return handleWebhookUpdate(cmd, deps);
    case "test":
      return handleWebhookTest(cmd, deps);
    case "help":
      return WEBHOOK_USAGE;
  }
}

async function handleWebhookRegister(
  cmd: WebhookCommand & { subcommand: "register" },
  deps: WebhookCommandDeps,
): Promise<string> {
  // Validate workflow exists
  if (!deps.workflowExists(cmd.workflowId)) {
    return `工作流不存在: ${cmd.workflowId}`;
  }

  // Validate pack if provided
  if (cmd.packId && !deps.packExists(cmd.packId)) {
    return `Pack 不存在: ${cmd.packId}`;
  }

  // Parse mapping JSON if provided
  let payloadMapping: Record<string, string> | undefined;
  if (cmd.mapping) {
    try {
      payloadMapping = JSON.parse(cmd.mapping);
    } catch {
      return `无效的 mapping JSON: ${cmd.mapping}`;
    }
  }

  // Parse allowed IPs if provided
  let allowedIps: string[] | undefined;
  if (cmd.allowedIps) {
    allowedIps = cmd.allowedIps.split(",").map((s) => s.trim()).filter(Boolean);
  }

  try {
    const trigger = await deps.triggerStore!.create({
      triggerId: cmd.triggerId,
      workflowId: cmd.workflowId,
      packId: cmd.packId,
      secret: cmd.secret,
      payloadMapping: payloadMapping ?? null,
      allowedIps: allowedIps ?? null,
      description: cmd.description,
    });

    const webhookUrl = `${deps.apiBaseUrl}/api/webhooks/${trigger.trigger_id}`;

    return [
      "Webhook 触发器已注册",
      `  触发器ID: ${trigger.trigger_id}`,
      `  工作流: ${trigger.workflow_id}`,
      `  Pack: ${trigger.pack_id ?? "(默认)"}`,
      `  签名验证: ${trigger.secret ? "已配置" : "未配置"}`,
      `  Payload映射: ${trigger.payload_mapping ? "已配置" : "无"}`,
      `  IP白名单: ${trigger.allowed_ips ? "已配置" : "无"}`,
      `  状态: 已启用`,
      `  Webhook URL: ${webhookUrl}`,
    ].join("\n");
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return `注册触发器失败: ${msg}`;
  }
}

async function handleWebhookList(
  cmd: WebhookCommand & { subcommand: "list" },
  deps: WebhookCommandDeps,
): Promise<string> {
  let triggers: WebhookTrigger[];

  if (cmd.workflowId) {
    triggers = await deps.triggerStore!.findByWorkflowId(cmd.workflowId);
  } else {
    triggers = await deps.triggerStore!.findAll();
  }

  if (triggers.length === 0) {
    return cmd.workflowId
      ? `工作流 '${cmd.workflowId}' 没有 Webhook 触发器`
      : "没有 Webhook 触发器";
  }

  const header = ["触发器ID", "工作流", "Pack", "签名", "状态", "描述"];
  const rows = triggers.map((t) => [
    t.trigger_id,
    t.workflow_id,
    t.pack_id ?? "-",
    t.secret ? "✓" : "-",
    t.enabled === 1 ? "启用" : "禁用",
    t.description ?? "-",
  ]);

  const allRows = [header, ...rows];
  const colWidths = header.map((_, colIdx) =>
    Math.max(...allRows.map((row) => row[colIdx].length)),
  );

  const lines = allRows.map((row) =>
    row.map((cell, i) => cell.padEnd(colWidths[i])).join("  "),
  );

  return lines.join("\n");
}

async function handleWebhookUnregister(
  cmd: WebhookCommand & { subcommand: "unregister" },
  deps: WebhookCommandDeps,
): Promise<string> {
  const trigger = await deps.triggerStore!.getByTriggerId(cmd.triggerId);
  if (!trigger) return `触发器不存在: ${cmd.triggerId}`;

  const deleted = await deps.triggerStore!.delete(cmd.triggerId);
  if (!deleted) return `删除触发器失败: ${cmd.triggerId}`;

  return `Webhook 触发器已删除\n  触发器ID: ${cmd.triggerId}\n  工作流: ${trigger.workflow_id}`;
}

async function handleWebhookUpdate(
  cmd: WebhookCommand & { subcommand: "update" },
  deps: WebhookCommandDeps,
): Promise<string> {
  const trigger = await deps.triggerStore!.getByTriggerId(cmd.triggerId);
  if (!trigger) return `触发器不存在: ${cmd.triggerId}`;

  // Parse mapping JSON if provided
  let payloadMapping: Record<string, string> | null | undefined;
  if (cmd.mapping !== undefined) {
    try {
      payloadMapping = cmd.mapping ? JSON.parse(cmd.mapping) : null;
    } catch {
      return `无效的 mapping JSON: ${cmd.mapping}`;
    }
  }

  // Parse allowed IPs if provided
  let allowedIps: string[] | null | undefined;
  if (cmd.allowedIps !== undefined) {
    allowedIps = cmd.allowedIps ? cmd.allowedIps.split(",").map((s) => s.trim()).filter(Boolean) : null;
  }

  const updated = await deps.triggerStore!.update(cmd.triggerId, {
    workflowId: cmd.workflowId,
    packId: cmd.packId,
    secret: cmd.secret,
    payloadMapping: payloadMapping,
    allowedIps: allowedIps,
    enabled: cmd.disabled ? false : undefined,
    description: cmd.description,
  });

  if (!updated) return `更新触发器失败: ${cmd.triggerId}`;

  return [
    "Webhook 触发器已更新",
    `  触发器ID: ${updated.trigger_id}`,
    `  工作流: ${updated.workflow_id}`,
    `  状态: ${updated.enabled === 1 ? "启用" : "禁用"}`,
  ].join("\n");
}

async function handleWebhookTest(
  cmd: WebhookCommand & { subcommand: "test" },
  deps: WebhookCommandDeps,
): Promise<string> {
  const trigger = await deps.triggerStore!.getByTriggerId(cmd.triggerId);
  if (!trigger) return `触发器不存在: ${cmd.triggerId}`;

  const body = cmd.body ? JSON.parse(cmd.body) : {};
  const headers: Record<string, string> = {};
  const payloadMapping = trigger.payload_mapping ? JSON.parse(trigger.payload_mapping) : {};

  // Import mapPayload lazily to avoid circular deps
  const { mapPayload } = await import("./payload-mapper.js");
  const mappedParams = mapPayload(payloadMapping, body, headers);

  return [
    "Payload 映射测试 (dry-run)",
    `  触发器ID: ${trigger.trigger_id}`,
    `  工作流: ${trigger.workflow_id}`,
    `  原始 Body: ${cmd.body ?? "{}"}`,
    `  映射规则: ${trigger.payload_mapping ?? "无"}`,
    `  映射结果:`,
    ...Object.entries(mappedParams).map(([k, v]) => `    ${k} = ${v}`),
  ].join("\n");
}