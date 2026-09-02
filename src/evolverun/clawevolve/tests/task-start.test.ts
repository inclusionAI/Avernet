import assert from "node:assert/strict";
import test from "node:test";

import type { EvolveDispatchInput } from "../src/server/ports/evolve-dispatcher.js";
import type {
  EvolveStepRow,
  EvolveTaskRepositoryPort,
  EvolveTaskRow,
} from "../src/server/ports/evolve-repository.js";
import {
  dispatchPendingBusinessStep,
  startInitialEvolveStep,
} from "../src/server/services/evolve/task-start.js";

function step(overrides: Partial<EvolveStepRow> = {}): EvolveStepRow {
  return {
    id: 7,
    step_id: "STEP-1",
    task_id: "TASK-1",
    step_type: "plan",
    step_no: 1,
    round_no: null,
    command: "/clawevolve-plan",
    status: "created",
    bot_run_id: null,
    bot_session_id: null,
    bot_response_json: null,
    output_json: null,
    summary: null,
    error_code: null,
    error_message: null,
    retryable: null,
    started_at: null,
    completed_at: null,
    gmt_create: 1,
    gmt_modified: 1,
    ...overrides,
  };
}

function taskRow(overrides: Partial<EvolveTaskRow> = {}): EvolveTaskRow {
  return {
    id: 1,
    task_id: "TASK-1",
    task_type: "full",
    user_id: "USER-1",
    bot_id: "BOT-1",
    task_name: null,
    remark: null,
    status: "pending",
    config_json: "{}",
    error_message: null,
    created_by: "USER-1",
    gmt_create: 1,
    gmt_modified: 1,
    ...overrides,
  };
}

function repository(currentStep: EvolveStepRow | null): EvolveTaskRepositoryPort & {
  dispatched: unknown[];
  failed: unknown[];
} {
  return {
    dispatched: [],
    failed: [],
    async markDispatched(...args) { this.dispatched.push(args); },
    async markDispatchFailed(...args) { this.failed.push(args); },
    async findStep() { return currentStep; },
    async claimCreatedBusinessStep() { return currentStep; },
    async resolveEvolveBotRuntime() {
      return {
        activeEngine: "engine",
        botType: "service",
        hasServiceBot: true,
        botStatus: "online",
        bindingId: 1,
        provider: "local",
        deviceId: null,
        bindingStatus: "active",
        env: "dev",
      };
    },
  };
}

test("dispatches and records the initial business step", async () => {
  const current = step({ status: "dispatched" });
  const repo = repository(current);
  const calls: EvolveDispatchInput[] = [];
  const result = await startInitialEvolveStep({
    repo,
    dispatch: async (input) => {
      calls.push(input);
      return { runId: "RUN-1", sessionId: "SESSION-1", platformResponse: { ok: true } };
    },
    task: taskRow(),
    businessStep: current,
    businessDispatch: {
      taskId: "TASK-1",
      stepId: "STEP-1",
      stepType: "plan",
      userId: "USER-1",
      botId: "BOT-1",
      command: "/clawevolve-plan",
      mode: "message",
    },
    callbackUrl: (stepId) => `https://example.test/callback/${stepId}`,
  });

  assert.equal(calls[0]?.stepPk, 7);
  assert.deepEqual(repo.dispatched[0], ["STEP-1", "RUN-1", "SESSION-1", { ok: true }]);
  assert.equal(result.businessStep?.status, "dispatched");
  assert.equal(result.deferredForInit, false);
});

test("records a dispatch failure without throwing away the step", async () => {
  const current = step();
  const repo = repository(current);
  const result = await startInitialEvolveStep({
    repo,
    dispatch: async () => { throw new Error("transport unavailable"); },
    task: taskRow(),
    businessStep: current,
    businessDispatch: {
      taskId: "TASK-1",
      stepId: "STEP-1",
      stepType: "plan",
      userId: "USER-1",
      botId: "BOT-1",
      command: "/clawevolve-plan",
      mode: "message",
    },
    callbackUrl: () => "https://example.test/callback",
  });

  assert.deepEqual(repo.failed[0], ["STEP-1", "transport unavailable"]);
  assert.equal(result.businessStep?.step_id, "STEP-1");
});

test("claims and dispatches a pending optimize step with frozen task config", async () => {
  const current = step({ step_type: "optimize", round_no: 3, command: "/clawevolve-workflow --stage optimize" });
  const repo = repository(current);
  const calls: EvolveDispatchInput[] = [];
  await dispatchPendingBusinessStep({
    repo,
    dispatch: async (input) => {
      calls.push(input);
      return { runId: null, sessionId: null, platformResponse: {} };
    },
    task: taskRow({
      config_json: JSON.stringify({
        dispatchMode: "run",
        botEnv: "dev",
        forceMessage: true,
        runtimeMaintenance: false,
        trainBenchDomainId: "train",
        testBenchDomainId: "test",
      }),
    }),
    callbackUrl: (stepId) => `https://example.test/callback/${stepId}`,
  });

  assert.equal(calls[0]?.mode, "run");
  assert.equal(calls[0]?.forceMessage, true);
  assert.equal(calls[0]?.runtimeMaintenance, false);
  assert.deepEqual(calls[0]?.optimizeArgs, {
    round: 3,
    trainBenchDomainId: "train",
    testBenchDomainId: "test",
  });
});
