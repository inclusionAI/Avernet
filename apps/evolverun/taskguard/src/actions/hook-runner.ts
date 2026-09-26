import type { ActionRegistry, ActionExecutionContext } from "./types.js";
import { resolveActionArgs, applySaveAs } from "./template.js";
import type { ActionState, HookActionSpec } from "../types.js";

export type HookRunOutcome =
  | { status: "succeeded" }
  | { status: "blocked"; hookId: string; action: string; error: string };

type RunHookActionsParams = {
  hooks: HookActionSpec[] | undefined;
  states: Record<string, ActionState>;
  registry: ActionRegistry;
  context: ActionExecutionContext;
};

function now(): number {
  return Date.now();
}

function sleep(ms: number): Promise<void> {
  if (ms <= 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function normalizeRetryConfig(hook: HookActionSpec): { maxAttempts: number; backoffMs: number } {
  const maxAttempts = hook.retry?.maxAttempts ?? 1;
  const backoffMs = hook.retry?.backoffMs ?? 0;

  if (!Number.isSafeInteger(maxAttempts) || maxAttempts < 1) {
    throw new Error(`hook ${hook.id} retry.maxAttempts must be a safe integer >= 1`);
  }
  if (!Number.isSafeInteger(backoffMs) || backoffMs < 0 || backoffMs > 60_000) {
    throw new Error(`hook ${hook.id} retry.backoffMs must be a safe integer between 0 and 60000`);
  }

  return { maxAttempts, backoffMs };
}

export async function runHookActions(params: RunHookActionsParams): Promise<HookRunOutcome> {
  const hooks = params.hooks ?? [];

  for (const hook of hooks) {
    const existing = params.states[hook.id];
    if (existing?.status === "succeeded") {
      continue;
    }

    const required = hook.required === true;
    let maxAttempts = 1;
    let backoffMs = 0;
    try {
      ({ maxAttempts, backoffMs } = normalizeRetryConfig(hook));
    } catch (error) {
      const message = errorMessage(error);
      params.states[hook.id] = {
        status: "failed",
        action: hook.action,
        required,
        attempts: 0,
        completedAt: now(),
        error: message,
      };
      delete params.context.actionOutputs[hook.id];
      if (required) {
        return { status: "blocked", hookId: hook.id, action: hook.action, error: message };
      }
      continue;
    }
    // Attempts are scoped to one hook run. A manual resume intentionally gives
    // failed hooks a fresh retry window while succeeded hooks are skipped.
    let attempts = existing?.status === "failed" ? 0 : existing?.attempts ?? 0;
    let lastError = "";

    while (attempts < maxAttempts) {
      attempts += 1;
      delete params.context.actionOutputs[hook.id];
      params.states[hook.id] = {
        status: "running",
        action: hook.action,
        required,
        attempts,
        startedAt: now(),
        error: null,
      };

      try {
        const hookContext: ActionExecutionContext = {
          ...params.context,
          actionId: hook.id,
        };
        const resolvedArgs = resolveActionArgs(hook.args ?? {}, hookContext);
        const result = await params.registry.execute(hook.action, resolvedArgs, hookContext);

        applySaveAs(params.context.workflowData, hook.saveAs, result, hookContext);
        params.context.actionOutputs[hook.id] = result;

        params.states[hook.id] = {
          ...params.states[hook.id],
          status: "succeeded",
          result,
          error: null,
          completedAt: now(),
        };
        break;
      } catch (error) {
        lastError = errorMessage(error);
        params.states[hook.id] = {
          ...params.states[hook.id],
          status: "failed",
          error: lastError,
          completedAt: now(),
        };
        delete params.context.actionOutputs[hook.id];

        if (attempts < maxAttempts) {
          await sleep(backoffMs);
        }
      }
    }

    if (params.states[hook.id]?.status === "failed" && required) {
      return {
        status: "blocked",
        hookId: hook.id,
        action: hook.action,
        error: lastError,
      };
    }
  }

  return { status: "succeeded" };
}
