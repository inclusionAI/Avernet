import type { WorkflowNode, ExecutorResult, SubagentExecutor, CollaborationExecutor } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";
import {
  buildStructuredWorkflowContext,
  resolveEffectiveContextPolicy,
} from "../execution-context.js";
import { resolveRequiredSkill, type SkillResolutionDirs } from "../skill-resolver.js";
import type { ExecutionMode, WorkflowSpec } from "../types.js";
import { collectTokenUsageFromMessages } from "../token-usage.js";
import { getLegacyApprovalExecutor } from "../legacy-runtime.js";

export type SubagentApi = {
  runtime: {
    subagent: {
      run: (params: {
        sessionKey: string;
        message: string;
        idempotencyKey: string;
        deliver: boolean;
        extraSystemPrompt?: string;
      }) => Promise<{ runId: string }>;
      waitForRun: (params: {
        runId: string;
        timeoutMs: number;
      }) => Promise<{ status: string; error?: string }>;
      getSessionMessages: (params: {
        sessionKey: string;
        limit: number;
      }) => Promise<{ messages: unknown[] }>;
    };
  };
};

export type ExecuteSubagentOptions = {
  flowId?: string;
  skillDirs?: SkillResolutionDirs;
  workflow?: WorkflowSpec;
  executionMode?: ExecutionMode;
  /** Global context compression defaults from application config. */
  compressionDefaults?: import("../context/types.js").ContextCompressionDefaults;
  /** Session compression config from application config. */
  sessionCompressionConfig?: import("../context/session-compressor.js").SessionCompressionConfig;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function contentToText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((block) => isRecord(block) && block.type === "text" && typeof block.text === "string")
    .map((block) => (block as { text: string }).text)
    .join("\n");
}

function assistantMessageText(message: unknown): string | null {
  if (!isRecord(message) || message.role !== "assistant") return null;
  return contentToText(message.content);
}

