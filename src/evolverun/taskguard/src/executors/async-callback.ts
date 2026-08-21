/**
 * Executor for the `async-callback` node type.
 *
 * When executed, this executor:
 * 1. Generates a callback token via the token registry.
 * 2. Computes the full callback URL (baseUrl + /api/callback/{token}).
 * 3. Returns `status: "waiting"` so the Controller pauses the workflow.
 *
 * The external business system should HTTP POST to the callback URL with
 * the result payload. The Controller will resume the workflow once the
 * callback is received and validated.
 *
 * @module executors/async-callback
 */

import type { WorkflowNode, ExecutorResult } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";
import {
  createCallbackTokenRegistry,
  parseTimeoutToEpoch,
} from "../callback/index.js";
import type { IDatabase } from "../db/types.js";

export type AsyncCallbackExecutorDeps = {
  database: IDatabase;
  flowId: string;
  nodeId: string;
  workflowId?: string;
  callbackBaseUrl: string;
  defaultTimeout: string;
};

/**
 * Execute an async-callback node.
 *
 * Registers a callback token and returns the callback URL in the waitConfig.
 * The workflow pauses at this node until the external system calls back.
 */
export async function executeAsyncCallback(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  deps: AsyncCallbackExecutorDeps,
): Promise<ExecutorResult> {
  if (node.executor.type !== "async-callback") {
    return {
      status: "failed",
      error: `Executor type mismatch: expected "async-callback", got "${node.executor.type}"`,
    };
  }

  const executor = node.executor;
  const registry = createCallbackTokenRegistry(deps.database);

  // Resolve template variables in timeout
  const timeoutRaw = executor.timeout ?? deps.defaultTimeout;
  const timeoutResolved = resolveTemplate(timeoutRaw, templateCtx);
  let timeoutAt: number | undefined;
  try {
    timeoutAt = parseTimeoutToEpoch(timeoutResolved);
  } catch {
    return {
      status: "failed",
      error: `Invalid timeout format: "${timeoutResolved}". Use e.g. "30m", "2h", "24h".`,
    };
  }

  // Generate token
  let token: string;
  try {
    token = await registry.create({
      flowId: deps.flowId,
      nodeId: deps.nodeId,
      workflowId: deps.workflowId,
      timeoutAt,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return {
      status: "failed",
      error: `Failed to create callback token: ${message}`,
      rawError: err,
    };
  }

  // Build callback URL
  const baseUrl = executor.callbackBaseUrl ?? deps.callbackBaseUrl;
  const callbackUrl = baseUrl
    ? `${baseUrl.replace(/\/+$/, "")}/api/callback/${token}`
    : `/api/callback/${token}`;

  // Build auth description for the prompt
  const authMode = executor.auth?.mode ?? (executor.auth?.secret ? "hmac" : "none");
  const authHint = authMode !== "none"
    ? ` (auth: ${authMode})`
    : "";

  const prompt = `等待外部回调 [${deps.nodeId}]。回调 URL: ${callbackUrl}${authHint}`;

  return {
    status: "waiting",
    result: {
      callbackToken: token,
      callbackUrl,
      timeout: timeoutResolved,
    },
    waitConfig: {
      prompt,
      hint: `回调 URL: ${callbackUrl}`,
      waitKind: "async-callback",
    },
  };
}