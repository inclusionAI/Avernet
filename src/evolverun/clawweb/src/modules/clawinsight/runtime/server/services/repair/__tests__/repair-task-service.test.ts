import { createHash } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  type EvolveRepository,
  type EvolveStepRow,
  type EvolveTaskRow,
} from "../../../repositories/evolve-repository.js";
import {
  assertRepairAuditSecretFree,
  type CreateRepairTaskWithStepInput,
  type CreateRepairToolCallInput,
  type ListRepairToolCallsOptions,
  type RepairRepository,
  type RepairToolCall,
  RepairToolCallIdempotencyConflictError,
  RepairToolCallWorkloadConflictError,
  type TransitionRepairStepInput,
} from "../../../repositories/repair-repository.js";
import type { AistudioService } from "../../aistudio-service.js";
import type { MistOssObjectStore } from "../../object-storage/oss-object-store.js";
import {
  LEGACY_REPAIR_PLAN_VERSION,
  REPAIR_CONTRACT_VERSION,
  REPAIR_PLAN_VERSION,
  type RepairCreateTaskInput,
  type RepairInsightSource,
  type RepairPlanArtifact,
  type RepairPlanQuality,
  type RepairPlanRecommendation,
  type RepairTaskConfig,
  type RepairTarget,
  type RepairWorkloadIdentity,
} from "../contracts.js";
import type { ImprovementDetail } from "../../insight/contracts.js";
import type { RepairConfig } from "../config.js";
import { RepairTaskService, REPAIR_PARAMS_KEY } from "../repair-runtime.js";
import { DatabaseRepairWorkloadVerifier } from "../workload-verifier.js";

const ACTOR = "297189";
const BOT_ID = "default";
const CREATE_COOKIE = "SSO=create-cookie";
const CFUSE_AGENT_INPUT = {
  agentMode: "cfuse",
  cfuseEngine: "claude-code",
  cfuseModel: "Kimi-K2.5",
} as const;

type SnapshotEnvelope = {
  schemaVersion: string;
  taskType: string;
  taskId: string;
  stepId: string;
  execution: { action: string; executionId: string; agentMode: string };
  input: Record<string, unknown>;
  runtime: Record<string, unknown> & { executionTicket?: string };
};

function hash(content: Buffer | string): string {
  return createHash("sha256").update(content).digest("hex");
}

function unpackStoredToolRequest(request: unknown): { payload: unknown; targetVersion: number | null } {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    return { payload: request, targetVersion: null };
  }
  const envelope = request as Record<string, unknown>;
  if (envelope.schemaVersion !== "repair-tool-request/v1"
    || !Number.isSafeInteger(envelope.runtimeTargetVersion)
    || Number(envelope.runtimeTargetVersion) < 1
    || !("payload" in envelope)) {
    return { payload: request, targetVersion: null };
  }
  return { payload: envelope.payload, targetVersion: Number(envelope.runtimeTargetVersion) };
}

function parseSnapshotEnvelope(globalParams: Record<string, string>): SnapshotEnvelope {
  return JSON.parse(globalParams[REPAIR_PARAMS_KEY]) as SnapshotEnvelope;
}

function target(observedAt = "2026-08-17T00:00:00.000Z"): RepairTarget {
  return {
    environment: "pre",
    ownerId: ACTOR,
    botId: BOT_ID,
    botType: "personal",
    botStatus: "active",
    bindingId: "binding-1",
    bindingStatus: "active",
    provider: "baas",
    deviceId: "bot-uuid",
    observedAt,
    source: "ocb_backend_current",
  };
}

function config(): RepairConfig {
  return {
    publicBaseUrl: "https://clawweb.example",
    controlPlaneEnvironment: "pre",
    aisSnapshotIds: { pre: 62310015, prod: 62310016 },
    decisionGraceSeconds: 900,
    contextWaitSeconds: 300,
    executionLeaseSeconds: 90,
    heartbeatIntervalSeconds: 30,
    agentTimeoutSeconds: 900,
    agentCloseoutTimeoutSeconds: 180,
    maxAgentAutoRecoveries: 2,
    agentCorrectionTimeoutSeconds: 120,
    maxAgentOutputCorrectionRetries: 3,
    maxAgentRateLimitRetries: 3,
    agentRateLimitRetryBaseSeconds: 5,
    baas: { commandTenant: "default", commandTimeoutSeconds: 30 } as never,
    requestTimeoutMs: 15_000,
  };
}

type Harness = {
  repo: EvolveRepository;
  repairRepo: RepairRepository;
  service: RepairTaskService;
  repairConfig: RepairConfig;
  objects: Map<string, Buffer>;
  execute: ReturnType<typeof vi.fn>;
  stopExecution: ReturnType<typeof vi.fn>;
  createSignedUrl: ReturnType<typeof vi.fn>;
  getObject: ReturnType<typeof vi.fn>;
  resolveTarget: ReturnType<typeof vi.fn>;
  executeOcb: ReturnType<typeof vi.fn>;
  searchLogs: ReturnType<typeof vi.fn>;
  inspectRuntime: ReturnType<typeof vi.fn>;
  applyApprovedAction: ReturnType<typeof vi.fn>;
  now: { value: number };
  insightBridge: {
    getDetail: ReturnType<typeof vi.fn>;
    findLinkByRequest: ReturnType<typeof vi.fn>;
    freezeTask: ReturnType<typeof vi.fn>;
    linkTask: ReturnType<typeof vi.fn>;
    resolvePlanSource: ReturnType<typeof vi.fn>;
    markApplied: ReturnType<typeof vi.fn>;
    ensurePersistentAuthorization: ReturnType<typeof vi.fn>;
    validatePersistentAuthorization: ReturnType<typeof vi.fn>;
  };
};

let harness: Harness;

function fakeRepositories(): { repo: EvolveRepository; repairRepo: RepairRepository } {
  const tasks = new Map<string, EvolveTaskRow>();
  const steps = new Map<string, EvolveStepRow>();
  const calls = new Map<string, RepairToolCall>();
  let rowId = 1;

  const repo = {
    findTask: vi.fn(async (taskId: string) => tasks.get(taskId) ?? null),
    findStep: vi.fn(async (stepId: string) => steps.get(stepId) ?? null),
    listSteps: vi.fn(async (taskId: string) => [...steps.values()]
      .filter(step => step.task_id === taskId)
      .sort((left, right) => left.step_no - right.step_no)),
    listEvolveBots: vi.fn(async () => []),
    resolveEvolveBotRuntime: vi.fn(async () => ({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-1",
      provider: "baas",
      deviceId: "bot-uuid",
      bindingStatus: "active",
      env: "pre",
      ownerId: ACTOR,
      accessType: "owner",
    })),
    resolveEvolveBotRuntimeForOwner: vi.fn(async (ownerId: string) => ({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-1",
      provider: "baas",
      deviceId: "bot-uuid",
      bindingStatus: "active",
      env: "pre",
      ownerId,
      accessType: "owner",
    })),
    markExternalDispatched: vi.fn(async (stepId: string, jobId: string, response: unknown) => {
      const step = steps.get(stepId)!;
      if (["created", "dispatching"].includes(step.status)) step.status = "dispatched";
      step.bot_run_id = jobId;
      step.bot_response_json = JSON.stringify(response);
      const task = tasks.get(step.task_id)!;
      if (["pending", "accepted", "dispatched"].includes(task.status)) task.status = "running";
    }),
    markDispatchFailed: vi.fn(async (stepId: string, error: string) => {
      const step = steps.get(stepId)!;
      step.status = "failed";
      step.error_message = error;
      const task = tasks.get(step.task_id)!;
      task.status = "failed";
      task.error_message = error;
    }),
    updateTaskConfig: vi.fn(async (taskId: string, next: Record<string, unknown>) => {
      tasks.get(taskId)!.config_json = JSON.stringify(next);
    }),
    updateTaskState: vi.fn(async (input: Parameters<EvolveRepository["updateTaskState"]>[0]) => {
      const task = tasks.get(input.taskId)!;
      task.status = input.status;
      task.error_message = input.errorMessage ?? null;
      if (input.config) task.config_json = JSON.stringify(input.config);
    }),
  } as unknown as EvolveRepository;

  const repairRepo = {
    createTaskWithStep: vi.fn(async (input: CreateRepairTaskWithStepInput) => {
      tasks.set(input.task.taskId, {
        id: rowId++,
        task_id: input.task.taskId,
        task_type: "repair",
        user_id: input.task.userId,
        bot_id: input.task.botId,
        task_name: input.task.taskName,
        remark: input.task.remark ?? null,
        status: "pending",
        config_json: JSON.stringify(input.task.config),
        error_message: null,
        created_by: input.task.createdBy,
        gmt_create: 1,
        gmt_modified: 1,
      });
      steps.set(input.step.stepId, {
        id: rowId++,
        step_id: input.step.stepId,
        task_id: input.task.taskId,
        step_type: input.step.stepType,
        step_no: input.step.stepNo,
        round_no: input.step.roundNo ?? null,
        command: input.step.command,
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
      });
    }),
    transitionStep: vi.fn(async (input: TransitionRepairStepInput) => {
      const task = tasks.get(input.taskId);
      if (!task || !input.expectedTaskStatuses.includes(task.status)) {
        return { outcome: "conflict", reason: "task_state" } as const;
      }
      const current = JSON.parse(task.config_json) as RepairTaskConfig;
      if (current.current.stepId !== input.expectedCurrentStepId) {
        return { outcome: "conflict", reason: "current_step" } as const;
      }
      if (hash(task.config_json) !== input.expectedTaskConfigDigest) {
        return { outcome: "conflict", reason: "task_config" } as const;
      }
      input.toolCallLedgerGuard?.([...calls.values()]
        .filter(call => call.taskId === input.taskId && call.stepId === input.expectedCurrentStepId)
        .sort((left, right) => left.id - right.id), {
        runtimeTargetVersion: current.runtimeTarget.version,
      });
      const active = [...calls.values()].find(call => call.taskId === input.taskId
        && (call.status === "pending" || call.status === "executing")
        && call.callId !== input.ignoreActiveToolCallId);
      if (active) return { outcome: "conflict", reason: "active_tool_calls" } as const;
      const previous = steps.get(input.expectedCurrentStepId);
      if (input.previousStep && (!previous
        || !input.previousStep.expectedStatuses.includes(previous.status))) {
        return { outcome: "conflict", reason: "previous_step" } as const;
      }
      if (input.reuseJobId && previous?.bot_run_id !== input.reuseJobId) {
        return { outcome: "conflict", reason: "reuse_job" } as const;
      }
      if (previous && input.previousStep) {
        previous.status = input.previousStep.status;
        if (input.previousStep.output !== undefined) {
          previous.output_json = JSON.stringify(input.previousStep.output);
        }
        if (input.previousStep.summary !== undefined) previous.summary = input.previousStep.summary ?? null;
        if (input.previousStep.errorCode !== undefined) previous.error_code = input.previousStep.errorCode ?? null;
        if (input.previousStep.errorMessage !== undefined) previous.error_message = input.previousStep.errorMessage ?? null;
        if (input.previousStep.retryable !== undefined) {
          previous.retryable = input.previousStep.retryable == null ? null : Number(input.previousStep.retryable);
        }
      }
      if (input.nextStep) {
        steps.set(input.nextStep.stepId, {
          id: rowId++,
          step_id: input.nextStep.stepId,
          task_id: input.taskId,
          step_type: input.nextStep.stepType,
          step_no: input.nextStep.stepNo,
          round_no: input.nextStep.roundNo ?? null,
          command: input.nextStep.command,
          status: input.reuseJobId ? "dispatched" : "created",
          bot_run_id: input.reuseJobId ?? null,
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
        });
      }
      task.status = input.nextTaskStatus;
      task.config_json = JSON.stringify(input.nextConfig);
      return { outcome: "transitioned" } as const;
    }),
    compareAndSetTaskConfig: vi.fn(async (
      input: Parameters<RepairRepository["compareAndSetTaskConfig"]>[0],
    ) => {
      const task = tasks.get(input.taskId);
      if (!task || !input.expectedTaskStatuses.includes(task.status)
        || (JSON.parse(task.config_json) as RepairTaskConfig).current.stepId !== input.expectedCurrentStepId
        || hash(task.config_json) !== input.expectedTaskConfigDigest) return false;
      task.config_json = JSON.stringify(input.nextConfig);
      return true;
    }),
    createToolCall: vi.fn(async (input: CreateRepairToolCallInput) => {
      const duplicate = [...calls.values()].find(call => call.stepId === input.stepId
        && call.clientRequestId === input.clientRequestId);
      if (duplicate) {
        const priorIntent = JSON.stringify({
          taskId: duplicate.taskId,
          stepId: duplicate.stepId,
          executionId: duplicate.executionId,
          authorizationScopeDigest: duplicate.authorizationScopeDigest,
          clientRequestId: duplicate.clientRequestId,
          toolName: duplicate.toolName,
          operation: duplicate.operation,
          actionId: duplicate.actionId,
          request: unpackStoredToolRequest(duplicate.request).payload,
          isWrite: duplicate.isWrite,
        });
        const retriedIntent = JSON.stringify({
          taskId: input.taskId,
          stepId: input.stepId,
          executionId: input.executionId,
          authorizationScopeDigest: input.authorizationScopeDigest,
          clientRequestId: input.clientRequestId,
          toolName: input.toolName,
          operation: input.operation,
          actionId: input.actionId ?? null,
          request: unpackStoredToolRequest(input.request).payload,
          isWrite: input.isWrite ?? false,
        });
        if (priorIntent !== retriedIntent) {
          throw new RepairToolCallIdempotencyConflictError(input.stepId, input.clientRequestId);
        }
        return { created: false, call: duplicate };
      }
      const task = tasks.get(input.taskId);
      if (!task || !["pending", "running"].includes(task.status)) {
        throw new RepairToolCallWorkloadConflictError(input.taskId, "task_state");
      }
      const current = JSON.parse(task.config_json) as RepairTaskConfig;
      if (current.current.stepId !== input.stepId) {
        throw new RepairToolCallWorkloadConflictError(input.taskId, "current_step");
      }
      if (current.execution.executionId !== input.executionId) {
        throw new RepairToolCallWorkloadConflictError(input.taskId, "execution");
      }
      if (current.authorizationScopeDigest !== input.authorizationScopeDigest) {
        throw new RepairToolCallWorkloadConflictError(input.taskId, "authorization_scope");
      }
      input.toolCallLedgerGuard?.([...calls.values()]
        .filter(call => call.taskId === input.taskId && call.stepId === input.stepId)
        .sort((left, right) => left.id - right.id), {
        runtimeTargetVersion: current.runtimeTarget.version,
      });
      const call: RepairToolCall = {
        id: rowId++,
        callId: input.callId,
        taskId: input.taskId,
        stepId: input.stepId,
        executionId: input.executionId,
        authorizationScopeDigest: input.authorizationScopeDigest,
        clientRequestId: input.clientRequestId,
        toolName: input.toolName,
        operation: input.operation,
        actionId: input.actionId ?? null,
        deadlineAt: input.deadlineAt ?? null,
        request: input.request,
        isWrite: input.isWrite ?? false,
        status: "pending",
        leaseOwner: null,
        leaseExpiresAt: null,
        result: null,
        resultDigest: null,
        errorCode: null,
        errorMessage: null,
        downstreamTraceId: null,
        gmtCreate: 1,
        gmtModified: 1,
      };
      calls.set(call.callId, call);
      return { created: true, call };
    }),
    findToolCall: vi.fn(async (callId: string) => calls.get(callId) ?? null),
    findToolCallByClientRequestId: vi.fn(async (stepId: string, clientRequestId: string) =>
      [...calls.values()].find((call) => call.stepId === stepId && call.clientRequestId === clientRequestId) ?? null),
    listToolCalls: vi.fn(async (taskId: string, options: ListRepairToolCallsOptions = {}) => [...calls.values()]
      .filter(call => call.taskId === taskId)
      .filter(call => !options.stepId || call.stepId === options.stepId)
      .filter(call => options.callIds == null || options.callIds.includes(call.callId))
      .filter(call => options.isWrite == null || call.isWrite === options.isWrite)
      .filter(call => !options.statuses || options.statuses.includes(call.status))
      .filter(call => options.afterId == null || call.id > options.afterId)
      .filter(call => options.recordKind == null || options.recordKind === "all"
        || (options.recordKind === "source"
          ? !(call.toolName === "repair_control" && call.operation === "record_conclusion")
          : call.toolName === "repair_control" && call.operation === "record_conclusion"))
      .filter(call => options.clientRequestIds == null || options.clientRequestIds.includes(call.clientRequestId))
      .sort((left, right) => left.id - right.id)
      .slice(0, options.limit ?? 500)),
    listPendingToolCalls: vi.fn(async (taskId: string, limit = 100) => [...calls.values()]
      .filter(call => call.taskId === taskId && call.status === "pending")
      .sort((left, right) => left.id - right.id)
      .slice(0, limit)),
    claimToolCall: vi.fn(async (input: Parameters<RepairRepository["claimToolCall"]>[0]) => {
      const call = calls.get(input.callId);
      const now = input.now ?? 0;
      if (!call || call.executionId !== input.executionId
        || call.authorizationScopeDigest !== input.authorizationScopeDigest
        || call.status !== "pending" || (call.deadlineAt != null && call.deadlineAt <= now)) return null;
      call.status = "executing";
      call.leaseOwner = input.leaseOwner;
      call.leaseExpiresAt = input.leaseExpiresAt;
      return call;
    }),
    completeToolCall: vi.fn(async (input: Parameters<RepairRepository["completeToolCall"]>[0]) => {
      const call = calls.get(input.callId)!;
      if (call.status !== "pending" && call.status !== "executing") {
        return { outcome: "duplicate", call } as const;
      }
      call.status = input.status;
      call.result = input.result ?? null;
      call.errorCode = input.errorCode ?? null;
      call.errorMessage = input.errorMessage ?? null;
      call.downstreamTraceId = input.downstreamTraceId ?? null;
      call.resultDigest = hash(JSON.stringify({
        status: call.status,
        result: call.result,
        errorCode: call.errorCode,
        errorMessage: call.errorMessage,
        downstreamTraceId: call.downstreamTraceId,
      }));
      call.leaseOwner = null;
      call.leaseExpiresAt = null;
      return { outcome: "completed", call } as const;
    }),
    cancelPendingToolCall: vi.fn(async (
      callId: string,
      executionId: string,
      scopeDigest: string,
      result: unknown,
    ) => {
      const call = calls.get(callId);
      if (!call || call.executionId !== executionId
        || call.authorizationScopeDigest !== scopeDigest || call.status !== "pending") return null;
      call.status = "canceled";
      call.result = result;
      call.resultDigest = hash(JSON.stringify({
        status: call.status,
        result,
        errorCode: null,
        errorMessage: null,
        downstreamTraceId: null,
      }));
      return { outcome: "completed", call } as const;
    }),
  } as unknown as RepairRepository;
  return { repo, repairRepo };
}

beforeEach(() => {
  const { repo, repairRepo } = fakeRepositories();
  const objects = new Map<string, Buffer>();
  const execute = vi.fn(async () => `job-${execute.mock.calls.length}`);
  const stopExecution = vi.fn(async () => undefined);
  const resolveTarget = vi.fn(async () => target());
  const executeOcb = vi.fn(async (input: { operation: { type: string } }) => ({
    operation: input.operation.type,
    result: { ok: true, operation: input.operation.type },
    requiresTargetRefresh: false,
  }));
  const searchLogs = vi.fn(async () => ({
    status: "success",
    returnedEntries: 0,
    totalEntries: 0,
    sources: [],
  }));
  const inspectRuntime = vi.fn(async (input: unknown, request: { operation: string }) => ({
    status: "success",
    operation: request.operation,
    target: { environment: "pre", bindingId: "binding-1", deviceId: "bot-uuid" },
    exitCode: 0,
    stdout: "line one\nline two\n",
    stderr: "",
    durationMs: 12,
  }));
  const applyApprovedAction = vi.fn(async () => ({ status: "success", exitCode: 0 }));
  const now = { value: 1_000 };
  const createSignedUrl = vi.fn(async (key: string) => `https://oss.example/${encodeURIComponent(key)}`);
  const getObject = vi.fn(async (key: string) => ({
    content: objects.get(key) ?? Buffer.from("{}"),
    etag: null,
    contentType: "application/json",
  }));
  const store = {
    createSignedUrl,
    getObject,
  } as unknown as MistOssObjectStore;
  const insightBridge = {
    getDetail: vi.fn(),
    findLinkByRequest: vi.fn(async () => null),
    freezeTask: vi.fn(async () => undefined),
    linkTask: vi.fn(async () => undefined),
    resolvePlanSource: vi.fn(async () => ({ status: "ready" })),
    markApplied: vi.fn(async () => undefined),
    ensurePersistentAuthorization: vi.fn(async () => ({ grantId: 7 })),
    validatePersistentAuthorization: vi.fn(async () => undefined),
  };
  const repairConfig = config();
  const service = new RepairTaskService({
    config: repairConfig,
    repo,
    repairRepo,
    store,
    ais: { execute, stopExecution } as unknown as AistudioService,
    targets: { resolve: resolveTarget } as never,
    ocb: { execute: executeOcb } as never,
    logs: { search: searchLogs } as never,
    runtimeTool: { inspect: inspectRuntime, applyApprovedAction } as never,
    insightBridge,
    nowSeconds: () => now.value,
  });
  harness = {
    repo, repairRepo, service, repairConfig, objects, execute, stopExecution, createSignedUrl, getObject, resolveTarget, executeOcb, searchLogs,
    inspectRuntime, applyApprovedAction, now, insightBridge,
  };
});

async function createTask(agentInput: Partial<RepairCreateTaskInput> = {}): Promise<{
  taskId: string;
  config: RepairTaskConfig;
  identity: RepairWorkloadIdentity;
}> {
  const created = await harness.service.createTask({
    actorUserId: ACTOR,
    authHeaders: { cookie: CREATE_COOKIE },
    body: {
      targetEnvironment: "pre",
      botId: BOT_ID,
      symptom: "gateway failed",
      ...agentInput,
    },
  });
  const taskId = String(created.taskId);
  const row = await harness.repo.findTask(taskId);
  const taskConfig = JSON.parse(row!.config_json) as RepairTaskConfig;
  return {
    taskId,
    config: taskConfig,
    identity: {
      taskId,
      stepId: taskConfig.current.stepId,
      executionId: taskConfig.execution.executionId,
    },
  };
}

it("freezes an explicit ADMIN_ONCE execution scope for administrator delegation", async () => {
  const created = await harness.service.createTask({
    actorUserId: "admin-1",
    isAdmin: true,
    authHeaders: { cookie: "SSO=admin-cookie" },
    body: {
      targetEnvironment: "pre",
      targetUserId: ACTOR,
      botId: BOT_ID,
      symptom: "管理员代处理测试",
      adminOverrideReason: "用户未响应，管理员代执行一次",
      repairDirection: "只检查测试 Bot 的配置",
    },
  });
  const task = await harness.repo.findTask(String(created.taskId));
  const persisted = JSON.parse(task!.config_json) as RepairTaskConfig;

  expect(persisted.authorizationScope).toMatchObject({
    actorUserId: "admin-1",
    ownerId: ACTOR,
    botId: BOT_ID,
    environment: "pre",
    executionMode: "ADMIN_ONCE",
  });
  expect(harness.resolveTarget).toHaveBeenCalledWith(expect.objectContaining({
    ownerId: ACTOR,
    botId: BOT_ID,
  }));
});

it("creates an Insight-backed Repair run with frozen evidence hints", async () => {
  const detail = {
    improvementId: 42,
    ownerUserId: ACTOR,
    botOwnerUserId: ACTOR,
    botId: BOT_ID,
    title: "修复 workflow_engine_dispatch 字段错误",
    userGuidance: "只修改测试 Bot 的查询模板",
    sourceType: "USER_SELECTED",
    sourceRuleId: null,
    evidenceCount: 1,
    sessionCount: 1,
    dataStartTime: null,
    dataEndTime: null,
    dataAsOf: "2026-08-17T00:00:00.000Z",
    batchId: "batch-42",
    status: "ACTIVE",
    actionType: "ASSIGN_OWNER",
    assignmentReason: null,
    rootCauseSummary: "字段名与当前 DB schema 不一致",
    suggestedAction: "检查并更新 gmt_create 查询字段",
    adminReviewStatus: "APPROVED",
    adminReviewedBy: "admin-1",
    adminReviewedAt: null,
    adminReviewComment: null,
    rejectReasonCode: null,
    rejectComment: null,
    rejectedAt: null,
    handledAt: null,
    verificationStatus: "NOT_STARTED",
    verificationLastCheckedAt: null,
    verificationNewSessionCount: 0,
    verificationLastRecurrenceAt: null,
    resolvedSource: null,
    latestEvolveTaskId: null,
    latestEvolveTaskStatus: null,
    appliedEvolveTaskId: null,
    appliedBy: null,
    appliedAt: null,
    version: 3,
    createdBy: "governance-agent",
    gmtCreate: 1,
    gmtModified: 1,
    evidence: [{
      sessionId: "session-42",
      taskIndex: 0,
      ordinal: 0,
      taskDescription: "执行打包任务",
      failureClass: "CONFIG_MISSING",
      reasoningSummary: "字段不存在",
      payloadRef: "oss://fixture/session-42.json",
      payloadEtag: "etag-42",
      payloadVersionId: "v1",
    }],
    evolveLinks: [],
  } as unknown as ImprovementDetail;
  harness.insightBridge.getDetail.mockResolvedValue(detail);

  const created = await createTask({
    insightImprovementId: 42,
    insightRequestId: "insight-repair-run-1",
    repairDirection: "只允许修改 workflow_engine_dispatch 查询模板",
  });

  expect(created.config.insightSource).toEqual(expect.objectContaining({
    improvementId: 42,
    requestId: "insight-repair-run-1",
    evidenceCount: 1,
    sessionIds: ["session-42"],
    authorizationMode: "ONCE",
    repairDirection: "只允许修改 workflow_engine_dispatch 查询模板",
  }));
  expect(harness.insightBridge.freezeTask).toHaveBeenCalledWith(expect.objectContaining({
    taskId: created.taskId,
    repairDirection: "只允许修改 workflow_engine_dispatch 查询模板",
  }));
  expect(harness.insightBridge.linkTask).toHaveBeenCalledWith(expect.objectContaining({
    improvementId: 42,
    evolveTaskId: created.taskId,
    requestId: "insight-repair-run-1",
  }));

  const bootstrap = await harness.service.bootstrap(created.identity);
  expect(bootstrap).toEqual(expect.objectContaining({
    insightSource: expect.objectContaining({ improvementId: 42, sessionIds: ["session-42"] }),
    insightPlanSource: { status: "ready" },
  }));
});

