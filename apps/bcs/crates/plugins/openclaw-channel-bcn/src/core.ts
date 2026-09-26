import type { OpenClawPluginApi, OpenClawPluginToolContext } from 'openclaw/plugin-sdk/core';
import { bcsPlugin } from './channel.js';
import { setBcsRuntime } from './runtime.js';
import {
  resolveActiveRunId,
  handleBcsRouteTool,
  handleBcsRouteToolBySession,
  getSessionRoutingMode,
  getSessionTaskGroupInfo,
  handleAssignTask,
  handleTaskMessage,
  handleTaskComplete,
  BCS_ROUTE_TOOL_SCHEMA,
  BCS_ASSIGN_TASK_TOOL_SCHEMA,
  BCS_TASK_MESSAGE_TOOL_SCHEMA,
  BCS_TASK_COMPLETE_TOOL_SCHEMA,
} from './inbound-handler.js';

export const BCS_CORE_TOOL_NAMES = [
  'bcs_route',
  'bcs_assign_task',
  'bcs_send_task_message',
  'bcs_task_complete',
] as const;

export interface BcsCoreRegistrationOptions {
  channelPlugin?: typeof bcsPlugin;
  warnWhenMissingBcsUrl?: boolean;
}

export interface BcsCoreRegistration {
  isSessionSandboxed(sessionKey: string): boolean;
}

let warnedMissingBcsUrl = false;

function getRuntimeLogger(api: OpenClawPluginApi): { warn: (message: string) => void } {
  const logging = (api as any).logging;
  return logging?.getLogger?.('openclaw-channel-bcn') ?? logging?.getLogger?.() ?? console;
}

function warnMissingBcsUrlOnce(api: OpenClawPluginApi): void {
  if (warnedMissingBcsUrl) return;

  void (async () => {
    const cfg = await (api as any).runtime?.config?.loadConfig?.();
    const bcsCfg = cfg?.channels?.bcs;
    if (!bcsCfg || bcsCfg.enabled === false) return;

    const configuredUrl = typeof bcsCfg.bcsUrl === 'string'
      ? bcsCfg.bcsUrl.trim()
      : (process.env.BCS_URL ?? '').trim();
    if (configuredUrl) return;

    warnedMissingBcsUrl = true;
    getRuntimeLogger(api).warn(
      '[openclaw-channel-bcn] plugin loaded, but BCS channel runtime did not start because channels.bcs.bcsUrl or BCS_URL is not configured.',
    );
  })().catch(err => {
    console.warn(`[openclaw-channel-bcn] failed to inspect BCS channel config: ${err instanceof Error ? err.message : err}`);
  });
}

