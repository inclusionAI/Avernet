/**
 * Shared dispatch logic — platform-agnostic workflow command execution.
 *
 * Both the OpenClaw plugin (index.ts) and the MCP Server entry point
 * (platform/mcp-entry.ts) use `executeAction` to route parsed commands
 * to the appropriate handler.
 *
 * This module is intentionally free of any OpenClaw Plugin SDK imports.
 * It depends only on ControllerDeps and ControllerAction, which are
 * platform-agnostic types from controller.ts and types.ts.
 *
 * @module dispatch
 */

import {
  handleRun,
  handleConfirm,
  handleRevise,
  handleReject,
  handleResume,
  handleState,
  handleDebug,
  handleInspect,
  handleFlows,
  handleFlowsCleanup,
  handleRepairLegacyIdentity,
  handleRepairExternalPackPin,
  handleFlowExport,
  handleFlowImport,
  handleRetry,
  handleSubmit,
  isAsyncExecutionActive,
  handleSkip,
  handleLogs,
  handleBcsCallback,
  handleAsyncCallback,
  handleList,
  handleDetail,
  handleValidate,
  handlePacks,
  handlePackInspect,
  handlePackValidate,
  handleCutoverCheck,
  handleReopen,
  handleTest,
  handleHelp,
  handleInjectNodes,
  handleSynthesize,
  handleDebugSegment,
  type ControllerDeps,
} from "./controller.js";
import {
  handleDeploy,
  handleInstallPack,
  handlePull,
  handleRollback,
  handleHistory,
  handleStatus,
  handleShare,
  handleUnshare,
  type VersionCommandDeps,
} from "./controller/version-commands.js";

import { loadConfig } from "./config/loader.js";

/** Adapt ControllerDeps to VersionCommandDeps. */
function toVersionDeps(deps: ControllerDeps): VersionCommandDeps {
  // Git config: prefer values already cached in ControllerDeps (set once at init
  // from loadConfig), fallback to re-reading loadConfig() for backward compat.
  // Previously this ALWAYS re-called loadConfig() per command invocation, which had two problems:
  //   1. The catch block silently swallowed errors, leaving git fields as undefined
  //   2. loadConfig() reads from disk each time — unnecessary I/O and fragile
  let gitRemoteUrl: string | undefined = deps.gitRemoteUrl;
  let gitUsername: string | undefined = deps.gitUsername;
  let gitToken: string | undefined = deps.gitToken;
  let gitEmail: string | undefined = deps.gitEmail;

  // Fallback: if ControllerDeps didn't carry git fields (old buildDeps), try loadConfig
  if (!gitRemoteUrl && !gitUsername && !gitToken) {
    try {
      const gitConfig = loadConfig().app.git;
      gitRemoteUrl = gitConfig?.remoteUrl || undefined;
      gitUsername = gitConfig?.username || undefined;
      gitToken = process.env.CLAWMIND_GIT_TOKEN || gitConfig?.token || undefined;
      gitEmail = process.env.CLAWMIND_GIT_EMAIL || gitConfig?.email || undefined;
    } catch (err) {
      console.error(`[version-deps] loadConfig().app.git fallback FAILED: ${err instanceof Error ? err.message : err}. Git versioning will not work.`);
    }
  }

  if (!gitRemoteUrl) {
    console.warn(`[version-deps] gitRemoteUrl is empty. Git push/pull/clone will fail. Check git.remoteUrl in application.yaml or CLAWMIND_GIT_TOKEN env var.`);
  }

  return {
    db: deps.db as any,
    clawWebBaseUrl: deps.clawWebBaseUrl,
    signatureKey: deps.signatureKey,
    botId: deps.botId,
    ownerId: deps.ownerId,
    resolvedWorkflows: deps.resolvedWorkflows as any[],
    resolvedPacks: deps.resolvedPacks as any[],
    failedWorkflows: deps.failedWorkflows,
    packsRoot: deps.packsRoot,
    gitRemoteUrl,
    gitUsername,
    gitToken,
    gitEmail,
    facadeBindingRepo: deps.facadeBindingRepo,
  };
}
import type { ControllerAction, WorkflowCommandSurface, ExecutionMode } from "./types.js";