it("records a secret-safe Insight projection in the bootstrap audit", async () => {
  const created = await createTask();
  await harness.repo.updateTaskConfig(created.taskId, {
    ...created.config,
    insightSource: {
      sourceType: "insight_improvement",
      improvementId: 99,
      requestId: "insight-secret-projection",
      version: 1,
      title: "历史会话包含认证材料",
      sourceBatchId: "batch-99",
      evidenceCount: 1,
      sessionIds: ["session-99"],
      evidenceTaskRefs: [{ sessionId: "session-99", taskIndex: 0, ordinal: 0 }],
      repairDirection: null,
      authorizationMode: "ONCE",
    } satisfies RepairInsightSource,
  });
  const rawSecret = "sk-insight-secret-value-123456789";
  harness.insightBridge.resolvePlanSource.mockResolvedValue({
    descriptorVersion: "plan-source-descriptor/v2",
    sourceType: "insight_improvement",
    schemaVersion: "plan-source/v2",
    digest: "sha256:original",
    delivery: {
      type: "inline",
      content: {
        schema_version: "plan-source/v2",
        cases: [{
          evidence: {
            messages: [
              { message_index: 1, content: "正常调查结论应当保留" },
              { message_index: 2, content: `apiKey=${rawSecret} request failed` },
            ],
          },
        }],
      },
    },
  });

  const bootstrap = await harness.service.bootstrap(created.identity);
  const serialized = JSON.stringify(bootstrap);
  const audit = (await harness.repairRepo.listToolCalls(created.taskId))
    .find((call) => call.operation === "bootstrap");

  expect(serialized).toContain("正常调查结论应当保留");
  expect(serialized).toContain("[REDACTED_SECRET_TEXT]");
  expect(serialized).not.toContain(rawSecret);
  expect(audit?.status).toBe("succeeded");
  expect(audit?.result).toEqual(bootstrap);
  expect(() => assertRepairAuditSecretFree(audit?.result, "terminalEnvelope.result")).not.toThrow();
});

it("continues Repair bootstrap when the optional Insight source cannot be safely projected", async () => {
  const created = await createTask();
  await harness.repo.updateTaskConfig(created.taskId, {
    ...created.config,
    insightSource: {
      sourceType: "insight_improvement",
      improvementId: 100,
      requestId: "insight-unsafe-projection",
      version: 1,
      title: "异常证据结构",
      sourceBatchId: "batch-100",
      evidenceCount: 1,
      sessionIds: ["session-100"],
      evidenceTaskRefs: [{ sessionId: "session-100", taskIndex: 0, ordinal: 0 }],
      repairDirection: null,
      authorizationMode: "ONCE",
    } satisfies RepairInsightSource,
  });
  const circular: Record<string, unknown> = { status: "ready" };
  circular.delivery = circular;
  harness.insightBridge.resolvePlanSource.mockResolvedValue(circular);

  const bootstrap = await harness.service.bootstrap(created.identity);
  const audit = (await harness.repairRepo.listToolCalls(created.taskId))
    .find((call) => call.operation === "bootstrap");

  expect(bootstrap.insightPlanSource).toEqual(expect.objectContaining({
    status: "unavailable",
    reason: "unsafe_projection",
  }));
  expect(audit?.status).toBe("succeeded");
  expect(() => assertRepairAuditSecretFree(audit?.result, "terminalEnvelope.result")).not.toThrow();
});

it("degrades an oversized Insight projection before the immutable bootstrap audit", async () => {
  const created = await createTask();
  await harness.repo.updateTaskConfig(created.taskId, {
    ...created.config,
    insightSource: {
      sourceType: "insight_improvement",
      improvementId: 101,
      requestId: "insight-oversized-projection",
      version: 1,
      title: "超大可选证据",
      sourceBatchId: "batch-101",
      evidenceCount: 1,
      sessionIds: ["session-101"],
      evidenceTaskRefs: [{ sessionId: "session-101", taskIndex: 0, ordinal: 0 }],
      repairDirection: null,
      authorizationMode: "ONCE",
    } satisfies RepairInsightSource,
  });
  harness.insightBridge.resolvePlanSource.mockResolvedValue({
    status: "ready",
    delivery: { type: "inline", content: { evidence: "x".repeat(300_000) } },
  });

  const bootstrap = await harness.service.bootstrap(created.identity);
  const audit = (await harness.repairRepo.listToolCalls(created.taskId))
    .find((call) => call.operation === "bootstrap");

  expect(bootstrap.insightPlanSource).toEqual(expect.objectContaining({
    status: "unavailable",
    reason: "unsafe_projection",
  }));
  expect(audit?.status).toBe("succeeded");
  expect(JSON.stringify(audit?.result).length).toBeLessThan(256 * 1_024);
});

it("validates a supplied persistent grant against the current Insight scope", async () => {
  const detail = {
    improvementId: 43,
    ownerUserId: ACTOR,
    botOwnerUserId: ACTOR,
    botId: BOT_ID,
    title: "修复配置缺失",
    userGuidance: null,
    sourceType: "ADMIN_RULE_DIRECT_EVOLUTION",
    sourceRuleId: "config-missing-v1",
    evidenceCount: 1,
    sessionCount: 1,
    dataStartTime: null,
    dataEndTime: null,
    dataAsOf: "2026-08-17T00:00:00.000Z",
    batchId: "batch-43",
    status: "ACTIVE",
    actionType: "DIRECT_EVOLUTION",
    assignmentReason: null,
    rootCauseSummary: "缺少运行配置",
    suggestedAction: "补齐测试 Bot 配置",
    adminReviewStatus: "APPROVED",
    adminReviewedBy: "admin-1",
    adminReviewedAt: null,
    adminReviewComment: null,
    rejectReasonCode: null,
    rejectComment: null,
    rejectedAt: null,
    handledAt: null,
    verificationStatus: "NOT_STARTED",
    verificationLastCheckedAt: null,
    verificationNewSessionCount: 0,
    verificationLastRecurrenceAt: null,
    resolvedSource: null,
    latestEvolveTaskId: null,
    latestEvolveTaskStatus: null,
    appliedEvolveTaskId: null,
    appliedBy: null,
    appliedAt: null,
    version: 1,
    createdBy: "governance-agent",
    gmtCreate: 1,
    gmtModified: 1,
    evidence: [{
      sessionId: "session-43",
      taskIndex: 0,
      ordinal: 0,
      taskDescription: "执行任务",
      failureClass: "CONFIG_MISSING",
      reasoningSummary: "配置缺失",
      payloadRef: "oss://fixture/session-43.json",
      payloadEtag: "etag-43",
      payloadVersionId: "v1",
    }],
    evolveLinks: [],
  } as unknown as ImprovementDetail;
  harness.insightBridge.getDetail.mockResolvedValue(detail);

  const created = await createTask({
    insightImprovementId: 43,
    insightRequestId: "insight-repair-persistent-1",
    authorizationGrantId: 7,
  });

  expect(harness.insightBridge.validatePersistentAuthorization).toHaveBeenCalledWith({
    ownerUserId: ACTOR,
    botId: BOT_ID,
    improvement: detail,
    grantId: 7,
  });
  expect(created.config.insightSource).toEqual(expect.objectContaining({
    authorizationMode: "PERSISTENT",
    authorizationGrantId: 7,
  }));
});

it("does not accept a persistent grant supplied by an administrator on behalf of an owner", async () => {
  harness.insightBridge.getDetail.mockResolvedValue({
    improvementId: 43,
    ownerUserId: ACTOR,
    botOwnerUserId: ACTOR,
    botId: BOT_ID,
    title: "配置缺失",
    status: "ACTIVE",
    actionType: "DIRECT_EVOLUTION",
    sourceRuleId: "config-missing-v1",
    evidenceCount: 0,
    sessionCount: 0,
    version: 1,
    evidence: [],
  } as unknown as ImprovementDetail);
  await expect(harness.service.createTask({
    actorUserId: "another-admin",
    authHeaders: { cookie: "SSO=admin" },
    body: {
      targetEnvironment: "pre",
      botId: BOT_ID,
      targetUserId: ACTOR,
      adminOverrideReason: "管理员代处理测试",
      insightImprovementId: 43,
      insightRequestId: "insight-admin-grant-1",
      authorizationGrantId: 7,
      symptom: "配置缺失",
    },
    isAdmin: true,
  })).rejects.toMatchObject({ status: 403, code: "repair_authorization_owner_required" });
  expect(harness.insightBridge.validatePersistentAuthorization).not.toHaveBeenCalled();
});

it("terminates the active Repair execution, stops its AIS job and invalidates the workload", async () => {
  const created = await createTask();

  const result = await harness.service.terminateTask({
    actorUserId: ACTOR,
    taskId: created.taskId,
    reason: "用户结束本次实验",
  }) as { status: string; termination: { status: string; aisJobId: string | null } };

  expect(result.status).toBe("canceled");
  expect(result.termination).toEqual({ status: "remote_stopped", aisJobId: "job-1" });
  expect(harness.stopExecution).toHaveBeenCalledWith("job-1");
  const task = await harness.repo.findTask(created.taskId);
  const step = await harness.repo.findStep(created.config.current.stepId);
  const config = JSON.parse(task!.config_json) as RepairTaskConfig;
  expect(task?.status).toBe("canceled");
  expect(step).toMatchObject({
    status: "canceled",
    error_code: "REPAIR_TERMINATED_BY_USER",
    error_message: "用户结束本次实验",
    retryable: 0,
  });
  expect(config.execution).toMatchObject({ state: "ended", invalidatedAt: 1_000 });
  expect(config.pendingDecision).toBeNull();
  await expect(harness.service.reportStep(created.identity, { status: "running" }))
    .rejects.toMatchObject({ code: "repair_step_already_terminal" });
});

it("allows only the Repair owner to terminate an execution", async () => {
  const created = await createTask();

  await expect(harness.service.terminateTask({
    actorUserId: "shared-viewer",
    taskId: created.taskId,
    reason: "尝试终止共享任务",
  })).rejects.toMatchObject({ status: 403, code: "repair_task_forbidden" });

  expect(harness.stopExecution).not.toHaveBeenCalled();
  await expect(harness.repo.findTask(created.taskId)).resolves.toMatchObject({ status: "running" });
});

it("stops a late AIS job created while the Repair dispatch is being terminated", async () => {
  harness.execute.mockImplementationOnce(async () => {
    const createCall = harness.repairRepo.createTaskWithStep as unknown as ReturnType<typeof vi.fn>;
    const created = createCall.mock.calls[0]?.[0] as CreateRepairTaskWithStepInput;
    await harness.service.terminateTask({
      actorUserId: ACTOR,
      taskId: created.task.taskId,
      reason: "在 AIS 分配期间终止",
    });
    return "job-late";
  });

  const created = await createTask();

  await expect(harness.repo.findTask(created.taskId)).resolves.toMatchObject({ status: "canceled" });
  expect(harness.stopExecution).toHaveBeenCalledTimes(1);
  expect(harness.stopExecution).toHaveBeenCalledWith("job-late");
  await expect(harness.repo.findStep(created.config.current.stepId)).resolves.toMatchObject({
    status: "canceled",
    bot_run_id: "job-late",
  });
});

function writePlan(
  taskConfig: RepairTaskConfig,
  actions?: RepairPlanArtifact["actions"],
  options: {
    schemaVersion?: typeof REPAIR_PLAN_VERSION | typeof LEGACY_REPAIR_PLAN_VERSION;
    recommendation?: RepairPlanRecommendation;
    quality?: RepairPlanQuality;
  } = {},
): { plan: RepairPlanArtifact; content: Buffer; digest: string } {
  const resolvedActions = actions ?? [{
    actionId: "restart-gateway",
    type: "container_command" as const,
    summary: "重启网关进程",
    risk: "可能产生短暂中断",
    verification: "重新读取当前健康状态",
    rollback: null,
    command: "supervisorctl restart gateway",
  }];
  const base = {
    taskId: taskConfig.taskId,
    stepId: taskConfig.current.stepId,
    attempt: taskConfig.current.attempt,
    authorizationScopeDigest: taskConfig.authorizationScopeDigest,
    runtimeTargetVersion: taskConfig.runtimeTarget.version,
    diagnosis: { facts: ["网关当前不可用"], inferences: [], unknowns: [] },
    actions: resolvedActions,
  };
  const schemaVersion = options.schemaVersion ?? REPAIR_PLAN_VERSION;
  const plan: RepairPlanArtifact = schemaVersion === LEGACY_REPAIR_PLAN_VERSION
    ? { ...base, schemaVersion }
    : {
      ...base,
      schemaVersion,
      quality: options.quality ?? "verified",
      recommendation: options.recommendation ?? (resolvedActions.length > 0
        ? {
          disposition: "execute_actions",
          summary: "建议执行最小修复操作",
          reason: "现有证据支持按批准动作修复并验证。",
        }
        : {
          disposition: "no_change",
          summary: "建议本次不执行修复",
          reason: "现有证据未显示需要写操作。",
        }),
    };
  const content = Buffer.from(JSON.stringify(plan));
  harness.objects.set(taskConfig.current.artifacts.plan.objectKey, content);
  return { plan, content, digest: hash(content) };
}

function artifactMetadata(taskConfig: RepairTaskConfig, primaryName: "plan" | "applyResult", primary: Buffer | string) {
  const primaryDigest = hash(primary);
  return Object.fromEntries(Object.entries(taskConfig.current.artifacts).map(([name, item]) => [name, {
    objectKey: item.objectKey,
    size: name === primaryName ? Buffer.byteLength(primary) : 1,
    sha256: name === primaryName ? primaryDigest : hash(name),
  }]));
}

async function reportPlanReady(
  created: Awaited<ReturnType<typeof createTask>>,
  actions?: RepairPlanArtifact["actions"],
  options?: Parameters<typeof writePlan>[2],
) {
  const written = writePlan(created.config, actions, options);
  await harness.service.reportStep(created.identity, {
    status: "succeeded",
    output: {
      schemaVersion: REPAIR_CONTRACT_VERSION,
      taskId: created.taskId,
      stepId: created.config.current.stepId,
      attempt: created.config.current.attempt,
      phase: "repair_plan",
      artifactDigest: written.digest,
      artifacts: artifactMetadata(created.config, "plan", written.content),
      summary: "plan ready",
    },
  });
  return written;
}

async function advanceToReplan(
  created: Awaited<ReturnType<typeof createTask>>,
  actions?: RepairPlanArtifact["actions"],
  options?: Parameters<typeof writePlan>[2],
) {
  const written = await reportPlanReady(created, actions, options);
  await harness.service.decidePlan({
    actorUserId: ACTOR,
    authHeaders: { cookie: "SSO=decision-cookie" },
    taskId: created.taskId,
    body: { decision: "reject", reason: "补充证据后重新生成方案" },
  });
  await harness.service.claimDecision(created.identity);
  return written;
}

async function replaceHistoricalPlan(
  created: Awaited<ReturnType<typeof createTask>>,
  plan: RepairPlanArtifact,
): Promise<string> {
  const task = (await harness.repo.findTask(created.taskId))!;
  const config = JSON.parse(task.config_json) as RepairTaskConfig;
  const history = config.history.find(item => item.stepId === created.config.current.stepId)!;
  const step = (await harness.repo.findStep(history.stepId))!;
  const output = JSON.parse(step.output_json!) as Record<string, unknown>;
  const artifacts = output.artifacts as Record<string, Record<string, unknown>>;
  const content = Buffer.from(JSON.stringify(plan));
  const digest = hash(content);
  output.artifactDigest = digest;
  artifacts.plan.size = content.length;
  artifacts.plan.sha256 = digest;
  step.output_json = JSON.stringify(output);
  history.artifactDigest = digest;
  await harness.repo.updateTaskConfig(created.taskId, config);
  harness.objects.set(String(artifacts.plan.objectKey), content);
  return digest;
}

async function advanceToApply(created: Awaited<ReturnType<typeof createTask>>): Promise<RepairTaskConfig> {
  const written = await reportPlanReady(created);
  await harness.service.decidePlan({
    actorUserId: ACTOR,
    authHeaders: { cookie: "SSO=decision-cookie" },
    taskId: created.taskId,
    body: { decision: "approve", artifactDigest: written.digest },
  });
  await harness.service.claimDecision(created.identity);
  const row = await harness.repo.findTask(created.taskId);
  return JSON.parse(row!.config_json) as RepairTaskConfig;
}

async function advanceToRestartApply(created: Awaited<ReturnType<typeof createTask>>): Promise<RepairTaskConfig> {
  const written = await reportPlanReady(created, [{
    actionId: "restart-bot",
    type: "ocb_operation",
    summary: "重启当前 Bot",
    risk: "服务会短暂不可用",
    verification: "复查原始症状",
    rollback: null,
    operation: { type: "restart_bot", params: {} },
  }]);
  await harness.service.decidePlan({
    actorUserId: ACTOR,
    authHeaders: { cookie: "SSO=decision-cookie" },
    taskId: created.taskId,
    body: { decision: "approve", artifactDigest: written.digest },
  });
  await harness.service.claimDecision(created.identity);
  const row = await harness.repo.findTask(created.taskId);
  return JSON.parse(row!.config_json) as RepairTaskConfig;
}

it("binds the approved action IDs into the Apply bootstrap contract", async () => {
  const created = await createTask();
  const applyConfig = await advanceToApply(created);

  const bootstrap = await harness.service.bootstrap({
    taskId: created.taskId,
    stepId: applyConfig.current.stepId,
    executionId: applyConfig.execution.executionId,
  });

  expect(bootstrap.approvedPlan).toMatchObject({
    stepId: created.config.current.stepId,
    actionIds: ["restart-gateway"],
  });
});

it("promotes the latest user feedback into a stable Plan investigation requirement", async () => {
  const created = await createTask();
  await reportPlanReady(created);
  await harness.service.decidePlan({
    actorUserId: ACTOR,
    authHeaders: { cookie: "SSO=decision-cookie" },
    taskId: created.taskId,
    body: { decision: "reject", reason: "请补充说明变更发生的时间和依据" },
  });
  await harness.service.claimDecision(created.identity);
  const row = await harness.repo.findTask(created.taskId);
  const config = JSON.parse(row!.config_json) as RepairTaskConfig;

  const bootstrap = await harness.service.bootstrap({
    taskId: created.taskId,
    stepId: config.current.stepId,
    executionId: config.execution.executionId,
  });

  expect(bootstrap.investigationRequirements).toEqual([{
    requirementId: `user-feedback:${created.config.current.stepId}`,
    source: "user_feedback",
    text: "请补充说明变更发生的时间和依据",
    introducedBy: {
      stepId: created.config.current.stepId,
      stepNo: 1,
      attempt: 1,
      phase: "repair_plan",
    },
  }]);
});

it("projects a rejected Plan audit into a new execution bootstrap", async () => {
  const created = await createTask();
  const inspected = await harness.service.inspectRuntime(created.identity, {
    clientRequestId: "rejected-plan-continuation-source",
    purpose: "读取当前配置并形成下一轮可以复用的结论",
    operation: "fs_read",
    path: "/home/admin/.openclaw/openclaw.json",
    startLine: 1,
    lines: 20,
  });
  const source = await harness.repairRepo.findToolCall(String(inspected.toolCallId));
  await harness.service.recordSemanticConclusion(created.identity, {
    sourceToolCallId: source!.callId,
    evidenceToolCallIds: [source!.callId],
    conclusionZh: "上一轮已经确认当前配置中存在需要进一步追溯来源的变更。",
    nextAction: "只补充配置来源证据，不要重复读取同一份当前配置。",
  });
  await reportPlanReady(created);

  harness.now.value += harness.repairConfig.decisionGraceSeconds + 1;
  await harness.service.decidePlan({
    actorUserId: ACTOR,
    authHeaders: { cookie: "SSO=decision-cookie" },
    taskId: created.taskId,
    body: { decision: "reject", reason: "请补充说明这项配置变更的来源" },
  });

  const row = await harness.repo.findTask(created.taskId);
  const replanned = JSON.parse(row!.config_json) as RepairTaskConfig;
  expect(replanned.execution.executionId).not.toBe(created.config.execution.executionId);
  expect(harness.execute).toHaveBeenCalledTimes(2);

  const bootstrap = await harness.service.bootstrap({
    taskId: created.taskId,
    stepId: replanned.current.stepId,
    executionId: replanned.execution.executionId,
  });

  expect(bootstrap.recoveryContext).toMatchObject({
    stepId: replanned.current.stepId,
    executionId: replanned.execution.executionId,
    toolCalls: [],
    priorStep: {
      stepId: created.config.current.stepId,
      attempt: 1,
      phase: "repair_plan",
      status: "succeeded",
      feedback: "请补充说明这项配置变更的来源",
      truncated: false,
      unconcludedToolCallIds: [],
      incompleteToolCallIds: [],
      toolCalls: [expect.objectContaining({
        toolCallId: source!.callId,
        conclusion: {
          text: "上一轮已经确认当前配置中存在需要进一步追溯来源的变更。",
          nextAction: "只补充配置来源证据，不要重复读取同一份当前配置。",
          evidenceToolCallIds: [source!.callId],
        },
      })],
    },
  });
  expect((bootstrap.recoveryContext as { priorStep: Record<string, unknown> }).priorStep)
    .not.toHaveProperty("failure");
});

it("does not invent investigation requirements without explicit user feedback", async () => {
  const created = await createTask();

  const bootstrap = await harness.service.bootstrap(created.identity);

  expect(bootstrap.investigationRequirements).toEqual([]);
});

it("exposes bounded Repair Agent recovery limits through bootstrap timings", async () => {
  const created = await createTask();

  const bootstrap = await harness.service.bootstrap(created.identity);

  expect(bootstrap.timings).toMatchObject({
    agentTimeoutSeconds: 900,
    agentCloseoutTimeoutSeconds: 180,
    maxAgentAutoRecoveries: 2,
    agentCorrectionTimeoutSeconds: 120,
    maxAgentOutputCorrectionRetries: 3,
    maxAgentRateLimitRetries: 3,
    agentRateLimitRetryBaseSeconds: 5,
  });
});

it("exposes the complete authoritative write ledger for Apply finalization recovery", async () => {
  const created = await createTask();
  const applyConfig = await advanceToApply(created);
  const toolCallId = await runApprovedAction(created, applyConfig);

  const bootstrap = await harness.service.bootstrap({
    taskId: created.taskId,
    stepId: applyConfig.current.stepId,
    executionId: applyConfig.execution.executionId,
  });

  expect(bootstrap.recoveryContext).toMatchObject({
    writeAttemptsTruncated: false,
    writeAttempts: [{
      toolCallId,
      toolName: "baas_write",
      actionId: "restart-gateway",
      status: "succeeded",
    }],
  });
});

it("exposes a bounded execution-scoped audit projection for compact timeout recovery", async () => {
  const created = await createTask();
  const inspected = await harness.service.inspectRuntime(created.identity, {
    clientRequestId: "compact-recovery-source",
    purpose: "读取 OpenClaw 配置并确认当前运行参数",
    operation: "fs_read",
    path: "/home/admin/.openclaw/openclaw.json",
    startLine: 1,
    lines: 20,
  });
  const source = await harness.repairRepo.findToolCall(String(inspected.toolCallId));
  await harness.service.recordSemanticConclusion(created.identity, {
    sourceToolCallId: source!.callId,
    evidenceToolCallIds: [source!.callId],
    conclusionZh: "配置读取成功，当前证据可用于生成修复方案。",
    nextAction: "停止扩展调查并生成最终方案。",
  });

  const bootstrap = await harness.service.bootstrap(created.identity);

  expect(bootstrap.recoveryContext).toMatchObject({
    schemaVersion: "ce-repair-recovery-context/v1",
    taskId: created.taskId,
    stepId: created.identity.stepId,
    executionId: created.identity.executionId,
    phase: "repair_plan",
    truncated: false,
    toolCalls: [expect.objectContaining({
      toolCallId: source!.callId,
      operation: "fs_read",
      purpose: "读取 OpenClaw 配置并确认当前运行参数",
      resultSummary: "文件片段读取完成，返回 2 行。",
      conclusion: {
        text: "配置读取成功，当前证据可用于生成修复方案。",
        nextAction: "停止扩展调查并生成最终方案。",
        evidenceToolCallIds: [source!.callId],
      },
    })],
  });
  const rendered = JSON.stringify(bootstrap.recoveryContext);
  expect(rendered).not.toContain("line one");
  expect(rendered).not.toContain("SSO=");
  expect(rendered).not.toContain("safeInvocation");
  expect(rendered).not.toContain("executionTicket");
});

it("keeps the first and latest evidence when compact timeout recovery audit is truncated", async () => {
  const created = await createTask();
  const sourceIds: string[] = [];
  for (let index = 0; index < 41; index += 1) {
    const inspected = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: `compact-recovery-bounded-${index}`,
      purpose: `读取第 ${index + 1} 项运行证据并判断是否可以收口`,
      operation: "fs_stat",
      path: `/home/admin/evidence-${index}`,
    });
    const source = await harness.repairRepo.findToolCall(String(inspected.toolCallId));
    sourceIds.push(source!.callId);
    await harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: source!.callId,
      evidenceToolCallIds: [source!.callId],
      conclusionZh: `第 ${index + 1} 项证据已经完成核验并形成结论。`,
      nextAction: index === 40 ? "停止调查并生成最终方案。" : "继续核验下一项独立证据。",
    });
  }

  const bootstrap = await harness.service.bootstrap(created.identity);
  const recovery = bootstrap.recoveryContext as {
    truncated: boolean;
    toolCalls: Array<{ toolCallId: string }>;
  };
  const projectedIds = recovery.toolCalls.map((call) => call.toolCallId);

  expect(recovery.truncated).toBe(true);
  expect(projectedIds).toHaveLength(40);
  expect(projectedIds).toEqual([...sourceIds.slice(0, 8), ...sourceIds.slice(-32)]);
  expect(projectedIds).not.toContain(sourceIds[8]);
});

