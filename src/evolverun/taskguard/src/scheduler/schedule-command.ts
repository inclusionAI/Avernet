/**
 * CLI command handler for `/workflow schedule` subcommands.
 *
 * Provides create, list, enable, disable, trigger, delete, and help operations
 * for scheduled triggers.
 */
import type { ScheduledTrigger } from "./types.js";
import { ScheduledTriggerRepository } from "./trigger-store.js";
import { validateCronExpression, validateTimezone, computeNextFireTime } from "./cron-parser.js";

// ── Types ──

export type ScheduleCommand =
  | { subcommand: "create"; workflowId: string; packId: string; cronExpression: string; timezone: string; paramsJson: string | null; maxConcurrent: number }
  | { subcommand: "list"; workflowId?: string }
  | { subcommand: "enable"; triggerId: string }
  | { subcommand: "disable"; triggerId: string }
  | { subcommand: "trigger"; triggerId: string }
  | { subcommand: "delete"; triggerId: string }
  | { subcommand: "help" };

export type ScheduleCommandDeps = {
  triggerStore: ScheduledTriggerRepository | null;
  schedulerEnabled: boolean;
  schedulerRunning: boolean;
  /** Callback to manually fire a trigger. Returns flowId or null. */
  fireTrigger: (trigger: ScheduledTrigger) => Promise<string | null>;
  /** Callback to check if a workflow exists. */
  workflowExists: (workflowId: string) => boolean;
  /** Callback to check if a pack exists. */
  packExists: (packId: string) => boolean;
};

// ── Command Parsing ──

const SCHEDULE_USAGE = [
  "用法: /workflow schedule <subcommand> [options]",
  "",
  "子命令:",
  "  create   创建定时触发器",
  "  list     列出触发器",
  "  enable   启用触发器",
  "  disable  禁用触发器",
  "  trigger  手动触发一次",
  "  delete   删除触发器",
  "  help     显示帮助",
  "",
  "示例:",
  '  /workflow schedule create --workflow my-wf --pack my-pack --cron "*/5 * * * *" --tz UTC',
  '  /workflow schedule create --workflow my-wf --pack my-pack --cron "0 9 * * 1-5" --tz Asia/Shanghai --params \'{"key":"value"}\' --max-concurrent 2',
  "  /workflow schedule list",
  "  /workflow schedule list --workflow my-wf",
  "  /workflow schedule enable trig_a1b2c3d4",
  "  /workflow schedule disable trig_a1b2c3d4",
  "  /workflow schedule trigger trig_a1b2c3d4",
  "  /workflow schedule delete trig_a1b2c3d4",
].join("\n");

export function parseScheduleArgs(parts: string[]): ScheduleCommand {
  const subcommand = parts[0]?.toLowerCase();

  switch (subcommand) {
    case "create":
      return parseScheduleCreate(parts.slice(1));
    case "list":
      return parseScheduleList(parts.slice(1));
    case "enable":
      return parseScheduleEnable(parts.slice(1));
    case "disable":
      return parseScheduleDisable(parts.slice(1));
    case "trigger":
      return parseScheduleTrigger(parts.slice(1));
    case "delete":
      return parseScheduleDelete(parts.slice(1));
    case "help":
    case undefined:
    case "":
      return { subcommand: "help" };
    default:
      throw new Error(`未知子命令: ${subcommand}\n\n${SCHEDULE_USAGE}`);
  }
}

function parseScheduleCreate(parts: string[]): ScheduleCommand {
  const params = parseNamedParams(parts);

  const workflowId = params.workflow;
  if (!workflowId) throw new Error("--workflow 是必填项");

  const packId = params.pack ?? workflowId;
  const cronExpression = params.cron;
  if (!cronExpression) throw new Error("--cron 是必填项");

  const timezone = params.tz ?? "UTC";
  const maxConcurrent = params["max-concurrent"] ? parseInt(params["max-concurrent"], 10) : 1;
  const paramsJson = params.params ?? null;

  if (isNaN(maxConcurrent) || maxConcurrent < 0) {
    throw new Error("--max-concurrent 必须是非负整数");
  }

  return {
    subcommand: "create",
    workflowId,
    packId,
    cronExpression,
    timezone,
    paramsJson,
    maxConcurrent,
  };
}

function parseScheduleList(parts: string[]): ScheduleCommand {
  const params = parseNamedParams(parts);
  return {
    subcommand: "list",
    ...(params.workflow ? { workflowId: params.workflow } : {}),
  };
}