// Re-export ExecutionMode for convenience
export type { ExecutionMode } from "./types.js";
import { parseScheduleArgs, handleScheduleCommand, type ScheduleCommandDeps } from "./scheduler/schedule-command.js";
import { parseWebhookArgs, handleWebhookCommand, type WebhookCommandDeps } from "./webhook/webhook-command.js";
import { tokenizeCommand } from "./command-parser.js";
import { handleDevWorkflowCallback } from "./dev-workflow-callback.js";

// ── Execution mode helpers ──

/** Detect if a sessionKey originates from a group chat (DingTalk group, BCS group, etc.). */
export function isGroupSessionKey(sessionKey: string | undefined): boolean {
  if (!sessionKey) return false;
  return sessionKey.includes(":group:");
}

/** Extract the group/conversation ID from a group sessionKey like `agent:main:dingtalk:group:cidXXX`. */
export function extractGroupIdFromSessionKey(sessionKey: string | undefined): string | undefined {
  if (!sessionKey) return undefined;
  const match = sessionKey.match(/:group:([^:]+)/);
  return match?.[1];
}

/**
 * Determine execution mode from delivery context and session key.
 * - "bcs-group": BCS platform group chat (messageChannel === "bcs")
 * - "dingtalk-group": DingTalk group chat (sessionKey contains ":group:" but NOT from BCS)
 * - "private": Direct/private chat (default)
 */
export function resolveExecutionMode(
  deliveryContext: Record<string, unknown> | undefined,
  sessionKey: string | undefined,
): ExecutionMode {
  if (deliveryContext?.messageChannel === "bcs") {
    return "bcs-group";
  }
  if (isGroupSessionKey(sessionKey)) {
    return "dingtalk-group";
  }
  return "private";
}

// ── Dispatch options ──

/**
 * Optional callbacks for building platform-specific command deps.
 *
 * Schedule and webhook commands require access to platform-specific state
 * (trigger stores, schedulers, API base URLs) that lives in the host module.
 * Instead of coupling dispatch.ts to that state, callers provide builders.
 */
export interface DispatchDepsBuilders {
  /** Build ScheduleCommandDeps from ControllerDeps. Required for `schedule` command support. */
  buildScheduleDeps?: (deps: ControllerDeps) => ScheduleCommandDeps;
  /** Build WebhookCommandDeps from ControllerDeps. Required for `webhook` command support. */
  buildWebhookDeps?: (deps: ControllerDeps) => WebhookCommandDeps;
}

// ── Main dispatch ──

/**
 * Execute a parsed ControllerAction against a ControllerDeps instance.
 *
 * This is the shared entry point used by both OpenClaw (index.ts) and
 * MCP Server (mcp-entry.ts). It routes the action to the appropriate
 * handler function from controller.ts.
 *
 * @param action - Parsed command action (from parseWorkflowCommandWithFacade)
 * @param deps - Controller dependencies (from PlatformAdapter + extras)
 * @param deliveryContext - Optional platform delivery context (e.g., BCS group info)
 * @param commandSurface - Whether this came from a facade or raw workflow command
 * @param sessionKey - Optional session key for execution mode detection
 * @param depsBuilders - Optional builders for schedule/webhook deps (platform-specific state)
 */
