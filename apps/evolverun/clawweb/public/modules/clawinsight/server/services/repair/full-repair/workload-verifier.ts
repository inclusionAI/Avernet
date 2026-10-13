import type { Request } from "express";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { RepairTaskConfig, RepairWorkloadIdentity } from "./contracts.js";
import { REPAIR_CONTRACT_VERSION } from "./contracts.js";
import { AgentToolError as RepairError } from "../common/agent-tools/errors.js";

export interface RepairWorkloadVerifier {
  verify(req: Request): Promise<RepairWorkloadIdentity>;
}
export { issueAgentExecutionTicket as issueRepairExecutionTicket, digestAgentExecutionTicket as digestRepairExecutionTicket, equalDigest } from "../common/agent-execution.js";
import { digestAgentExecutionTicket as digestRepairExecutionTicket, equalDigest, parseAgentExecutionTicket } from "../common/agent-execution.js";

export function bearerTicket(req: Request): string {
  return parseAgentExecutionTicket(req.header("authorization"));
}

function parseConfig(raw: string): RepairTaskConfig {
  try {
    const parsed = JSON.parse(raw) as RepairTaskConfig;
    if (parsed.schemaVersion !== REPAIR_CONTRACT_VERSION || !parsed.execution || !parsed.current) {
      throw new Error("contract mismatch");
    }
    return parsed;
  } catch {
    throw new RepairError(401, "repair_execution_ticket_invalid", "Repair execution ticket 无法匹配活动任务");
  }
}

/**
 * Verifies the opaque per-execution ticket against the digest in ce_tasks.
 * jobId/taskId/stepId remain identifiers only; none of them are credentials.
 */
export class DatabaseRepairWorkloadVerifier implements RepairWorkloadVerifier {
  constructor(
    private readonly repo: EvolveRepository,
    private readonly nowSeconds: () => number = () => Math.floor(Date.now() / 1_000),
  ) {}

  async verify(req: Request): Promise<RepairWorkloadIdentity> {
    const taskId = String(req.params.taskId ?? "");
    const requestedStepId = String(req.params.stepId ?? "");
    const ticket = bearerTicket(req);
    const task = await this.repo.findTask(taskId);
    if (!task || task.task_type !== "repair") {
      throw new RepairError(401, "repair_execution_ticket_invalid", "Repair execution ticket 无法匹配活动任务");
    }
    const config = parseConfig(task.config_json);
    const execution = config.execution;
    const currentStepId = config.current.stepId;
    const terminalReportRetry = req.method === "POST"
      && (req.path?.endsWith("/report") || String(req.route?.path ?? "").endsWith("/report"));
    const decisionClaim = req.method === "POST"
      && (req.path?.endsWith("/decision/claim")
        || String(req.route?.path ?? "").endsWith("/decision/claim"));
    const previous = Array.isArray(config.history) ? config.history.at(-1) : undefined;
    const decisionClaimAlias = decisionClaim
      && requestedStepId !== currentStepId
      && previous?.stepId === requestedStepId
      && Number.isSafeInteger(previous.stepNo)
      && previous.stepNo > 0
      && Number.isSafeInteger(config.current.stepNo)
      && previous.stepNo + 1 === config.current.stepNo;
    if (config.taskId !== taskId
      || (requestedStepId !== currentStepId && !decisionClaimAlias)
      || execution.stepId !== currentStepId
      || (!terminalReportRetry && (execution.invalidatedAt != null
        || execution.state === "ended"
        || execution.leaseExpiresAt <= this.nowSeconds()))
      || !equalDigest(digestRepairExecutionTicket(ticket), execution.ticketDigest)) {
      throw new RepairError(401, "repair_execution_ticket_invalid", "Repair execution ticket 已失效或作用域不匹配");
    }
    return {
      taskId,
      stepId: currentStepId,
      executionId: execution.executionId,
      ...(decisionClaimAlias ? { requestedStepId } : {}),
    };
  }
}