it("defaults old and new Tasks to broad structured observation without raw shell", async () => {
  const created = await createTask();

  const bootstrap = await harness.service.bootstrap(created.identity);

  expect(created.config.diagnosticMode).toBe("observe");
  expect(bootstrap).toMatchObject({
    normalizedTimeRange: {
      fromUnixSeconds: created.config.issue.timeRange.from,
      toUnixSeconds: created.config.issue.timeRange.to,
      fromIsoUtc: new Date(created.config.issue.timeRange.from * 1_000).toISOString(),
      toIsoUtc: new Date(created.config.issue.timeRange.to * 1_000).toISOString(),
      queryUsesUnixSeconds: true,
    },
    tools: {
      filesystemScope: "container_user_readable",
      resultEvidenceLocators: "verified_result_v1",
      shellObservedLocators: "unverified_confirm_v1",
      rawShell: false,
    },
  });
  expect((bootstrap.tools as { runtimeRead: string[] }).runtimeRead).not.toContain("shell_exec");
});

it("exposes Task-authorized diagnostic shell and audits its exact command", async () => {
  const created = await createTask({ diagnosticMode: "deep" });
  const bootstrap = await harness.service.bootstrap(created.identity);

  expect(created.config.diagnosticMode).toBe("deep");
  expect(bootstrap).toMatchObject({ tools: { rawShell: true } });
  expect((bootstrap.tools as { runtimeRead: string[] }).runtimeRead).toContain("shell_exec");

  const result = await harness.service.inspectRuntime(created.identity, {
    clientRequestId: "deep-shell-1",
    purpose: "读取运行时版本和阶段日志以定位故障",
    operation: "shell_exec",
    command: "openclaw --version && ps -ef",
  });

  expect(result).toMatchObject({ status: "success", operation: "shell_exec" });
  expect(harness.inspectRuntime).toHaveBeenCalledWith(
    expect.objectContaining({ taskId: created.taskId }),
    { operation: "shell_exec", command: "openclaw --version && ps -ef" },
  );
  await expect(harness.service.getTask(ACTOR, created.taskId)).resolves.toMatchObject({
    diagnosticMode: "deep",
    toolCalls: expect.arrayContaining([expect.objectContaining({
      operation: "shell_exec",
      safeInvocation: {
        kind: "diagnostic_command",
        command: "openclaw --version && ps -ef",
      },
    })]),
  });
});

it("rejects raw shell before dispatch when the Task only authorized observation", async () => {
  const created = await createTask();

  await expect(harness.service.inspectRuntime(created.identity, {
    purpose: "尝试执行未经授权的深度诊断",
    operation: "shell_exec",
    command: "uname -a",
  })).rejects.toMatchObject({
    status: 403,
    code: "repair_diagnostic_shell_not_authorized",
  });
  expect(harness.inspectRuntime).not.toHaveBeenCalled();
});

async function reportApplyReady(
  created: Awaited<ReturnType<typeof createTask>>,
  applyConfig: RepairTaskConfig,
): Promise<void> {
  const toolCallId = await runApprovedAction(created, applyConfig);
  await reportApplyArtifact(created, applyConfig, validApplyResult(created, applyConfig, toolCallId));
}

async function runApprovedAction(
  created: Awaited<ReturnType<typeof createTask>>,
  applyConfig: RepairTaskConfig,
  options: { clientRequestId?: string; retry?: boolean } = {},
): Promise<string> {
  const actionResult = await harness.service.applyAction({
    taskId: created.taskId,
    stepId: applyConfig.current.stepId,
    executionId: applyConfig.execution.executionId,
  }, {
    clientRequestId: options.clientRequestId ?? `apply-${applyConfig.current.stepId}`,
    actionId: "restart-gateway",
    retry: options.retry,
  });
  return String(actionResult.toolCallId);
}

function validApplyResult(
  created: Awaited<ReturnType<typeof createTask>>,
  applyConfig: RepairTaskConfig,
  toolCallId: string,
): Record<string, unknown> {
  return {
    schemaVersion: "ce-repair-apply-result/v1",
    taskId: created.taskId,
    stepId: applyConfig.current.stepId,
    attempt: applyConfig.current.attempt,
    actions: [{
      actionId: "restart-gateway",
      status: "succeeded",
      attempts: [{
        status: "succeeded",
        toolCallId,
        evidence: ["已完成获批写操作"],
      }],
      verification: { status: "verified", evidence: ["已复查目标运行状态"] },
    }],
    verdict: "verified",
    evidence: [{ source: toolCallId, claim: "写操作与验证证据已绑定" }],
    summary: "修复动作已执行并完成验证",
  };
}

async function reportApplyArtifact(
  created: Awaited<ReturnType<typeof createTask>>,
  applyConfig: RepairTaskConfig,
  applyResult: Record<string, unknown>,
  outputSummary = "apply finished",
): Promise<void> {
  const applyContent = Buffer.from(JSON.stringify(applyResult));
  harness.objects.set(applyConfig.current.artifacts.applyResult.objectKey, applyContent);
  await harness.service.reportStep({
    taskId: created.taskId,
    stepId: applyConfig.current.stepId,
    executionId: applyConfig.execution.executionId,
  }, {
    status: "succeeded",
    output: {
      schemaVersion: REPAIR_CONTRACT_VERSION,
      taskId: created.taskId,
      stepId: applyConfig.current.stepId,
      attempt: applyConfig.current.attempt,
      phase: "repair_apply",
      artifactDigest: hash(applyContent),
      artifacts: artifactMetadata(applyConfig, "applyResult", applyContent),
      summary: outputSummary,
    },
  });
}

async function resumeAfterContextCheckpoint(input: {
  config: RepairTaskConfig;
  checkpoint: unknown;
  metadata?: Partial<{ objectKey: string; size: number; sha256: string }>;
}): Promise<RepairTaskConfig> {
  const content = Buffer.from(JSON.stringify(input.checkpoint));
  const checkpointKey = input.config.current.artifacts.checkpoint.objectKey;
  harness.objects.set(checkpointKey, content);
  await harness.service.reportStep({
    taskId: input.config.taskId,
    stepId: input.config.current.stepId,
    executionId: input.config.execution.executionId,
  }, {
    status: "waiting_context",
    output: {
      artifacts: {
        checkpoint: {
          objectKey: checkpointKey,
          size: content.byteLength,
          sha256: hash(content),
          ...input.metadata,
        },
      },
    },
  });
  await harness.service.resumeTask({
    actorUserId: ACTOR,
    authHeaders: { cookie: "SSO=resume-cookie" },
    taskId: input.config.taskId,
    body: {},
  });
  const row = await harness.repo.findTask(input.config.taskId);
  return JSON.parse(row!.config_json) as RepairTaskConfig;
}

async function bootstrapAfterContextRecovery(input: {
  created: Awaited<ReturnType<typeof createTask>>;
  checkpoint: unknown;
  metadata?: Partial<{ objectKey: string; size: number; sha256: string }>;
}): Promise<Record<string, unknown>> {
  const recovered = await resumeAfterContextCheckpoint({
    config: input.created.config,
    checkpoint: input.checkpoint,
    metadata: input.metadata,
  });
  return harness.service.bootstrap({
    taskId: input.created.taskId,
    stepId: recovered.current.stepId,
    executionId: recovered.execution.executionId,
  });
}

