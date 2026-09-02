import assert from "node:assert/strict";
import test from "node:test";

import type { RunAnalysisTimeoutRepositoryPort } from "../src/server/ports/evolve-repository.js";
import { sweepRunAnalysisTimeouts } from "../src/server/services/evolve/run-analysis-timeout.js";

test("marks stale analysis steps and their flows as failed", async () => {
  const updates: string[] = [];
  const flows: string[] = [];
  const logs: string[] = [];
  const repo: RunAnalysisTimeoutRepositoryPort = {
    async findStaleRunAnalysisSteps(timeoutMs) {
      assert.equal(timeoutMs, 60_000);
      return [
        { step_id: "STEP-1", task_id: "TASK-1", flow_id: "FLOW-1", gmt_create: 1 },
        { step_id: "STEP-2", task_id: "TASK-2", flow_id: "FLOW-2", gmt_create: 1 },
      ];
    },
    async updateStepStatus(stepId, input) {
      assert.equal(input.errorCode, "RUN_ANALYSIS_TIMEOUT");
      if (stepId === "STEP-2") throw new Error("write failed");
      updates.push(stepId);
    },
    async failFlowAnalysis(flowId) { flows.push(flowId); },
  };

  const count = await sweepRunAnalysisTimeouts(repo, {
    timeoutMs: 60_000,
    logger: {
      info: (message) => { logs.push(String(message)); },
      warn: (message) => { logs.push(String(message)); },
    },
  });

  assert.equal(count, 1);
  assert.deepEqual(updates, ["STEP-1"]);
  assert.deepEqual(flows, ["FLOW-1"]);
  assert.match(logs.join("\n"), /STEP-2.*write failed/);
});

test("does nothing when no stale analysis step exists", async () => {
  const repo: RunAnalysisTimeoutRepositoryPort = {
    async findStaleRunAnalysisSteps() { return []; },
    async updateStepStatus() { throw new Error("unexpected"); },
    async failFlowAnalysis() { throw new Error("unexpected"); },
  };
  assert.equal(await sweepRunAnalysisTimeouts(repo), 0);
});
