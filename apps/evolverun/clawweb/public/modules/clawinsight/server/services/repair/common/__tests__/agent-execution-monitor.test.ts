// @vitest-environment node
import { expect, it, vi } from "vitest";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { agentExecutionContract } from "../agent-execution.js";
import { reconcileAgentExecutions } from "../agent-execution-monitor.js";

it.each([
  { handler: "session_recovery", submittedBy: null, expected: "owner" },
  { handler: "repair", submittedBy: null, expected: "creator" },
  { handler: "repair", submittedBy: "retry-operator", expected: "retry-operator" },
])("stops $handler using its original submission identity ($expected)", async ({ handler, submittedBy, expected }) => {
  const contract = agentExecutionContract({ taskId: "task", executionId: "execution", executionNo: 1, handler });
  const task = { task_id: "task", task_type: handler, user_id: "owner", created_by: "creator", config_json: JSON.stringify({
    controlPlaneEnvironment: "pre", execution: { executionId: "execution", state: "dispatching", leaseExpiresAt: 1 }, aisExecution: contract,
  }) };
  const step = { step_id: contract.stepId, bot_run_id: "job", bot_response_json: submittedBy ? JSON.stringify({ submittedBy }) : null,
    ext_json: JSON.stringify({ aisExecution: contract }) };
  const db = { query: vi.fn().mockResolvedValueOnce([{ id: 1, step_id: step.step_id, task_id: "task" }]).mockResolvedValueOnce([]) } as unknown as IDatabase;
  const repo = { findTask: vi.fn().mockResolvedValue(task), findStep: vi.fn().mockResolvedValue(step) } as unknown as EvolveRepository;
  const stopExecution = vi.fn().mockResolvedValue(undefined), report = vi.fn();
  await reconcileAgentExecutions({ db, repo, controlPlaneEnvironment: "pre", nowSeconds: () => 2,
    providers: { [handler]: { getJobStatusDetail: async () => ({ status: "running", rawStatus: "queued", errorMessage: null }), stopExecution } },
    handlers: { [handler]: report } });
  expect(stopExecution).toHaveBeenCalledWith("job", expected);
  expect(report).toHaveBeenCalledOnce();
});
