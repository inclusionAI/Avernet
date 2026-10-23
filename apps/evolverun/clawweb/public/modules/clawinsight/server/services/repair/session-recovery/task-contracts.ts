import type { AgentExecution } from "../common/agent-execution.js";
import type { AisArtifactContract } from "@avernet/clawevolve/server/services/ais/ais-artifact-contract";
import type { AgentRuntimeTargetSnapshot } from "../common/agent-tools/contracts.js";
import type { RecoveryResult } from "./contracts.js";
import type { AgentPlanAction } from "../common/agent-tools/contracts.js";
import type { RecoveryRequest } from "./contracts.js";

export type RecoveryLevel = "debug" | "approval" | "automatic";
export type RecoveryTaskStatus = "running" | "waiting_owner" | "waiting_effect" | "completed" | "failed" | "canceled";
export type RecoveryPhase = "assess" | "observe" | "plan" | "apply" | "verify";
export type RecoveryEffect = "recovered" | "not_recovered" | "unknown" | "not_intervened";
export type RecoveryInterventionStatus = "none" | "performed" | "unknown";
export type RecoveryEvidence = {
  evidence_id: string;
  tool_call_id: string;
  source: string;
  location: string;
  excerpt: string;
};
export type RecoveryJudgment = {
  delivery: "confirmed" | "unconfirmed";
  response: "acted" | "replied" | "none" | "unknown";
  effect: RecoveryEffect;
  summary: string;
  evidence_ids: string[];
};
/** Approved business action; concrete transport details are optional execution references. */
export type RecoveryPlanAction = Omit<AgentPlanAction, "type"> & { type?: AgentPlanAction["type"]; operationScope?: import("./action-scope.js").RecoveryActionScope };
export type RecoveryPlan = {
  summary: string;
  basis: string;
  scope: string;
  verification: string;
  actions: RecoveryPlanAction[];
  owner_cooperation: Array<{ request: string; basis: string }>;
};
export type RecoveryTimelineEvent = {
  event_id: string;
  at_ms: number;
  kind: "created" | "handbook" | "assessment" | "message" | "observation" | "plan" | "owner_decision" | "operation" | "waiting" | "resumed" | "execution" | "notification" | "result" | "annotation" | "termination";
  actor: "service" | "agent" | "owner";
  summary: string;
  payload: Record<string, unknown>;
};
export type RecoveryTaskState = {
  /** Present only for a frozen offline run; never grants live target access. */
  simulation?: import("./simulation.js").RecoverySimulation;
  schemaVersion: "session-recovery-task/v1";
  taskId: string;
  request: RecoveryRequest;
  level: RecoveryLevel;
  status: RecoveryTaskStatus;
  /** Normal completion because a real user resumed the original conversation. */
  completionReason?: "user_continued";
  phase: RecoveryPhase;
  version: number;
  /** Absent on legacy tasks; never inferred from the target Bot environment. */
  controlPlaneEnvironment?: import("../common/task-control-plane.js").AgentControlPlaneEnvironment;
  /** Absent on legacy tasks; only true enables read-only sharing. */
  shared?: boolean;
  observationDeadlineAt: number | null;
  ownerDeadlineAt: number | null;
  plan: (RecoveryPlan & { digest: string; approvedAt: number | null }) | null;
  judgment: RecoveryJudgment | null;
  evidence: RecoveryEvidence[];
  delivery?: RecoveryResult;
  runtimeTarget?: AgentRuntimeTargetSnapshot;
  authorizationScopeDigest?: string;
  current?: { stepId: string };
  execution?: AgentExecution;
  executionNo?: number;
  /** Task-wide infrastructure failure budget; retryAt is zero while an attempt is active. */
  executionRetry?: { failures: number; retryAt: number; executionId?: string };
  aisExecution?: AisArtifactContract;
  requiresFreshObservation?: boolean;
};
export type RecoveryTaskResult = {
  event_id: string;
  task_id: string;
  result_version: number;
  status: "waiting_effect" | "completed" | "failed" | "canceled";
  phase: RecoveryPhase;
  waiting_reason: "effect" | null;
  completion_reason?: "user_continued";
  effect: RecoveryEffect;
  /** Whether any intervention is confirmed; independent of business recovery. Omitted by legacy senders. */
  intervention_status?: RecoveryInterventionStatus;
  result_text: string;
  evidence_ids: string[];
  delivery?: RecoveryResult["delivery"];
  evidence?: RecoveryEvidence[];
  error?: string | null;
};

export type RecoveryStoredTask = RecoveryTaskState & {
  /** Transient projection of the complete tool ledger; never persisted or supplied by the Agent. */
  interventionStatus?: RecoveryInterventionStatus;
  /** Written only at task creation; absence means historical audit coverage cannot be proven. */
  interventionAuditComplete?: true;
  timeline: RecoveryTimelineEvent[];
  pending_results: RecoveryTaskResult[];
};
export interface RecoveryTaskStore {
  create(value: RecoveryStoredTask, stepId: string): Promise<void>;
  find(taskId: string): Promise<RecoveryStoredTask | null>;
  listForEvent(eventId: string, ownerId: string, isAdmin: boolean): Promise<RecoveryStoredTask[]>;
  update(taskId: string, expectedVersion: number, change: (current: RecoveryStoredTask) => RecoveryStoredTask): Promise<RecoveryStoredTask | null>;
  /** Append a notification fact atomically without changing the Agent's business-state version. */
  appendNotification(taskId: string, event: RecoveryTimelineEvent): Promise<boolean>;
  /** Atomically reserve a due attempt. A stale worker must not send or settle another attempt. */
  claimResult(taskId: string, resultVersion: number): Promise<number | null>;
  failResult(taskId: string, resultVersion: number, attempt: number): Promise<void>;
  acknowledgeResult(taskId: string, resultVersion: number, attempt: number): Promise<void>;
  listForReporting?(limit: number, afterTaskId: string, controlPlaneEnvironment: import("../common/task-control-plane.js").AgentControlPlaneEnvironment): Promise<{ tasks: RecoveryStoredTask[]; nextTaskId: string }>;
  listForMaintenance?(limit: number, afterTaskId?: string): Promise<RecoveryStoredTask[]>;
}