export async function executeAction(
  action: ControllerAction,
  deps: ControllerDeps,
  deliveryContext?: Record<string, unknown>,
  commandSurface: WorkflowCommandSurface = { type: "workflow" },
  sessionKey?: string,
  depsBuilders?: DispatchDepsBuilders,
): Promise<string> {
  switch (action.action) {
    case "help":
      return handleHelp(action.workflowId, deps);
    case "run": {
      const executionMode = resolveExecutionMode(deliveryContext, sessionKey);
      const bcsGroupId = executionMode !== "private"
        ? (deliveryContext?.bcsGroupId as string | undefined) ?? extractGroupIdFromSessionKey(sessionKey)
        : undefined;
      const asyncRun = loadConfig().app.execution.asyncRun;
      const flowId = await handleRun(deps, {
        workflowId: action.workflowId,
        params: action.params,
        message: action.message,
        files: action.files,
        executionMode,
        bcsGroupId,
        commandSurface,
        debug: action.debug,
        startAsync: asyncRun,
        chatInjectLevel: action.chatInjectLevel,
      });
      if (isAsyncExecutionActive(flowId)) {
        return (
          `流程已异步启动 (workflow: ${action.workflowId}, flowId: ${flowId})\n` +
          `工作流正在后台执行中。\n\n` +
          `📌 监控进度:\n` +
          `- 使用 workflow_recent_events 工具查询近期事件 (传 flowId=${flowId})\n` +
          `- 使用 workflow_inspect 工具查看完整执行状态 (传 flowId=${flowId})\n` +
          `- 建议每 30-60 秒轮询一次直到工作流完成或到达等待节点\n\n` +
          `注: Channel 通知可能在某些环境(如 VS Code 插件)中不可用，轮询是最可靠的进度获取方式。`
        );
      }
      // Synchronous mode (asyncRun=false): workflow has completed (or reached a
      // wait state) by the time handleRun returns.  Return the final status so
      // the caller (e.g., Claude Code via MCP) gets the execution result
      // without needing to poll.
      if (!asyncRun) {
        const inspectResult = await handleInspect(deps, flowId);
        return `流程已结束 (workflow: ${action.workflowId}, flowId: ${flowId})\n\n${inspectResult}`;
      }
      // triggerChatSubscribe marker: appended to the sync "流程已启动" event.
      // Frontend detects <!-- triggerChatSubscribe:true --> HTML comment to fire
      // window.aixBridge.sendMessage('', { method:'chat.subscribe' }).
      // Also added to the "收到命令" echo message in index.ts injectCommandEchoMessage.
      // Markdown renders HTML comments as invisible, so users won't see it.
      return `流程已启动 (workflow: ${action.workflowId}, flowId: ${flowId})\n<!-- triggerChatSubscribe:true -->`;
    }
    case "inspect":
      return handleInspect(deps, action.flowId, { analyze: action.analyze, full: action.full });
    case "state":
      return handleInspect(deps, action.flowId);
    case "logs":
      return handleInspect(deps, action.flowId);
    case "debug":
      return handleInspect(deps, action.flowId, { full: action.full });
    case "runs":
      return handleFlows(deps, action.limit, {
        includeHidden: action.includeHidden,
        global: action.global,
        identityKey: action.identityKey,
        workflowId: action.workflowId,
        status: action.status,
      });
    case "runsCleanup":
      return handleFlowsCleanup(deps, action);
    case "repairLegacyIdentity":
      return handleRepairLegacyIdentity(deps, action);
    case "repairExternalPackPin":
      return handleRepairExternalPackPin(deps, action);
    case "detail":
      try {
        return await handleDetail(deps, action.workflowId, action.source);
      } catch {
        // Workflow not found locally — check if it exists in DB (shared by another bot)
        if (deps.clawWebBaseUrl) {
          try {
            const resp = await fetch(`${deps.clawWebBaseUrl}/api/workflows/${encodeURIComponent(action.workflowId)}`);
            if (resp.ok) {
              return `❌ Workflow "${action.workflowId}" 不在本地，但 DB 中存在（可能由其他 bot 共享）。\n\n💡 使用 \`workflow pull ${action.workflowId}\` 同步到本地。`;
            }
          } catch { /* DB check failed */ }
        }
        throw new Error(`Workflow "${action.workflowId}" 未找到。`);
      }
    case "confirm":
      return handleConfirm(deps, action.note, { flowId: action.flowId });
    case "retry":
      return handleRetry(deps, action);
    case "submit":
      return handleSubmit(deps, action);
    case "skip":
      return handleSkip(deps, action);
    case "revise":
      return handleRevise(deps, action.note, { nodeId: action.nodeId, flowId: action.flowId });
    case "reject":
      return handleReject(deps, action.note, { flowId: action.flowId });
    case "reopen": {
      const executionMode = resolveExecutionMode(deliveryContext, sessionKey);
      const bcsGroupId = executionMode !== "private"
        ? (deliveryContext?.bcsGroupId as string | undefined) ?? extractGroupIdFromSessionKey(sessionKey)
        : undefined;
      return handleReopen(deps, action.workflowId, action.params, executionMode, bcsGroupId, commandSurface);
    }
    case "export":
      return handleFlowExport(deps, action.flowId);
    case "import":
      return handleFlowImport(deps, action.token);
    case "list":
      return handleList(deps, action.filter);
    case "validate":
      return handleValidate(action.workflowId, {
        actionRegistry: deps.actionRegistry,
        resolvedPacks: deps.resolvedPacks,
        resolvedWorkflows: deps.resolvedWorkflows,
        failedWorkflows: deps.failedWorkflows,
        ...(action.file ? { loadWorkflowByIdFromFile: action.file } : {}),
      });
    case "packs":
      return handlePacks(deps);
    case "packInspect":
      return handlePackInspect(deps, action.packId);
    case "packValidate":
      return handlePackValidate(deps, action.packId);
    case "cutoverCheck":
      return handleCutoverCheck(deps, action.workflowId);
    case "test": {
      const result = await handleTest(action.workflowId, {
        dryRun: action.dryRun,
        mockFile: action.mockFile,
        assertEnabled: action.assertEnabled,
        json: action.json,
        resolvedWorkflows: deps.resolvedWorkflows,
        resolvedPacks: deps.resolvedPacks,
      });
      return result.output;
    }
    case "resume":
      return handleResume(deps, action.flowId, action.revision);
    case "bcs-callback":
      return handleBcsCallback(deps, action.flowId, action.nodeId, action.result);
    case "async-callback":
      return handleAsyncCallback(deps, action.flowId, action.nodeId, action.callbackToken, action.result, action.userId);
    case "schedule": {
      if (!depsBuilders?.buildScheduleDeps) {
        return "schedule 命令在当前平台不支持。请使用 OpenClaw 平台或外部 cron 触发 workflow_dispatch";
      }
      const scheduleDeps = depsBuilders.buildScheduleDeps(deps);
      const scheduleCmd = parseScheduleArgs(tokenizeCommand(action.rawArgs));
      return handleScheduleCommand(scheduleCmd, scheduleDeps);
    }
    case "webhook": {
      if (!depsBuilders?.buildWebhookDeps) {
        return "webhook 命令在当前平台不支持。请使用 clawweb 代理或 OpenClaw 平台注册 webhook";
      }
      const webhookDeps = depsBuilders.buildWebhookDeps(deps);
      const webhookCmd = parseWebhookArgs(tokenizeCommand(action.rawArgs));
      return handleWebhookCommand(webhookCmd, webhookDeps);
    }
    case "injectNodes":
      return handleInjectNodes(deps, action.flowId, action.sourceNodeId, action.nodes);
    case "synthesize":
      return handleSynthesize(deps, action.goal, {
        model: action.model,
        validateOnly: action.validateOnly,
        maxCorrections: action.maxCorrections,
      });
    case "deploy":
      return handleDeploy(toVersionDeps(deps), action.workflowId, { file: action.file, force: action.force, note: action.note });
    case "install-pack":
      return handleInstallPack(toVersionDeps(deps), action.packDir, { only: action.only, force: action.force, move: action.move });
    case "pull":
      return handlePull(toVersionDeps(deps), action.workflowId);
    case "rollback":
      return handleRollback(toVersionDeps(deps), action.workflowId, { version: action.version, deployNumber: action.deployNumber, pack: action.pack, tag: action.tag, note: action.note });
    case "deploys":
      return handleHistory(toVersionDeps(deps), action.workflowId, action.limit, action.detailVersion, action.detailDeployNumber);
    case "status":
      return handleStatus(toVersionDeps(deps), action.workflowId, action.diff, action.gitDiff);
    case "share":
      return handleShare(toVersionDeps(deps), action.workflowId, action.to);
    case "unshare":
      return handleUnshare(toVersionDeps(deps), action.workflowId, action.from);
    case "dev-workflow-callback":
      return handleDevWorkflowCallback(deps, action.params);
    case "debug-segment":
      return handleDebugSegment(deps, action);
    default:
      return "未知命令";
  }
}