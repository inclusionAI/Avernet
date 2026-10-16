import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { AgentToolError } from "./agent-tools/errors.js";
import type { AisArtifactContract } from "@avernet/clawevolve/server/services/ais/ais-artifact-contract";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";

export type AgentExecution = {
  executionId: string; stepId: string; ticketDigest: string; jobId: string | null; ccSessionId: string | null;
  state: "dispatching" | "running" | "waiting_decision" | "ended";
  leaseExpiresAt: number; decisionDeadlineAt: number | null; lastHeartbeatAt: number | null; invalidatedAt: number | null;
};

/** Shared execution mechanics; business adapters retain phases and terminal decisions. */
export class AgentExecutionService {
  constructor(private readonly deps: {
    leaseSeconds: number; nowSeconds: () => number;
  }) {
    if (!Number.isSafeInteger(deps.leaseSeconds) || deps.leaseSeconds <= 20) throw new Error("Agent execution lease must exceed heartbeat interval");
  }
  issue(executionId: string, stepId: string, leaseSeconds = this.deps.leaseSeconds): { execution: AgentExecution; ticket: string } {
    const issued = issueAgentExecutionTicket();
    return { ticket: issued.ticket, execution: { executionId, stepId, ticketDigest: issued.digest,
      jobId: null, ccSessionId: null, state: "dispatching", leaseExpiresAt: this.deps.nowSeconds() + leaseSeconds,
      decisionDeadlineAt: null, lastHeartbeatAt: null, invalidatedAt: null } };
  }
  assertActive(execution: AgentExecution, ticket: string): void {
    if (execution.state === "ended" || execution.invalidatedAt !== null || execution.leaseExpiresAt <= this.deps.nowSeconds()
      || !equalDigest(digestAgentExecutionTicket(ticket), execution.ticketDigest)) throw new Error("Agent execution is no longer active");
  }
  heartbeat<T extends AgentExecution>(execution: T, sessionId?: string): T {
    const now = this.deps.nowSeconds();
    if (execution.state === "ended" || execution.invalidatedAt !== null || execution.leaseExpiresAt <= now) throw new Error("Agent execution lease expired");
    return { ...execution, ccSessionId: sessionId ?? execution.ccSessionId, state: execution.state === "dispatching" ? "running" : execution.state,
      lastHeartbeatAt: now, leaseExpiresAt: now + this.deps.leaseSeconds };
  }
  end<T extends AgentExecution>(execution: T): T {
    return { ...execution, state: "ended", invalidatedAt: this.deps.nowSeconds(), decisionDeadlineAt: null };
  }
}

/** Freeze Base's archive/report contract independently of business-stage artifacts. */
export async function createAgentExecutionStep(repo: EvolveRepository, input: {
  taskId: string; executionId: string; executionNo: number; handler: string;
}): Promise<AisArtifactContract> {
  const contract = agentExecutionContract(input);
  const stepId = contract.stepId;
  // Auxiliary execution records do not consume the positive business Step sequence.
  await repo.createStep({ taskId: input.taskId, stepId, stepType: "agent_execution", stepNo: -input.executionNo,
    command: "agent_execution", ext: { executionId: input.executionId, aisExecution: contract } });
  return contract;
}

export function agentExecutionContract(input: { taskId: string; executionId: string; executionNo: number; handler: string }): AisArtifactContract {
  const stepId = `${input.taskId}-AIS-${input.executionNo}`;
  const artifacts: AisArtifactContract["artifacts"] = {};
  for (const name of ["artifactBundle", "runtimeBundle", "openclawSessions"]) {
    artifacts[name] = { objectKey: `evolution/${input.taskId}/executions/${input.executionId}/${name}.tar.gz`,
      contentType: "application/gzip", requiredOnSuccess: true, allowEmpty: false };
  }
  const contract: AisArtifactContract = { aisBase: { statusHandler: input.handler, executionId: input.executionId }, stepId, artifacts };
  return contract;
}

export function sameAgentExecutionReport(step: { status: string; output_json?: string | null; summary?: string | null; error_code?: string | null; error_message?: string | null },
  report: { status: string; output?: unknown; summary?: string; errorCode?: string; errorMessage?: string }): boolean {
  return step.status === report.status && (step.output_json ?? null) === (report.output == null ? null : JSON.stringify(report.output))
    && (report.summary === undefined || report.summary === step.summary)
    && (report.errorCode === undefined || report.errorCode === step.error_code)
    && (report.errorMessage === undefined || report.errorMessage === step.error_message);
}

const TICKET_PREFIX = "ce_repair_";

export function digestAgentExecutionTicket(ticket: string): string {
  return createHash("sha256").update(ticket, "utf8").digest("hex");
}

export function issueAgentExecutionTicket(): { ticket: string; digest: string } {
  const ticket = `${TICKET_PREFIX}${randomBytes(32).toString("base64url")}`;
  return { ticket, digest: digestAgentExecutionTicket(ticket) };
}

export function parseAgentExecutionTicket(authorizationHeader: string | undefined): string {
  const authorization = authorizationHeader?.trim() ?? "";
  const match = /^Bearer\s+(\S+)$/i.exec(authorization);
  const ticket = match?.[1] ?? "";
  if (!ticket.startsWith(TICKET_PREFIX) || ticket.length > 256) {
    throw new AgentToolError(401, "repair_execution_ticket_missing", "Repair execution ticket 缺失或无效");
  }
  return ticket;
}

export function equalDigest(actual: string, expected: string): boolean {
  if (!/^[a-f0-9]{64}$/.test(expected)) return false;
  const left = Buffer.from(actual, "hex");
  const right = Buffer.from(expected, "hex");
  return left.length === right.length && timingSafeEqual(left, right);
}