function tryParseJsonObject(candidate: string): Record<string, unknown> | null {
  const braceStart = candidate.indexOf("{");
  const braceEnd = candidate.lastIndexOf("}");
  if (braceStart === -1 || braceEnd === -1 || braceEnd <= braceStart) return null;

  try {
    const parsed = JSON.parse(candidate.substring(braceStart, braceEnd + 1));
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function parseJsonObjectsFromText(text: string): Record<string, unknown>[] {
  const results: Record<string, unknown>[] = [];
  const jsonBlockPattern = /```json\s*([\s\S]*?)```/gi;
  for (const match of text.matchAll(jsonBlockPattern)) {
    const parsed = tryParseJsonObject(match[1].trim());
    if (parsed) results.push(parsed);
  }

  if (results.length === 0) {
    const parsed = tryParseJsonObject(text.trim());
    if (parsed) results.push(parsed);
  }

  return results;
}

function findApprovalJsonFromMessages(messages: unknown[]): {
  approval: Record<string, unknown> | null;
  firstJson: Record<string, unknown> | null;
} {
  let firstJson: Record<string, unknown> | null = null;

  for (const msg of [...messages].reverse()) {
    const text = assistantMessageText(msg);
    if (text === null) continue;

    const candidates = parseJsonObjectsFromText(text);
    for (const candidate of [...candidates].reverse()) {
      firstJson ??= candidate;
      if (typeof candidate.approved === "boolean") {
        return { approval: candidate, firstJson };
      }
    }
  }

  return { approval: null, firstJson };
}

function tryParseJsonFromMessages(messages: unknown[]): Record<string, unknown> | null {
  for (const msg of [...messages].reverse()) {
    const text = assistantMessageText(msg);
    if (text === null) continue;

    const candidates = parseJsonObjectsFromText(text);
    const parsed = [...candidates].reverse()[0];
    if (parsed) return parsed;
  }
  return null;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isCompletedRunStatus(status: string): boolean {
  return status === "completed" || status === "succeeded" || status === "ok";
}

function flowSuffix(flowId?: string): string {
  const cleanedFlowId = flowId?.replace(/[^a-zA-Z0-9]/g, "") ?? "";
  return cleanedFlowId ? cleanedFlowId.slice(-6) : "noflow";
}

function buildChildSessionKey(nodeId: string, suffix: string): string {
  return `child:${nodeId}:${suffix}:${Date.now()}`;
}

function buildSkillMessage(params: {
  skillName: string;
  skillDir: string;
  skillFile: string;
  message: string;
}): string {
  return [
    `请使用 ${params.skillName} skill 执行本节点。`,
    `Skill 目录：${params.skillDir}`,
    `Skill 入口：${params.skillFile}`,
    "",
    params.message,
  ].join("\n");
}

function minimalWorkflow(node: WorkflowNode): WorkflowSpec {
  return {
    id: "unknown-workflow",
    version: 1,
    title: "Unknown Workflow",
    nodes: [node],
  };
}

export async function executeSubagent(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  api: unknown,
  options: ExecuteSubagentOptions = {},
): Promise<ExecutorResult> {
  const approvalExecutor = getLegacyApprovalExecutor(node);
  if (node.executor.type !== "subagent" && node.executor.type !== "collaboration" && !approvalExecutor) {
    return { status: "failed", error: "not a subagent/collaboration/approval node" };
  }

  const subagentApi = api as SubagentApi;
  const workflow = options.workflow ?? minimalWorkflow(node);
  const executionMode = options.executionMode ?? "private";
  const contextPolicy = resolveEffectiveContextPolicy({ workflow, node, executionMode });
  // tail mode is not supported for subagent because it requires session file access
  // that the subagent runtime doesn't provide
  if (contextPolicy.history === "tail") {
    return {
      status: "failed",
      error: "subagent contextPolicy.history=tail is not supported for subagent nodes. Use isolated or structured instead.",
    };
  }
  if (contextPolicy.history === "inherit") {
    return {
      status: "failed",
      error: "subagent contextPolicy.history=inherit 暂未支持：OpenClaw subagent runtime 当前不支持指定 sessionFile，请使用 isolated 或 structured。",
    };
  }

  let skillName: string;
  let message: string;
  let timeoutSeconds: number;

  if (approvalExecutor) {
    skillName = approvalExecutor.skillName;
    message = resolveTemplate(approvalExecutor.message, templateCtx);
    timeoutSeconds = approvalExecutor.timeoutSeconds ?? 300;
  } else if (node.executor.type === "collaboration") {
    const exec = node.executor as CollaborationExecutor;
    if (!exec.skillName?.trim()) {
      return { status: "failed", error: `collaboration node ${node.id} requires skillName for subagent execution` };
    }
    skillName = exec.skillName;
    message = resolveTemplate(exec.message, templateCtx);
    timeoutSeconds = exec.timeoutSeconds ?? 300;
  } else {
    const exec = node.executor as SubagentExecutor;
    skillName = exec.skillName;
    message = resolveTemplate(exec.prompt, templateCtx);
    timeoutSeconds = exec.timeoutSeconds ?? 300;
  }

  /** Truncated resolved message/prompt for inclusion in ExecutorResult (max 4000 chars). */
  const resolvedPromptTruncated = message.length > 4000
    ? message.substring(0, 3989) + "... [TRUNCATED]"
    : message;

  let resolvedSkill;
  try {
    resolvedSkill = await resolveRequiredSkill(skillName, options.skillDirs);
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    return { status: "failed", error: errMsg, rawError: err };
  }

  const suffix = flowSuffix(options.flowId);
  const childSessionKey = buildChildSessionKey(node.id, suffix);
  const idempotencyKey = `${node.id}:${suffix}:${Date.now()}`;
  const flowId = options.flowId ?? "unknown-flow";
  if (contextPolicy.history === "structured") {
    const { workflowContext } = await buildStructuredWorkflowContext({
      workflow,
      node,
      flowId,
      templateCtx,
      history: "structured",
      contextPolicy,
      compressionDefaults: options.compressionDefaults,
    });
    message = [
      message,
      "",
      "Workflow Context JSON:",
      JSON.stringify(workflowContext, null, 2),
    ].join("\n");
  }
  const runMessage = buildSkillMessage({
    skillName,
    skillDir: resolvedSkill.skillDir,
    skillFile: resolvedSkill.skillFile,
    message,
  });

  try {
    const { runId } = await subagentApi.runtime.subagent.run({
      sessionKey: childSessionKey,
      message: runMessage,
      idempotencyKey,
      deliver: false,
      extraSystemPrompt: `You are executing skill: ${skillName}. Follow the skill's instructions precisely. Output your result as JSON.`,
    });

    const { status, error } = await subagentApi.runtime.subagent.waitForRun({
      runId,
      timeoutMs: timeoutSeconds * 1000,
    });

    if (status === "error" || error) {
      return {
        status: "failed",
        error: `subagent error: ${error ?? status}`,
      };
    }
    if (!isCompletedRunStatus(status)) {
      return {
        status: "failed",
        error: `subagent run did not complete: ${status}`,
      };
    }

    if (approvalExecutor) {
      let messages: unknown[] = [];
      let approval: Record<string, unknown> | null = null;
      let firstJson: Record<string, unknown> | null = null;
      for (let attempt = 0; attempt <= 3; attempt += 1) {
        ({ messages } = await subagentApi.runtime.subagent.getSessionMessages({
          sessionKey: childSessionKey,
          limit: 30,
        }));
        ({ approval, firstJson } = findApprovalJsonFromMessages(messages));
        if (approval) break;
        if (attempt < 3) await delay(50);
      }

      if (!firstJson) {
        return {
          status: "failed",
          error: `subagent approval output is not valid JSON. childSessionKey=${childSessionKey}`,
          usage: collectTokenUsageFromMessages(messages),
        };
      }
      if (!approval) {
        return {
          status: "failed",
          error: `subagent approval JSON missing boolean approved. childSessionKey=${childSessionKey}`,
          usage: collectTokenUsageFromMessages(messages),
        };
      }

      return {
        status: "succeeded",
        result: { ...approval, childSessionKey },
        usage: collectTokenUsageFromMessages(messages),
        resolvedPrompt: resolvedPromptTruncated,
      };
    }

    const { messages } = await subagentApi.runtime.subagent.getSessionMessages({
      sessionKey: childSessionKey,
      limit: 10,
    });
    const parsed = tryParseJsonFromMessages(messages);

    if (parsed) {
      return {
        status: "succeeded",
        result: { ...parsed, childSessionKey },
        usage: collectTokenUsageFromMessages(messages),
        resolvedPrompt: resolvedPromptTruncated,
      };
    }

    const lastAssistantText = [...messages]
      .reverse()
      .map((message) => assistantMessageText(message))
      .find((text): text is string => text !== null);
    return {
      status: "succeeded",
      result: {
        output: lastAssistantText ?? "",
        childSessionKey,
      },
      usage: collectTokenUsageFromMessages(messages),
      resolvedPrompt: resolvedPromptTruncated,
    };
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    return { status: "failed", error: `subagent execution failed: ${errMsg}`, resolvedPrompt: resolvedPromptTruncated, rawError: err };
  }
}
