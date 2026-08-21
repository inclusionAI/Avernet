import type { ExecutionMode, FlowInput, WorkflowSpec } from "../types.js";

export type ActionExecutionContext = {
  flowId: string;
  workflowId: string;
  actionId?: string;
  nodeId?: string;
  sessionKey: string;
  executionMode: ExecutionMode;
  bcsGroupId?: string;
  params: Record<string, string>;
  input?: FlowInput | Record<string, unknown>;
  workflowData: Record<string, unknown>;
  nodeOutput: Record<string, Record<string, unknown>>;
  actionOutputs: Record<string, Record<string, unknown>>;
  loop?: { id: string; iteration: number; bodyNodeId: string };
  templateAliases?: Record<string, unknown>;
  user: { id?: string; name?: string };
  workflow?: WorkflowSpec;
};

export type ActionHandlerArgs = {
  args: Record<string, unknown>;
  context: ActionExecutionContext;
};

export type ActionHandler = {
  name: string;
  requiredArgs?: string[];
  execute: (input: ActionHandlerArgs) => Promise<Record<string, unknown>>;
};

export type ActionRegistry = {
  register: (handler: ActionHandler) => void;
  has: (name: string) => boolean;
  names: () => string[];
  execute: (
    name: string,
    args: Record<string, unknown>,
    context: ActionExecutionContext,
  ) => Promise<Record<string, unknown>>;
};
