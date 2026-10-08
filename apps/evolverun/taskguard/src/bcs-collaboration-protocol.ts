import type {
  ApprovalExecutor,
  BcsRouteSelector,
  CollaborationExecutor,
  FlowState,
  WorkflowNode,
  WorkflowParticipant,
  WorkflowSpec,
} from "./types.js";
import { buildNodeOutputContext, resolveTemplate, type TemplateContext } from "./runner.js";
import { getLegacyApprovalExecutor, getLegacyWorkflowActors } from "./legacy-runtime.js";

export const WORKFLOW_COLLABORATION_PROTOCOL_VERSION = "workflow-collaboration-v1";
export const WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION = "workflow-collaboration-batch-v1";
export const BCS_COLLABORATION_PROTOCOL_VERSION = WORKFLOW_COLLABORATION_PROTOCOL_VERSION;

export type WorkflowCollaborationSubject = {
  type: string;
  id: string;
  title?: string;
};

export type BcsRouteTarget = BcsRouteSelector;

export type BcsCollaborationRequestItem = {
  workflowId: string;
  flowId: string;
  nodeId: string;
  taskId: string;
  taskKind: string;
  skillId?: string;
  title: string;
  participant?: WorkflowParticipant;
  route: {
    to: BcsRouteTarget[];
  };
  message: string;
  data: Record<string, unknown>;
};

export type BcsCollaborationBatchRequest = {
  protocolVersion: typeof WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION;
  messageType: "collaboration_batch_request";
  workflowId: string;
  flowId: string;
  batchId: string;
  subject?: WorkflowCollaborationSubject;
  requester: {
    bot: string;
    workflow: string;
  };
  commonContext: Record<string, unknown>;
  tasks: BcsCollaborationRequestItem[];
};

export type BcsCollaborationResultMessage = {
  protocolVersion: typeof WORKFLOW_COLLABORATION_PROTOCOL_VERSION;
  messageType: "collaboration_result";
  workflowId: string;
  flowId: string;
  nodeId?: string;
  taskId?: string;
  status: "succeeded" | "failed" | "rejected";
  result?: unknown;
  participant?: WorkflowParticipant;
};

export type BcsCollaborationErrorMessage = {
  protocolVersion: typeof WORKFLOW_COLLABORATION_PROTOCOL_VERSION;
  messageType: "collaboration_error";
  workflowId: string;
  flowId: string;
  nodeId?: string;
  taskId?: string;
  errorCode: string;
  errorMessage: string;
  participant?: WorkflowParticipant;
};

export type BcsCollaborationMessage = BcsCollaborationResultMessage | BcsCollaborationErrorMessage;

export type BcsCollaborationMessageClassification =
  | { kind: "valid"; message: BcsCollaborationMessage }
  | { kind: "invalid" }
  | { kind: "none" };

export type BcsCollaborationTaskState = {
  nodeId: string;
  protocolVersion: typeof WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION;
  batchId: string;
  taskId: string;
  workflowId: string;
  flowId: string;
  taskKind: string;
  skillId?: string;
  participant?: WorkflowParticipant;
  route: {
    to: BcsRouteTarget[];
  };
};

export type BcsCollaborationBatch = {
  protocolVersion: typeof WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION;
  batchId: string;
  request: BcsCollaborationBatchRequest;
  tasks: BcsCollaborationTaskState[];
  targets: BcsRouteTarget[];
};

function requireBatchExecutor(node: WorkflowNode): ApprovalExecutor | CollaborationExecutor {
  const approvalExecutor = getLegacyApprovalExecutor(node);
  if (approvalExecutor) return approvalExecutor;
  if (node.executor.type === "collaboration") return node.executor;
  throw new Error(`node ${node.id} is not an approval/collaboration node`);
}

function resolveRouteTargets(
  node: WorkflowNode,
  executor: ApprovalExecutor | CollaborationExecutor,
  templateCtx: TemplateContext,
): BcsRouteTarget[] {
  const routeTargets = executor.route?.to?.filter((target) => target && typeof target.type === "string") ?? [];
  if (routeTargets.length > 0) {
    return routeTargets.map((target) => {
      if (!("value" in target) || typeof target.value !== "string") return target;
      return {
        ...target,
        value: resolveTemplate(target.value, templateCtx),
      } as BcsRouteTarget;
    });
  }
  throw new Error(`collaboration node ${node.id} missing route.to`);
}

