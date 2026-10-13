import { describe, expect, it } from "vitest";
import type { Request } from "express";
import type { EvolveRepository, EvolveTaskRow } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { REPAIR_CONTRACT_VERSION } from "../contracts.js";
import {
  DatabaseRepairWorkloadVerifier,
  issueRepairExecutionTicket,
} from "../workload-verifier.js";

function request(taskId: string, stepId: string, ticket?: string, path = "/bootstrap"): Request {
  return {
    params: { taskId, stepId },
    method: path === "/report" || path === "/decision/claim" ? "POST" : "GET",
    path,
    header: (name: string) => name.toLowerCase() === "authorization" && ticket ? `Bearer ${ticket}` : undefined,
  } as unknown as Request;
}

function advancedRepository(ticketDigest: string): EvolveRepository {
  const task = {
    task_id: "REPAIR-1",
    task_type: "repair",
    config_json: JSON.stringify({
      schemaVersion: REPAIR_CONTRACT_VERSION,
      taskId: "REPAIR-1",
      current: { stepId: "STEP-2", stepNo: 3 },
      history: [
        { stepId: "STEP-0", stepNo: 1 },
        { stepId: "STEP-1", stepNo: 2 },
      ],
      execution: {
        executionId: "EXEC-1",
        stepId: "STEP-2",
        ticketDigest,
        state: "running",
        leaseExpiresAt: 2_000,
        invalidatedAt: null,
      },
    }),
  } as EvolveTaskRow;
  return { findTask: async (taskId: string) => taskId === task.task_id ? task : null } as EvolveRepository;
}

function repository(ticketDigest: string, leaseExpiresAt = 2_000): EvolveRepository {
  const task = {
    task_id: "REPAIR-1",
    task_type: "repair",
    config_json: JSON.stringify({
      schemaVersion: REPAIR_CONTRACT_VERSION,
      taskId: "REPAIR-1",
      current: { stepId: "STEP-1" },
      execution: {
        executionId: "EXEC-1",
        stepId: "STEP-1",
        ticketDigest,
        state: "running",
        leaseExpiresAt,
        invalidatedAt: null,
      },
    }),
  } as EvolveTaskRow;
  return { findTask: async (taskId: string) => taskId === task.task_id ? task : null } as EvolveRepository;
}

describe("DatabaseRepairWorkloadVerifier", () => {
  it("authenticates the current execution with a digest-only stored ticket", async () => {
    const issued = issueRepairExecutionTicket();
    const identity = await new DatabaseRepairWorkloadVerifier(repository(issued.digest), () => 1_000)
      .verify(request("REPAIR-1", "STEP-1", issued.ticket));
    expect(identity).toEqual({ taskId: "REPAIR-1", stepId: "STEP-1", executionId: "EXEC-1" });
  });

  it("rejects missing, wrong, expired, and cross-step tickets", async () => {
    const issued = issueRepairExecutionTicket();
    const verifier = new DatabaseRepairWorkloadVerifier(repository(issued.digest), () => 1_000);
    await expect(verifier.verify(request("REPAIR-1", "STEP-1"))).rejects.toMatchObject({ status: 401 });
    await expect(verifier.verify(request("REPAIR-1", "STEP-1", issueRepairExecutionTicket().ticket)))
      .rejects.toMatchObject({ status: 401 });
    await expect(verifier.verify(request("REPAIR-1", "OTHER", issued.ticket)))
      .rejects.toMatchObject({ status: 401 });
    await expect(new DatabaseRepairWorkloadVerifier(repository(issued.digest, 999), () => 1_000)
      .verify(request("REPAIR-1", "STEP-1", issued.ticket))).rejects.toMatchObject({ status: 401 });
  });

  it("allows only the terminal report retry path after the execution lease expires", async () => {
    const issued = issueRepairExecutionTicket();
    const verifier = new DatabaseRepairWorkloadVerifier(repository(issued.digest, 999), () => 1_000);
    await expect(verifier.verify(request("REPAIR-1", "STEP-1", issued.ticket, "/report")))
      .resolves.toMatchObject({ executionId: "EXEC-1" });
    await expect(verifier.verify(request("REPAIR-1", "STEP-1", issued.ticket, "/heartbeat")))
      .rejects.toMatchObject({ status: 401 });
  });

  it("maps only a decision/claim request for the immediately previous Step to the current identity", async () => {
    const issued = issueRepairExecutionTicket();
    const verifier = new DatabaseRepairWorkloadVerifier(advancedRepository(issued.digest), () => 1_000);

    await expect(verifier.verify(request(
      "REPAIR-1", "STEP-1", issued.ticket, "/decision/claim",
    ))).resolves.toEqual({
      taskId: "REPAIR-1",
      stepId: "STEP-2",
      executionId: "EXEC-1",
      requestedStepId: "STEP-1",
    });
    await expect(verifier.verify(request("REPAIR-1", "STEP-1", issued.ticket, "/heartbeat")))
      .rejects.toMatchObject({ status: 401 });
    await expect(verifier.verify(request(
      "REPAIR-1", "STEP-0", issued.ticket, "/decision/claim",
    ))).rejects.toMatchObject({ status: 401 });
  });
});
