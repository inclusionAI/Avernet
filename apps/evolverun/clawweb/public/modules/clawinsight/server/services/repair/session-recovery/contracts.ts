import type { BotRuntimeIdentity } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { AisStatusHandler } from "@avernet/clawevolve/server/services/ais/status-handler";
import type { RecoveryStoredTask, RecoveryJudgment, RecoveryPlan, RecoveryEvidence } from "./task-contracts.js";
import type { AgentToolCall } from "../../../repositories/repair/agent-tool-call-record.js";
export type RecoveryRequest = {
  event_id: string;
  tc_fault_label: string | null;
  target: { bot_id: string; entity_id: string; env: "pre" | "prod"; engine: "OC" | "CC" | "TE"; runtime_identity?: BotRuntimeIdentity };
  session_key: string;
  session_id?: string;
  delivery_key: string;
  diagnosis: string;
  error_location?: string;
  execution_level?: "debug" | "approval" | "automatic";
};
export type RecoveryAcknowledgment = { event_id: string; delivery_key: string; status: "accepted" };
export type { MessageReceipt as DeliveryReceipt } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { MessageReceipt as DeliveryReceipt } from "@avernet/clawweb-shared/server/services/bot-runtime";
export type RecoveryActor = { userId: string; isAdmin: boolean };
/** Optional after-hook; implementations must isolate notification failures from business work. */
export interface RecoveryActionNotifier {
  after(input: { taskId: string; eventId: string; kind: "message" | "plan" | "execution" | "result" }): void;
}
/** Read-only projection of existing auxiliary Steps; never contains workload credentials. */
export type RecoveryTermination = { status: "remote_stopped" | "remote_stop_failed" | "stop_pending"; aisJobId: string | null };
export type RecoveryExecutionView = { executionId: string; attempt: number; status: string; jobId: string | null; jobUrl: string | null; termination?: RecoveryTermination };
export type RecoveryTaskView = RecoveryStoredTask & { shared: boolean; canOperate: boolean; canManageShare: boolean; executions: RecoveryExecutionView[]; termination?: RecoveryTermination };
export interface SessionRecoveryApi { create(body: unknown, actor: RecoveryActor): Promise<RecoveryAcknowledgment> }
/** Task Service API, independent of the HTTP delivery adapter and implementation. */
export interface SessionRecoveryTaskApi extends SessionRecoveryApi {
  createSimulations(body: unknown, actor: RecoveryActor): Promise<{ batch_id: string; items: Array<RecoveryAcknowledgment & { task_id: string }> }>;
  assessSimulation(taskId: string, version: number, input: import("./simulation.js").RecoverySimulationAssessment): Promise<void>;
  create(body: unknown, actor: RecoveryActor): Promise<RecoveryAcknowledgment & { task_id: string }>;
  list(eventId: string, actor: RecoveryActor): Promise<Array<{ task_id: string; status: RecoveryStoredTask["status"]; phase: RecoveryStoredTask["phase"]; effect: RecoveryJudgment["effect"]; completion_reason?: "user_continued" | "target_unavailable" }>>;
  detail(taskId: string, actor: RecoveryActor): Promise<RecoveryTaskView>;
  setTaskShared(taskId: string, shared: boolean, actor: RecoveryActor): Promise<RecoveryTaskView>;
  terminate(taskId: string, actor: RecoveryActor, reason?: unknown): Promise<RecoveryTaskView & { termination: RecoveryTermination }>;
  decide(taskId: string, digest: string, approved: boolean, actor: RecoveryActor, cooperationDone?: boolean, feedback?: string): Promise<void>;
  annotate(taskId: string, actor: RecoveryActor, input: { layer: "delivery" | "response" | "effect"; correct: boolean | null; reason: string; evidence_ids: string[] }): Promise<void>;
  resume(taskId: string): Promise<void>;
  assess(taskId: string, version: number, input: { decision: "message" | "environment" | "observe"; summary: string; evidence_ids: string[] }): Promise<void>;
  observe(taskId: string, version: number, judgment: RecoveryJudgment, evidence: RecoveryEvidence[]): Promise<void>;
  plan(taskId: string, version: number, plan: RecoveryPlan): Promise<void>;
  finishApply(taskId: string, version: number, input: { applicable: boolean; success: boolean; summary: string }): Promise<void>;
  finishForUserContinuation(taskId: string, version: number, input: { summary: string; evidence_ids: string[]; executionId: string }): Promise<void>;
  finishForTargetUnavailable(taskId: string, version: number, input: { summary: string; evidence_ids: string[]; executionId: string }): Promise<void>;
  executionFailure(taskId: string, reason: string, executionId?: string, retryable?: boolean): Promise<void>;
  maintenance(): Promise<void>;
  reportResults(): Promise<void>;
}
/** Worker Service API; the factory supplies the existing Agent service. */
export interface SessionRecoveryAgentApi {
  readonly handleAisStatus: AisStatusHandler;
  verify(taskId: string, executionId: string, ticket: string, control?: boolean): Promise<RecoveryStoredTask>;
  bootstrap(task: RecoveryStoredTask): Promise<unknown>;
  heartbeat(task: RecoveryStoredTask, sessionId?: string): Promise<unknown>;
  sessionRead(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  trajectoryRead(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  inspect(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  logs(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  apply(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  retryMessage(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  reconcileWrite(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  conclusion(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  report(task: RecoveryStoredTask, body: Record<string, unknown>): Promise<unknown>;
  toolGet(task: RecoveryStoredTask, id: string): Promise<AgentToolCall & { toolCallId: string }>;
}

/** Outbound contract owned by monitoring; message delivery does not imply task recovery. */
export type RecoveryResult = {
  event_id: string;
  delivery: {
    delivery_key: string;
    status: DeliveryReceipt["status"];
    result_text: string;
    result_payload?: Record<string, string | number>;
    started_at_ms: number;
    finished_at_ms: number;
    error: string | null;
  };
};
export interface RecoveryResultReporter { report(result: RecoveryResult): Promise<void> }
export type RecoveryDeliveryLog = RecoveryResult & { event: "session_recovery_delivery" };
export interface RecoveryLogger { log(result: RecoveryDeliveryLog): void }

/** Service assembly contract; HTTP authentication belongs to the router adapter. */
export type SessionRecoveryRuntime = {
  service: SessionRecoveryApi;
  tasks?: SessionRecoveryTaskApi;
  agent?: SessionRecoveryAgentApi;
  maintenance?: () => Promise<void>;
  reportResults?: () => Promise<void>;
};