function resolveTaskKind(node: WorkflowNode, executor: ApprovalExecutor | CollaborationExecutor): string {
  if (executor.type === "approval") {
    const approvalType = executor.approvalType?.trim();
    return approvalType || node.id;
  }
  return executor.taskKind?.trim() || node.id;
}

function resolveSkillId(node: WorkflowNode, executor: ApprovalExecutor | CollaborationExecutor): string | undefined {
  if (executor.type === "approval") return executor.skillName?.trim() || node.id;
  return executor.skillName?.trim() || executor.taskKind?.trim() || node.id;
}

function resolveParticipant(params: {
  executor: ApprovalExecutor | CollaborationExecutor;
  state: FlowState;
  workflow: WorkflowSpec;
}): WorkflowParticipant | undefined {
  if (params.executor.type === "collaboration") return params.executor.participant;
  if (!params.executor.reviewerRef) return undefined;
  return params.state.actors?.[params.executor.reviewerRef] ?? getLegacyWorkflowActors(params.workflow)?.[params.executor.reviewerRef];
}

export function renderWorkflowCollaborationSubject(params: {
  workflow: WorkflowSpec;
  templateCtx: TemplateContext;
}): WorkflowCollaborationSubject | undefined {
  const subjectSpec = params.workflow.collaboration?.subject;
  if (!subjectSpec) return undefined;

  const subject: WorkflowCollaborationSubject = {
    type: resolveTemplate(subjectSpec.type, params.templateCtx),
    id: resolveTemplate(subjectSpec.id, params.templateCtx),
  };
  if (subjectSpec.title) {
    const title = resolveTemplate(subjectSpec.title, params.templateCtx);
    if (title) subject.title = title;
  }
  if (!subject.type.trim() || !subject.id.trim()) {
    throw new Error(`workflow ${params.workflow.id} collaboration.subject resolved empty type or id`);
  }
  return subject;
}

function assertUniqueTaskIds(tasks: BcsCollaborationRequestItem[]): void {
  const seen = new Map<string, string>();
  for (const task of tasks) {
    const existing = seen.get(task.taskId);
    if (existing) {
      throw new Error(`duplicate BCS collaboration taskId: ${task.taskId} (${existing}, ${task.nodeId})`);
    }
    seen.set(task.taskId, task.nodeId);
  }
}

export function buildBcsCollaborationBatch(params: {
  workflow: WorkflowSpec;
  state: FlowState;
  nodes: WorkflowNode[];
  flowId: string;
  templateCtx: TemplateContext;
  nodeTemplateCtx?: (node: WorkflowNode) => TemplateContext;
}): BcsCollaborationBatch {
  const batchId = `${params.flowId}:collaboration-batch:1`;
  const nodeOutput = buildNodeOutputContext(params.state.nodeStates);
  const subject = renderWorkflowCollaborationSubject({
    workflow: params.workflow,
    templateCtx: params.templateCtx,
  });

  const tasks: BcsCollaborationRequestItem[] = params.nodes.map((node) => {
    const templateCtx = params.nodeTemplateCtx?.(node) ?? params.templateCtx;
    const executor = requireBatchExecutor(node);
    const taskKind = resolveTaskKind(node, executor);
    const routeTargets = resolveRouteTargets(node, executor, templateCtx);
    const skillId = resolveSkillId(node, executor);
    const participant = resolveParticipant({ executor, state: params.state, workflow: params.workflow });
    const message = resolveTemplate(executor.message, templateCtx);
    const taskId = `TASK__${params.flowId}__${node.id}`;

    return {
      workflowId: params.workflow.id,
      flowId: params.flowId,
      nodeId: node.id,
      taskId,
      taskKind,
      ...(skillId ? { skillId } : {}),
      title: node.title,
      ...(participant ? { participant } : {}),
      route: { to: routeTargets },
      message,
      data: {
        workflowId: params.workflow.id,
        flowId: params.flowId,
        nodeId: node.id,
        taskId,
        taskKind,
        ...(skillId ? { skillId } : {}),
        ...(participant ? { participant } : {}),
        route: { to: routeTargets },
        message,
      },
    };
  });

  assertUniqueTaskIds(tasks);

  const request: BcsCollaborationBatchRequest = {
    protocolVersion: WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION,
    messageType: "collaboration_batch_request",
    workflowId: params.workflow.id,
    flowId: params.flowId,
    batchId,
    ...(subject ? { subject } : {}),
    requester: {
      bot: "clawmind",
      workflow: params.workflow.title,
    },
    commonContext: {
      workflowData: params.state.workflowData,
      nodeOutput,
    },
    tasks,
  };

  return {
    protocolVersion: WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION,
    batchId,
    request,
    tasks: tasks.map((task) => ({
      nodeId: task.nodeId,
      protocolVersion: WORKFLOW_COLLABORATION_BATCH_PROTOCOL_VERSION,
      batchId,
      taskId: task.taskId,
      workflowId: task.workflowId,
      flowId: task.flowId,
      taskKind: task.taskKind,
      ...(task.skillId ? { skillId: task.skillId } : {}),
      ...(task.participant ? { participant: task.participant } : {}),
      route: task.route,
    })),
    targets: tasks.flatMap((task) => task.route.to),
  };
}