describe("RepairTaskService execution contract", () => {
  it("accepts multiline problem descriptions and normalizes pasted line endings", async () => {
    const created = await createTask({
      symptom: "first symptom line\r\n\r\nsecond symptom line",
      errorText: "first error line\rsecond error line",
    });

    expect(created.config.issue).toMatchObject({
      symptom: "first symptom line\n\nsecond symptom line",
      errorText: "first error line\nsecond error line",
    });
    const envelope = parseSnapshotEnvelope(harness.execute.mock.calls[0][1] as Record<string, string>);
    expect(envelope.input.issue).toMatchObject(created.config.issue);
  });

  it.each([
    ["NUL in symptom", { symptom: "invalid\0symptom" }],
    ["oversized symptom", { symptom: "x".repeat(4_001) }],
    ["oversized error text", { errorText: "x".repeat(2_001) }],
  ])("keeps multiline Repair input bounded: %s", async (_case, input) => {
    await expect(createTask(input)).rejects.toMatchObject({
      status: 400,
      code: "invalid_repair_input",
    });
    expect(harness.execute).not.toHaveBeenCalled();
  });

  it("lets an administrator inspect and operate an unshared Repair", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created);
    const ownerView = await harness.service.getTask(ACTOR, created.taskId);
    const adminView = await harness.service.getTask("another-admin", created.taskId, true);

    expect(adminView).toMatchObject({
      canOperate: true,
      canManageShare: true,
      canAdminOperate: true,
      canTerminate: true,
    });
    expect(adminView.plan).toEqual(ownerView.plan);
    expect(adminView.currentStep).toEqual(ownerView.currentStep);
    expect(adminView.steps).toEqual(ownerView.steps);
    expect(adminView.history).toEqual(ownerView.history);
    const progressed = await harness.service.decidePlan({
      actorUserId: "another-admin",
      authHeaders: { cookie: "SSO=admin" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
      isAdmin: true,
    });
    expect(progressed).toMatchObject({ canOperate: true, canManageShare: true, canAdminOperate: true });
  });

  it("lets an administrator create a frozen Repair for another owner without OCB target lookup", async () => {
    const adminId = "global-admin";
    const targetOwner = "target-owner";
    vi.mocked(harness.repo.resolveEvolveBotRuntimeForOwner).mockResolvedValue({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-other",
      provider: "baas",
      deviceId: "device-other",
      bindingStatus: "active",
      env: "pre",
      ownerId: targetOwner,
      accessType: "owner",
    });
    harness.resolveTarget.mockResolvedValue({ ...target(), ownerId: targetOwner, botId: "other-bot" });

    const created = await harness.service.createTask({
      actorUserId: adminId,
      isAdmin: true,
      authHeaders: { cookie: "SSO=admin" },
      body: {
        ownerId: targetOwner,
        botId: "other-bot",
        targetEnvironment: "pre",
        symptom: "目标 Bot 出现异常",
      },
    }) as { taskId: string };
    const task = await harness.repo.findTask(created.taskId);
    const stored = JSON.parse(task!.config_json) as RepairTaskConfig;
    expect(task).toMatchObject({ user_id: targetOwner, created_by: adminId, bot_id: "other-bot" });
    expect(stored.authorizationScope).toEqual({
      actorUserId: adminId,
      ownerId: targetOwner,
      botId: "other-bot",
      environment: "pre",
      executionMode: "ADMIN_ONCE",
    });
    expect(harness.resolveTarget).toHaveBeenCalledWith({
      environment: "pre",
      ownerId: targetOwner,
      botId: "other-bot",
    });
  });

  it("does not let a non-admin select another owner", async () => {
    await expect(createTask({ ownerId: "another-owner" }))
      .rejects.toMatchObject({ status: 403, code: "repair_admin_required" });
    expect(harness.resolveTarget).not.toHaveBeenCalled();
  });

  it("returns only Repair-supported Bots with the environment resolved from their current runtime", async () => {
    vi.mocked(harness.repo.listEvolveBots).mockResolvedValue([
      { botId: "pre-bot", botName: "Pre Bot", env: "stale", activeEngine: "openclaw", botType: "personal" },
      { botId: "dev-bot", botName: "Dev Bot", env: "dev", activeEngine: "openclaw", botType: "personal" },
      { botId: "missing-bot", botName: "Missing Bot", env: null, activeEngine: "openclaw", botType: "personal" },
    ]);
    vi.mocked(harness.repo.resolveEvolveBotRuntime).mockImplementation(async (_userId, botId) => ({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: `binding-${botId}`,
      provider: "baas",
      deviceId: `device-${botId}`,
      bindingStatus: "active",
      env: botId === "pre-bot" ? "prepub" : botId === "dev-bot" ? "dev" : null,
      ownerId: ACTOR,
      accessType: "owner",
    }));

    await expect(harness.service.listBots(ACTOR)).resolves.toEqual([
      {
        botId: "pre-bot",
        botName: "Pre Bot",
        ownerId: ACTOR,
        env: "pre",
        activeEngine: "openclaw",
        botType: "personal",
      },
    ]);
  });

  it("cancels abandoned runtime reads before recording a failed Step", async () => {
    const created = await createTask();
    const pending = await harness.repairRepo.createToolCall({
      callId: "rtc-abandoned-read",
      taskId: created.taskId,
      stepId: created.identity.stepId,
      executionId: created.identity.executionId,
      authorizationScopeDigest: created.config.authorizationScopeDigest,
      clientRequestId: "abandoned-read-before-failure",
      toolName: "baas_read",
      operation: "fs_stat",
      request: { operation: "fs_stat", path: "/home/admin" },
      isWrite: false,
    });

    await expect(harness.service.reportStep(created.identity, {
      status: "failed",
      error: { code: "OPENCLAW_INVOCATION_FAILED", message: "Agent 调用超时", retryable: true },
    })).resolves.toMatchObject({ ok: true, status: "failed" });

    expect(await harness.repo.findTask(created.taskId)).toMatchObject({ status: "failed" });
    expect(await harness.repairRepo.findToolCall(pending.call.callId)).toMatchObject({
      status: "canceled",
    });
  });

  it("marks an executing write unknown instead of replaying it when the Step fails", async () => {
    const created = await createTask();
    const pending = await harness.repairRepo.createToolCall({
      callId: "rtc-write-active-at-step-failure",
      taskId: created.taskId,
      stepId: created.identity.stepId,
      executionId: created.identity.executionId,
      authorizationScopeDigest: created.config.authorizationScopeDigest,
      clientRequestId: "write-active-at-step-failure",
      toolName: "ocb_write",
      operation: "restart_bot",
      isWrite: true,
      request: { operation: "restart_bot", params: {} },
    });
    await harness.repairRepo.claimToolCall({
      callId: pending.call.callId,
      executionId: pending.call.executionId,
      authorizationScopeDigest: pending.call.authorizationScopeDigest,
      leaseOwner: "browser-write",
      leaseExpiresAt: harness.now.value + 90,
      now: harness.now.value,
    });

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: { code: "OPENCLAW_INVOCATION_FAILED", message: "Agent 调用超时", retryable: true },
    });

    expect(await harness.repairRepo.findToolCall(pending.call.callId)).toMatchObject({
      status: "unknown",
      errorCode: "repair_write_outcome_unknown",
    });
  });

  it("derives the target environment from the current Bot runtime when the request omits it", async () => {
    vi.mocked(harness.repo.resolveEvolveBotRuntime).mockResolvedValue({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-prod",
      provider: "baas",
      deviceId: "device-prod",
      bindingStatus: "active",
      env: "gray",
      ownerId: ACTOR,
      accessType: "owner",
    });
    harness.resolveTarget.mockImplementationOnce(async (input: { environment: "pre" | "prod" }) => ({
      ...target(),
      environment: input.environment,
    }));

    const created = await harness.service.createTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: CREATE_COOKIE },
      body: {
        botId: BOT_ID,
        symptom: "gateway failed",
      },
    });

    expect(harness.resolveTarget).toHaveBeenCalledWith(expect.objectContaining({
      environment: "prod",
      ownerId: ACTOR,
      botId: BOT_ID,
    }));
    expect(created).toMatchObject({ targetEnvironment: "prod" });
  });

  it("fails closed when a legacy client submits an environment different from the current Bot runtime", async () => {
    vi.mocked(harness.repo.resolveEvolveBotRuntime).mockResolvedValue({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-prod",
      provider: "baas",
      deviceId: "device-prod",
      bindingStatus: "active",
      env: "prod",
      ownerId: ACTOR,
      accessType: "owner",
    });

    await expect(createTask()).rejects.toMatchObject({
      status: 409,
      code: "target_environment_mismatch",
    });
    expect(harness.resolveTarget).not.toHaveBeenCalled();
    expect(harness.repairRepo.createTaskWithStep).not.toHaveBeenCalled();
  });

  it("does not treat a collaborator-visible Bot runtime as an owned Repair target", async () => {
    vi.mocked(harness.repo.resolveEvolveBotRuntime).mockResolvedValue({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-shared",
      provider: "baas",
      deviceId: "device-shared",
      bindingStatus: "active",
      env: "pre",
      ownerId: "another-owner",
      accessType: "collaborator",
    });

    await expect(createTask()).rejects.toMatchObject({
      status: 403,
      code: "repair_target_not_owned",
    });
    expect(harness.resolveTarget).not.toHaveBeenCalled();
    expect(harness.repairRepo.createTaskWithStep).not.toHaveBeenCalled();
  });

  it("rejects a Bot whose current runtime environment is not pre or prod", async () => {
    vi.mocked(harness.repo.resolveEvolveBotRuntime).mockResolvedValue({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-dev",
      provider: "baas",
      deviceId: "device-dev",
      bindingStatus: "active",
      env: "dev",
      ownerId: ACTOR,
      accessType: "owner",
    });

    await expect(createTask()).rejects.toMatchObject({
      status: 400,
      code: "unsupported_target_environment",
    });
    expect(harness.resolveTarget).not.toHaveBeenCalled();
    expect(harness.repairRepo.createTaskWithStep).not.toHaveBeenCalled();
    expect(harness.execute).not.toHaveBeenCalled();
  });

  it("dispatches the default OpenClaw envelope without a model override and keeps credentials out of persistence", async () => {
    const created = await createTask();

    expect(harness.execute).toHaveBeenCalledTimes(1);
    const [userId, globalParams, snapshotId] = harness.execute.mock.calls[0] as [
      string, Record<string, string>, number,
    ];
    expect(userId).toBe(ACTOR);
    expect(snapshotId).toBe(62310015);
    expect(Object.keys(globalParams)).toEqual([REPAIR_PARAMS_KEY]);

    const envelope = parseSnapshotEnvelope(globalParams);
    expect(envelope).toMatchObject({
      schemaVersion: "clawevolve-task/v1",
      taskType: "repair",
      taskId: created.taskId,
      stepId: created.config.current.stepId,
      execution: {
        action: "repair_plan",
        executionId: created.config.execution.executionId,
        agentMode: "openclaw",
      },
      input: {
        agent: {
          openclaw: { useDefaultModelConfig: true, model: null },
        },
      },
      runtime: { executionTicket: expect.stringMatching(/^ce_repair_/) },
    });
    expect(JSON.stringify(envelope)).not.toContain("modelApiKey");
    expect(JSON.stringify(envelope)).not.toContain(CREATE_COOKIE);
    expect(harness.createSignedUrl.mock.calls).toEqual(expect.arrayContaining([
      [created.config.current.artifacts.plan.objectKey, "PUT", 86_400,
        { "Content-Type": "application/json; charset=utf-8" }],
      [created.config.current.artifacts.markdown.objectKey, "PUT", 86_400,
        { "Content-Type": "text/markdown; charset=utf-8" }],
      [created.config.current.artifacts.result.objectKey, "PUT", 86_400,
        { "Content-Type": "application/json; charset=utf-8" }],
      [created.config.current.artifacts.checkpoint.objectKey, "PUT", 86_400,
        { "Content-Type": "application/json; charset=utf-8" }],
    ]));

    const rawTicket = String(envelope.runtime.executionTicket);
    const taskRow = await harness.repo.findTask(created.taskId);
    const stepRow = await harness.repo.findStep(created.config.current.stepId);
    const persisted = `${taskRow!.config_json}\n${stepRow!.bot_response_json ?? ""}`;
    expect(persisted).not.toContain(CREATE_COOKIE);
    expect(persisted).not.toContain(rawTicket);
    expect((JSON.parse(taskRow!.config_json) as RepairTaskConfig).execution.ticketDigest)
      .toMatch(/^[a-f0-9]{64}$/);
    expect(created.config).toMatchObject({
      agentMode: "openclaw",
      llmUseDefault: true,
      llmModel: null,
      openclawUsesCustomApiKey: false,
      cfuseEngine: null,
      cfuseModel: null,
    });
    expect(await harness.repairRepo.listToolCalls(created.taskId)).toHaveLength(0);
  });

  it("uses the PRE control-plane Snapshot for a PRE task targeting a PROD Bot", async () => {
    vi.mocked(harness.repo.resolveEvolveBotRuntime).mockResolvedValue({
      activeEngine: "openclaw",
      botType: "personal",
      hasServiceBot: false,
      botStatus: "active",
      bindingId: "binding-prod",
      provider: "baas",
      deviceId: "device-prod",
      bindingStatus: "active",
      env: "prod",
      ownerId: ACTOR,
      accessType: "owner",
    });
    harness.resolveTarget.mockResolvedValue({ ...target(), environment: "prod" });

    const created = await createTask({ targetEnvironment: "prod" });

    expect(created.config.controlPlaneEnvironment).toBe("pre");
    expect(harness.execute.mock.calls[0]?.[2]).toBe(62310015);
  });

  it("uses the PROD control-plane Snapshot for a PROD task targeting a PRE Bot", async () => {
    harness.repairConfig.controlPlaneEnvironment = "prod";

    const created = await createTask();

    expect(created.config.controlPlaneEnvironment).toBe("prod");
    expect(harness.execute.mock.calls[0]?.[2]).toBe(62310016);
  });

  it("refreshes only the canonical artifact upload target for the active Step", async () => {
    const created = await createTask();
    harness.createSignedUrl.mockClear();
    harness.createSignedUrl.mockResolvedValueOnce("https://oss.example/refreshed?signature=secret");

    const refreshed = await harness.service.refreshArtifactUpload(created.identity, {
      artifactName: "plan",
    });

    expect(refreshed).toEqual({
      artifact: {
        name: "plan",
        objectKey: created.config.current.artifacts.plan.objectKey,
        contentType: "application/json; charset=utf-8",
        putUrl: "https://oss.example/refreshed?signature=secret",
      },
      expiresInSeconds: 86_400,
    });
    expect(harness.createSignedUrl).toHaveBeenCalledExactlyOnceWith(
      created.config.current.artifacts.plan.objectKey,
      "PUT",
      86_400,
      { "Content-Type": "application/json; charset=utf-8" },
    );

    await expect(harness.service.refreshArtifactUpload(created.identity, {
      artifactName: "unknown",
    })).rejects.toMatchObject({
      status: 400,
      code: "invalid_repair_artifact_name",
    });
  });

  it("shares a Repair task read-only without losing a concurrent execution update", async () => {
    const created = await createTask();
    const ownerView = await harness.service.getTask(ACTOR, created.taskId);
    expect(ownerView).toMatchObject({
      shared: false,
      canOperate: true,
      canManageShare: true,
    });
    expect(ownerView).not.toHaveProperty("authorizationScope");
    expect(ownerView.execution).not.toHaveProperty("executionId");
    expect(ownerView.execution).not.toHaveProperty("jobId");
    expect(ownerView.execution).not.toHaveProperty("ccSessionId");

    await expect(harness.service.getTask("other-user", created.taskId)).rejects.toMatchObject({
      status: 403,
      code: "repair_task_not_shared",
    });
    await expect(harness.service.setTaskShared({
      actorUserId: "other-user",
      taskId: created.taskId,
      shared: true,
    })).rejects.toMatchObject({ status: 403, code: "repair_task_forbidden" });

    const compareAndSet = vi.mocked(harness.repairRepo.compareAndSetTaskConfig);
    compareAndSet.mockClear();
    compareAndSet.mockImplementationOnce(async () => {
      const row = await harness.repo.findTask(created.taskId);
      const concurrent = JSON.parse(row!.config_json) as RepairTaskConfig;
      await harness.repo.updateTaskConfig(created.taskId, {
        ...concurrent,
        execution: {
          ...concurrent.execution,
          lastHeartbeatAt: 1_111,
          leaseExpiresAt: 1_222,
        },
      });
      return false;
    });

    const shared = await harness.service.setTaskShared({
      actorUserId: ACTOR,
      taskId: created.taskId,
      shared: true,
    });
    expect(compareAndSet).toHaveBeenCalledTimes(2);
    expect(shared).toMatchObject({
      shared: true,
      canOperate: true,
      canManageShare: true,
    });
    const persisted = JSON.parse((await harness.repo.findTask(created.taskId))!.config_json) as RepairTaskConfig;
    expect(persisted.shared).toBe(true);
    expect(persisted.execution).toMatchObject({
      lastHeartbeatAt: 1_111,
      leaseExpiresAt: 1_222,
    });

    const leakedTicket = "ce_repair_shared_view_canary_1234567890";
    const leakedApiKey = "sk-shared-view-canary-1234567890";
    const currentStep = await harness.repo.findStep(created.config.current.stepId);
    currentStep!.summary = `upstream echoed ${leakedTicket}`;
    const currentTask = await harness.repo.findTask(created.taskId);
    currentTask!.error_message = `provider rejected ${leakedApiKey}`;

    const readerView = await harness.service.getTask("other-user", created.taskId);
    expect(readerView).toMatchObject({
      shared: true,
      canOperate: false,
      canManageShare: false,
      canResume: false,
    });
    expect(JSON.stringify(readerView)).not.toContain(leakedTicket);
    expect(JSON.stringify(readerView)).not.toContain(leakedApiKey);
    await expect(harness.service.resumeTask({
      actorUserId: "other-user",
      authHeaders: { cookie: "SSO=reader" },
      taskId: created.taskId,
      body: {},
    })).rejects.toMatchObject({ status: 403, code: "repair_task_forbidden" });
  });

  it("passes a custom OpenClaw API key only to the current dispatch envelope", async () => {
    const apiKey = "openclaw-runtime-key-canary";
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "GLM-5.2",
      llmApiKey: apiKey,
    });
    const envelope = parseSnapshotEnvelope(harness.execute.mock.calls[0][1] as Record<string, string>);
    expect(envelope).toMatchObject({
      execution: { agentMode: "openclaw" },
      input: {
        agent: {
          openclaw: {
            useDefaultModelConfig: false,
            model: "GLM-5.2",
            modelApiKey: apiKey,
          },
        },
      },
    });
    expect(created.config).toMatchObject({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "GLM-5.2",
      openclawUsesCustomApiKey: true,
      cfuseEngine: null,
      cfuseModel: null,
    });
    const task = await harness.repo.findTask(created.taskId);
    const step = await harness.repo.findStep(created.config.current.stepId);
    const view = await harness.service.getTask(ACTOR, created.taskId);
    expect(view).toMatchObject({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "GLM-5.2",
      openclawUsesCustomApiKey: true,
      cfuseEngine: null,
      cfuseModel: null,
    });
    expect(JSON.stringify({ task, step, view })).not.toContain(apiKey);
    expect(JSON.stringify({ task, step, view })).not.toContain("modelApiKey");
  });

  it("passes the Repair OpenClaw DeepSeek preset unchanged to the AIS envelope", async () => {
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "DeepSeek-V4-Flash-0731",
    });

    expect(created.config).toMatchObject({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "DeepSeek-V4-Flash-0731",
      openclawUsesCustomApiKey: false,
    });
    expect(parseSnapshotEnvelope(
      harness.execute.mock.calls[0][1] as Record<string, string>,
    )).toMatchObject({
      input: {
        agent: {
          openclaw: {
            useDefaultModelConfig: false,
            model: "DeepSeek-V4-Flash-0731",
          },
        },
      },
    });
  });

  it("uses the default token for a selected OpenClaw model when no custom key was chosen", async () => {
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "Kimi-K2.5",
    });
    const first = parseSnapshotEnvelope(harness.execute.mock.calls[0][1] as Record<string, string>);
    expect(first).toMatchObject({
      input: { agent: { openclaw: { useDefaultModelConfig: false, model: "Kimi-K2.5" } } },
    });
    expect(JSON.stringify(first)).not.toContain("modelApiKey");
    expect(created.config.openclawUsesCustomApiKey).toBe(false);

    const written = await reportPlanReady(created);
    harness.now.value = 2_000;
    await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    });
    expect(harness.execute).toHaveBeenCalledTimes(2);
    const second = parseSnapshotEnvelope(harness.execute.mock.calls[1][1] as Record<string, string>);
    expect(second).toMatchObject({ input: { agent: { openclaw: { model: "Kimi-K2.5" } } } });
    expect(JSON.stringify(second)).not.toContain("modelApiKey");
  });

  it("never persists an AIS response body that echoes the execution ticket or a custom API key", async () => {
    const apiKey = "dispatch-\\\"key\\\\canary";
    let executionTicket = "";
    harness.execute.mockImplementationOnce(async (_userId, globalParams: Record<string, string>) => {
      executionTicket = String(parseSnapshotEnvelope(globalParams).runtime.executionTicket);
      throw new Error(`AIS executeSnapshot HTTP 503: ${JSON.stringify({
        error: { apiKey, executionTicket },
      })}`);
    });
    let caught: unknown;
    try {
      await harness.service.createTask({
        actorUserId: ACTOR,
        authHeaders: { cookie: CREATE_COOKIE },
        body: {
          targetEnvironment: "pre",
          botId: BOT_ID,
          symptom: "gateway failed",
          agentMode: "openclaw",
          llmUseDefault: false,
          llmModel: "GLM-5",
          llmApiKey: apiKey,
        },
      });
    } catch (error) {
      caught = error;
    }
    expect(caught).toMatchObject({ status: 502, code: "repair_ais_dispatch_failed" });
    expect(String((caught as Error).message)).toBe("Repair AIS dispatch failed (HTTP 503)");
    expect(vi.mocked(harness.repo.markDispatchFailed)).toHaveBeenCalledWith(
      expect.any(String),
      "Repair AIS dispatch failed (HTTP 503)",
    );
    const created = vi.mocked(harness.repairRepo.createTaskWithStep).mock.calls[0][0];
    const task = await harness.repo.findTask(created.task.taskId);
    const step = await harness.repo.findStep(created.step.stepId);
    const persisted = JSON.stringify({
      config: task?.config_json,
      taskError: task?.error_message,
      stepError: step?.error_message,
      thrown: String((caught as Error).message),
    });
    for (const secret of [apiKey, executionTicket]) {
      expect(secret).not.toBe("");
      expect(persisted).not.toContain(secret);
      expect(persisted).not.toContain(JSON.stringify(secret).slice(1, -1));
    }
  });

  it("dispatches the selected cfuse engine/model without accepting a model API key", async () => {
    const created = await createTask(CFUSE_AGENT_INPUT);
    const envelope = parseSnapshotEnvelope(harness.execute.mock.calls[0][1] as Record<string, string>);
    expect(envelope).toMatchObject({
      execution: { agentMode: "cfuse" },
      input: {
        agent: {
          cfuse: { engine: "claude-code", model: "Kimi-K2.5" },
        },
      },
    });
    expect(JSON.stringify(envelope)).not.toContain("modelApiKey");
    expect(created.config).toMatchObject({
      agentMode: "cfuse",
      llmUseDefault: true,
      llmModel: null,
      openclawUsesCustomApiKey: false,
      cfuseEngine: "claude-code",
      cfuseModel: "Kimi-K2.5",
    });
  });

  it("keeps a legacy Codex task readable but blocks any new execution", async () => {
    const created = await createTask(CFUSE_AGENT_INPUT);
    await harness.repo.updateTaskConfig(created.taskId, {
      ...created.config,
      cfuseEngine: "codex",
    });

    const view = await harness.service.getTask(ACTOR, created.taskId);
    expect(view).toMatchObject({
      cfuseEngine: "codex",
      executionSupported: false,
      executionBlock: {
        code: "repair_legacy_cfuse_engine_unsupported",
      },
      canResume: false,
    });
    await expect(harness.service.bootstrap(created.identity)).rejects.toMatchObject({
      status: 409,
      code: "repair_legacy_cfuse_engine_unsupported",
    });
    const beforeHeartbeat = (await harness.repo.findTask(created.taskId))!.config_json;
    await expect(harness.service.heartbeat(created.identity, { ccSessionId: "must-not-persist" }))
      .rejects.toMatchObject({
        status: 409,
        code: "repair_legacy_cfuse_engine_unsupported",
      });
    expect((await harness.repo.findTask(created.taskId))!.config_json).toBe(beforeHeartbeat);
    await expect(harness.service.reportStep(created.identity, { status: "running" }))
      .rejects.toMatchObject({
        status: 409,
        code: "repair_legacy_cfuse_engine_unsupported",
      });
    expect((await harness.repo.findStep(created.config.current.stepId))?.status).toBe("dispatched");
    await expect(harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=owner" },
      taskId: created.taskId,
      body: {},
    })).rejects.toMatchObject({
      status: 409,
      code: "repair_legacy_cfuse_engine_unsupported",
    });
  });

  it.each([
    [{ diagnosticMode: "admin" }, "invalid_repair_diagnostic_mode"],
    [{ agentMode: "openclaw", llmUseDefault: false }, "invalid_repair_input"],
    [{ agentMode: "openclaw", llmUseDefault: false, llmModel: "x".repeat(129) }, "invalid_repair_input"],
    [{ agentMode: "openclaw", llmUseDefault: true, llmApiKey: "unexpected" }, "invalid_openclaw_default_config"],
    [{ agentMode: "cfuse", cfuseEngine: "unsupported", cfuseModel: "Kimi-K2.5" }, "invalid_cfuse_engine"],
    [{ agentMode: "cfuse", cfuseEngine: "codex", cfuseModel: "Kimi-K2.5" }, "invalid_cfuse_engine"],
    [{ agentMode: "cfuse", cfuseEngine: "cfuse" }, "invalid_repair_input"],
  ])("rejects an invalid Agent selection without creating a Task: %j", async (agentInput, code) => {
    await expect(createTask(agentInput)).rejects.toMatchObject({ status: 400, code });
    expect(harness.execute).not.toHaveBeenCalled();
  });

  it("moves a successful Plan report to waiting_approval without dispatching Apply", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created);

    const task = await harness.repo.findTask(created.taskId);
    const step = await harness.repo.findStep(created.config.current.stepId);
    const taskConfig = JSON.parse(task!.config_json) as RepairTaskConfig;
    expect(task!.status).toBe("waiting_approval");
    expect(step).toMatchObject({ status: "succeeded", bot_run_id: "job-1" });
    expect(JSON.parse(step!.output_json!)).toMatchObject({
      phase: "repair_plan",
      artifactDigest: written.digest,
    });
    expect(taskConfig.execution).toMatchObject({
      state: "waiting_decision",
      jobId: "job-1",
      stepId: created.config.current.stepId,
    });
    expect(harness.execute).toHaveBeenCalledTimes(1);
    expect((await harness.repo.listSteps(created.taskId)).map((item) => item.step_type))
      .toEqual(["repair_plan"]);
  });

  it("retains the same safe Component failure diagnostics for every task reader", async () => {
    const created = await createTask();
    const rawOutputCanary = "Bearer raw-component-output-must-not-persist";
    const unknownCanary = "unknown-component-field-must-not-persist";

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_AGENT_OUTPUT_INVALID",
        message: "格式纠正后仍未返回合法的单个 JSON 对象",
        stage: "model_output_parse",
        reason: "format_retry_invalid",
        artifactName: "plan",
        exitCode: 45,
        httpStatus: 422,
        retryable: false,
        rawOutput: rawOutputCanary,
        unknownField: unknownCanary,
      },
    });

    const expectedFailure = {
      code: "REPAIR_AGENT_OUTPUT_INVALID",
      stage: "model_output_parse",
      reason: "format_retry_invalid",
      artifactName: "plan",
      exitCode: 45,
      httpStatus: 422,
      retryable: false,
    };
    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(ownerView.currentStep).toMatchObject({
      status: "failed",
      error: "格式纠正后仍未返回合法的单个 JSON 对象",
      failure: expectedFailure,
    });
    expect(ownerView.steps).toEqual([
      expect.objectContaining({ stepId: created.config.current.stepId, failure: expectedFailure }),
    ]);

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(persistedStep).toMatchObject({
      status: "failed",
      error_code: "REPAIR_AGENT_OUTPUT_INVALID",
      error_message: "格式纠正后仍未返回合法的单个 JSON 对象",
      retryable: 0,
    });
    expect(JSON.parse(persistedStep!.output_json!)).toEqual({
      failure: {
        schemaVersion: "ce-repair-step-failure/v1",
        stage: "model_output_parse",
        reason: "format_retry_invalid",
        artifactName: "plan",
        exitCode: 45,
        httpStatus: 422,
      },
    });
    expect(JSON.stringify(persistedStep)).not.toContain(rawOutputCanary);
    expect(JSON.stringify(persistedStep)).not.toContain(unknownCanary);

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(sharedView.currentStep).toMatchObject({
      error: "格式纠正后仍未返回合法的单个 JSON 对象",
      failure: expectedFailure,
    });
    expect(sharedView.steps[0]).toMatchObject({ failure: expectedFailure });
    expect(JSON.stringify(sharedView)).not.toContain(rawOutputCanary);
    expect(JSON.stringify(sharedView)).not.toContain(unknownCanary);
  });

  it("retains bounded OSS rejection diagnostics after upload retries are exhausted", async () => {
    const created = await createTask();

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_ARTIFACT_UPLOAD_FAILED",
        message: "Repair artifact upload failed",
        stage: "artifact_upload",
        reason: "http_rejected",
        artifactName: "plan",
        httpStatus: 403,
        providerCode: "SignatureDoesNotMatch",
        providerRequestId: "request-id-123",
        retryCount: 3,
        retryable: false,
      },
    });

    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      currentStep: { failure?: Record<string, unknown> } | null;
    };
    expect(ownerView.currentStep?.failure).toMatchObject({
      stage: "artifact_upload",
      reason: "http_rejected",
      artifactName: "plan",
      httpStatus: 403,
      providerCode: "SignatureDoesNotMatch",
      providerRequestId: "request-id-123",
      retryCount: 3,
      retryable: false,
    });
    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(JSON.parse(persistedStep!.output_json!)).toEqual({
      failure: {
        schemaVersion: "ce-repair-step-failure/v1",
        stage: "artifact_upload",
        reason: "http_rejected",
        artifactName: "plan",
        httpStatus: 403,
        providerCode: "SignatureDoesNotMatch",
        providerRequestId: "request-id-123",
        retryCount: 3,
      },
    });
  });

  it("retains only the safe credential-detection diagnostic for every task reader", async () => {
    const created = await createTask();
    const rawSecretCanary = "sk-component-output-secret-canary";

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_AGENT_OUTPUT_INVALID",
        message: "Repair Agent 输出包含凭据形态内容，已安全拒绝",
        stage: "model_output_security",
        reason: "credential_detected",
        retryable: false,
        rawOutput: `password=${rawSecretCanary}`,
        credential: rawSecretCanary,
      },
    });

    const expectedFailure = {
      code: "REPAIR_AGENT_OUTPUT_INVALID",
      stage: "model_output_security",
      reason: "credential_detected",
      retryable: false,
    };
    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(ownerView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(ownerView.steps).toEqual([expect.objectContaining({ failure: expectedFailure })]);

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(JSON.parse(persistedStep!.output_json!)).toEqual({
      failure: {
        schemaVersion: "ce-repair-step-failure/v1",
        stage: "model_output_security",
        reason: "credential_detected",
      },
    });
    expect(JSON.stringify(persistedStep)).not.toContain(rawSecretCanary);

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(sharedView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(sharedView.steps[0]).toMatchObject({ failure: expectedFailure });
  });

  it("retains only the safe locked-field correction diagnostic for every task reader", async () => {
    const created = await createTask();
    const rawSecretCanary = "ce_repair_correction_output_secret_canary";

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_AGENT_OUTPUT_INVALID",
        message: "输出纠正修改了锁定字段，已拒绝该结果",
        stage: "model_output_correction",
        reason: "locked_field_changed",
        retryable: false,
        rawOutput: `Bearer ${rawSecretCanary}`,
      },
    });

    const expectedFailure = {
      code: "REPAIR_AGENT_OUTPUT_INVALID",
      stage: "model_output_correction",
      reason: "locked_field_changed",
      retryable: false,
    };
    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(ownerView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(ownerView.steps).toEqual([expect.objectContaining({ failure: expectedFailure })]);

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(JSON.parse(persistedStep!.output_json!)).toEqual({
      failure: {
        schemaVersion: "ce-repair-step-failure/v1",
        stage: "model_output_correction",
        reason: "locked_field_changed",
      },
    });
    expect(JSON.stringify(persistedStep)).not.toContain(rawSecretCanary);

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(sharedView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(sharedView.steps[0]).toMatchObject({ failure: expectedFailure });
  });

  it("retains only the safe OpenClaw system-context diagnostic for every task reader", async () => {
    const created = await createTask();
    const rawSecretCanary = "ce_repair_system_context_secret_canary";

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "OPENCLAW_INVOCATION_FAILED",
        message: "Repair 系统上下文在 Agent 初始化期间被修改",
        stage: "agents_add",
        reason: "system_context_changed",
        retryable: false,
        rawOutput: `Bearer ${rawSecretCanary}`,
      },
    });

    const expectedFailure = {
      code: "OPENCLAW_INVOCATION_FAILED",
      stage: "agents_add",
      reason: "system_context_changed",
      retryable: false,
    };
    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(ownerView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(ownerView.steps).toEqual([expect.objectContaining({ failure: expectedFailure })]);

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(JSON.parse(persistedStep!.output_json!)).toEqual({
      failure: {
        schemaVersion: "ce-repair-step-failure/v1",
        stage: "agents_add",
        reason: "system_context_changed",
      },
    });
    expect(JSON.stringify(persistedStep)).not.toContain(rawSecretCanary);

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(sharedView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(sharedView.steps[0]).toMatchObject({ failure: expectedFailure });
  });

  it("retains bounded language-correction diagnostics for every task reader", async () => {
    const created = await createTask();

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_AGENT_OUTPUT_INVALID",
        message: "格式纠正已使用，尚未执行中文纠正",
        stage: "user_facing_language_validation",
        reason: "language_retry_invalid",
        field: "markdown.line[12]",
        rule: "chinese_dominance",
        retryBranch: "already_consumed",
        retryable: true,
      },
    });

    const expectedFailure = {
      code: "REPAIR_AGENT_OUTPUT_INVALID",
      stage: "user_facing_language_validation",
      reason: "language_retry_invalid",
      field: "markdown.line[12]",
      rule: "chinese_dominance",
      retryBranch: "already_consumed",
      retryable: true,
    };
    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(ownerView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(ownerView.steps).toEqual([expect.objectContaining({ failure: expectedFailure })]);

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(JSON.parse(persistedStep!.output_json!)).toEqual({
      failure: {
        schemaVersion: "ce-repair-step-failure/v1",
        stage: "user_facing_language_validation",
        reason: "language_retry_invalid",
        field: "markdown.line[12]",
        rule: "chinese_dominance",
        retryBranch: "already_consumed",
      },
    });

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    expect(sharedView.currentStep).toMatchObject({ failure: expectedFailure });
    expect(sharedView.steps[0]).toMatchObject({ failure: expectedFailure });
  });

  it.each([
    ["unknown field path", { field: "unknown.path", rule: "han_required", retryBranch: "still_non_chinese" }],
    ["oversized field path", { field: `markdown.line[${"1".repeat(260)}]`, rule: "han_required", retryBranch: "still_non_chinese" }],
    ["unknown language rule", { field: "summary", rule: "translate_everything", retryBranch: "still_non_chinese" }],
    ["unknown retry branch", { field: "summary", rule: "han_required", retryBranch: "retry_forever" }],
  ])("drops the structured failure envelope for an unsafe language diagnostic: %s", async (_label, diagnostic) => {
    const created = await createTask();
    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_AGENT_OUTPUT_INVALID",
        message: "Repair Agent 返回了安全的失败摘要",
        stage: "user_facing_language_validation",
        reason: "language_retry_invalid",
        ...diagnostic,
        retryable: false,
      },
    });

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(persistedStep?.output_json).toBeNull();
  });

  it("does not accept language-only diagnostic fields for another failure stage", async () => {
    const created = await createTask();
    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_AGENT_OUTPUT_INVALID",
        message: "格式纠正失败",
        stage: "model_output_parse",
        reason: "format_retry_invalid",
        field: "summary",
        rule: "han_required",
        retryBranch: "output_invalid",
        retryable: false,
      },
    });

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(persistedStep?.output_json).toBeNull();
  });

  it("finishes the Step with canonical diagnostics when Component failure fields contain credentials or invalid values", async () => {
    const created = await createTask();
    const codeSecret = "component-code-secret-must-not-persist";
    const messageSecret = "component-message-secret-must-not-persist";
    const reasonSecret = "component-reason-secret-must-not-persist";

    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: `apiKey=${codeSecret}`,
        message: `Cookie: ${messageSecret}`,
        stage: "model_output_parse",
        reason: `token=${reasonSecret}`,
        artifactName: "../raw-output",
        exitCode: 999,
        httpStatus: 999,
        retryable: "false",
      },
    });

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(persistedStep).toMatchObject({
      status: "failed",
      error_code: "REPAIR_EXECUTION_FAILED",
      error_message: "[REDACTED_SECRET_TEXT]",
      retryable: 0,
    });
    expect(persistedStep!.output_json).toBeNull();
    const persisted = JSON.stringify(persistedStep);
    expect(persisted).not.toContain(codeSecret);
    expect(persisted).not.toContain(messageSecret);
    expect(persisted).not.toContain(reasonSecret);
  });

  it.each([
    ["stage", `ce_repair_${"a".repeat(24)}`],
    ["reason", `ce_repair_${"b".repeat(24)}`],
    ["artifactName", `sk-${"c".repeat(24)}`],
    ["field", `ce_repair_${"d".repeat(24)}`],
    ["rule", `sk-${"e".repeat(24)}`],
    ["retryBranch", `ce_repair_${"f".repeat(24)}`],
  ])("does not persist a bare credential literal from failure metadata field %s", async (field, secret) => {
    const created = await createTask();
    const isLanguageDiagnostic = field === "field" || field === "rule" || field === "retryBranch";
    await harness.service.reportStep(created.identity, {
      status: "failed",
      error: {
        code: "REPAIR_AGENT_OUTPUT_INVALID",
        message: "Repair Agent 返回了安全的失败摘要",
        stage: isLanguageDiagnostic ? "user_facing_language_validation" : "model_output_parse",
        reason: isLanguageDiagnostic ? "language_retry_invalid" : "format_retry_invalid",
        artifactName: "plan",
        ...(isLanguageDiagnostic ? {
          field: "summary",
          rule: "han_required",
          retryBranch: "still_non_chinese",
        } : {}),
        [field]: secret,
        retryable: false,
      },
    });

    const persistedStep = await harness.repo.findStep(created.config.current.stepId);
    expect(JSON.stringify(persistedStep)).not.toContain(secret);
    expect(persistedStep?.output_json).toBeNull();
  });

  it("keeps a legacy failed Step readable when it has no structured failure envelope", async () => {
    const created = await createTask();
    const task = await harness.repo.findTask(created.taskId);
    const step = await harness.repo.findStep(created.config.current.stepId);
    task!.status = "failed";
    step!.status = "failed";
    step!.output_json = null;
    step!.error_code = "OPENCLAW_INVOCATION_FAILED";
    step!.error_message = "OpenClaw invocation failed";
    step!.retryable = 1;

    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
    };
    const expectedFailure = { code: "OPENCLAW_INVOCATION_FAILED", retryable: true };
    expect(ownerView.currentStep).toMatchObject({
      error: "OpenClaw invocation failed",
      failure: expectedFailure,
    });
    expect(ownerView.steps).toEqual([expect.objectContaining({ failure: expectedFailure })]);
  });

  it("shows shared viewers the same exact approved operations and commands as the owner", async () => {
    const created = await createTask();
    const exactCommand = "printf 'shared-plan-command-canary'";
    await reportPlanReady(created, [{
      actionId: "restart-bot",
      type: "ocb_operation",
      summary: "重启当前 Bot",
      risk: "服务会短暂不可用",
      verification: "复查原始症状",
      rollback: null,
      operation: {
        type: "restart_bot",
        params: {},
      },
    }, {
      actionId: "run-approved-command",
      type: "container_command",
      summary: "执行容器修复命令",
      risk: "命令会修改运行状态",
      verification: "重新检查运行状态",
      rollback: null,
      command: exactCommand,
    }]);

    const ownerView = await harness.service.getTask(ACTOR, created.taskId);
    expect((ownerView.plan as RepairPlanArtifact).actions[0].operation)
      .toEqual({ type: "restart_bot", params: {} });
    expect((ownerView.plan as RepairPlanArtifact).actions[1].command).toBe(exactCommand);

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId);
    expect(sharedView.plan).toEqual(ownerView.plan);
    expect((sharedView.plan as RepairPlanArtifact).actions[0].operation)
      .toEqual({ type: "restart_bot", params: {} });
    expect((sharedView.plan as RepairPlanArtifact).actions[1].command).toBe(exactCommand);
    expect(sharedView).toMatchObject({ canOperate: false, canManageShare: false, canTerminate: false });
  });

  it("projects the same safe task content to a shared viewer while keeping it read-only", async () => {
    const issueCanary = "private issue body canary";
    const stepSummaryCanary = "private step summary canary";
    const stepErrorCanary = "private step error canary";
    const stepOutputCanary = "private step output canary";
    const historyFeedbackCanary = "private history feedback canary";
    const pendingFeedbackCanary = "private pending feedback canary";
    const applyResultCanary = "private apply result canary";
    const taskErrorCanary = "private task error canary";
    const created = await createTask({ symptom: issueCanary });
    const task = await harness.repo.findTask(created.taskId);
    const step = await harness.repo.findStep(created.config.current.stepId);
    const next = JSON.parse(task!.config_json) as RepairTaskConfig;
    const applyObjectKey = `evolution/${created.taskId}/repair/${step!.step_id}/shared-apply-result.json`;
    const applyContent = Buffer.from(JSON.stringify({ detail: applyResultCanary }));
    harness.objects.set(applyObjectKey, applyContent);
    next.current = {
      ...next.current,
      phase: "repair_apply",
      artifacts: { applyResult: { objectKey: applyObjectKey, contentType: "application/json" } },
    };
    next.artifacts = next.current.artifacts;
    next.history = [{
      stepId: "historical-private-step",
      stepNo: 1,
      attempt: 1,
      phase: "repair_plan",
      status: "succeeded",
      artifactDigest: "a".repeat(64),
      feedback: historyFeedbackCanary,
    }];
    next.pendingDecision = {
      kind: "retry_result",
      requestedBy: ACTOR,
      requestedAt: "2026-08-20T00:00:00.000Z",
      artifactDigest: hash(applyContent),
      feedback: pendingFeedbackCanary,
    };
    await harness.repo.updateTaskConfig(created.taskId, next);
    step!.step_type = "repair_apply";
    step!.status = "succeeded";
    step!.summary = stepSummaryCanary;
    step!.error_message = stepErrorCanary;
    step!.output_json = JSON.stringify({ artifactDigest: hash(applyContent), detail: stepOutputCanary });
    task!.error_message = taskErrorCanary;

    const ownerView = await harness.service.getTask(ACTOR, created.taskId);
    expect(JSON.stringify(ownerView)).toContain(issueCanary);
    expect(JSON.stringify(ownerView)).toContain(stepSummaryCanary);
    expect(JSON.stringify(ownerView)).toContain(stepErrorCanary);
    expect(JSON.stringify(ownerView)).toContain(historyFeedbackCanary);
    expect(JSON.stringify(ownerView)).toContain(pendingFeedbackCanary);
    expect(JSON.stringify(ownerView)).toContain(applyResultCanary);
    expect(JSON.stringify(ownerView)).toContain(taskErrorCanary);

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId) as {
      currentStep: Record<string, unknown> | null;
      steps: Array<Record<string, unknown>>;
      history: Array<Record<string, unknown>>;
      pendingDecision: Record<string, unknown> | null;
    };
    expect(sharedView).toMatchObject({
      issue: ownerView.issue,
      currentStep: ownerView.currentStep,
      steps: ownerView.steps,
      history: ownerView.history,
      pendingDecision: ownerView.pendingDecision,
      plan: ownerView.plan,
      applyResult: ownerView.applyResult,
      error: ownerView.error,
      canOperate: false,
      canManageShare: false,
      canTerminate: false,
    });
    for (const canary of [
      issueCanary,
      stepSummaryCanary,
      stepErrorCanary,
      historyFeedbackCanary,
      pendingFeedbackCanary,
      applyResultCanary,
      taskErrorCanary,
    ]) {
      expect(JSON.stringify(sharedView)).toContain(canary);
    }
    expect(JSON.stringify(ownerView)).not.toContain(stepOutputCanary);
    expect(JSON.stringify(sharedView)).not.toContain(stepOutputCanary);
  });

  it("rejects executable Plan payloads containing credentials before approval", async () => {
    const cases = [
      "TOKEN=plain-token-canary",
      "SECRET=plain-secret-canary",
      "PASSWORD=plain-password-canary",
      "access_key=plain-access-key-canary",
      "access_token=plain-access-token-canary run repair",
      "ANTCHAT_API_KEY=plain-provider-key-canary run repair",
      "repair --token=plain-cli-token-canary",
    ];
    for (const secretPayload of cases) {
      const created = await createTask();
      const actions: RepairPlanArtifact["actions"] = [{
        actionId: "run-repair",
        type: "container_command",
        summary: "执行修复命令",
        risk: "进程状态会变化",
        verification: "重新检查进程",
        rollback: null,
        command: secretPayload,
      }];
      const written = writePlan(created.config, actions);

      await expect(harness.service.reportStep(created.identity, {
        status: "succeeded",
        output: {
          schemaVersion: REPAIR_CONTRACT_VERSION,
          taskId: created.taskId,
          stepId: created.config.current.stepId,
          attempt: created.config.current.attempt,
          phase: "repair_plan",
          artifactDigest: written.digest,
          artifacts: artifactMetadata(created.config, "plan", written.content),
        },
      })).rejects.toMatchObject({ status: 400, code: "invalid_repair_plan_secret" });
      expect((await harness.repo.findTask(created.taskId))?.status).toBe("running");
    }
  });

  it.each([
    "kill -9 2843 4902 6776",
    "/bin/kill -TERM 2843",
    "pkill -f mcp-repro-20260820",
    "killall openclaw",
    'command "kill" -9 2843',
    "k\\ill -9 2843",
    'bash -c "kill -9 2843"',
    "/bin/k\\ill -TERM 2843",
  ])("rejects raw process signaling in a new Plan: %s", async (command) => {
    const created = await createTask();
    const written = writePlan(created.config, [{
      actionId: "terminate-stalled-process",
      type: "container_command",
      summary: "终止挂起进程",
      risk: "错误进程可能被终止",
      verification: "重新检查进程和调度状态",
      rollback: null,
      command,
    }]);

    await expect(harness.service.reportStep(created.identity, {
      status: "succeeded",
      output: {
        schemaVersion: REPAIR_CONTRACT_VERSION,
        taskId: created.taskId,
        stepId: created.config.current.stepId,
        attempt: created.config.current.attempt,
        phase: "repair_plan",
        artifactDigest: written.digest,
        artifacts: artifactMetadata(created.config, "plan", written.content),
      },
    })).rejects.toMatchObject({ status: 400, code: "unsafe_raw_process_action" });
    expect((await harness.repo.findTask(created.taskId))?.status).toBe("running");
  });

  it("revalidates a historical raw process signal before approval", async () => {
    const created = await createTask();
    await reportPlanReady(created);
    const unsafe = writePlan(created.config, [{
      actionId: "terminate-stalled-process",
      type: "container_command",
      summary: "终止挂起进程",
      risk: "错误进程可能被终止",
      verification: "重新检查进程和调度状态",
      rollback: null,
      command: "kill -9 2843 4902 6776",
    }]);
    const step = await harness.repo.findStep(created.config.current.stepId);
    step!.output_json = JSON.stringify({
      ...JSON.parse(step!.output_json!),
      artifactDigest: unsafe.digest,
    });

    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: unsafe.digest },
    })).rejects.toMatchObject({ status: 400, code: "unsafe_raw_process_action" });
    expect((JSON.parse((await harness.repo.findTask(created.taskId))!.config_json) as RepairTaskConfig).approvedPlan)
      .toBeNull();
  });

  it("revalidates a historical approved raw process signal before Apply execution", async () => {
    const created = await createTask();
    const safe = await reportPlanReady(created);
    await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: safe.digest },
    });
    const unsafe = writePlan(created.config, [{
      actionId: "restart-gateway",
      type: "container_command",
      summary: "终止挂起进程",
      risk: "错误进程可能被终止",
      verification: "重新检查进程和调度状态",
      rollback: null,
      command: "pkill -f mcp-repro-20260820",
    }]);
    const row = await harness.repo.findTask(created.taskId);
    const config = JSON.parse(row!.config_json) as RepairTaskConfig;
    await harness.repo.updateTaskConfig(created.taskId, {
      ...config,
      approvedPlan: { ...config.approvedPlan!, artifactDigest: unsafe.digest },
    });
    const claimed = await harness.service.claimDecision(created.identity);

    await expect(harness.service.applyAction({
      ...created.identity,
      stepId: String(claimed.stepId),
    }, {
      clientRequestId: "historical-approved-process-signal",
      actionId: "restart-gateway",
    })).rejects.toMatchObject({ status: 400, code: "unsafe_raw_process_action" });
    expect(harness.applyApprovedAction).not.toHaveBeenCalled();
  });

  it("rejects a new Plan that attempts a full engine config replacement", async () => {
    const created = await createTask();
    const written = writePlan(created.config, [{
      actionId: "replace-engine-config",
      type: "ocb_operation",
      summary: "更新引擎配置",
      risk: "不完整配置可能覆盖无关字段",
      verification: "重新读取配置并复验原始症状",
      rollback: null,
      operation: {
        type: "engine_config_replace",
        params: { config: { mcp: { servers: {} } } },
      },
    }]);

    await expect(harness.service.reportStep(created.identity, {
      status: "succeeded",
      output: {
        schemaVersion: REPAIR_CONTRACT_VERSION,
        taskId: created.taskId,
        stepId: created.config.current.stepId,
        attempt: created.config.current.attempt,
        phase: "repair_plan",
        artifactDigest: written.digest,
        artifacts: artifactMetadata(created.config, "plan", written.content),
      },
    })).rejects.toMatchObject({ status: 400, code: "unsafe_engine_config_replace" });
    expect((await harness.repo.findTask(created.taskId))?.status).toBe("running");
  });

  it("rejects an engine config JSON Patch Plan because config changes run in the target container", async () => {
    const created = await createTask();
    await expect(reportPlanReady(created, [{
      actionId: "clear-mcp-servers",
      type: "ocb_operation",
      summary: "清空当前 MCP 服务配置",
      risk: "现有 MCP 服务将停止加载",
      verification: "重新触发原始任务并确认启动阶段不再超时",
      rollback: null,
      operation: {
        type: "engine_config_patch",
        params: {
          engineType: "openclaw",
          patch: [{ op: "replace", path: "/mcp/servers", value: {} }],
        },
      },
    }])).rejects.toMatchObject({ status: 400, code: "invalid_repair_plan" });
    expect((await harness.repo.findTask(created.taskId))?.status).toBe("running");
  });

  it("rejects operation-specific Plan params before approval", async () => {
    const created = await createTask();
    await expect(reportPlanReady(created, [{
      actionId: "restart-bot",
      type: "ocb_operation",
      summary: "重启当前 Bot",
      risk: "可能短暂不可用",
      verification: "重新读取当前运行目标并复验原始症状",
      rollback: null,
      operation: {
        type: "restart_bot",
        params: { engineType: "openclaw" },
      },
    }])).rejects.toMatchObject({ status: 400, code: "invalid_ocb_operation" });
    expect((await harness.repo.findTask(created.taskId))?.status).toBe("running");
  });

  it("revalidates a historical waiting-approval Plan before recording approval", async () => {
    const created = await createTask();
    await reportPlanReady(created);
    const secret = writePlan(created.config, [{
      actionId: "run-repair",
      type: "container_command",
      summary: "执行修复命令",
      risk: "进程状态会变化",
      verification: "重新检查进程",
      rollback: null,
      command: "TOKEN=historical-plan-canary run repair",
    }]);
    const step = await harness.repo.findStep(created.config.current.stepId);
    step!.output_json = JSON.stringify({
      ...JSON.parse(step!.output_json!),
      artifactDigest: secret.digest,
    });

    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: secret.digest },
    })).rejects.toMatchObject({ status: 400, code: "invalid_repair_plan_secret" });
    expect((JSON.parse((await harness.repo.findTask(created.taskId))!.config_json) as RepairTaskConfig).approvedPlan)
      .toBeNull();
  });

  it("rejects a historical approved Plan containing credentials before Apply execution", async () => {
    const created = await createTask();
    const safe = await reportPlanReady(created);
    await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: safe.digest },
    });
    const secret = writePlan(created.config, [{
      actionId: "run-repair",
      type: "container_command",
      summary: "执行修复命令",
      risk: "进程状态会变化",
      verification: "重新检查进程",
      rollback: null,
      command: "ANTCHAT_API_KEY=historical-approved-canary run repair",
    }]);
    const row = await harness.repo.findTask(created.taskId);
    const config = JSON.parse(row!.config_json) as RepairTaskConfig;
    await harness.repo.updateTaskConfig(created.taskId, {
      ...config,
      approvedPlan: { ...config.approvedPlan!, artifactDigest: secret.digest },
    });
    const claimed = await harness.service.claimDecision(created.identity);

    await expect(harness.service.applyAction({
      ...created.identity,
      stepId: String(claimed.stepId),
    }, {
      clientRequestId: "historical-approved-secret",
      actionId: "run-repair",
    })).rejects.toMatchObject({ status: 400, code: "invalid_repair_plan_secret" });
    expect(harness.execute).toHaveBeenCalledTimes(1);
  });

  it.each([
    {
      name: "execute_actions without actions",
      actions: [] as RepairPlanArtifact["actions"],
      quality: "verified" as const,
      recommendation: {
        disposition: "execute_actions" as const,
        summary: "建议执行修复",
        reason: "需要执行动作。",
      },
    },
    {
      name: "no_change with an action",
      actions: [{
        actionId: "unexpected-action",
        type: "container_command" as const,
        summary: "不应存在的动作",
        risk: "短暂影响",
        verification: "重新检查",
        rollback: null,
        command: "true",
      }],
      quality: "verified" as const,
      recommendation: {
        disposition: "no_change" as const,
        summary: "建议不变更",
        reason: "不应同时携带动作。",
      },
    },
    {
      name: "verified insufficient evidence",
      actions: [] as RepairPlanArtifact["actions"],
      quality: "verified" as const,
      recommendation: {
        disposition: "insufficient_evidence" as const,
        summary: "证据不足",
        reason: "缺少日志。",
      },
    },
    {
      name: "partially verified insufficient evidence",
      actions: [] as RepairPlanArtifact["actions"],
      quality: "partially_verified" as const,
      recommendation: {
        disposition: "insufficient_evidence" as const,
        summary: "证据不足",
        reason: "关键证据仍然缺失。",
      },
    },
    {
      name: "blocked no-change conclusion",
      actions: [] as RepairPlanArtifact["actions"],
      quality: "blocked" as const,
      recommendation: {
        disposition: "no_change" as const,
        summary: "建议不变更",
        reason: "调查受阻。",
      },
    },
  ])("rejects an inconsistent v2 Plan: $name", async ({ actions, quality, recommendation }) => {
    const created = await createTask();
    await expect(reportPlanReady(created, actions, { quality, recommendation }))
      .rejects.toMatchObject({ status: 400, code: "invalid_repair_plan" });
    await expect(harness.repo.findTask(created.taskId)).resolves.toMatchObject({ status: "running" });
  });

  it("derives the v2 Plan Step summary from the digest-bound recommendation", async () => {
    const created = await createTask();
    await reportPlanReady(created, undefined, {
      recommendation: {
        disposition: "execute_actions",
        summary: "以方案产物中的中文结论为准",
        reason: "该结论与待审批动作由同一份不可变方案绑定。",
      },
    });

    await expect(harness.service.getTask(ACTOR, created.taskId)).resolves.toMatchObject({
      currentStep: { summary: "以方案产物中的中文结论为准" },
    });
    expect((await harness.repo.listSteps(created.taskId))[0]?.summary).toBe("以方案产物中的中文结论为准");
  });

  it("completes an in-grace v2 no-change Plan approval without creating or dispatching Apply", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created, []);

    const decision = await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    });

    expect(decision).toMatchObject({
      status: "completed",
      currentStep: {
        stepId: created.config.current.stepId,
        phase: "repair_plan",
        status: "succeeded",
      },
      plan: {
        quality: "verified",
        recommendation: { disposition: "no_change", summary: "建议本次不执行修复" },
        actions: [],
      },
      approvedPlan: {
        stepId: created.config.current.stepId,
        artifactDigest: written.digest,
      },
      pendingDecision: null,
      execution: {
        state: "ended",
        phase: "repair_plan",
        decisionDeadlineAt: null,
      },
    });
    expect(decision.history).toEqual([expect.objectContaining({
      stepId: created.config.current.stepId,
      phase: "repair_plan",
      status: "succeeded",
      artifactDigest: written.digest,
    })]);
    const persisted = JSON.parse((await harness.repo.findTask(created.taskId))!.config_json) as RepairTaskConfig;
    expect(persisted).toMatchObject({
      approvedPlan: {
        stepId: created.config.current.stepId,
        artifactDigest: written.digest,
        objectKey: created.config.current.artifacts.plan.objectKey,
      },
      pendingDecision: null,
      execution: {
        state: "ended",
        invalidatedAt: 1_000,
        leaseExpiresAt: 1_000,
        decisionDeadlineAt: null,
      },
    });
    expect(await harness.repo.listSteps(created.taskId)).toHaveLength(1);
    expect(harness.execute).toHaveBeenCalledTimes(1);

    const ticket = String(parseSnapshotEnvelope(
      harness.execute.mock.calls[0][1] as Record<string, string>,
    ).runtime.executionTicket);
    const verifier = new DatabaseRepairWorkloadVerifier(harness.repo, () => harness.now.value);
    await expect(verifier.verify({
      params: { taskId: created.taskId, stepId: created.config.current.stepId },
      method: "POST",
      path: "/decision/claim",
      header: (name: string) => name.toLowerCase() === "authorization" ? `Bearer ${ticket}` : undefined,
    } as never)).rejects.toMatchObject({
      status: 401,
      code: "repair_execution_ticket_invalid",
    });

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("other-user", created.taskId);
    expect(sharedView).toMatchObject({
      status: "completed",
      approvedPlan: {
        stepId: created.config.current.stepId,
        artifactDigest: written.digest,
      },
      history: [expect.objectContaining({
        stepId: created.config.current.stepId,
        artifactDigest: written.digest,
      })],
      execution: { state: "ended" },
    });
    expect(sharedView.plan).toEqual(decision.plan);
  });

  it("completes an expired v2 no-change Plan approval without a fresh key or a new AIS job", async () => {
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "vendor/custom-model",
      llmApiKey: "initial-noop-key-canary",
    });
    const written = await reportPlanReady(created, []);
    harness.now.value = 2_000;

    const decision = await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    });

    expect(decision).toMatchObject({
      status: "completed",
      currentStep: { phase: "repair_plan", status: "succeeded" },
      approvedPlan: { artifactDigest: written.digest },
      pendingDecision: null,
      execution: { state: "ended", decisionDeadlineAt: null },
    });
    expect(await harness.repo.listSteps(created.taskId)).toHaveLength(1);
    expect(harness.execute).toHaveBeenCalledTimes(1);
    const persisted = JSON.parse((await harness.repo.findTask(created.taskId))!.config_json) as RepairTaskConfig;
    expect(persisted.execution).toMatchObject({
      executionId: created.config.execution.executionId,
      jobId: "job-1",
      state: "ended",
      invalidatedAt: 2_000,
      leaseExpiresAt: 2_000,
    });
  });

  it("does not overwrite a concurrent config update while completing a no-change Plan", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created, []);
    harness.getObject.mockImplementationOnce(async (key: string) => {
      const row = await harness.repo.findTask(created.taskId);
      const concurrent = JSON.parse(row!.config_json) as RepairTaskConfig;
      await harness.repo.updateTaskConfig(created.taskId, { ...concurrent, shared: true });
      return {
        content: harness.objects.get(key) ?? Buffer.from("{}"),
        etag: null,
        contentType: "application/json",
      };
    });

    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    })).rejects.toMatchObject({ status: 409, code: "repair_decision_conflict" });

    const after = await harness.repo.findTask(created.taskId);
    expect(after!.status).toBe("waiting_approval");
    expect(JSON.parse(after!.config_json)).toMatchObject({ shared: true, approvedPlan: null });
    expect(await harness.repo.listSteps(created.taskId)).toHaveLength(1);
  });

  it("keeps an insufficient-evidence Plan unchanged when approval is attempted", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created, [], {
      quality: "blocked",
      recommendation: {
        disposition: "insufficient_evidence",
        summary: "现有证据不足以形成修复方案",
        reason: "缺少关键业务日志，不能安全决定是否写入。",
        nextSteps: ["补充故障时间段内的业务日志"],
      },
    });
    const before = await harness.repo.findTask(created.taskId);

    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    })).rejects.toMatchObject({ status: 409, code: "repair_plan_not_approvable" });

    const after = await harness.repo.findTask(created.taskId);
    expect(after).toMatchObject({ status: "waiting_approval", config_json: before!.config_json });
    expect(await harness.repo.listSteps(created.taskId)).toEqual([
      expect.objectContaining({ step_type: "repair_plan", status: "succeeded" }),
    ]);
    expect(harness.execute).toHaveBeenCalledTimes(1);
    await expect(harness.service.getTask(ACTOR, created.taskId)).resolves.toMatchObject({
      plan: {
        quality: "blocked",
        recommendation: {
          disposition: "insufficient_evidence",
          summary: "现有证据不足以形成修复方案",
          nextSteps: ["补充故障时间段内的业务日志"],
        },
        actions: [],
      },
    });
  });

  it("accepts one English fact when the complete v2 Plan is Chinese-dominant", async () => {
    const created = await createTask();
    const written = writePlan(created.config);
    written.plan.diagnosis.facts = ["The target bot stayed in attempt-dispatch."];
    const content = Buffer.from(JSON.stringify(written.plan));
    harness.objects.set(created.config.current.artifacts.plan.objectKey, content);

    await expect(harness.service.reportStep(created.identity, {
      status: "succeeded",
      output: {
        schemaVersion: REPAIR_CONTRACT_VERSION,
        taskId: created.taskId,
        stepId: created.config.current.stepId,
        attempt: created.config.current.attempt,
        phase: "repair_plan",
        artifactDigest: hash(content),
        artifacts: artifactMetadata(created.config, "plan", content),
        summary: "plan ready",
      },
    })).resolves.toBeDefined();
  });

  it("rejects a v2 Plan whose complete user-facing result is English-dominant", async () => {
    const created = await createTask();
    const written = writePlan(created.config);
    written.plan.diagnosis = {
      facts: ["The target bot stayed in attempt-dispatch."],
      inferences: ["MCP initialization is the strongest explanation."],
      unknowns: ["Direct startup logs are unavailable."],
    };
    written.plan.recommendation = {
      disposition: "execute_actions",
      summary: "Remove the reproduction configuration",
      reason: "The evidence supports a controlled retry",
      nextSteps: ["Run the original symptom again"],
    };
    written.plan.actions[0].summary = "Update the reproduction configuration";
    written.plan.actions[0].risk = "The test bot temporarily loses configured servers";
    written.plan.actions[0].verification = "Run the original symptom";
    written.plan.actions[0].rollback = "Restore the previous configuration";
    const content = Buffer.from(JSON.stringify(written.plan));
    harness.objects.set(created.config.current.artifacts.plan.objectKey, content);

    await expect(harness.service.reportStep(created.identity, {
      status: "succeeded",
      output: {
        schemaVersion: REPAIR_CONTRACT_VERSION,
        taskId: created.taskId,
        stepId: created.config.current.stepId,
        attempt: created.config.current.attempt,
        phase: "repair_plan",
        artifactDigest: hash(content),
        artifacts: artifactMetadata(created.config, "plan", content),
        summary: "plan ready",
      },
    })).rejects.toMatchObject({ status: 400, code: "invalid_repair_plan_language" });
  });

  it("keeps legacy v1 Plan prose readable without applying the v2 Chinese gate", async () => {
    const created = await createTask();
    const written = writePlan(created.config, undefined, { schemaVersion: LEGACY_REPAIR_PLAN_VERSION });
    written.plan.diagnosis.facts = ["The target bot is currently unhealthy"];
    written.plan.actions[0].summary = "Restart the gateway";
    written.plan.actions[0].risk = "Brief interruption";
    written.plan.actions[0].verification = "Check the health endpoint";
    const content = Buffer.from(JSON.stringify(written.plan));
    harness.objects.set(created.config.current.artifacts.plan.objectKey, content);

    await expect(harness.service.reportStep(created.identity, {
      status: "succeeded",
      output: {
        schemaVersion: REPAIR_CONTRACT_VERSION,
        taskId: created.taskId,
        stepId: created.config.current.stepId,
        attempt: created.config.current.attempt,
        phase: "repair_plan",
        artifactDigest: hash(content),
        artifacts: artifactMetadata(created.config, "plan", content),
        summary: "legacy plan ready",
      },
    })).resolves.toMatchObject({ ok: true, status: "succeeded" });
    expect((await harness.repo.findTask(created.taskId))!.status).toBe("waiting_approval");
  });

  it("does not infer that a legacy v1 empty Plan means no change", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created, [], { schemaVersion: LEGACY_REPAIR_PLAN_VERSION });

    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    })).rejects.toMatchObject({ status: 409, code: "repair_legacy_empty_plan_not_approvable" });
    await expect(harness.service.getTask(ACTOR, created.taskId)).resolves.toMatchObject({
      status: "waiting_approval",
      plan: { schemaVersion: LEGACY_REPAIR_PLAN_VERSION, legacySemantics: true, actions: [] },
      pendingDecision: null,
    });
    expect(await harness.repo.listSteps(created.taskId)).toHaveLength(1);
    expect(harness.execute).toHaveBeenCalledTimes(1);
  });

  it("keeps a non-empty legacy v1 Plan executable", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created, undefined, { schemaVersion: LEGACY_REPAIR_PLAN_VERSION });

    const decision = await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    });

    expect(decision).toMatchObject({
      status: "waiting_approval",
      plan: { schemaVersion: LEGACY_REPAIR_PLAN_VERSION, legacySemantics: true },
      pendingDecision: { kind: "approve_plan", artifactDigest: written.digest },
    });
  });

  it("records an in-grace approval without dispatch, then atomically claims an Apply Step on the same job", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created);

    const decision = await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    });
    expect(decision).toMatchObject({ status: "waiting_approval" });
    expect(harness.execute).toHaveBeenCalledTimes(1);
    expect((await harness.repo.listSteps(created.taskId)).map((item) => item.step_type))
      .toEqual(["repair_plan"]);

    const claimed = await harness.service.claimDecision(created.identity);
    expect(claimed).toMatchObject({
      status: "claimed",
      reusedJob: true,
      phase: "repair_apply",
      continuation: {
        taskType: "repair",
        execution: {
          action: "repair_apply",
          executionId: created.config.execution.executionId,
          agentMode: "openclaw",
        },
        input: { approvedPlan: { artifactDigest: written.digest } },
      },
    });
    const continuation = claimed.continuation as {
      input: Record<string, unknown>;
      runtime: Record<string, unknown>;
    };
    expect(JSON.stringify(continuation.input)).not.toContain("modelApiKey");
    expect(continuation.runtime).not.toHaveProperty("executionTicket");
    expect(harness.execute).toHaveBeenCalledTimes(1);

    const task = await harness.repo.findTask(created.taskId);
    const taskConfig = JSON.parse(task!.config_json) as RepairTaskConfig;
    const steps = await harness.repo.listSteps(created.taskId);
    expect(task).toMatchObject({ status: "running" });
    expect(taskConfig.execution).toMatchObject({
      executionId: created.config.execution.executionId,
      jobId: "job-1",
      stepId: taskConfig.current.stepId,
      phase: "repair_apply",
      state: "running",
    });
    expect(steps.map((item) => [item.step_type, item.bot_run_id]))
      .toEqual([["repair_plan", "job-1"], ["repair_apply", "job-1"]]);
    expect(harness.createSignedUrl.mock.calls).toEqual(expect.arrayContaining([
      [taskConfig.current.artifacts.applyResult.objectKey, "PUT", 86_400,
        { "Content-Type": "application/json; charset=utf-8" }],
      [taskConfig.current.artifacts.markdown.objectKey, "PUT", 86_400,
        { "Content-Type": "text/markdown; charset=utf-8" }],
      [taskConfig.current.artifacts.result.objectKey, "PUT", 86_400,
        { "Content-Type": "application/json; charset=utf-8" }],
      [taskConfig.current.artifacts.checkpoint.objectKey, "PUT", 86_400,
        { "Content-Type": "application/json; charset=utf-8" }],
    ]));
  });

  it("loads a digest-bound historical Plan after re-planning has started", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created, undefined, {
      recommendation: {
        disposition: "execute_actions",
        summary: "第一轮建议重启网关",
        reason: "第一轮证据显示网关当前不可用。",
        nextSteps: ["批准前复核第一轮风险"],
      },
      quality: "partially_verified",
    });
    await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "reject", reason: "补充证据后重新生成方案" },
    });
    await harness.service.claimDecision(created.identity);

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).resolves.toMatchObject({
      taskId: created.taskId,
      step: {
        stepId: created.config.current.stepId,
        stepNo: 1,
        attempt: 1,
        status: "succeeded",
        artifactDigest: written.digest,
      },
      source: "history",
      readOnly: true,
      approvable: false,
      plan: {
        schemaVersion: REPAIR_PLAN_VERSION,
        taskId: created.taskId,
        stepId: created.config.current.stepId,
        quality: "partially_verified",
        recommendation: {
          summary: "第一轮建议重启网关",
          reason: "第一轮证据显示网关当前不可用。",
          nextSteps: ["批准前复核第一轮风险"],
        },
        diagnosis: { facts: ["网关当前不可用"], inferences: [], unknowns: [] },
        actions: [expect.objectContaining({ actionId: "restart-gateway" })],
      },
    });
    expect(harness.getObject).toHaveBeenCalledWith(
      created.config.current.artifacts.plan.objectKey,
    );
    expect(written.plan.stepId).toBe(created.config.current.stepId);
  });

  it("lets a shared viewer read a historical Plan from OSS as read-only", async () => {
    const created = await createTask();
    await advanceToReplan(created);
    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    harness.getObject.mockClear();

    await expect(harness.service.getStepPlan(
      "shared-viewer",
      created.taskId,
      created.config.current.stepId,
    )).resolves.toMatchObject({
      taskId: created.taskId,
      source: "history",
      readOnly: true,
      approvable: false,
      plan: { stepId: created.config.current.stepId },
    });
    expect(harness.getObject).toHaveBeenCalled();
  });

  it("does not resolve a Step from another Task as historical Plan content", async () => {
    const created = await createTask();
    await advanceToReplan(created);
    const other = await createTask();
    harness.getObject.mockClear();

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      other.config.current.stepId,
    )).rejects.toMatchObject({ status: 404, code: "repair_historical_plan_not_found" });
    expect(harness.getObject).not.toHaveBeenCalled();
  });

  it("does not expose the current Plan through the historical Plan endpoint", async () => {
    const created = await createTask();
    await reportPlanReady(created);
    harness.getObject.mockClear();

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).rejects.toMatchObject({ status: 404, code: "repair_historical_plan_not_found" });
    expect(harness.getObject).not.toHaveBeenCalled();
  });

  it.each([
    {
      name: "non-canonical objectKey",
      mutate: (metadata: Record<string, unknown>) => { metadata.objectKey = "evolution/other/plan.json"; },
      expectedCode: "repair_historical_plan_artifact_invalid",
      readsObject: false,
    },
    {
      name: "metadata digest mismatch",
      mutate: (metadata: Record<string, unknown>) => { metadata.sha256 = "b".repeat(64); },
      expectedCode: "repair_historical_plan_artifact_invalid",
      readsObject: false,
    },
    {
      name: "metadata size mismatch",
      mutate: (metadata: Record<string, unknown>) => { metadata.size = Number(metadata.size) + 1; },
      expectedCode: "repair_historical_plan_artifact_changed",
      readsObject: true,
    },
  ])("rejects historical Plan artifact tampering: $name", async ({ mutate, expectedCode, readsObject }) => {
    const created = await createTask();
    await advanceToReplan(created);
    const step = (await harness.repo.findStep(created.config.current.stepId))!;
    const output = JSON.parse(step.output_json!) as Record<string, unknown>;
    const artifacts = output.artifacts as Record<string, Record<string, unknown>>;
    mutate(artifacts.plan);
    step.output_json = JSON.stringify(output);
    harness.getObject.mockClear();

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).rejects.toMatchObject({ status: 409, code: expectedCode });
    expect(harness.getObject).toHaveBeenCalledTimes(readsObject ? 1 : 0);
  });

  it("rejects historical Plan content that no longer matches its persisted digest", async () => {
    const created = await createTask();
    const written = await advanceToReplan(created);
    const changed = Buffer.from(written.content);
    changed[changed.length - 2] = changed[changed.length - 2] === 48 ? 49 : 48;
    harness.objects.set(created.config.current.artifacts.plan.objectKey, changed);

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).rejects.toMatchObject({ status: 409, code: "repair_historical_plan_artifact_changed" });
  });

  it.each([
    ["task identity", (plan: RepairPlanArtifact) => { plan.taskId = "REPAIR-other"; }],
    ["step identity", (plan: RepairPlanArtifact) => { plan.stepId = "REPAIR-other-PLAN-1"; }],
    ["attempt identity", (plan: RepairPlanArtifact) => { plan.attempt += 1; }],
    ["authorization scope", (plan: RepairPlanArtifact) => { plan.authorizationScopeDigest = "b".repeat(64); }],
    ["runtime target history", (plan: RepairPlanArtifact) => { plan.runtimeTargetVersion = 999; }],
  ])("rejects a digest-consistent historical Plan with mismatched $0", async (_name, mutate) => {
    const created = await createTask();
    const written = await advanceToReplan(created);
    const changed = structuredClone(written.plan) as RepairPlanArtifact;
    mutate(changed);
    await replaceHistoricalPlan(created, changed);

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).rejects.toMatchObject({ status: 409, code: "repair_historical_plan_identity_mismatch" });
  });

  it("shows a legacy unsafe process action only through the non-approvable historical view", async () => {
    const created = await createTask();
    const written = await advanceToReplan(created);
    const historical = structuredClone(written.plan) as RepairPlanArtifact;
    historical.actions[0] = { ...historical.actions[0], command: "kill -9 2843" };
    await replaceHistoricalPlan(created, historical);

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).resolves.toMatchObject({
      source: "history",
      readOnly: true,
      approvable: false,
      plan: { actions: [expect.objectContaining({ command: "kill -9 2843" })] },
    });
  });

  it("shows a legacy engine config replacement only through the non-approvable historical view", async () => {
    const created = await createTask();
    const written = await advanceToReplan(created);
    const historical = structuredClone(written.plan) as RepairPlanArtifact;
    historical.actions[0] = {
      actionId: "legacy-engine-config-replace",
      type: "ocb_operation",
      summary: "更新引擎配置",
      risk: "旧方案可能覆盖完整配置",
      verification: "重新检查原始症状",
      rollback: null,
      operation: {
        type: "engine_config_replace",
        params: { config: { mcp: { servers: {} } } },
      },
    };
    await replaceHistoricalPlan(created, historical);

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).resolves.toMatchObject({
      source: "history",
      readOnly: true,
      approvable: false,
      plan: {
        actions: [expect.objectContaining({
          operation: expect.objectContaining({ type: "engine_config_replace" }),
        })],
      },
    });
  });

  it("shows legacy operation params only through the non-approvable historical view", async () => {
    const created = await createTask();
    const written = await advanceToReplan(created);
    const historical = structuredClone(written.plan) as RepairPlanArtifact;
    historical.actions[0] = {
      actionId: "legacy-restart-params",
      type: "ocb_operation",
      summary: "重启当前 Bot",
      risk: "可能短暂不可用",
      verification: "重新读取当前运行目标并复验原始症状",
      rollback: null,
      operation: {
        type: "restart_bot",
        params: { engineType: "openclaw" },
      },
    };
    await replaceHistoricalPlan(created, historical);

    await expect(harness.service.getStepPlan(
      ACTOR,
      created.taskId,
      created.config.current.stepId,
    )).resolves.toMatchObject({
      source: "history",
      readOnly: true,
      approvable: false,
      plan: {
        actions: [expect.objectContaining({
          operation: { type: "restart_bot", params: { engineType: "openclaw" } },
        })],
      },
    });
  });

  it("continues the same OpenClaw job without asking for its custom API key again", async () => {
    const apiKey = "same-job-key-canary";
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "Kimi-K2.6",
      llmApiKey: apiKey,
    });
    const written = await reportPlanReady(created);
    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    })).resolves.toMatchObject({ status: "waiting_approval" });

    const claimed = await harness.service.claimDecision(created.identity);
    expect(claimed).toMatchObject({
      status: "claimed",
      continuation: {
        execution: { agentMode: "openclaw" },
        input: { agent: { openclaw: { model: "Kimi-K2.6" } } },
      },
    });
    expect(JSON.stringify(claimed)).not.toContain(apiKey);
    expect(JSON.stringify(claimed)).not.toContain("modelApiKey");
    expect(harness.execute).toHaveBeenCalledTimes(1);
  });

  it("replays the current continuation without creating another Step when a decision/claim response was lost", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created);
    await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    });

    const first = await harness.service.claimDecision(created.identity);
    const task = await harness.repo.findTask(created.taskId);
    const current = JSON.parse(task!.config_json) as RepairTaskConfig;
    const transitionsBeforeRetry = vi.mocked(harness.repairRepo.transitionStep).mock.calls.length;
    const recovered = await harness.service.claimDecision({
      taskId: created.taskId,
      stepId: current.current.stepId,
      executionId: current.execution.executionId,
      requestedStepId: created.config.current.stepId,
    });

    expect(recovered).toMatchObject({
      status: "claimed",
      reusedJob: true,
      stepId: current.current.stepId,
      phase: "repair_apply",
      continuation: {
        stepId: current.current.stepId,
        execution: { executionId: created.config.execution.executionId, action: "repair_apply" },
      },
    });
    expect(recovered).toMatchObject(first);
    expect(await harness.repo.listSteps(created.taskId)).toHaveLength(2);
    expect(vi.mocked(harness.repairRepo.transitionStep)).toHaveBeenCalledTimes(transitionsBeforeRetry);
  });

  it("returns the latest valid historical checkpoint to a new execution with secret redaction", async () => {
    const created = await createTask();
    const bootstrap = await bootstrapAfterContextRecovery({
      created,
      checkpoint: {
        stage: "waiting_for_ocb",
        cursor: 17,
        apiKey: "must-not-leave-oss",
        nested: { cookie: "must-not-leave-oss" },
      },
    });

    expect(bootstrap.recoveryCheckpoint).toEqual({
      sourceStepId: created.config.current.stepId,
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
      content: {
        stage: "waiting_for_ocb",
        cursor: 17,
        apiKey: "[REDACTED]",
        nested: { cookie: "[REDACTED]" },
      },
    });
  });

  it("requires the custom OpenClaw API key again when waiting_context resumes into a new job", async () => {
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "GLM-5.1",
      llmApiKey: "initial-resume-key-canary",
    });
    await harness.service.reportStep(created.identity, { status: "waiting_context", output: {} });

    await expect(harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=resume-cookie" },
      taskId: created.taskId,
      body: {},
    })).rejects.toMatchObject({ status: 400, code: "llm_api_key_required" });
    expect(harness.execute).toHaveBeenCalledTimes(1);

    await harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=resume-cookie" },
      taskId: created.taskId,
      body: { llmApiKey: "fresh-resume-key-canary" },
    });
    expect(harness.execute).toHaveBeenCalledTimes(2);
    const envelope = parseSnapshotEnvelope(harness.execute.mock.calls[1][1] as Record<string, string>);
    expect(envelope).toMatchObject({
      execution: { agentMode: "openclaw", action: "repair_plan" },
      input: { agent: { openclaw: { modelApiKey: "fresh-resume-key-canary" } } },
    });
    expect(JSON.stringify(await harness.repo.findTask(created.taskId))).not.toContain("fresh-resume-key-canary");
  });

  it("retries a failed Plan in a new Step after Component initialization becomes stable", async () => {
    const created = await createTask();
    await harness.service.reportStep(created.identity, {
      status: "failed",
      output: {},
      error: {
        code: "OPENCLAW_INVOCATION_FAILED",
        message: "OpenClaw invocation failed",
        stage: "agents_add",
        reason: "system_context_changed",
        retryable: false,
      },
    });

    await expect(harness.service.getTask(ACTOR, created.taskId)).resolves.toMatchObject({
      status: "failed",
      canResume: true,
      currentStep: {
        stepId: created.config.current.stepId,
        status: "failed",
        failure: { stage: "agents_add", reason: "system_context_changed" },
      },
    });

    const retried = await harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=resume-cookie" },
      taskId: created.taskId,
      body: {},
    });

    expect(retried).toMatchObject({
      status: "running",
      currentStep: {
        stepId: `${created.taskId}-PLAN-2`,
        stepNo: 2,
        attempt: 2,
        phase: "repair_plan",
      },
      history: [{
        stepId: created.config.current.stepId,
        status: "failed",
        artifactDigest: null,
      }],
    });
    expect(harness.execute).toHaveBeenCalledTimes(2);
  });

  it("keeps a failed Plan audit out of the fresh retry bootstrap", async () => {
    const created = await createTask();
    const concludedResult = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "failed-plan-recovery-concluded",
      purpose: "读取上一轮配置并形成可复用结论",
      operation: "fs_read",
      path: "/home/admin/.openclaw/openclaw.json",
      startLine: 1,
      lines: 20,
    });
    const concluded = await harness.repairRepo.findToolCall(String(concludedResult.toolCallId));
    await harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: concluded!.callId,
      evidenceToolCallIds: [concluded!.callId],
      conclusionZh: "上一轮已经确认 OpenClaw 配置中存在待核验的 MCP 服务。",
      nextAction: "继续读取 Cron 运行记录，不要重复读取同一配置。",
    });
    const unconcludedResult = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "failed-plan-recovery-unconcluded",
      purpose: "读取 Cron 运行记录并定位中断位置",
      operation: "fs_read",
      path: "/home/admin/.openclaw/cron/jobs-state.json",
      startLine: 1,
      lines: 20,
    });
    const unconcluded = await harness.repairRepo.findToolCall(String(unconcludedResult.toolCallId));

    await harness.service.reportStep(created.identity, {
      status: "failed",
      output: {},
      error: {
        code: "OPENCLAW_INVOCATION_FAILED",
        message: "OpenClaw invocation failed",
        stage: "agent_invoke",
        reason: "rate_limited",
        httpStatus: 429,
        retryable: true,
      },
    });
    await harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=resume-cookie" },
      taskId: created.taskId,
      body: {},
    });
    const resumedTask = await harness.repo.findTask(created.taskId);
    const resumed = JSON.parse(resumedTask!.config_json) as RepairTaskConfig;

    const bootstrap = await harness.service.bootstrap({
      taskId: created.taskId,
      stepId: resumed.current.stepId,
      executionId: resumed.execution.executionId,
    });

    expect(bootstrap.recoveryContext).toMatchObject({
      stepId: resumed.current.stepId,
      executionId: resumed.execution.executionId,
      toolCalls: [],
      writeAttempts: [],
      priorStep: null,
    });
    expect(bootstrap.history).toEqual([]);
    expect(concluded).not.toBeNull();
    expect(unconcluded).not.toBeNull();
    const retainedCalls = await harness.repairRepo.listToolCalls(created.taskId, {
      stepId: created.config.current.stepId,
      limit: 10,
      recordKind: "source",
    });
    expect(retainedCalls.map(call => call.callId)).toEqual(expect.arrayContaining([
      concluded!.callId,
      unconcluded!.callId,
    ]));
  });

  it.each([
    ["omitted", undefined],
    ["explicitly false", false],
  ])("allows a manual fresh retry when a failed Plan retryable flag is %s", async (_label, retryable) => {
    const created = await createTask();
    await harness.service.reportStep(created.identity, {
      status: "failed",
      output: {},
      error: {
        code: "OPENCLAW_INVOCATION_FAILED",
        message: "OpenClaw invocation failed",
        stage: "agent_invoke",
        reason: "authentication_failed",
        ...(retryable === undefined ? {} : { retryable }),
      },
    });

    await expect(harness.service.getTask(ACTOR, created.taskId)).resolves.toMatchObject({
      status: "failed",
      canResume: true,
    });
    await expect(harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=resume-cookie" },
      taskId: created.taskId,
      body: {},
    })).resolves.toMatchObject({
      status: "running",
      currentStep: { stepId: `${created.taskId}-PLAN-2`, attempt: 2 },
    });
    expect(harness.execute).toHaveBeenCalledTimes(2);
  });

  it("reuses a live AIS for a failed Plan while starting a fresh Agent session", async () => {
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "GLM-5.1",
      llmApiKey: "same-ais-retry-key-canary",
    });
    await harness.service.heartbeat(created.identity, { ccSessionId: "failed-session-id" });
    const failed = await harness.service.reportStep(created.identity, {
      status: "failed",
      output: {},
      retryWaitSupported: true,
      error: {
        code: "OPENCLAW_INVOCATION_FAILED",
        message: "OpenClaw invocation failed",
        stage: "agent_invoke",
        reason: "rate_limited",
        httpStatus: 429,
        retryable: false,
      },
    });

    expect(failed).toMatchObject({
      ok: true,
      retryWaitSupported: true,
      decisionDeadlineAt: expect.any(Number),
    });
    const failedTask = await harness.repo.findTask(created.taskId);
    const failedConfig = JSON.parse(failedTask!.config_json) as RepairTaskConfig;
    expect(failedConfig.execution).toMatchObject({
      executionId: created.config.execution.executionId,
      state: "waiting_decision",
      invalidatedAt: null,
      ccSessionId: "failed-session-id",
    });

    const retried = await harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=resume-cookie" },
      taskId: created.taskId,
      body: {},
    });
    expect(retried).toMatchObject({
      status: "running",
      currentStep: { stepId: `${created.taskId}-PLAN-2`, attempt: 2 },
      execution: { state: "running" },
    });
    expect(harness.execute).toHaveBeenCalledTimes(1);

    const retriedTask = await harness.repo.findTask(created.taskId);
    const retriedConfig = JSON.parse(retriedTask!.config_json) as RepairTaskConfig;
    expect(retriedConfig.execution).toMatchObject({
      executionId: created.config.execution.executionId,
      jobId: failedConfig.execution.jobId,
      ccSessionId: null,
      state: "running",
    });
    const claimed = await harness.service.claimDecision({
      taskId: created.taskId,
      stepId: retriedConfig.current.stepId,
      executionId: retriedConfig.execution.executionId,
      requestedStepId: created.config.current.stepId,
    });
    expect(claimed).toMatchObject({
      status: "claimed",
      reusedJob: true,
      stepId: retriedConfig.current.stepId,
      phase: "repair_plan",
      continuation: {
        stepId: retriedConfig.current.stepId,
        execution: { resumeSessionId: null },
        input: { history: [] },
      },
    });
    const bootstrap = await harness.service.bootstrap({
      taskId: created.taskId,
      stepId: retriedConfig.current.stepId,
      executionId: retriedConfig.execution.executionId,
    });
    expect(bootstrap.recoveryContext).toMatchObject({ priorStep: null });
  });

  it("returns the same retry-wait acknowledgement when a failed Plan report is replayed", async () => {
    const created = await createTask();
    const body = {
      status: "failed",
      output: {},
      retryWaitSupported: true,
      error: {
        code: "OPENCLAW_INVOCATION_FAILED",
        message: "OpenClaw invocation failed",
        stage: "agent_invoke",
        reason: "upstream_unavailable",
        httpStatus: 503,
        retryable: true,
      },
    };
    const first = await harness.service.reportStep(created.identity, body);
    const replayed = await harness.service.reportStep(created.identity, body);
    expect(replayed).toMatchObject({
      ok: true,
      duplicate: true,
      retryWaitSupported: true,
      decisionDeadlineAt: first.decisionDeadlineAt,
    });
  });

  it("starts a new AIS when the failed Plan retry wait has expired", async () => {
    const created = await createTask();
    await harness.service.reportStep(created.identity, {
      status: "failed",
      output: {},
      retryWaitSupported: true,
      error: {
        code: "OPENCLAW_INVOCATION_FAILED",
        message: "OpenClaw invocation failed",
        stage: "agent_invoke",
        reason: "upstream_unavailable",
        httpStatus: 503,
        retryable: true,
      },
    });
    harness.now.value += harness.repairConfig.decisionGraceSeconds + 1;

    await expect(harness.service.resumeTask({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=resume-cookie" },
      taskId: created.taskId,
      body: {},
    })).resolves.toMatchObject({
      status: "running",
      currentStep: { stepId: `${created.taskId}-PLAN-2`, attempt: 2 },
    });
    expect(harness.execute).toHaveBeenCalledTimes(2);
  });

  it("falls back from an invalid latest checkpoint to the newest valid older history entry", async () => {
    const created = await createTask();
    const second = await resumeAfterContextCheckpoint({
      config: created.config,
      checkpoint: { cursor: "older-valid" },
    });
    const third = await resumeAfterContextCheckpoint({
      config: second,
      checkpoint: { cursor: "latest-invalid" },
      metadata: { objectKey: `${second.current.artifacts.checkpoint.objectKey}.forged` },
    });

    const bootstrap = await harness.service.bootstrap({
      taskId: created.taskId,
      stepId: third.current.stepId,
      executionId: third.execution.executionId,
    });
    expect(bootstrap.recoveryCheckpoint).toMatchObject({
      sourceStepId: created.config.current.stepId,
      content: { cursor: "older-valid" },
    });
  });

  it("returns live bootstrap context after an approved restart refresh without growing bootstrap audit rows", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created, [{
      actionId: "restart-bot",
      type: "ocb_operation",
      summary: "重启当前 Bot",
      risk: "服务会短暂不可用",
      verification: "复查原始症状",
      rollback: null,
      operation: { type: "restart_bot", params: {} },
    }]);
    await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    });
    const claimed = await harness.service.claimDecision(created.identity);
    const applyIdentity = {
      taskId: created.taskId,
      stepId: String(claimed.stepId),
      executionId: created.identity.executionId,
    };
    const first = await harness.service.bootstrap(applyIdentity);
    expect(first.runtimeTargetVersion).toBe(1);

    const pending = await harness.service.requestOcbOperation(applyIdentity, {
      clientRequestId: "restart-and-refresh-target",
      purpose: "执行获批的 Bot 重启并刷新运行目标",
      actionId: "restart-bot",
    });
    const restartedTarget = {
      ...target("2026-08-17T00:01:00.000Z"),
      bindingId: "binding-2",
      deviceId: "bot-uuid-2",
    };
    harness.resolveTarget
      .mockResolvedValueOnce(target())
      .mockResolvedValue(restartedTarget);
    harness.executeOcb.mockResolvedValueOnce({
      operation: "restart_bot",
      result: { success: true },
      requiresTargetRefresh: true,
    });
    await harness.service.fulfillToolCall({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=refresh-cookie", "x-user-id": ACTOR },
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
    });
    expect(harness.executeOcb).toHaveBeenCalledWith({
      scope: created.config.authorizationScope,
      operation: { type: "restart_bot", params: {} },
      authHeaders: { cookie: "SSO=refresh-cookie", "x-user-id": ACTOR },
      callerUserId: ACTOR,
      callerIsAdmin: false,
    });

    const second = await harness.service.bootstrap(applyIdentity);
    expect(second).toMatchObject({
      runtimeTargetVersion: 2,
      target: {
        bindingId: "binding-2",
        deviceId: "bot-uuid-2",
      },
    });
    const bootstrapCalls = (await harness.repairRepo.listToolCalls(created.taskId))
      .filter(call => call.toolName === "repair_control" && call.operation === "bootstrap");
    expect(bootstrapCalls).toHaveLength(1);
    expect(bootstrapCalls[0].request).toEqual({
      schemaVersion: "repair-tool-request/v1",
      runtimeTargetVersion: 1,
      purpose: "载入本次 Repair 的目标、历史记录和可用工具",
      payload: {},
    });
    expect(bootstrapCalls[0].result).toMatchObject({ runtimeTargetVersion: 1 });
    const bootstrapCreates = vi.mocked(harness.repairRepo.createToolCall).mock.calls
      .map(([input]) => input)
      .filter(input => input.toolName === "repair_control" && input.operation === "bootstrap");
    expect(bootstrapCreates.map(input => input.request)).toEqual([
      {
        schemaVersion: "repair-tool-request/v1",
        runtimeTargetVersion: 1,
        purpose: "载入本次 Repair 的目标、历史记录和可用工具",
        payload: {},
      },
      {
        schemaVersion: "repair-tool-request/v1",
        runtimeTargetVersion: 2,
        purpose: "载入本次 Repair 的目标、历史记录和可用工具",
        payload: {},
      },
    ]);
    const publicBootstrap = (await harness.service.getTask(ACTOR, created.taskId)).toolCalls as Array<Record<string, unknown>>;
    const browserBootstrap = publicBootstrap.find(call => call.toolName === "repair_control"
      && call.operation === "bootstrap");
    expect(browserBootstrap).toMatchObject({ targetVersion: 1, status: "succeeded" });
    expect(browserBootstrap).not.toHaveProperty("request");
    expect(browserBootstrap).not.toHaveProperty("result");
  });

  it.each([
    ["non-canonical objectKey", (key: string) => ({ objectKey: `${key}.forged` })],
    ["metadata size mismatch", (_key: string, content: Buffer) => ({ size: content.byteLength + 1 })],
    ["metadata digest mismatch", () => ({ sha256: "0".repeat(64) })],
    ["checkpoint larger than 64 KiB", (_key: string, content: Buffer) => ({ size: content.byteLength })],
  ])("ignores an invalid historical checkpoint: %s", async (_label, override) => {
    const created = await createTask();
    const checkpoint = _label === "checkpoint larger than 64 KiB"
      ? { state: "x".repeat(64 * 1024) }
      : { state: "safe" };
    const content = Buffer.from(JSON.stringify(checkpoint));
    const key = created.config.current.artifacts.checkpoint.objectKey;
    const bootstrap = await bootstrapAfterContextRecovery({
      created,
      checkpoint,
      metadata: override(key, content),
    });
    expect(bootstrap.recoveryCheckpoint).toBeNull();
  });

  it("ignores a historical checkpoint whose verified JSON is not an object", async () => {
    const created = await createTask();
    const bootstrap = await bootstrapAfterContextRecovery({ created, checkpoint: ["not", "an", "object"] });
    expect(bootstrap.recoveryCheckpoint).toBeNull();
  });

  it("starts a new execution and dispatches a new job when the approval can no longer be claimed", async () => {
    const created = await createTask();
    const written = await reportPlanReady(created);
    harness.now.value = 2_000;

    const result = await harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=fallback-cookie" },
      taskId: created.taskId,
      body: {
        decision: "approve",
        artifactDigest: written.digest,
      },
    });

    expect(result).toMatchObject({ status: "running", currentStep: { phase: "repair_apply" } });
    expect(harness.execute).toHaveBeenCalledTimes(2);
    const secondParams = harness.execute.mock.calls[1][1] as Record<string, string>;
    expect(Object.keys(secondParams)).toEqual([REPAIR_PARAMS_KEY]);
    const secondEnvelope = parseSnapshotEnvelope(secondParams);
    expect(secondEnvelope).toMatchObject({
      execution: { action: "repair_apply" },
      input: {
        approvedPlan: { artifactDigest: written.digest },
      },
      runtime: { executionTicket: expect.stringMatching(/^ce_repair_/) },
    });
    expect(secondEnvelope.execution.executionId).not.toBe(created.config.execution.executionId);
    const firstParams = harness.execute.mock.calls[0][1] as Record<string, string>;
    expect(secondEnvelope.runtime.executionTicket)
      .not.toBe(parseSnapshotEnvelope(firstParams).runtime.executionTicket);

    const steps = await harness.repo.listSteps(created.taskId);
    expect(steps.map((item) => [item.step_type, item.bot_run_id]))
      .toEqual([["repair_plan", "job-1"], ["repair_apply", "job-2"]]);
    const task = await harness.repo.findTask(created.taskId);
    expect((JSON.parse(task!.config_json) as RepairTaskConfig).execution.executionId)
      .toBe(secondEnvelope.execution.executionId);
  });

  it("requires a fresh custom OpenClaw API key only when approval starts a new job", async () => {
    const firstKey = "first-execution-key-canary";
    const secondKey = "second-execution-key-canary";
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "vendor/custom-model",
      llmApiKey: firstKey,
    });
    const written = await reportPlanReady(created);
    harness.now.value = 2_000;

    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest },
    })).rejects.toMatchObject({ status: 400, code: "llm_api_key_required" });
    expect(harness.execute).toHaveBeenCalledTimes(1);
    expect(await harness.repo.listSteps(created.taskId)).toHaveLength(1);

    await expect(harness.service.decidePlan({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=decision-cookie" },
      taskId: created.taskId,
      body: { decision: "approve", artifactDigest: written.digest, llmApiKey: secondKey },
    })).resolves.toMatchObject({ status: "running", currentStep: { phase: "repair_apply" } });
    expect(harness.execute).toHaveBeenCalledTimes(2);
    const envelope = parseSnapshotEnvelope(harness.execute.mock.calls[1][1] as Record<string, string>);
    expect(envelope).toMatchObject({
      execution: { agentMode: "openclaw", action: "repair_apply" },
      input: {
        agent: {
          openclaw: {
            useDefaultModelConfig: false,
            model: "vendor/custom-model",
            modelApiKey: secondKey,
          },
        },
      },
    });
    const persisted = JSON.stringify({
      task: await harness.repo.findTask(created.taskId),
      steps: await harness.repo.listSteps(created.taskId),
    });
    expect(persisted).not.toContain(firstKey);
    expect(persisted).not.toContain(secondKey);
  });

  it("rejects a malformed Apply artifact instead of trusting executor metadata", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);

    await expect(reportApplyArtifact(created, applyConfig, { status: "not_fixed" }))
      .rejects.toMatchObject({ status: 400, code: "invalid_repair_apply_result" });
    expect((await harness.repo.findTask(created.taskId))!.status).toBe("running");
    expect((await harness.repo.findStep(applyConfig.current.stepId))!.status).not.toBe("succeeded");
  });

  it("rejects an Apply artifact that omits an earlier failed write attempt", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    const succeededCallId = await runApprovedAction(created, applyConfig);
    harness.applyApprovedAction.mockResolvedValueOnce({ status: "failed", exitCode: 1 });
    await runApprovedAction(created, applyConfig, { clientRequestId: "apply-retry-failed", retry: true });

    await expect(reportApplyArtifact(
      created,
      applyConfig,
      validApplyResult(created, applyConfig, succeededCallId),
    )).rejects.toMatchObject({
      status: 409,
      code: "repair_apply_evidence_incomplete",
      recovery: {
        recoveryClass: "model_output",
        recoveryAction: "regenerate_final_result",
        automatic: true,
      },
    });
    expect((await harness.repo.findTask(created.taskId))!.status).toBe("running");
  });

  it("rechecks the complete Apply write ledger inside the terminal Step transaction", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    const succeededCallId = await runApprovedAction(created, applyConfig);
    const transition = vi.mocked(harness.repairRepo.transitionStep);
    const originalTransition = transition.getMockImplementation()!;
    transition.mockImplementationOnce(async (input) => {
      const raced = await harness.repairRepo.createToolCall({
        callId: "rtc-raced-failed-write",
        taskId: created.taskId,
        stepId: applyConfig.current.stepId,
        executionId: applyConfig.execution.executionId,
        authorizationScopeDigest: applyConfig.authorizationScopeDigest,
        clientRequestId: "raced-failed-write",
        toolName: "baas_write",
        operation: "apply_action:restart-gateway",
        actionId: "restart-gateway",
        request: { actionId: "restart-gateway", retry: true },
        isWrite: true,
      });
      await harness.repairRepo.completeToolCall({
        callId: raced.call.callId,
        executionId: applyConfig.execution.executionId,
        authorizationScopeDigest: applyConfig.authorizationScopeDigest,
        status: "failed",
        result: { status: "failed" },
      });
      return originalTransition(input);
    });

    await expect(reportApplyArtifact(
      created,
      applyConfig,
      validApplyResult(created, applyConfig, succeededCallId),
    )).rejects.toMatchObject({ status: 409, code: "repair_apply_evidence_incomplete" });
    expect((await harness.repo.findTask(created.taskId))!.status).toBe("running");
  });

  it("rejects an Apply attempt whose claimed status does not match its write ledger call", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    const toolCallId = await runApprovedAction(created, applyConfig);
    const result = validApplyResult(created, applyConfig, toolCallId);
    const action = (result.actions as Array<Record<string, unknown>>)[0];
    action.status = "failed";
    action.attempts = [{ status: "failed", toolCallId, evidence: ["写操作被报告为失败"] }];
    action.verification = { status: "failed", evidence: ["验证结果被报告为失败"] };
    result.verdict = "failed";

    await expect(reportApplyArtifact(created, applyConfig, result))
      .rejects.toMatchObject({ status: 409, code: "repair_apply_evidence_mismatch" });
    expect((await harness.repo.findTask(created.taskId))!.status).toBe("running");
  });

  it("rejects Apply attempts reported out of their immutable ledger order", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    const firstCallId = await runApprovedAction(created, applyConfig);
    const secondCallId = await runApprovedAction(created, applyConfig, {
      clientRequestId: "apply-retry-succeeded",
      retry: true,
    });
    const result = validApplyResult(created, applyConfig, firstCallId);
    const action = (result.actions as Array<Record<string, unknown>>)[0];
    action.attempts = [
      { status: "succeeded", toolCallId: secondCallId, evidence: ["第二次写操作已完成"] },
      { status: "succeeded", toolCallId: firstCallId, evidence: ["第一次写操作已完成"] },
    ];

    await expect(reportApplyArtifact(created, applyConfig, result))
      .rejects.toMatchObject({ status: 409, code: "repair_apply_evidence_order_mismatch" });
  });

  it("accepts one English summary when the complete Apply result is Chinese-dominant", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    const toolCallId = await runApprovedAction(created, applyConfig);
    const result = validApplyResult(created, applyConfig, toolCallId);
    result.summary = "Repair completed and verified";

    await expect(reportApplyArtifact(created, applyConfig, result))
      .resolves.toBeUndefined();
  });

  it("rejects an Apply result whose complete user-facing result is English-dominant", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    const toolCallId = await runApprovedAction(created, applyConfig);
    const result = validApplyResult(created, applyConfig, toolCallId);
    const action = (result.actions as Array<Record<string, unknown>>)[0];
    const attempts = action.attempts as Array<Record<string, unknown>>;
    const verification = action.verification as Record<string, unknown>;
    attempts[0].evidence = ["The write operation completed successfully"];
    verification.evidence = ["The service recovered successfully"];
    result.evidence = [{ source: toolCallId, claim: "The final verification succeeded" }];
    result.summary = "Repair completed and verified";

    await expect(reportApplyArtifact(created, applyConfig, result))
      .rejects.toMatchObject({ status: 400, code: "invalid_repair_apply_result_language" });
  });

  it("keeps a successful Apply in the configured decision window and reuses its job for an in-grace retry", async () => {
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "Kimi-K2.5",
      llmApiKey: "initial-result-key-canary",
    });
    const applyConfig = await advanceToApply(created);
    await reportApplyReady(created, applyConfig);

    const waitingTask = await harness.repo.findTask(created.taskId);
    const waitingConfig = JSON.parse(waitingTask!.config_json) as RepairTaskConfig;
    expect(waitingTask!.status).toBe("waiting_acceptance");
    expect((await harness.repo.findStep(applyConfig.current.stepId))!.summary)
      .toBe("修复动作已执行并完成验证");
    expect(waitingConfig.execution).toMatchObject({
      executionId: applyConfig.execution.executionId,
      jobId: applyConfig.execution.jobId,
      stepId: applyConfig.current.stepId,
      phase: "repair_apply",
      state: "waiting_decision",
      decisionDeadlineAt: 1_900,
      invalidatedAt: null,
    });

    const decision = await harness.service.decideResult({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=result-cookie" },
      taskId: created.taskId,
      body: { decision: "retry", reason: "still broken" },
    });
    expect(decision).toMatchObject({
      status: "waiting_acceptance",
      pendingDecision: { kind: "retry_result", feedback: "still broken" },
    });
    expect(harness.execute).toHaveBeenCalledTimes(1);

    const claimed = await harness.service.claimDecision({
      taskId: created.taskId,
      stepId: applyConfig.current.stepId,
      executionId: applyConfig.execution.executionId,
    });
    expect(claimed).toMatchObject({
      status: "claimed",
      reusedJob: true,
      phase: "repair_plan",
      continuation: {
        execution: {
          action: "repair_plan",
          executionId: applyConfig.execution.executionId,
        },
      },
    });
    expect(harness.execute).toHaveBeenCalledTimes(1);
    const continuedTask = await harness.repo.findTask(created.taskId);
    const continued = JSON.parse(continuedTask!.config_json) as RepairTaskConfig;
    expect(continued.current.phase).toBe("repair_plan");
    expect(continued.execution).toMatchObject({
      executionId: applyConfig.execution.executionId,
      jobId: applyConfig.execution.jobId,
      state: "running",
    });
  });

  it("derives an expired browser decision window without mutating the stored execution audit", async () => {
    const created = await createTask();
    await advanceToApply(created);
    const applyTask = await harness.repo.findTask(created.taskId);
    const applyConfig = JSON.parse(applyTask!.config_json) as RepairTaskConfig;
    await reportApplyReady(created, applyConfig);
    const before = await harness.repo.findTask(created.taskId);
    const storedBefore = JSON.parse(before!.config_json) as RepairTaskConfig;
    expect(storedBefore.execution).toMatchObject({
      state: "waiting_decision",
      leaseExpiresAt: 1_090,
      decisionDeadlineAt: 1_900,
    });

    harness.now.value = 1_936;
    const view = await harness.service.getTask(ACTOR, created.taskId) as {
      execution: Record<string, unknown>;
    };
    expect(view.execution).toMatchObject({
      state: "ended",
      decisionWindowExpired: true,
      leaseExpiresAt: 1_090,
      decisionDeadlineAt: 1_900,
    });
    const after = await harness.repo.findTask(created.taskId);
    expect(after!.config_json).toBe(before!.config_json);
    expect((JSON.parse(after!.config_json) as RepairTaskConfig).execution.state).toBe("waiting_decision");
  });

  it("accepts an Apply result immediately and releases the waiting execution", async () => {
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    await reportApplyReady(created, applyConfig);

    const completed = await harness.service.decideResult({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=result-cookie" },
      taskId: created.taskId,
      body: { decision: "accept" },
    });
    expect(completed).toMatchObject({
      status: "completed",
      pendingDecision: null,
      execution: { state: "ended", decisionDeadlineAt: null },
    });
    const task = await harness.repo.findTask(created.taskId);
    const finalConfig = JSON.parse(task!.config_json) as RepairTaskConfig;
    expect(finalConfig.execution).toMatchObject({
      executionId: applyConfig.execution.executionId,
      jobId: applyConfig.execution.jobId,
      state: "ended",
      invalidatedAt: 1_000,
      leaseExpiresAt: 1_000,
      decisionDeadlineAt: null,
    });
    expect((await harness.repo.listSteps(created.taskId))).toHaveLength(2);
    expect(harness.execute).toHaveBeenCalledTimes(1);
  });

  it("requires a fresh custom OpenClaw API key after the Apply decision window expires", async () => {
    const created = await createTask({
      agentMode: "openclaw",
      llmUseDefault: false,
      llmModel: "Kimi-K2.5",
      llmApiKey: "initial-result-key-canary",
    });
    const applyConfig = await advanceToApply(created);
    await reportApplyReady(created, applyConfig);
    harness.now.value = 1_901;

    await expect(harness.service.decideResult({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=result-cookie" },
      taskId: created.taskId,
      body: { decision: "retry", reason: "still broken" },
    })).rejects.toMatchObject({ status: 400, code: "llm_api_key_required" });
    expect(harness.execute).toHaveBeenCalledTimes(1);

    await harness.service.decideResult({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=result-cookie" },
      taskId: created.taskId,
      body: { decision: "retry", reason: "still broken", llmApiKey: "fresh-result-key-canary" },
    });
    expect(harness.execute).toHaveBeenCalledTimes(2);
    const envelope = parseSnapshotEnvelope(harness.execute.mock.calls[1][1] as Record<string, string>);
    expect(envelope).toMatchObject({
      execution: { agentMode: "openclaw", action: "repair_plan" },
      input: { agent: { openclaw: { modelApiKey: "fresh-result-key-canary" } } },
    });
    expect(JSON.stringify(await harness.repo.findTask(created.taskId))).not.toContain("fresh-result-key-canary");
  });

  it("relays one cfuse AuthCode from the owner to the same execution without persisting or replaying it", async () => {
    const created = await createTask(CFUSE_AGENT_INPUT);
    const loginUrl = "https://codefuse.antgroup-inc.cn/cloud/oauth?port=31337";
    const pending = await harness.service.requestCfuseLogin(created.identity, {
      clientRequestId: "cfuse-login-1",
      loginUrl,
    });
    expect(pending).toMatchObject({
      toolName: "cfuse_login",
      operation: "authorize",
      status: "pending",
      request: { loginUrl },
      requiresBrowserRelay: false,
      deadlineAt: 1_300,
    });
    expect(await harness.service.takeCfuseAuthCode(
      created.identity,
      String(pending.toolCallId),
    )).toEqual({ status: "waiting", toolCallId: pending.toolCallId });

    await expect(harness.service.submitCfuseAuthCode({
      actorUserId: "attacker",
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
      body: { authCode: "stolen-code" },
    })).rejects.toMatchObject({ status: 403, code: "repair_task_forbidden" });

    const submitted = await harness.service.submitCfuseAuthCode({
      actorUserId: ACTOR,
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
      body: { authCode: "first-one-time-code" },
    });
    expect(submitted).toMatchObject({
      toolCallId: pending.toolCallId,
      toolName: "cfuse_login",
      operation: "authorize",
      status: "executing",
      deadlineAt: 1_300,
      requiresBrowserRelay: false,
    });
    expect(submitted).not.toHaveProperty("request");
    expect(submitted).not.toHaveProperty("result");
    expect(await harness.service.submitCfuseAuthCode({
      actorUserId: ACTOR,
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
      body: { authCode: "must-not-overwrite" },
    })).toMatchObject({ status: "executing", requiresBrowserRelay: false });

    await expect(harness.service.takeCfuseAuthCode({
      ...created.identity,
      executionId: "exec-other",
    }, String(pending.toolCallId))).rejects.toMatchObject({
      status: 403,
      code: "repair_workload_scope_mismatch",
    });
    expect(await harness.service.takeCfuseAuthCode(
      created.identity,
      String(pending.toolCallId),
    )).toEqual({
      status: "available",
      toolCallId: pending.toolCallId,
      authCode: "first-one-time-code",
    });
    expect(await harness.service.takeCfuseAuthCode(
      created.identity,
      String(pending.toolCallId),
    )).toEqual({ status: "already_taken", toolCallId: pending.toolCallId });

    const beforeReport = await harness.service.getTask(ACTOR, created.taskId);
    expect(JSON.stringify(beforeReport)).not.toContain("first-one-time-code");
    expect(JSON.stringify(beforeReport)).not.toContain("must-not-overwrite");
    expect(beforeReport).toMatchObject({
      toolCalls: [expect.objectContaining({
        toolCallId: pending.toolCallId,
        status: "executing",
        cfuseLoginUrl: loginUrl,
      })],
    });
    const browserLogin = (beforeReport.toolCalls as Array<Record<string, unknown>>)[0];
    expect(browserLogin).not.toHaveProperty("request");
    expect(browserLogin).not.toHaveProperty("result");

    await harness.service.setTaskShared({
      actorUserId: ACTOR,
      taskId: created.taskId,
      shared: true,
    });
    const sharedView = await harness.service.getTask("other-user", created.taskId);
    const sharedLogin = (sharedView.toolCalls as Array<Record<string, unknown>>)[0];
    expect(sharedLogin).not.toHaveProperty("cfuseLoginUrl");
    expect(sharedLogin).not.toHaveProperty("request");
    expect(sharedLogin).not.toHaveProperty("result");

    const reported = await harness.service.reportCfuseLogin(
      created.identity,
      String(pending.toolCallId),
      { status: "succeeded" },
    );
    expect(reported).toMatchObject({
      toolCallId: pending.toolCallId,
      status: "succeeded",
      result: { loginStatus: "succeeded" },
    });
    const persisted = JSON.stringify({
      task: await harness.repo.findTask(created.taskId),
      step: await harness.repo.findStep(created.config.current.stepId),
      calls: await harness.repairRepo.listToolCalls(created.taskId),
    });
    expect(persisted).not.toContain("first-one-time-code");
    expect(persisted).not.toContain("must-not-overwrite");
  });

  it.each([
    "http://codefuse.antgroup-inc.cn/cloud/oauth?port=31337",
    "https://evil.example/cloud/oauth?port=31337",
    "https://codefuse.antgroup-inc.cn/cloud/oauth/extra?port=31337",
    "https://codefuse.antgroup-inc.cn/cloud/oauth",
    "https://codefuse.antgroup-inc.cn/cloud/oauth?port=0",
    "https://codefuse.antgroup-inc.cn/cloud/oauth?port=65536",
    "https://codefuse.antgroup-inc.cn/cloud/oauth?port=31337&identifier=abc",
    "https://codefuse.antgroup-inc.cn/cloud/oauth?identifier=bad%2Fidentifier",
    "https://codefuse.antgroup-inc.cn/cloud/oauth?identifier=valid#fragment",
  ])("rejects a cfuse login URL outside the exact OAuth allowlist: %s", async (loginUrl) => {
    const created = await createTask(CFUSE_AGENT_INPUT);
    await expect(harness.service.requestCfuseLogin(created.identity, {
      clientRequestId: "invalid-login-url",
      loginUrl,
    })).rejects.toMatchObject({ status: 400, code: "invalid_cfuse_login_url" });
    expect(await harness.repairRepo.listToolCalls(created.taskId)).toHaveLength(0);
  });

  it("accepts a bounded cfuse identifier URL and expires the relay without retaining an AuthCode", async () => {
    const created = await createTask(CFUSE_AGENT_INPUT);
    const pending = await harness.service.requestCfuseLogin(created.identity, {
      clientRequestId: "cfuse-identifier",
      loginUrl: "https://codefuse.antgroup-inc.cn/cloud/oauth?identifier=abc_123-def",
    });
    harness.now.value = 1_301;
    await expect(harness.service.submitCfuseAuthCode({
      actorUserId: ACTOR,
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
      body: { authCode: "expired-code" },
    })).rejects.toMatchObject({ status: 409, code: "repair_cfuse_login_expired" });
    expect(await harness.service.takeCfuseAuthCode(
      created.identity,
      String(pending.toolCallId),
    )).toEqual({ status: "expired", toolCallId: pending.toolCallId });
    expect(JSON.stringify(await harness.repairRepo.listToolCalls(created.taskId)))
      .not.toContain("expired-code");
  });

  it("exposes target-container reads directly and reserves OCB for approved Bot restart", async () => {
    const created = await createTask();
    const bootstrap = await harness.service.bootstrap(created.identity);

    expect(bootstrap).toMatchObject({
      tools: {
        ocbRead: [],
        ocbApply: ["restart_bot"],
      },
    });
    expect((bootstrap.tools as Record<string, unknown>)).not.toHaveProperty("identityReadRouting");
    expect(harness.resolveTarget).toHaveBeenCalledWith({
      environment: "pre",
      ownerId: ACTOR,
      botId: BOT_ID,
    });
  });

  it("retires a pending historical OCB read instead of relaying it to the browser", async () => {
    const created = await createTask();
    const historical = await harness.repairRepo.createToolCall({
      callId: "rtc-historical-ocb-read",
      taskId: created.taskId,
      stepId: created.identity.stepId,
      executionId: created.identity.executionId,
      authorizationScopeDigest: created.config.authorizationScopeDigest,
      clientRequestId: "historical-current-target",
      toolName: "ocb_read",
      operation: "current_target",
      request: { operation: "current_target", params: {} },
      isWrite: false,
      deadlineAt: harness.now.value + 300,
    });

    await expect(harness.service.fulfillToolCall({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=owner", "x-user-id": ACTOR },
      taskId: created.taskId,
      toolCallId: historical.call.callId,
    })).rejects.toMatchObject({
      status: 409,
      code: "repair_ocb_operation_retired",
    });
    expect(harness.executeOcb).not.toHaveBeenCalled();
    await expect(harness.repairRepo.findToolCall(historical.call.callId)).resolves.toMatchObject({
      status: "failed",
      errorCode: "repair_ocb_operation_retired",
    });
  });
  it("keeps a legacy ARCA runtime inspection pending until the Owner page relays its identity", async () => {
    const arcaTarget: RepairTarget = {
      ...target(),
      provider: "arca",
      deviceId: "legacy-arca-device",
      sandboxId: "ARCA-SANDBOX-123",
    };
    harness.resolveTarget.mockResolvedValue(arcaTarget);
    const created = await createTask({ diagnosticMode: "deep" });

    const pending = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "arca-runtime-read-1",
      purpose: "读取旧 ARCA 容器中的运行进程",
      operation: "process_list",
      pattern: "openclaw",
    });
    expect(pending).toMatchObject({
      toolName: "arca_read",
      operation: "process_list",
      status: "pending",
      requiresBrowserRelay: true,
      targetVersion: 1,
    });
    expect(harness.inspectRuntime).not.toHaveBeenCalled();

    await expect(harness.service.fulfillToolCall({
      actorUserId: "shared-viewer",
      authHeaders: { Cookie: "SSO=wrong", "x-user-id": "shared-viewer" },
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
    })).rejects.toMatchObject({ status: 403, code: "repair_task_forbidden" });

    const fulfilled = await harness.service.fulfillToolCall({
      actorUserId: ACTOR,
      authHeaders: { Cookie: "SSO=owner", "x-user-id": ACTOR },
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
    });
    expect(fulfilled).toMatchObject({
      toolCallId: pending.toolCallId,
      status: "succeeded",
      requiresBrowserRelay: false,
    });
    expect(harness.inspectRuntime).toHaveBeenCalledWith(
      expect.objectContaining({ target: arcaTarget, runtimeTargetVersion: 1 }),
      { operation: "process_list", pattern: "openclaw" },
      { Cookie: "SSO=owner", "x-user-id": ACTOR },
    );
    expect(JSON.stringify(await harness.repairRepo.listToolCalls(created.taskId))).not.toContain("SSO=owner");
  });

  it("relays an approved legacy ARCA container action without accepting a replacement command", async () => {
    const arcaTarget: RepairTarget = {
      ...target(),
      provider: "arca",
      deviceId: "legacy-arca-device",
      sandboxId: "ARCA-SANDBOX-123",
    };
    harness.resolveTarget.mockResolvedValue(arcaTarget);
    const created = await createTask();
    const applyConfig = await advanceToApply(created);
    const applyIdentity = {
      taskId: created.taskId,
      stepId: applyConfig.current.stepId,
      executionId: applyConfig.execution.executionId,
    };

    const pending = await harness.service.applyAction(applyIdentity, {
      clientRequestId: "arca-approved-write-1",
      purpose: "执行已批准的旧 ARCA 容器修复动作",
      actionId: "restart-gateway",
    });
    expect(pending).toMatchObject({
      toolName: "arca_write",
      status: "pending",
      actionId: "restart-gateway",
      requiresBrowserRelay: true,
    });
    expect(harness.applyApprovedAction).not.toHaveBeenCalled();

    await harness.service.fulfillToolCall({
      actorUserId: ACTOR,
      authHeaders: { Cookie: "SSO=owner", "x-user-id": ACTOR },
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
    });
    expect(harness.applyApprovedAction).toHaveBeenCalledWith(
      expect.objectContaining({ target: arcaTarget, phase: "repair_apply" }),
      expect.objectContaining({
        actionId: "restart-gateway",
        command: "supervisorctl restart gateway",
      }),
      { Cookie: "SSO=owner", "x-user-id": ACTOR },
    );
  });

  it("keeps an approved restart request idempotent when the server recomputes its context deadline", async () => {
    const created = await createTask();
    const applyConfig = await advanceToRestartApply(created);
    const identity = {
      taskId: created.taskId,
      stepId: applyConfig.current.stepId,
      executionId: applyConfig.execution.executionId,
    };
    const first = await harness.service.requestOcbOperation(identity, {
      clientRequestId: "restart-context-retry",
      purpose: "执行获批的 Bot 重启",
      actionId: "restart-bot",
    });
    harness.now.value = 1_017;
    const retried = await harness.service.requestOcbOperation(identity, {
      clientRequestId: "restart-context-retry",
      purpose: "执行获批的 Bot 重启",
      actionId: "restart-bot",
    });

    expect(retried).toMatchObject({
      toolCallId: first.toolCallId,
      status: "pending",
      deadlineAt: 1_300,
      targetVersion: 1,
      request: { operation: "restart_bot", params: {}, actionId: "restart-bot", retry: false },
    });
    const stored = (await harness.repairRepo.listToolCalls(created.taskId, {
      stepId: applyConfig.current.stepId,
    })).filter(call => call.actionId === "restart-bot");
    expect(stored).toHaveLength(1);
    expect(unpackStoredToolRequest(stored[0].request).payload).toEqual({
      operation: "restart_bot",
      params: {},
      actionId: "restart-bot",
      retry: false,
    });
  });

  it("records an indeterminate restart failure as unknown without exposing the original secret", async () => {
    const created = await createTask();
    const applyConfig = await advanceToRestartApply(created);
    const identity = {
      taskId: created.taskId,
      stepId: applyConfig.current.stepId,
      executionId: applyConfig.execution.executionId,
    };
    const pending = await harness.service.requestOcbOperation(identity, {
      clientRequestId: "restart-failure",
      purpose: "执行获批的 Bot 重启",
      actionId: "restart-bot",
    });
    harness.executeOcb.mockRejectedValueOnce(new Error("upstream Cookie: SSO=terminal-secret"));

    await expect(harness.service.fulfillToolCall({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=owner", "x-user-id": ACTOR },
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
    })).resolves.toMatchObject({
      status: "unknown",
      error: {
        code: "repair_ocb_failed",
        message: "upstream [REDACTED_SECRET_TEXT]",
      },
    });
    const terminal = await harness.service.getToolCall(identity, String(pending.toolCallId));
    expect(terminal).toMatchObject({
      status: "unknown",
      error: {
        code: "repair_ocb_failed",
        message: "upstream [REDACTED_SECRET_TEXT]",
      },
    });
    expect(JSON.stringify(await harness.repairRepo.listToolCalls(created.taskId)))
      .not.toContain("terminal-secret");
  });

  it("stores a multiline indeterminate restart failure as a single-line unknown result", async () => {
    const created = await createTask();
    const applyConfig = await advanceToRestartApply(created);
    const identity = {
      taskId: created.taskId,
      stepId: applyConfig.current.stepId,
      executionId: applyConfig.execution.executionId,
    };
    const pending = await harness.service.requestOcbOperation(identity, {
      clientRequestId: "restart-multiline-failure",
      purpose: "执行获批的 Bot 重启",
      actionId: "restart-bot",
    });
    harness.executeOcb.mockRejectedValueOnce(new Error("restart failed\nupstream detail"));

    await expect(harness.service.fulfillToolCall({
      actorUserId: ACTOR,
      authHeaders: { cookie: "SSO=owner", "x-user-id": ACTOR },
      taskId: created.taskId,
      toolCallId: String(pending.toolCallId),
    })).resolves.toMatchObject({
      status: "unknown",
      error: {
        code: "repair_ocb_failed",
        message: "restart failed upstream detail",
      },
    });
    await expect(harness.service.getToolCall(identity, String(pending.toolCallId)))
      .resolves.toMatchObject({
        status: "unknown",
        error: {
          code: "repair_ocb_failed",
          message: "restart failed upstream detail",
        },
      });
  });
  it.each([
    {
      name: "unknown with no usable entries",
      result: {
        status: "unknown",
        returnedEntries: 0,
        totalEntries: 0,
        sources: [{ name: "agentclaw", status: "failed", error: "RAW_SOURCE_ERROR_CANARY" }],
      },
      expected: "日志查询调用已结束，未获得可用条目。未覆盖：agentclaw（查询失败）。未覆盖仅表示本次未取得对应日志源证据，不代表相应服务异常。",
    },
    {
      name: "partial with usable entries",
      result: {
        status: "partial",
        returnedEntries: 2,
        totalEntries: 2,
        sources: [
          { name: "agentclaw", status: "success" },
          { name: "other", status: "failed", error: "RAW_SOURCE_ERROR_CANARY" },
        ],
      },
      expected: "日志查询部分完成，返回 2 / 2 条记录。已覆盖：agentclaw（0 条）。未覆盖：other（查询失败）。未覆盖仅表示本次未取得对应日志源证据，不代表相应服务异常。",
    },
    {
      name: "successful",
      result: {
        status: "success",
        returnedEntries: 3,
        totalEntries: 3,
        sources: [{ name: "agentclaw", status: "success" }],
      },
      expected: "日志查询完成，返回 3 / 3 条记录。已覆盖：agentclaw（0 条）。",
    },
    {
      name: "mixed coverage with clawweb READ ACL denied",
      result: {
        status: "unknown",
        returnedEntries: 0,
        totalEntries: 0,
        sources: [
          { name: "agentclaw", status: "success", entriesCount: 0 },
          { name: "bcn", status: "success", entriesCount: 0 },
          { name: "clawweb", status: "failed", error: "region et15 ACL READ 被拒" },
        ],
      },
      expected: "日志查询部分完成，未发现匹配条目。已覆盖：agentclaw（0 条）、bcn（0 条）。未覆盖：clawweb（READ 权限不足）。未覆盖仅表示本次未取得对应日志源证据，不代表相应服务异常。",
    },
  ])("derives a safe AntLogs summary for $name", async ({ result, expected }) => {
    const created = await createTask();
    harness.searchLogs.mockResolvedValueOnce(result);
    await harness.service.searchLogs(created.identity, {
      clientRequestId: `logs-summary-${result.status}`,
      purpose: "查询 AgentClaw 后端日志并确认是否取得有效证据",
      identifiers: ["botId"],
    });

    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      toolCalls: Array<Record<string, unknown>>;
    };
    expect(ownerView.toolCalls[0]).toMatchObject({ resultSummary: expected });
    expect(JSON.stringify(ownerView)).not.toContain("RAW_SOURCE_ERROR_CANARY");
  });

  it("treats a partially covered AntLogs result as effective evidence even when the aggregate status is unknown", async () => {
    const created = await createTask();
    harness.searchLogs.mockResolvedValueOnce({
      status: "unknown",
      returnedEntries: 0,
      totalEntries: 0,
      sources: [
        { name: "agentclaw", status: "success", entriesCount: 0 },
        { name: "clawweb", status: "failed", error: "region et15 ACL READ 被拒" },
      ],
    });
    const searched = await harness.service.searchLogs(created.identity, {
      clientRequestId: "logs-partial-effective-evidence",
      purpose: "查询后端日志并确认已覆盖来源中是否存在匹配异常",
      identifiers: ["botId"],
    });
    const source = await harness.repairRepo.findToolCall(String(searched.toolCallId));
    expect(source).toMatchObject({ status: "succeeded", result: { status: "unknown" } });

    await expect(harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: source!.callId,
      evidenceToolCallIds: [source!.callId],
      conclusionZh: "已覆盖的后端日志源查询成功且未发现匹配异常，另一个来源未覆盖。",
      nextAction: "结合其他只读证据继续判断，并保留未覆盖来源的不确定性。",
    })).resolves.toMatchObject({ recorded: true, sourceToolCallId: source!.callId });
  });

  it("persists a bounded purpose before execution and appends one immutable evidence-bound conclusion", async () => {
    const created = await createTask();
    const purpose = "读取 OpenClaw 配置片段并核对启动参数";
    const result = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "inspect-openclaw-config",
      purpose,
      operation: "fs_read",
      path: "/home/admin/.openclaw/openclaw.json",
      startLine: 1,
      lines: 20,
    });
    expect(result).toMatchObject({ status: "success", toolCallId: expect.stringMatching(/^rtc-/) });
    expect(harness.inspectRuntime).toHaveBeenCalledOnce();

    const [source] = await harness.repairRepo.listToolCalls(created.taskId);
    expect(source.request).toEqual({
      schemaVersion: "repair-tool-request/v1",
      runtimeTargetVersion: 1,
      purpose,
      semanticConclusionRequired: true,
      payload: {
        operation: "fs_read",
        path: "/home/admin/.openclaw/openclaw.json",
        startLine: 1,
        lines: 20,
      },
    });
    const terminalSource = JSON.stringify(source);
    const conclusionInput = {
      sourceToolCallId: source.callId,
      evidenceToolCallIds: [source.callId, source.callId],
      conclusionZh: "配置片段存在且包含两行有效内容，当前证据未显示文件读取失败。",
      nextAction: "继续检查对应进程的启动参数，并把结果绑定到本次调用。",
    };
    const recorded = await harness.service.recordSemanticConclusion(created.identity, conclusionInput);
    expect(recorded).toMatchObject({
      recorded: true,
      sourceToolCallId: source.callId,
      toolCallId: expect.stringMatching(/^rtc-/),
    });
    const afterRecord = await harness.repairRepo.listToolCalls(created.taskId);
    expect(afterRecord).toHaveLength(2);
    expect(JSON.stringify(afterRecord[0])).toBe(terminalSource);
    expect(afterRecord[1]).toMatchObject({
      toolName: "repair_control",
      operation: "record_conclusion",
      clientRequestId: `conclusion:${source.callId}`,
      status: "succeeded",
      request: {
        schemaVersion: "repair-tool-request/v1",
        runtimeTargetVersion: 1,
        purpose: "记录本次工具调用的证据结论",
        payload: {
          sourceToolCallId: source.callId,
          sourceResultDigest: source.resultDigest,
          evidenceToolCallIds: [source.callId],
          evidenceResultDigests: { [source.callId]: source.resultDigest },
          conclusionZh: conclusionInput.conclusionZh,
          nextAction: conclusionInput.nextAction,
        },
      },
    });

    await expect(harness.service.recordSemanticConclusion(created.identity, conclusionInput))
      .resolves.toMatchObject({ toolCallId: recorded.toolCallId });
    await expect(harness.service.recordSemanticConclusion(created.identity, {
      ...conclusionInput,
      conclusionZh: "同一源调用不允许用改写文本生成第二条结论。",
    })).rejects.toMatchObject({ status: 409, code: "repair_conclusion_already_recorded" });
    expect(await harness.repairRepo.listToolCalls(created.taskId)).toHaveLength(2);

    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      toolCalls: Array<Record<string, unknown>>;
    };
    expect(ownerView.toolCalls).toHaveLength(1);
    expect(ownerView.toolCalls[0]).toMatchObject({
      toolCallId: source.callId,
      purpose,
      executionTarget: "Bot 容器路径 /home/admin/.openclaw/openclaw.json",
      safeInvocation: {
        kind: "readonly_command",
        command: expect.stringContaining("sed -n '1,20p'"),
      },
      resultSummary: "文件片段读取完成，返回 2 行。",
      conclusion: {
        text: conclusionInput.conclusionZh,
        nextAction: conclusionInput.nextAction,
        evidenceToolCallIds: [source.callId],
      },
    });

    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("shared-viewer", created.taskId) as {
      toolCalls: Array<Record<string, unknown>>;
    };
    expect(sharedView.toolCalls).toHaveLength(1);
    expect(sharedView.toolCalls[0]).toEqual(ownerView.toolCalls[0]);
    expect(JSON.stringify(sharedView)).toContain("openclaw.json");
    expect(JSON.stringify(sharedView)).toContain(String(conclusionInput.conclusionZh));
    expect(JSON.stringify(sharedView)).toContain(String(conclusionInput.nextAction));
  });

  it("gates the next new-protocol business call and successful Step report on immutable conclusions", async () => {
    const created = await createTask();
    const first = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "first-sequenced-inspection",
      purpose: "先检查当前容器端口并形成第一条证据",
      operation: "port_list",
    });
    await expect(harness.service.inspectRuntime(created.identity, {
      clientRequestId: "first-sequenced-inspection",
      purpose: "先检查当前容器端口并形成第一条证据",
      operation: "port_list",
    })).resolves.toMatchObject({ toolCallId: first.toolCallId });

    await expect(harness.service.inspectRuntime(created.identity, {
      clientRequestId: "second-sequenced-inspection",
      purpose: "再检查当前容器进程并串联后续证据",
      operation: "process_list",
    })).rejects.toMatchObject({ status: 409, code: "repair_tool_conclusion_required" });
    await expect(reportPlanReady(created))
      .rejects.toMatchObject({
        status: 409,
        code: "repair_tool_conclusion_required",
        toolCallId: first.toolCallId,
        recovery: {
          recoveryClass: "agent_recovery",
          recoveryAction: "complete_missing_conclusions",
          automatic: true,
        },
      });
    expect(harness.inspectRuntime).toHaveBeenCalledTimes(1);

    await harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: String(first.toolCallId),
      evidenceToolCallIds: [String(first.toolCallId)],
      conclusionZh: "端口检查返回了两行有效结果，可以继续核对相关进程。",
      nextAction: "继续检查当前容器进程，并把新结果绑定到下一条结论。",
    });
    const second = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "second-sequenced-inspection",
      purpose: "再检查当前容器进程并串联后续证据",
      operation: "process_list",
    });
    await harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: String(second.toolCallId),
      evidenceToolCallIds: [String(first.toolCallId), String(second.toolCallId)],
      conclusionZh: "进程检查返回了有效结果，并与先前端口证据完成关联。",
      nextAction: "汇总当前步骤证据并生成待用户批准的修复方案。",
    });
    await expect(reportPlanReady(created)).resolves.toBeDefined();
    await expect(harness.repo.findTask(created.taskId))
      .resolves.toMatchObject({ status: "waiting_approval" });
  });

  it("accepts a conclusion without nextAction and system-closes only the unique tail gap", async () => {
    const created = await createTask();
    const first = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "optional-next-action-source",
      purpose: "检查当前容器端口并形成可独立解释的证据",
      operation: "port_list",
    });
    await expect(harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: first.toolCallId,
      evidenceToolCallIds: [first.toolCallId],
      conclusionZh: "端口检查已终态完成，返回结果已绑定到本次审计。",
    })).resolves.toMatchObject({ recorded: true, sourceToolCallId: first.toolCallId });

    const second = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "system-closeout-tail-source",
      purpose: "检查当前容器进程并验证系统尾部收口条件",
      operation: "process_list",
    });
    await expect(harness.service.systemCloseSemanticConclusion(created.identity, {
      sourceToolCallId: first.toolCallId,
    })).rejects.toMatchObject({ status: 409, code: "repair_system_closeout_unsafe" });
    await expect(harness.service.systemCloseSemanticConclusion(created.identity, {
      sourceToolCallId: second.toolCallId,
    })).resolves.toMatchObject({
      recorded: true,
      sourceToolCallId: second.toolCallId,
      systemGenerated: true,
    });

    const calls = await harness.repairRepo.listToolCalls(created.taskId);
    const systemRecord = calls.find(call => call.clientRequestId === `conclusion:${second.toolCallId}`);
    expect(systemRecord).toMatchObject({
      toolName: "repair_control",
      operation: "record_conclusion",
      status: "succeeded",
      request: {
        payload: {
          sourceToolCallId: second.toolCallId,
          conclusionZh: "调用已终态保存，但 Agent 未追加业务语义结论；本记录仅关闭审计缺口，不确认根因、方案或验证结果。",
          systemGenerated: true,
        },
      },
    });
    expect((systemRecord?.request as { payload?: Record<string, unknown> }).payload)
      .not.toHaveProperty("nextAction");
    await expect(reportPlanReady(created)).resolves.toBeDefined();
  });

  it("atomically admits only one concurrent semantic business call", async () => {
    const created = await createTask();
    let releaseFirst!: () => void;
    let firstStarted!: () => void;
    const firstStartedPromise = new Promise<void>((resolve) => { firstStarted = resolve; });
    const releaseFirstPromise = new Promise<void>((resolve) => { releaseFirst = resolve; });
    harness.inspectRuntime.mockImplementationOnce(async (_context, request: { operation: string }) => {
      firstStarted();
      await releaseFirstPromise;
      return {
        status: "success",
        operation: request.operation,
        target: { environment: "pre", bindingId: "binding-1", deviceId: "bot-uuid" },
        exitCode: 0,
        stdout: "line one\n",
        stderr: "",
        durationMs: 12,
      };
    });

    const first = harness.service.inspectRuntime(created.identity, {
      clientRequestId: "concurrent-semantic-one",
      purpose: "先检查当前容器端口并等待结果形成证据",
      operation: "port_list",
    });
    await firstStartedPromise;
    await expect(harness.service.inspectRuntime(created.identity, {
      clientRequestId: "concurrent-semantic-two",
      purpose: "同时检查当前容器进程并尝试形成第二条证据",
      operation: "process_list",
    })).rejects.toMatchObject({ status: 409, code: "repair_tool_conclusion_required" });
    expect(harness.inspectRuntime).toHaveBeenCalledTimes(1);
    releaseFirst();
    await expect(first).resolves.toMatchObject({ status: "success" });
  });

  it("rechecks the semantic ledger inside the successful Step transition", async () => {
    const created = await createTask();
    const transition = vi.mocked(harness.repairRepo.transitionStep);
    const implementation = transition.getMockImplementation()!;
    transition.mockImplementationOnce(async (input) => {
      const concurrentTerminalSource: RepairToolCall = {
        id: 10_000,
        callId: "concurrent-terminal-source",
        taskId: created.taskId,
        stepId: created.identity.stepId,
        executionId: created.identity.executionId,
        authorizationScopeDigest: created.config.authorizationScopeDigest,
        clientRequestId: "concurrent-terminal-source",
        toolName: "baas_read",
        operation: "port_list",
        actionId: null,
        deadlineAt: null,
        request: {
          schemaVersion: "repair-tool-request/v1",
          runtimeTargetVersion: 1,
          purpose: "并发完成端口检查并要求记录中文证据结论",
          semanticConclusionRequired: true,
          payload: { operation: "port_list" },
        },
        isWrite: false,
        status: "succeeded",
        leaseOwner: null,
        leaseExpiresAt: null,
        result: { status: "success" },
        resultDigest: "c".repeat(64),
        errorCode: null,
        errorMessage: null,
        downstreamTraceId: null,
        gmtCreate: 1,
        gmtModified: 1,
      };
      input.toolCallLedgerGuard?.([concurrentTerminalSource], {
        runtimeTargetVersion: created.config.runtimeTarget.version,
      });
      return implementation(input);
    });

    await expect(reportPlanReady(created))
      .rejects.toMatchObject({ status: 409, code: "repair_tool_conclusion_required" });
    await expect(harness.repo.findTask(created.taskId))
      .resolves.toMatchObject({ status: "running" });
  });

  it("does not impose the semantic-conclusion capability gate on legacy purpose-less calls", async () => {
    const created = await createTask();
    await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "legacy-purpose-less-inspection",
      operation: "port_list",
    });
    const [source] = await harness.repairRepo.listToolCalls(created.taskId, { recordKind: "source" });
    expect(source.request).not.toHaveProperty("semanticConclusionRequired");
    await expect(reportPlanReady(created)).resolves.toBeDefined();
  });

  it.each([
    {
      name: "nested failed status",
      result: { status: "failed", operation: "fs_read", exitCode: 44, stdout: "", stderr: "missing" },
      summary: "路径不存在或无法解析（退出码 44）。",
    },
    {
      name: "non-zero exit code",
      result: { status: "success", operation: "fs_read", exitCode: 45, stdout: "", stderr: "outside" },
      summary: "路径解析后超出允许范围（退出码 45）。",
    },
  ])("keeps ineffective source status separate from the Agent-authored conclusion for $name", async ({ result, summary }) => {
    const created = await createTask();
    harness.inspectRuntime.mockResolvedValueOnce(result);
    const inspected = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: `ineffective-evidence-${result.exitCode}`,
      purpose: "检查目标文件是否能在允许范围内安全读取",
      operation: "fs_read",
      path: "/home/admin/.openclaw/runtime-link",
      startLine: 1,
      lines: 20,
    });
    const [source] = await harness.repairRepo.listToolCalls(created.taskId, { recordKind: "source" });
    expect(source).toMatchObject({ callId: inspected.toolCallId, status: "succeeded" });

    await harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: source.callId,
      evidenceToolCallIds: [source.callId],
      conclusionZh: "本次检查未获得有效证据，不能据此确认目标文件内容。",
      nextAction: "修正路径或权限范围后重新检查，再根据新证据继续判断。",
    });

    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      toolCalls: Array<Record<string, unknown>>;
    };
    expect(ownerView.toolCalls[0]).toMatchObject({ status: "failed", resultSummary: summary });
    expect(source).toMatchObject({ status: "succeeded" });
    await harness.service.setTaskShared({ actorUserId: ACTOR, taskId: created.taskId, shared: true });
    const sharedView = await harness.service.getTask("shared-viewer", created.taskId) as {
      toolCalls: Array<Record<string, unknown>>;
    };
    expect(sharedView.toolCalls[0]).toMatchObject({ status: "failed", resultSummary: summary });
    expect(sharedView.toolCalls[0]).toEqual(ownerView.toolCalls[0]);
    expect(JSON.stringify(sharedView)).toContain("runtime-link");
  });

  it.each([
    { nestedStatus: "UNKNOWN", browserStatus: "unknown", summary: "目标结果未知。" },
    { nestedStatus: "canceled", browserStatus: "canceled", summary: "目标调用已取消。" },
    { nestedStatus: "ERROR", browserStatus: "failed", summary: "目标返回失败。" },
  ])("keeps the effective BaaS $nestedStatus status and summary aligned", async ({
    nestedStatus, browserStatus, summary,
  }) => {
    const created = await createTask();
    harness.inspectRuntime.mockResolvedValueOnce({
      status: nestedStatus,
      operation: "port_list",
      exitCode: null,
      stdout: "",
      stderr: "",
    });
    await harness.service.inspectRuntime(created.identity, {
      clientRequestId: `effective-${nestedStatus.toLowerCase()}`,
      purpose: "检查目标运行状态并验证有效业务结果投影",
      operation: "port_list",
    });

    const [source] = await harness.repairRepo.listToolCalls(created.taskId, { recordKind: "source" });
    expect(source).toMatchObject({ status: "succeeded" });
    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      toolCalls: Array<Record<string, unknown>>;
    };
    expect(ownerView.toolCalls[0]).toMatchObject({ status: browserStatus, resultSummary: summary });
  });

  it("accepts English audit text but rejects secrets and conclusions that are early or cross-execution", async () => {
    const created = await createTask();
    const englishResult = await harness.service.inspectRuntime(created.identity, {
      clientRequestId: "english-purpose",
      purpose: "read runtime state",
      operation: "port_list",
    });
    expect(englishResult).toMatchObject({ status: "success" });
    await expect(harness.service.inspectRuntime(created.identity, {
      clientRequestId: "secret-purpose",
      purpose: "检查 token=must-not-persist 的配置",
      operation: "port_list",
    })).rejects.toMatchObject({ status: 400, code: "repair_audit_secret_forbidden" });
    await expect(harness.service.inspectRuntime(created.identity, {
      clientRequestId: "signed-query-purpose",
      purpose: "检查链接 https://oss.example/object?X-Amz-Signature=must-not-persist",
      operation: "port_list",
    })).rejects.toMatchObject({ status: 400, code: "repair_audit_secret_forbidden" });
    await expect(harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: englishResult.toolCallId,
      evidenceToolCallIds: [englishResult.toolCallId],
      conclusionZh: "The runtime inspection returned a bounded port-list result.",
      nextAction: "Continue with the next relevant control-plane check.",
    })).resolves.toMatchObject({ recorded: true, sourceToolCallId: englishResult.toolCallId });
    expect(await harness.repairRepo.listToolCalls(created.taskId)).toHaveLength(2);

    const pending = await harness.repairRepo.createToolCall({
      callId: "rtc-pending-conclusion-source",
      taskId: created.taskId,
      stepId: created.identity.stepId,
      executionId: created.identity.executionId,
      authorizationScopeDigest: created.config.authorizationScopeDigest,
      clientRequestId: "pending-conclusion-source",
      toolName: "baas_read",
      operation: "port_list",
      request: { operation: "port_list" },
      isWrite: false,
    });
    await expect(harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: pending.call.callId,
      evidenceToolCallIds: [pending.call.callId],
      conclusionZh: "调用尚未完成，不能提前形成结论。",
      nextAction: "等待调用结束后再根据结果记录结论。",
    })).rejects.toMatchObject({ status: 409, code: "repair_conclusion_source_not_terminal" });

    const other = await createTask();
    const otherResult = await harness.service.inspectRuntime(other.identity, {
      clientRequestId: "other-task-evidence",
      purpose: "检查另一个任务的端口监听状态",
      operation: "port_list",
    });
    const claimed = await harness.repairRepo.claimToolCall({
      callId: pending.call.callId,
      executionId: pending.call.executionId,
      authorizationScopeDigest: pending.call.authorizationScopeDigest,
      leaseOwner: "conclusion-test",
      now: harness.now.value,
      leaseExpiresAt: harness.now.value + 60,
    });
    await harness.repairRepo.completeToolCall({
      callId: pending.call.callId,
      executionId: pending.call.executionId,
      authorizationScopeDigest: pending.call.authorizationScopeDigest,
      leaseOwner: claimed!.leaseOwner!,
      status: "succeeded",
      result: { status: "success", operation: "port_list", exitCode: 0, stdout: "", stderr: "" },
    });
    await expect(harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: pending.call.callId,
      evidenceToolCallIds: [pending.call.callId, otherResult.toolCallId],
      conclusionZh: "当前运行状态读取成功，但不能引用其他任务的结果。",
      nextAction: "只使用当前任务和当前步骤内的终态证据继续检查。",
    })).rejects.toMatchObject({ status: 409, code: "invalid_repair_conclusion_evidence" });
  });

  it("rejects a signed loopback URL path before persistence or runtime execution", async () => {
    const created = await createTask();
    const secret = "runtime-signed-query-canary";

    await expect(harness.service.inspectRuntime(created.identity, {
      clientRequestId: "signed-loopback-path",
      purpose: "检查本地服务健康状态并确认是否可以继续诊断",
      operation: "http_get",
      port: 18_789,
      path: `/ready?X-Amz-Signature=${secret}`,
    })).rejects.toMatchObject({
      status: 400,
      code: "repair_tool_request_secret_forbidden",
      message: "Repair 工具请求不能包含凭据或签名链接",
    });
    expect(harness.inspectRuntime).not.toHaveBeenCalled();
    expect(await harness.repairRepo.listToolCalls(created.taskId)).toHaveLength(0);
  });

  it("does not expose a signed query from a historical runtime audit row to the owner browser DTO", async () => {
    const created = await createTask();
    const secret = "historical-runtime-signature-canary";
    const call = await harness.repairRepo.createToolCall({
      callId: "rtc-historical-signed-query",
      taskId: created.taskId,
      stepId: created.identity.stepId,
      executionId: created.identity.executionId,
      clientRequestId: "historical-signed-query",
      toolName: "baas_read",
      operation: "http_get",
      isWrite: false,
      authorizationScopeDigest: created.config.authorizationScopeDigest,
      request: {
        operation: "http_get",
        port: 18_789,
        path: `/ready?X-Amz-Signature=${secret}`,
      },
    });
    const claimed = await harness.repairRepo.claimToolCall({
      callId: call.call.callId,
      executionId: call.call.executionId,
      authorizationScopeDigest: call.call.authorizationScopeDigest,
      leaseOwner: "historical-test",
      now: harness.now.value,
      leaseExpiresAt: harness.now.value + 60,
    });
    await harness.repairRepo.completeToolCall({
      callId: call.call.callId,
      executionId: call.call.executionId,
      authorizationScopeDigest: call.call.authorizationScopeDigest,
      leaseOwner: claimed!.leaseOwner!,
      status: "succeeded",
      result: { status: "success", operation: "http_get", exitCode: 0, stdout: "ok", stderr: "" },
    });

    const ownerView = await harness.service.getTask(ACTOR, created.taskId) as {
      toolCalls: Array<Record<string, unknown>>;
    };
    expect(JSON.stringify(ownerView)).not.toContain(secret);
    expect(ownerView.toolCalls[0]).not.toHaveProperty("safeInvocation");
    expect(ownerView.toolCalls[0].executionTarget).toContain("[REDACTED]");
  });

  it("returns a safe toolCallId when an audited server tool fails", async () => {
    const created = await createTask();
    harness.inspectRuntime.mockRejectedValueOnce(new Error("upstream token=must-not-leak"));
    await expect(harness.service.inspectRuntime(created.identity, {
      clientRequestId: "failed-runtime-inspection",
      purpose: "检查当前容器端口并确认服务是否启动",
      operation: "port_list",
    })).rejects.toMatchObject({
      status: 500,
      code: "repair_tool_failed",
      message: "Repair tool call 执行失败",
      toolCallId: expect.stringMatching(/^rtc-/),
    });
    const [failed] = await harness.repairRepo.listToolCalls(created.taskId);
    expect(failed).toMatchObject({ status: "failed", errorCode: "repair_tool_failed" });
    expect(JSON.stringify(failed)).not.toContain("must-not-leak");
    await expect(harness.service.recordSemanticConclusion(created.identity, {
      sourceToolCallId: failed.callId,
      evidenceToolCallIds: [failed.callId],
      conclusionZh: "The port inspection failed before returning runtime evidence.",
      nextAction: "Continue with another bounded read-only check.",
    })).resolves.toMatchObject({ recorded: true, sourceToolCallId: failed.callId });
  });
});