export function registerBcsCore(
  api: OpenClawPluginApi,
  options: BcsCoreRegistrationOptions = {},
): BcsCoreRegistration {
  setBcsRuntime(api.runtime);

  api.registerChannel(options.channelPlugin ?? bcsPlugin);
  if (options.warnWhenMissingBcsUrl ?? true) {
    warnMissingBcsUrlOnce(api);
  }

  const sessionSandboxed = new Map<string, boolean>();

  function rememberSessionSandbox(ctx: OpenClawPluginToolContext): {
    sessionKey: string;
    channel: string;
  } {
    const sessionKey = ctx.sessionKey ?? '';
    const channel = ctx.messageChannel ?? '';

    if (sessionKey && channel === 'bcs') {
      sessionSandboxed.set(sessionKey, ctx.sandboxed ?? false);
    }

    return { sessionKey, channel };
  }

  // 9.4: Register bcs_route tool only for BCS group sessions (not 1:1 or onboarding)
  api.registerTool(
    (ctx: OpenClawPluginToolContext) => {
      const { sessionKey, channel } = rememberSessionSandbox(ctx);

      console.log(`[bcs_route] registerTool probe: channel=${channel}, sessionKey=${sessionKey}, ctxKeys=${Object.keys(ctx).join(',')}`);

      // Only activate for BCS channel
      if (channel !== 'bcs') return null;

      if (!sessionKey) return null;

      // Skip onboarding sessions (no group to route within)
      if (sessionKey.includes('onboarding')) return null;

      // Hide bcs_route when group routing mode is "mention" (structured routing disabled)
      const routingMode = getSessionRoutingMode(sessionKey);
      if (routingMode === 'mention') return null;

      // Hide bcs_route in manager_worker mode (routing is handled by task.dispatch)
      const taskInfo = getSessionTaskGroupInfo(sessionKey);
      if (taskInfo?.groupType === 'manager_worker') return null;

      console.log(`[bcs_route] ACTIVATED for sessionKey=${sessionKey} (routingMode=${routingMode ?? 'default'})`);

      return {
        name: BCS_ROUTE_TOOL_SCHEMA.name,
        label: 'BCS Route',
        description: BCS_ROUTE_TOOL_SCHEMA.description,
        parameters: BCS_ROUTE_TOOL_SCHEMA.parameters,
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const runId = resolveActiveRunId(sessionKey);
          let result;
          if (runId) {
            result = await handleBcsRouteTool(runId, sessionKey, params);
          } else {
            // Fallback: store by sessionKey when run_id is not tracked
            // (e.g. OpenClaw queued the message and used its own run_id)
            console.log(`[bcs_route] No active runId for sessionKey=${sessionKey}, using session-level fallback`);
            result = await handleBcsRouteToolBySession(sessionKey, params);
          }
          return {
            content: [{ type: 'text' as const, text: JSON.stringify(result) }],
            details: result,
          };
        },
      };
    },
    { name: 'bcs_route' },
  );

  // Register bcs_assign_task tool — only for manager bot in manager_worker service groups
  api.registerTool(
    (ctx: OpenClawPluginToolContext) => {
      const { sessionKey, channel } = rememberSessionSandbox(ctx);

      console.log(`[bcs_assign_task] probe: channel=${channel}, sessionKey=${sessionKey}`);

      if (channel !== 'bcs') return null;
      if (!sessionKey) return null;

      const taskInfo = getSessionTaskGroupInfo(sessionKey);
      console.log(`[bcs_assign_task] taskInfo: ${JSON.stringify(taskInfo)}, BCN_BOT_UUID=${process.env.BCN_BOT_UUID}`);
      if (!taskInfo || taskInfo.groupType !== 'manager_worker') return null;

      // Manager check: prefer recipient_role (new BCS), fall back to
      // originator.includes(botUuid) for older BCS that didn't surface role.
      const botUuid = process.env.BCN_BOT_UUID;
      const isManager = taskInfo.recipientRole
        ? taskInfo.recipientRole === 'manager'
        : (botUuid ? taskInfo.originator.includes(botUuid) : false);
      if (!isManager) return null;

      return {
        name: BCS_ASSIGN_TASK_TOOL_SCHEMA.name,
        label: 'BCS Assign Task',
        description: BCS_ASSIGN_TASK_TOOL_SCHEMA.description,
        parameters: BCS_ASSIGN_TASK_TOOL_SCHEMA.parameters,
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const result = await handleAssignTask(sessionKey, params);
          return {
            content: [{ type: 'text' as const, text: JSON.stringify(result) }],
            details: result,
          };
        },
      };
    },
    { name: 'bcs_assign_task' },
  );

  // Register bcs_send_task_message tool — only for worker bot in manager_worker service groups
  api.registerTool(
    (ctx: OpenClawPluginToolContext) => {
      const { sessionKey, channel } = rememberSessionSandbox(ctx);

      if (channel !== 'bcs') return null;
      if (!sessionKey) return null;

      const taskInfo = getSessionTaskGroupInfo(sessionKey);
      if (!taskInfo || taskInfo.groupType !== 'manager_worker') return null;
      if (taskInfo.recipientRole !== 'worker') return null;

      return {
        name: BCS_TASK_MESSAGE_TOOL_SCHEMA.name,
        label: 'BCS Send Task Message',
        description: BCS_TASK_MESSAGE_TOOL_SCHEMA.description,
        parameters: BCS_TASK_MESSAGE_TOOL_SCHEMA.parameters,
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const result = await handleTaskMessage(sessionKey, params);
          return {
            content: [{ type: 'text' as const, text: JSON.stringify(result) }],
            details: result,
          };
        },
      };
    },
    { name: 'bcs_send_task_message' },
  );

  // Register bcs_task_complete tool — only for manager bot in manager_worker service groups
  api.registerTool(
    (ctx: OpenClawPluginToolContext) => {
      const { sessionKey, channel } = rememberSessionSandbox(ctx);

      if (channel !== 'bcs') return null;
      if (!sessionKey) return null;

      const taskInfo = getSessionTaskGroupInfo(sessionKey);
      if (!taskInfo || taskInfo.groupType !== 'manager_worker') return null;

      // Manager check: prefer recipient_role (new BCS), fall back to
      // originator.includes(botUuid) for older BCS that didn't surface role.
      const botUuid = process.env.BCN_BOT_UUID;
      const isManager = taskInfo.recipientRole
        ? taskInfo.recipientRole === 'manager'
        : (botUuid ? taskInfo.originator.includes(botUuid) : false);
      if (!isManager) return null;

      return {
        name: BCS_TASK_COMPLETE_TOOL_SCHEMA.name,
        label: 'BCS Task Complete',
        description: BCS_TASK_COMPLETE_TOOL_SCHEMA.description,
        parameters: BCS_TASK_COMPLETE_TOOL_SCHEMA.parameters,
        async execute(_toolCallId: string, params: Record<string, unknown>) {
          const result = await handleTaskComplete(sessionKey, params);
          return {
            content: [{ type: 'text' as const, text: JSON.stringify(result) }],
            details: result,
          };
        },
      };
    },
    { name: 'bcs_task_complete' },
  );

  return {
    isSessionSandboxed(sessionKey: string) {
      return sessionSandboxed.get(sessionKey) ?? false;
    },
  };
}