function parseJsonObject(text: string): Record<string, unknown> | undefined {
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

function extractJsonCandidate(text: string): string {
  const stripped = text.replace(/^\[from:[^\]]+\]\s*/, "").trim();
  const fenced = stripped.match(/```(?:json)?\s*([\s\S]*?)```/i);
  return fenced ? fenced[1].trim() : stripped;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

function isOptionalString(value: unknown): boolean {
  return value === undefined || typeof value === "string";
}

function isParticipant(value: unknown): value is WorkflowParticipant {
  if (value === undefined) return true;
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const participant = value as Record<string, unknown>;
  return isOptionalString(participant.role)
    && isOptionalString(participant.id)
    && isOptionalString(participant.name);
}

function isCollaborationStatus(value: unknown): value is BcsCollaborationResultMessage["status"] {
  return value === "succeeded" || value === "failed" || value === "rejected";
}

function isGenericBcsCollaborationResultMessage(raw: Record<string, unknown>): raw is BcsCollaborationResultMessage {
  return isNonEmptyString(raw.workflowId)
    && isNonEmptyString(raw.flowId)
    && isOptionalString(raw.nodeId)
    && isOptionalString(raw.taskId)
    && isCollaborationStatus(raw.status)
    && isParticipant(raw.participant);
}

function isGenericBcsCollaborationErrorMessage(raw: Record<string, unknown>): raw is BcsCollaborationErrorMessage {
  return isNonEmptyString(raw.workflowId)
    && isNonEmptyString(raw.flowId)
    && isOptionalString(raw.nodeId)
    && isOptionalString(raw.taskId)
    && isNonEmptyString(raw.errorCode)
    && isNonEmptyString(raw.errorMessage)
    && isParticipant(raw.participant);
}

function looksLikeBcsCollaborationProtocol(candidate: string): boolean {
  return candidate.includes(WORKFLOW_COLLABORATION_PROTOCOL_VERSION)
    && (
      candidate.includes('"messageType"')
      || candidate.includes("'messageType'")
      || candidate.includes("messageType")
    )
    && (
      candidate.includes("collaboration_result")
      || candidate.includes("collaboration_error")
    );
}

export function classifyBcsCollaborationMessage(text: string): BcsCollaborationMessageClassification {
  const candidate = extractJsonCandidate(text);
  const raw = parseJsonObject(candidate);
  if (!raw) return looksLikeBcsCollaborationProtocol(candidate) ? { kind: "invalid" } : { kind: "none" };

  if (raw.protocolVersion === WORKFLOW_COLLABORATION_PROTOCOL_VERSION) {
    if (raw.messageType === "collaboration_result") {
      return isGenericBcsCollaborationResultMessage(raw) ? { kind: "valid", message: raw } : { kind: "invalid" };
    }
    if (raw.messageType === "collaboration_error") {
      return isGenericBcsCollaborationErrorMessage(raw) ? { kind: "valid", message: raw } : { kind: "invalid" };
    }
    return { kind: "invalid" };
  }

  return { kind: "none" };
}

export function extractBcsCollaborationMessage(text: string): BcsCollaborationMessage | undefined {
  const classification = classifyBcsCollaborationMessage(text);
  if (classification.kind === "valid") return classification.message;
  return undefined;
}
