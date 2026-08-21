import type { ResolvedWorkflowPack } from "../packs/types.js";
import type { ActionExecutionContext, ActionRegistry } from "./types.js";
import { createPythonRunner, type PythonRunner } from "./python.js";

type RegisterPackPythonActionsOptions = {
  runPython?: PythonRunner["runPython"];
};

function buildWorkflowActionEnv(context: ActionExecutionContext, actionId: string): Record<string, string> {
  return {
    WORKFLOW_ENGINE_FLOW_ID: context.flowId,
    WORKFLOW_ENGINE_WORKFLOW_ID: context.workflowId,
    WORKFLOW_ENGINE_ACTION_ID: context.actionId ?? actionId,
    WORKFLOW_ENGINE_SESSION_KEY: context.sessionKey,
  };
}

function buildScriptContext(context: ActionExecutionContext, pack: ResolvedWorkflowPack): Record<string, unknown> {
  return {
    flowId: context.flowId,
    workflowId: context.workflowId,
    actionId: context.actionId,
    nodeId: context.nodeId,
    sessionKey: context.sessionKey,
    executionMode: context.executionMode,
    bcsGroupId: context.bcsGroupId,
    params: context.params,
    workflowData: context.workflowData,
    nodeOutput: context.nodeOutput,
    actionOutputs: context.actionOutputs,
    user: context.user,
    workflow: context.workflow,
    pack: {
      id: pack.manifest.id,
      version: pack.manifest.version,
      root: pack.root,
      digest: pack.digest,
      source: pack.source,
    },
  };
}

export function registerPackPythonActions(
  registry: ActionRegistry,
  packs: ResolvedWorkflowPack[] = [],
  options: RegisterPackPythonActionsOptions = {},
): void {
  const runner = options.runPython ? { runPython: options.runPython } : createPythonRunner();

  for (const pack of packs) {
    for (const actionPack of pack.actions ?? []) {
      if (actionPack.type !== "python-scripts") continue;
      for (const command of Object.values(actionPack.commands ?? {})) {
        registry.register({
          name: command.actionName,
          execute: async ({ args, context }) => runner.runPython(
            command.absolutePath,
            [
              "--workflow-engine-action", command.actionName,
              "--workflow-engine-args-json", JSON.stringify(args),
              "--workflow-engine-context-json", JSON.stringify(buildScriptContext(context, pack)),
            ],
            buildWorkflowActionEnv(context, command.actionName),
          ),
        });
      }
    }
  }
}