function parseScheduleEnable(parts: string[]): ScheduleCommand {
  const triggerId = parts[0];
  if (!triggerId) throw new Error("用法: /workflow schedule enable <triggerId>");
  return { subcommand: "enable", triggerId };
}

function parseScheduleDisable(parts: string[]): ScheduleCommand {
  const triggerId = parts[0];
  if (!triggerId) throw new Error("用法: /workflow schedule disable <triggerId>");
  return { subcommand: "disable", triggerId };
}

function parseScheduleTrigger(parts: string[]): ScheduleCommand {
  const triggerId = parts[0];
  if (!triggerId) throw new Error("用法: /workflow schedule trigger <triggerId>");
  return { subcommand: "trigger", triggerId };
}

function parseScheduleDelete(parts: string[]): ScheduleCommand {
  const triggerId = parts[0];
  if (!triggerId) throw new Error("用法: /workflow schedule delete <triggerId>");
  return { subcommand: "delete", triggerId };
}

function parseNamedParams(parts: string[]): Record<string, string> {
  const params: Record<string, string> = {};
  let i = 0;
  while (i < parts.length) {
    if (parts[i].startsWith("--")) {
      const key = parts[i].substring(2);
      const value = parts[i + 1];
      if (!value || value.startsWith("--")) {
        throw new Error(`--${key} 需要一个值`);
      }
      params[key] = value;
      i += 2;
    } else {
      i++;
    }
  }
  return params;
}

// ── Command Execution ──

export async function handleScheduleCommand(
  cmd: ScheduleCommand,
  deps: ScheduleCommandDeps,
): Promise<string> {
  // Guard: scheduler must be enabled
  if (!deps.schedulerEnabled) {
    return "调度器未启用。请在 application.yaml 中设置 scheduler.enabled: true 或设置 SCHEDULER_ENABLED=true";
  }

  // Guard: trigger store must be available (DB initialized)
  if (!deps.triggerStore) {
    return "调度器不可用，数据库错误";
  }

  switch (cmd.subcommand) {
    case "create":
      return handleScheduleCreate(cmd, deps);
    case "list":
      return handleScheduleList(cmd, deps);
    case "enable":
      return handleScheduleEnable(cmd, deps);
    case "disable":
      return handleScheduleDisable(cmd, deps);
    case "trigger":
      return handleScheduleTrigger(cmd, deps);
    case "delete":
      return handleScheduleDelete(cmd, deps);
    case "help":
      return SCHEDULE_USAGE;
  }
}

async function handleScheduleCreate(
  cmd: ScheduleCommand & { subcommand: "create" },
  deps: ScheduleCommandDeps,
): Promise<string> {
  // Validate cron expression
  const cronResult = validateCronExpression(cmd.cronExpression);
  if (!cronResult.valid) {
    return `无效的 cron 表达式: ${cronResult.error}`;
  }

  // Validate timezone
  if (!validateTimezone(cmd.timezone)) {
    return `无效的时区: ${cmd.timezone}`;
  }

  // Validate workflow exists
  if (!deps.workflowExists(cmd.workflowId)) {
    return `工作流不存在: ${cmd.workflowId}`;
  }

  // Validate pack exists
  if (!deps.packExists(cmd.packId)) {
    return `Pack 不存在: ${cmd.packId}`;
  }

  // Validate params JSON if provided
  if (cmd.paramsJson) {
    try {
      JSON.parse(cmd.paramsJson);
    } catch {
      return `无效的 params JSON: ${cmd.paramsJson}`;
    }
  }

  try {
    const trigger = await deps.triggerStore!.create({
      workflowId: cmd.workflowId,
      packId: cmd.packId,
      cronExpression: cmd.cronExpression,
      timezone: cmd.timezone,
      paramsJson: cmd.paramsJson,
      maxConcurrent: cmd.maxConcurrent,
    });

    const nextFire = trigger.next_fire_time
      ? new Date(trigger.next_fire_time).toISOString()
      : "未计算";

    return [
      "定时触发器已创建",
      `  触发器ID: ${trigger.trigger_id}`,
      `  工作流: ${trigger.workflow_id}`,
      `  Pack: ${trigger.pack_id}`,
      `  Cron: ${trigger.cron_expression}`,
      `  时区: ${trigger.timezone}`,
      `  最大并发: ${trigger.max_concurrent}`,
      `  下次触发: ${nextFire}`,
      `  状态: 已启用`,
    ].join("\n");
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return `创建触发器失败: ${msg}`;
  }
}

