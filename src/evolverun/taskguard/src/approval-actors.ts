import type { FlowState, WorkflowActor, WorkflowNode, WorkflowSpec } from "./types.js";
import { getLegacyApprovalExecutor, getLegacyWorkflowActors } from "./legacy-runtime.js";

export type DecorateApprovalCallbackInput = {
  workflow: WorkflowSpec;
  state: FlowState;
  node: WorkflowNode;
  fromBot: string;
  approved: boolean;
  reviewTime: string;
  note: string;
};

export function getWorkflowActors(
  workflow: WorkflowSpec,
  state: FlowState,
): Record<string, WorkflowActor> {
  return state.actors ?? getLegacyWorkflowActors(workflow) ?? {};
}

export function resolveApprovalActor(
  workflow: WorkflowSpec,
  state: FlowState,
  node: WorkflowNode,
): WorkflowActor | undefined {
  const executor = getLegacyApprovalExecutor(node);
  if (!executor) return undefined;
  const reviewerRef = executor.reviewerRef;
  if (!reviewerRef) return undefined;
  return getWorkflowActors(workflow, state)[reviewerRef];
}

export function decorateApprovalCallbackResult(
  input: DecorateApprovalCallbackInput,
): Record<string, unknown> {
  const actor = resolveApprovalActor(input.workflow, input.state, input.node);
  const reviewerRole = actor?.role ?? input.node.title;

  const result: Record<string, unknown> = {
    approved: input.approved,
    action: input.approved ? "approve" : "reject",
    reviewer: reviewerRole,
    reviewerRole,
    reviewerBot: input.fromBot,
    reviewTime: input.reviewTime,
    note: input.note,
  };

  if (actor?.id) result.reviewerId = actor.id;
  if (actor?.name) result.reviewerName = actor.name;

  return result;
}