async function handleScheduleList(
  cmd: ScheduleCommand & { subcommand: "list" },
  deps: ScheduleCommandDeps,
): Promise<string> {
  let triggers: ScheduledTrigger[];

  if (cmd.workflowId) {
    triggers = await deps.triggerStore!.listByWorkflow(cmd.workflowId);
  } else {
    triggers = await deps.triggerStore!.listEnabled();
    // listEnabled only returns enabled; we also need disabled for a full list
    // For now, listEnabled gives the primary view; a full listAll would be better
  }

  if (triggers.length === 0) {
    return cmd.workflowId
      ? `工作流 '${cmd.workflowId}' 没有定时触发器`
      : "没有定时触发器";
  }

  const header = ["触发器ID", "工作流", "Pack", "Cron", "时区", "并发", "状态", "下次触发"];
  const rows = triggers.map(formatTriggerRow);
  const allRows = [header, ...rows];

  // Simple column alignment
  const colWidths = header.map((_, colIdx) =>
    Math.max(...allRows.map((row) => row[colIdx].length)),
  );

  const lines = allRows.map((row) =>
    row.map((cell, i) => cell.padEnd(colWidths[i])).join("  "),
  );

  return lines.join("\n");
}

async function handleScheduleEnable(
  cmd: ScheduleCommand & { subcommand: "enable" },
  deps: ScheduleCommandDeps,
): Promise<string> {
  const trigger = await deps.triggerStore!.getById(cmd.triggerId);
  if (!trigger) return `触发器不存在: ${cmd.triggerId}`;
  if (trigger.enabled === 1) return `触发器 ${cmd.triggerId} 已经是启用状态`;

  const updated = await deps.triggerStore!.enable(cmd.triggerId);
  const nextFire = updated?.next_fire_time
    ? new Date(updated.next_fire_time).toISOString()
    : "未计算";

  return `触发器 ${cmd.triggerId} 已启用\n  下次触发: ${nextFire}`;
}

async function handleScheduleDisable(
  cmd: ScheduleCommand & { subcommand: "disable" },
  deps: ScheduleCommandDeps,
): Promise<string> {
  const trigger = await deps.triggerStore!.getById(cmd.triggerId);
  if (!trigger) return `触发器不存在: ${cmd.triggerId}`;
  if (trigger.enabled === 0) return `触发器 ${cmd.triggerId} 已经是禁用状态`;

  await deps.triggerStore!.disable(cmd.triggerId);
  return `触发器 ${cmd.triggerId} 已禁用`;
}

async function handleScheduleTrigger(
  cmd: ScheduleCommand & { subcommand: "trigger" },
  deps: ScheduleCommandDeps,
): Promise<string> {
  const trigger = await deps.triggerStore!.getById(cmd.triggerId);
  if (!trigger) return `触发器不存在: ${cmd.triggerId}`;

  const flowId = await deps.fireTrigger(trigger);
  if (!flowId) return `手动触发失败: 工作流 ${trigger.workflow_id} 启动返回空`;

  return `手动触发成功\n  触发器: ${cmd.triggerId}\n  工作流: ${trigger.workflow_id}\n  FlowID: ${flowId}`;
}

async function handleScheduleDelete(
  cmd: ScheduleCommand & { subcommand: "delete" },
  deps: ScheduleCommandDeps,
): Promise<string> {
  const trigger = await deps.triggerStore!.getById(cmd.triggerId);
  if (!trigger) return `触发器不存在: ${cmd.triggerId}`;

  const deleted = await deps.triggerStore!.delete(cmd.triggerId);
  if (!deleted) return `删除触发器失败: ${cmd.triggerId}`;

  return `触发器 ${cmd.triggerId} 已删除\n  工作流: ${trigger.workflow_id}\n  Cron: ${trigger.cron_expression}`;
}

// ── Helpers ──

function formatTriggerRow(trigger: ScheduledTrigger): string[] {
  const status = trigger.enabled === 1 ? "启用" : "禁用";
  const nextFire = trigger.next_fire_time
    ? new Date(trigger.next_fire_time).toISOString()
    : "禁用";
  return [
    trigger.trigger_id,
    trigger.workflow_id,
    trigger.pack_id,
    trigger.cron_expression,
    trigger.timezone,
    String(trigger.max_concurrent),
    status,
    nextFire,
  ];
}