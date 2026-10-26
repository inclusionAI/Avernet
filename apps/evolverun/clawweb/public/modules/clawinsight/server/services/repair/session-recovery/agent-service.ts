import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "../common/task-control-plane.js";
import { simulationReadTools, type RecoverySimulationAssessment } from "./simulation.js";
import { assertActionScope, engineActionScope, recoveryActionOperations, validateRecoveryAction } from "./action-scope.js";
import { comparableRequestPayload } from "../../../repositories/repair/agent-tool-call-record.js";
import { createHash, randomUUID } from "node:crypto";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { AisExecutor } from "@avernet/clawevolve/server/contracts/ais-executor";
import type { AisStatusHandler } from "@avernet/clawevolve/server/services/ais/status-handler";
import { BotEngineOperations } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { BotSessionTrajectoryReader as RecoveryTrajectoryReader } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { BotRuntime, BotTarget, BotTargetResolver, ResolvedBotTarget, EngineOperation } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { AgentToolCallRepository } from "../../../repositories/repair/agent-tool-call-repository.js";
import { sameAgentExecutionReport, AgentExecutionService, createAgentExecutionStep, agentExecutionContract } from "../common/agent-execution.js";
import { AgentRuntimeTool } from "../common/agent-tools/runtime-tool.js";
import type { AgentLogTool } from "../common/agent-tools/log-tool.js";
import type { AgentToolContext, AgentRuntimeInspectInput, AgentRuntimeTargetSnapshot, AgentPlanAction } from "../common/agent-tools/contracts.js";
import { redactEvidenceValue, redactPersistableText } from "@avernet/clawweb-shared/server/services/redaction";
import { SessionRecoveryError } from "./errors.js";
import { RecoverySessionReader } from "./session-reader.js";
import { cronRunSessionId } from "./session-identity.js";
import { savedRecoveryHandbook } from "./handbook.js";
import { recoveryResult } from "./result.js";
import type { RecoveryAgentExecutor } from "./task-service.js";
import type { RecoveryActionNotifier, RecoveryExecutionView, RecoveryTermination, SessionRecoveryTaskApi, SessionRecoveryAgentApi } from "./contracts.js";
import type { RecoveryStoredTask, RecoveryTaskStore, RecoveryJudgment, RecoveryPlan } from "./task-contracts.js";

function fail(status: number, message: string): never { throw new SessionRecoveryError(status, "recovery_execution_conflict", message); }
function recoveryToolRequest(body: Record<string, unknown>): Record<string, unknown> {
  return { ...redactEvidenceValue(body) as Record<string, unknown>,
    requestDigest: createHash("sha256").update(comparableRequestPayload(body)).digest("hex") };
}
export function recoveryTargetSnapshot(target: ResolvedBotTarget): AgentRuntimeTargetSnapshot {
  const identity = [target.environment, target.ownerId, target.botId, target.provider, target.bindingId, target.deviceId, target.sandboxId ?? null, target.activeEngine ?? null];
  if (target.deviceUuid || target.runtimeIdentity) identity.push(target.deviceUuid ?? null, target.providerDeviceId ?? null, JSON.stringify(target.runtimeIdentity ?? null));
  const fingerprint = createHash("sha256").update(JSON.stringify(identity)).digest("hex");
  return { version: 1, fingerprint, reason: "task_created", target: { ...target, environment: target.environment as "pre" | "prod",
    botType: target.botType === "service" ? "service" : "personal", botStatus: null, bindingStatus: null, observedAt: new Date().toISOString(), source: "clawweb_runtime_catalog" } };
}

/** Business adapter over existing AIS dispatch, workload tickets, tool ledger and Bot runtime. */
export class SessionRecoveryAgentService implements RecoveryAgentExecutor, SessionRecoveryAgentApi {
  private readonly executions: AgentExecutionService;
  private readonly reader: RecoverySessionReader;
  private readonly runtimeTool: AgentRuntimeTool;
  private readonly now: () => number;
  constructor(private readonly deps: {
    controlPlaneEnvironment: AgentControlPlaneEnvironment;
    tasks: RecoveryTaskStore; repo: EvolveRepository; audit: AgentToolCallRepository;
    ais: AisExecutor; runtime: BotRuntime; targets: BotTargetResolver; trajectory?: RecoveryTrajectoryReader; logs?: AgentLogTool; runtimeTool?: AgentRuntimeTool;
    business: () => SessionRecoveryTaskApi; publicBaseUrl: string; snapshotId: number;
    packageId?: string; nasRoots?: readonly string[]; leaseSeconds: number; launchGraceSeconds: number; observationMs: number;
    actionNotifier?: RecoveryActionNotifier;
    now?: () => number;
  }) {
    this.now = deps.now ?? Date.now;
    this.executions = new AgentExecutionService({ leaseSeconds: deps.leaseSeconds, nowSeconds: () => Math.floor(this.now() / 1000) });
    this.reader = new RecoverySessionReader(deps.runtime); this.runtimeTool = deps.runtimeTool ?? new AgentRuntimeTool(deps.runtime);
  }
  async start(input: RecoveryStoredTask): Promise<void> {
    let task = await this.required(input.taskId);
    if (task.status !== "running" || resolveTaskControlPlaneEnvironment(task) !== this.deps.controlPlaneEnvironment) return;
    // Only the lifecycle monitor may retire an expired execution; never overlap live jobs.
    if (task.execution && task.execution.state !== "ended") return;
    if ((task.executionRetry?.retryAt ?? 0) > this.now()) return;
    if (task.executionRetry?.retryAt) {
      if (task.executionRetry.executionId !== task.execution?.executionId) return;
      if (task.execution?.jobId) {
        try {
          const remote = await this.deps.ais.getJobStatusDetail(task.execution.jobId);
          if (remote.status === "running" || remote.rawStatus === "notfound") return;
        } catch { console.error("[session-recovery] previous AIS job state unavailable; retry remains pending", task.taskId); return; }
      }
      if (!await this.settleInterruptedTools(task)) return;
    }
    if ((!task.simulation && !task.runtimeTarget) || !task.current || !task.authorizationScopeDigest) fail(503, "目标快照未配置");
    if (!Number.isSafeInteger(this.deps.snapshotId) || this.deps.snapshotId <= 0) fail(503, "自愈 AIS Base Snapshot 未配置");
    const executionNo = (task.executionNo ?? 0) + 1;
    const issued = this.executions.issue(`exec-${randomUUID()}`, task.current.stepId, this.deps.launchGraceSeconds);
    const identity = { taskId: task.taskId, executionId: issued.execution.executionId, executionNo, handler: "session_recovery" };
    const contract = agentExecutionContract(identity);
    const reserved = await this.deps.tasks.update(task.taskId, task.version, value => ({ ...value, version: value.version + 1,
      execution: issued.execution, executionNo, aisExecution: contract,
      ...(value.executionRetry ? { executionRetry: { ...value.executionRetry, retryAt: 0 } } : {}),
      timeline: [...value.timeline, this.event("execution", "启动自愈 Agent", { execution_id: issued.execution.executionId })] }));
    if (!reserved) return; // Another dispatcher/decision owns the current state; never launch a second job.
    task = reserved;
    try { await createAgentExecutionStep(this.deps.repo, identity); }
    catch (error) { await this.deps.business().executionFailure(task.taskId, "自愈执行登记失败", issued.execution.executionId); throw error; }
    const beforeDispatch = await this.required(task.taskId);
    if (beforeDispatch.status === "canceled") {
      await this.stopTerminatedExecution(contract.stepId, null); return;
    }
    const envelope = { taskType: "session_recovery", taskId: task.taskId, stepId: contract.stepId, attempt: executionNo,
      input: { taskId: task.taskId, executionId: issued.execution.executionId,
        ...(task.simulation ? { simulation: true } : {}),
        toolsBaseUrl: `${this.deps.publicBaseUrl}/api/insight/v1/internal/monitoring/session-recoveries/${encodeURIComponent(task.taskId)}/executions/${issued.execution.executionId}`,
        credentials: { executionTicket: issued.ticket } },
      runtime: { clawwebUrl: this.deps.publicBaseUrl, package: { packageId: this.deps.packageId ?? "clawevolve-session-recovery" } } };
    let jobId: string;
    try { jobId = await this.deps.ais.execute(task.request.target.entity_id, { "${clawevolve_params}": JSON.stringify(envelope) }, this.deps.snapshotId); }
    catch (error) {
      const reason = redactPersistableText(error instanceof Error ? error.message : "未取得 AIS 派发回执", 1500);
      await this.deps.business().executionFailure(task.taskId, `自愈 Agent 派发失败：${reason}`, issued.execution.executionId, true);
      await this.deps.repo.updateStepStatus(contract.stepId, { status: "failed", errorCode: "AIS_DISPATCH_FAILED", errorMessage: reason, retryable: true });
      return;
    }
    await this.deps.repo.markExternalDispatched(contract.stepId, jobId, { executionId: issued.execution.executionId, jobId });
    const latest = await this.required(task.taskId);
    if (latest.status === "canceled") { await this.stopTerminatedExecution(contract.stepId, jobId); return; }
    if (latest.execution?.executionId !== issued.execution.executionId || latest.execution.state === "ended") {
      await this.deps.ais.stopExecution(jobId); return;
    }
    const saved = await this.save(latest, value => ({ ...value, execution: { ...value.execution!, jobId } }), issued.execution.executionId);
    if (saved.status === "canceled") await this.stopTerminatedExecution(contract.stepId, jobId);
    else if (saved.execution?.executionId !== issued.execution.executionId || saved.execution.state === "ended") await this.deps.ais.stopExecution(jobId);
  }
  async release(input: RecoveryStoredTask, onlyWaitingOwner = false): Promise<void> {
    const task = await this.required(input.taskId);
    const owner = resolveTaskControlPlaneEnvironment(task);
    if (owner && owner !== this.deps.controlPlaneEnvironment) return;
    if (!task.execution || task.execution.state === "ended" || (input.execution && input.execution.executionId !== task.execution.executionId)) return;
    await this.save(task, value => {
      if (onlyWaitingOwner && value.status !== "waiting_owner") fail(409, "Owner 已确认，请继续当前任务");
      return { ...value, execution: this.executions.end(value.execution!),
        timeline: [...value.timeline, this.event("execution", "本次执行结束，已保存任务上下文", { execution_id: value.execution!.executionId })] };
    }, task.execution.executionId);
    // The wrapper sees ended on its next control poll and exits for Base to archive.
    // Forced cancellation is reserved for expired leases / the outer hard timeout.
  }
  async listExecutions(taskId: string): Promise<RecoveryExecutionView[]> {
    const steps = await this.deps.repo.listSteps(taskId);
    return steps.filter(step => step.step_type === "agent_execution").flatMap(step => {
      const executionId = JSON.parse(step.ext_json ?? "{}").executionId;
      if (typeof executionId !== "string") return [];
      const cancellation = JSON.parse(step.bot_response_json ?? "{}").evolve_cancel;
      return [{ executionId, attempt: -step.step_no, status: step.status, jobId: step.bot_run_id,
        jobUrl: step.bot_run_id ? this.deps.ais.jobUrl?.(step.bot_run_id) ?? null : null,
        ...(cancellation?.status === "remote_stopped" || cancellation?.status === "remote_stop_failed"
          ? { termination: { status: cancellation.status, aisJobId: step.bot_run_id } } : {}) }];
    }).sort((left, right) => left.attempt - right.attempt);
  }
  async terminate(input: RecoveryStoredTask): Promise<RecoveryTermination> {
    let task = await this.required(input.taskId);
    if (task.status !== "canceled" || resolveTaskControlPlaneEnvironment(task) !== this.deps.controlPlaneEnvironment) fail(409, "任务尚未停止或不属于当前环境");
    if (task.execution && task.execution.state !== "ended") {
      task = await this.save(task, value => ({ ...value, execution: this.executions.end(value.execution!) }), task.execution.executionId);
    }
    const stepId = task.aisExecution?.stepId;
    const step = stepId ? await this.deps.repo.findStep(stepId) : null;
    const jobId = task.execution?.jobId ?? step?.bot_run_id ?? null;
    const termination = stepId ? await this.stopTerminatedExecution(stepId, jobId) : { status: "stop_pending" as const, aisJobId: null };
    // Retain interrupted writes as unknown; cancellation never claims to roll back a dispatched operation.
    for (;;) {
      const calls = await this.deps.audit.listActiveToolCalls(task.taskId, 500);
      if (!calls.length) break;
      for (const call of calls) {
        const result = { dispatched: call.status === "executing", recorded_at_ms: this.now(),
          error: { code: "RECOVERY_TERMINATED_BY_USER", message: "用户停止自愈任务，已发出的操作需核对实际结果" } };
        try {
          if (call.status === "pending") await this.deps.audit.cancelPendingToolCall(call.callId, call.executionId, call.authorizationScopeDigest, result);
          else await this.deps.audit.completeToolCall({ callId: call.callId, executionId: call.executionId,
            authorizationScopeDigest: call.authorizationScopeDigest, leaseOwner: call.leaseOwner ?? undefined,
            status: call.isWrite ? "unknown" : "failed", result });
        } catch (error) {
          const latest = await this.deps.audit.findToolCall(call.callId);
          if (!latest || ["pending", "executing"].includes(latest.status)) throw error;
        }
      }
    }
    return termination;
  }
  private async stopTerminatedExecution(stepId: string, jobId: string | null): Promise<RecoveryTermination> {
    const step = await this.deps.repo.findStep(stepId);
    if (step && !["succeeded", "failed", "canceled"].includes(step.status)) {
      await this.deps.repo.updateStepStatus(stepId, { status: "canceled", retryable: false,
        errorCode: "RECOVERY_TERMINATED_BY_USER", errorMessage: "用户停止本次自愈任务",
        expectedStep: { status: step.status, output_json: step.output_json } });
    }
    if (!jobId) return { status: "stop_pending", aisJobId: null };
    let status: "remote_stopped" | "remote_stop_failed" = "remote_stopped";
    try { await this.deps.ais.stopExecution(jobId); }
    catch {
      status = "remote_stop_failed";
      try {
        const remote = await this.deps.ais.getJobStatusDetail(jobId);
        if (remote.status !== "running" && remote.rawStatus !== "notfound") status = "remote_stopped";
      } catch { /* Keep the failed stop visible when remote state is unavailable. */ }
    }
    await this.deps.repo.recordCancellationAttempt(stepId, { status,
      ...(status === "remote_stop_failed" ? { error: "AIS stopExecution failed" } : {}) });
    return { status, aisJobId: jobId };
  }
  private async settleInterruptedTools(task: RecoveryStoredTask): Promise<boolean> {
    // An interrupted HTTP request may still finish. Wait for its existing lease before
    // marking an abandoned write unknown; the next Agent must reconcile, never replay it.
    const calls = await this.deps.audit.listActiveToolCalls(task.taskId, 500);
    for (const call of calls) {
      if (call.executionId !== task.execution?.executionId) continue;
      if (call.status === "executing" && (call.leaseExpiresAt ?? Infinity) > Math.floor(this.now() / 1000)) return false;
      const result = { dispatched: call.status === "executing", recorded_at_ms: this.now(),
        error: { code: "AGENT_EXECUTION_INTERRUPTED", message: "原执行已中断，写入需读取现场后核实" } };
      try {
        if (call.status === "pending") await this.deps.audit.cancelPendingToolCall(call.callId, call.executionId, call.authorizationScopeDigest, result);
        else await this.deps.audit.completeToolCall({ callId: call.callId, executionId: call.executionId,
          authorizationScopeDigest: call.authorizationScopeDigest, leaseOwner: call.leaseOwner ?? undefined,
          status: call.isWrite ? "unknown" : "failed", result });
      } catch (error) {
        // A late receipt or another reconciler may have finalized the immutable call.
        const latest = await this.deps.audit.findToolCall(call.callId);
        if (!latest || ["pending", "executing"].includes(latest.status)) throw error;
      }
    }
    return calls.length < 500; // Continue settling a larger ledger on the next maintenance pass.
  }
  async verify(taskId: string, executionId: string, ticket: string, control = false): Promise<RecoveryStoredTask> {
    const task = await this.required(taskId);
    if (!control && task.status === "canceled") fail(409, "任务已停止");
    if (!task.execution || task.execution.executionId !== executionId) fail(401, "执行身份已失效");
    try {
      if (control && task.execution.state === "ended") {
        // Control-only read permits graceful exit; no mutation/tool routes use it.
        this.executions.assertActive({ ...task.execution, state: "running", invalidatedAt: null, leaseExpiresAt: Number.MAX_SAFE_INTEGER }, ticket);
      } else this.executions.assertActive(task.execution, ticket);
    } catch { fail(401, "执行票据已失效或作用域不匹配"); }
    return task;
  }
  async bootstrap(task: RecoveryStoredTask) {
    const { pending_results: _pending, execution: rawExecution, aisExecution: _ais, interventionStatus: _intervention, interventionAuditComplete: _auditCoverage, ...business } = task;
    const { ticketDigest: _digest, ...execution } = task.status === "canceled" ? this.executions.end(rawExecution!) : rawExecution!;
    const engine = task.runtimeTarget?.target.activeEngine ?? { OC: "openclaw", CC: "claude_code", TE: "teclaw" }[task.request.target.engine] as "openclaw" | "claude_code" | "teclaw";
    return { taskId: task.taskId, stepId: task.current!.stepId, executionId: execution.executionId, deliveryKey: this.latestDeliveryKey(task), task: { ...business, execution,
        timeline: business.timeline.filter(event => event.payload.channel !== "group_webhook").slice(-60).map(event => ({ ...event, payload: event.kind === "operation" ? { tool_call_id: event.payload.tool_call_id, status: event.payload.status, evidence_id: event.payload.evidence_id } : event.payload })),
        evidence: business.evidence.map(item => ({ ...item, excerpt: item.excerpt.slice(0, 1500) })),
      }, handbook: savedRecoveryHandbook(task),
      timings: { heartbeatSeconds: 20, decisionPollSeconds: 20 }, planProtocol: {
        allowedOperations: recoveryActionOperations,
        operationScope: "所有 Agent 执行动作需在方案内提供参考 Engine 请求或结构化 operationScope: { operation, target? }。operation 必须取自 allowedOperations，不自行构造动作名；定时任务启用和禁用使用 cron.update，target 填定时任务ID，文件操作的 target 填绝对路径。合法动作名不代表目标支持该操作，实际接口与工具以 tools.engine 和 supportedActionTypes 为准。参数内容可调整，主要动作和对象不可更换。MCP 和用户 Skill 的全部变更交给 Owner，写入 owner_cooperation；无需 Agent 操作时 actions 用空数组。Owner 完成配合后再继续验证和重试。Cron 必须使用声明的接口工具。Shell 仅承载其余获批环境动作，不得绕过接口或变更 MCP／Skill；服务不解析完整 Shell 业务语义。",
        unknownWrite: "未知写入先读取现场，用 reconcile_write 提交 applied / not_applied 和后续读取证据。原调用记录保持不可变；核实后可继续方案，不会自动重放。",
      }, tools: { logs_search: !!this.deps.logs, knowledge_read: true,
        runtime_inspect: true, session_read: task.request.target.engine !== "TE", supportedActionTypes: task.request.target.engine === "TE" ? ["engine_operation"] : ["container_command", "engine_operation"], engine: BotEngineOperations.capabilities(engine), trajectory_read: !!this.deps.trajectory && (!task.simulation || this.deps.trajectory.supportsHistoricalBoundary === true), nasSessionRead: task.request.target.engine !== "TE", nasRoots: this.deps.nasRoots ?? [], ocb_operation: false, apply_action: !task.simulation && task.plan?.approvedAt != null, retry_message: !task.simulation, reconcile_write: !task.simulation } };
  }
  async heartbeat(task: RecoveryStoredTask, sessionId?: string) {
    if (task.status === "canceled" || task.execution?.state === "ended") return { state: "ended" };
    if (sessionId !== undefined && (typeof sessionId !== "string" || !/^[A-Za-z0-9_.:-]{1,255}$/.test(sessionId))) fail(422, "观察会话 ID 无效");
    const saved = await this.save(task, value => { if (value.execution?.executionId !== task.execution!.executionId) fail(401, "旧执行不能更新新执行心跳"); return { ...value, execution: this.executions.heartbeat(value.execution!, sessionId) }; });
    return { state: saved.execution!.state, leaseExpiresAt: saved.execution!.leaseExpiresAt };
  }
  async sessionRead(task: RecoveryStoredTask, body: Record<string, unknown>) {
    if (task.request.target.engine === "TE") throw new SessionRecoveryError(422, "unsupported_session_read", "TE 通过 trajectory_read 读取 AIVision，不提供会话 API 读取");
    return this.tool(task, body, "session_read", false, async () => {
      const output = body.nas_evidence ? this.nasEvidence(task, body.nas_evidence) : await this.reader.read({ target: task.simulation ? await this.historicalSessionTarget(task) : task.runtimeTarget!.target, sessionKey: task.request.session_key,
        ...(task.simulation ? { cutoff: { messageId: task.simulation.cutoffNode.messageId!, atMs: task.simulation.cutoffAtMs } } : {}),
        deliveryKey: this.latestDeliveryKey(task), expectedSessionId: this.originalSessionId(task),
        ...(body.from_line === undefined ? {} : { fromLine: body.from_line as number }), ...(body.offset === undefined ? {} : { offset: body.offset as number }), ...(body.limit === undefined ? {} : { limit: body.limit as number }) });
      if (!task.simulation && task.requiresFreshObservation) {
        const latest = await this.required(task.taskId);
        await this.save(latest, value => ({ ...value, requiresFreshObservation: false, observationDeadlineAt: this.now() + this.deps.observationMs }), task.execution!.executionId);
      }
      return { ...output, delivery_key: this.latestDeliveryKey(task) };
    });
  }
  private nasEvidence(task: RecoveryStoredTask, value: unknown): Record<string, unknown> {
    const evidence = value as Record<string, unknown>, target = task.runtimeTarget?.target ?? {
      ...this.logicalTarget(task), entityId: task.request.target.entity_id, entityType: "staff",
      activeEngine: { OC: "openclaw", CC: "claude_code", TE: "teclaw" }[task.request.target.engine],
    };
    const storage = [target.environment, target.entityType || "staff", target.entityId || target.ownerId, target.activeEngine, target.botId].join("_");
    if (task.request.target.engine === "TE" || !evidence || typeof evidence.source !== "string" || !Array.isArray(evidence.messages)
      || evidence.messages.length > 500 || JSON.stringify(evidence).length > 100000 || evidence.source.includes("..")
      || !(this.deps.nasRoots ?? []).some(root => String(evidence.source).startsWith(`${root}/${target.environment}/${storage}/`))) fail(422, "NAS 证据不属于本任务的只读会话范围");
    if (task.simulation) {
      if (task.request.target.runtime_identity) fail(422, "服务实例 NAS 读取需要原始权威定位，不能推断个人目录");
      const boundary = evidence.historical_boundary as Record<string, unknown> | undefined;
      if (!boundary || boundary.message_id !== task.simulation.cutoffNode.messageId || boundary.cutoff_at_ms !== task.simulation.cutoffAtMs
        || !Number.isSafeInteger(boundary.end_line) || Number(boundary.end_line) < 1
        || evidence.next_line != null && (!Number.isSafeInteger(evidence.next_line) || Number(evidence.next_line) > Number(boundary.end_line))
        || evidence.messages.some((item: { line_no?: number; record?: { timestamp?: unknown } }) => !Number.isSafeInteger(item.line_no)
          || Number(item.line_no) < 1 || Number(item.line_no) > Number(boundary.end_line)
          || this.recordAfterCutoff(item.record?.timestamp ?? (item.record as { message?: { timestamp?: unknown } })?.message?.timestamp, task.simulation!.cutoffAtMs))
        || Array.isArray(evidence.failure_context) && evidence.failure_context.length) fail(422, "NAS 证据超出任务历史边界");
    }
    const originalId = this.originalSessionId(task);
    if (originalId && originalId !== evidence.session_id) fail(409, "原会话已变化");
    const key = this.latestDeliveryKey(task);
    if (evidence.delivery_key !== key) fail(409, "NAS 读取不对应当前投递，请刷新任务后重读");
    const marker = `[会话自愈：${key}]\n`;
    const records = [...(Array.isArray(evidence.failure_context) ? evidence.failure_context : []), ...evidence.messages];
    const matches = records.flatMap(item => {
      const record = item?.record, message = record?.message;
      if (!record || (message?.role ?? record.type) !== "user") return [];
      const content = typeof message?.content === "string" ? message.content : Array.isArray(message?.content)
        ? message.content.filter((part: { type?: string; text?: string }) => part?.type === "text" && typeof part.text === "string").map((part: { text: string }) => part.text).join("\n") : "";
      return content.includes(marker) ? [{ message_id: record.id ?? record.uuid ?? null, line_no: item.line_no, text: content }] : [];
    });
    return redactEvidenceValue({ ...evidence, delivery: matches[0] ?? null, delivery_confirmed: matches.length > 0, delivery_matches: matches }) as Record<string, unknown>;
  }
  async trajectoryRead(task: RecoveryStoredTask, body: Record<string, unknown>) {
    if (!this.deps.trajectory) fail(503, "轨迹读取未配置");
    if (task.simulation && !this.deps.trajectory.supportsHistoricalBoundary) fail(503, "轨迹读取器未声明历史边界能力");
    const offset = body.offset ?? 0, limit = body.limit ?? 20;
    if (!Number.isSafeInteger(offset) || Number(offset) < 0 || !Number.isSafeInteger(limit) || Number(limit) < 1 || Number(limit) > 100
      || body.trace_id !== undefined && (typeof body.trace_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(body.trace_id))
      || body.observation_id !== undefined && (typeof body.observation_id !== "string" || !/^[A-Za-z0-9_.:-]{1,255}$/.test(body.observation_id))
      || body.field !== undefined && (typeof body.field !== "string" || !["input", "output"].includes(body.field) || !body.trace_id)
      || body.observation_id !== undefined && !body.field
      || body.content_offset !== undefined && (!Number.isSafeInteger(body.content_offset) || Number(body.content_offset) < 0 || !body.field)
      || body.content_limit !== undefined && (!Number.isSafeInteger(body.content_limit) || Number(body.content_limit) < 1 || Number(body.content_limit) > 12000 || !body.field)) fail(422, "轨迹范围无效");
    return this.tool(task, body, "trajectory_read", false, async () => {
      const deliveryKey = this.latestDeliveryKey(task);
      const sent = [...task.timeline].reverse().find(event => event.kind === "operation"
        && (event.payload.result as Record<string, unknown>)?.delivery_key === deliveryKey
        && typeof (event.payload.result as Record<string, unknown>)?.initial_message === "boolean");
      const call = sent ? await this.deps.audit.findToolCall(String(sent.payload.tool_call_id)) : null;
      const receipt = call?.taskId === task.taskId && call.toolName === "retry_message" ? call.result as Record<string, unknown> : undefined;
      const startedAt = receipt?.dry_run !== true && typeof receipt?.started_at_ms === "number" ? receipt.started_at_ms : undefined;
      const result = await this.deps.trajectory!.read({ target: task.runtimeTarget?.target ?? this.logicalTarget(task),
        sessionKey: task.request.session_key, offset: Number(offset), limit: Number(limit), traceId: body.trace_id as string | undefined, maxBytes: 128 * 1024,
        observationId: body.observation_id as string | undefined, field: body.field as "input" | "output" | undefined,
        contentOffset: body.content_offset as number | undefined, contentLimit: body.content_limit as number | undefined,
        ...(task.simulation ? { until: { atMs: task.simulation.cutoffAtMs, traceId: task.simulation.cutoffNode.traceId,
          observationId: task.simulation.cutoffNode.observationId, sessionId: this.originalSessionId(task) } } : {}),
        ...(!task.simulation && startedAt !== undefined ? { messageMarker: `[会话自愈：${deliveryKey}]\n`, notBeforeMs: startedAt } : {}) });
      if (Buffer.byteLength(JSON.stringify(result), "utf8") > 128 * 1024) {
        throw new SessionRecoveryError(422, "recovery_trajectory_page_too_large", "轨迹页过大，请缩小读取范围；正文使用 field 和 content_offset 分段读取");
      }
      if (!task.simulation && task.requiresFreshObservation) {
        const latest = await this.required(task.taskId);
        await this.save(latest, value => ({ ...value, requiresFreshObservation: false, observationDeadlineAt: this.now() + this.deps.observationMs }), task.execution!.executionId);
      }
      return { ...result, delivery_key: this.latestDeliveryKey(task) };
    });
  }
  async inspect(task: RecoveryStoredTask, body: Record<string, unknown>) {
    if (body.operation === "trajectory_read") {
      if (body.sessionKey !== task.request.session_key) fail(403, "轨迹必须属于原会话");
      return this.trajectoryRead(task, { clientRequestId: body.clientRequestId, purpose: body.purpose,
        offset: body.offset, limit: body.limit, trace_id: body.traceId });
    }
    if (body.operation === "shell_exec") fail(403, "调查阶段只开放结构化读取；环境命令经方案操作入口执行");
    if (task.simulation) {
      const candidate = JSON.stringify(body.operation === "engine_read" ? body.request : body.path ?? "");
      if (body.operation === "http_get" || /(?:\.openclaw\/agents\/[^/]+\/sessions|\.claude\/projects|\/(?:sessions|trajectories)(?:[\/?."\s]|$))/i.test(candidate)) {
        fail(403, "历史会话和轨迹须通过专用受控工具读取，环境接口使用已声明的结构化只读操作");
      }
    }
    return this.tool(task, body, "runtime_inspect", false, async () => {
      const context = this.context(task, task.simulation ? await this.currentTarget(task) : task.runtimeTarget!.target);
      const result = await this.runtimeTool.inspect(context, body as AgentRuntimeInspectInput, { allowSessionHistory: !task.simulation });
      return task.simulation ? { ...result, temporal_context: "current_environment", observed_at_ms: this.now() } : result;
    });
  }
  async logs(task: RecoveryStoredTask, body: Record<string, unknown>) {
    if (!this.deps.logs) fail(503, "日志工具未配置");
    const end = task.simulation?.cutoffAtMs ?? this.now();
    const context = { taskId: task.taskId, target: this.logicalTarget(task),
      issue: { traceId: null, relatedTaskId: null, errorText: task.request.diagnosis,
        timeRange: { from: Math.max(1, Math.floor(end / 1000) - 86400), to: Math.floor(end / 1000) } },
      ...(task.simulation ? { notAfterMs: task.simulation.cutoffAtMs } : {}) };
    return this.tool(task, body, "logs_search", false, () => this.deps.logs!.search(context, body));
  }
  async apply(task: RecoveryStoredTask, body: Record<string, unknown>) {
    this.requireLiveTask(task);
    if (task.status !== "running" || task.phase !== "apply" || !task.plan?.approvedAt || task.level === "debug") fail(403, "环境修改需要有效的完整方案授权");
    const action = task.plan.actions.find(item => item.actionId === body.actionId);
    if (!action) fail(422, "操作不属于获批方案");
    validateRecoveryAction(action);
    const command = body.command;
    const engineOperation = body.engineOperation;
    if (command !== undefined && engineOperation !== undefined) fail(422, "一次调用只能提交一种实际执行操作");
    const actualCommand = engineOperation === undefined ? (command !== undefined ? command : action.command) : undefined;
    const actualEngine = command === undefined ? (engineOperation !== undefined ? engineOperation : action.engineOperation) : undefined;
    if (actualCommand === undefined && actualEngine === undefined) fail(422, "请提交落实获批动作的实际命令或 Engine 请求");
    let actualAction: AgentPlanAction;
    let actualRequest: Record<string, unknown>;
    if (actualEngine !== undefined) {
      if (!actualEngine || typeof actualEngine !== "object" || Array.isArray(actualEngine)
        || Object.keys(actualEngine).some(key => !["method", "path", "body", "upload", "query"].includes(key))) fail(422, "Engine 请求只能包含 method、path、body、upload 或受支持的 query");
      const request = actualEngine as EngineOperation;
      BotEngineOperations.validate(task.runtimeTarget!.target.activeEngine, request, true);
      assertActionScope(action, request);
      actualAction = { ...action, type: "engine_operation", command: undefined, operation: undefined, engineOperation: request };
      actualRequest = { ...body, engineOperation: request };
    } else {
      if (task.request.target.engine === "TE") fail(422, "TE 不提供 Shell，请使用声明的 Engine 接口");
      if (typeof actualCommand !== "string" || !actualCommand.trim() || actualCommand.length > 16384 || actualCommand.includes("\0")) fail(422, "命令格式无效");
      const shellAction = { ...action, type: "container_command" as const, operationScope: action.operationScope ?? (action.engineOperation ? engineActionScope(action.engineOperation) : undefined), engineOperation: undefined, operation: undefined, command: actualCommand };
      validateRecoveryAction(shellAction);
      actualAction = shellAction;
      actualRequest = { ...body, command: actualCommand };
    }
    const current = await this.deps.targets.resolve({ environment: task.request.target.env, ownerId: task.request.target.entity_id, botId: task.request.target.bot_id, ...(task.request.target.runtime_identity ? { runtimeIdentity: task.request.target.runtime_identity } : {}) });
    if (recoveryTargetSnapshot(current).fingerprint !== task.runtimeTarget!.fingerprint) fail(409, "目标现场已变化，需上报方案不再适用");
    // Approval identifies the business action; actual transport details are audited, not equality-fenced.
    return this.tool(task, actualRequest, "apply_action", true, markDispatched => { markDispatched(); return this.runtimeTool.applyApprovedAction(this.context(task), actualAction); });
  }

  async retryMessage(task: RecoveryStoredTask, body: Record<string, unknown>) {
    this.requireLiveTask(task);
    const prior = task.timeline.find(event => event.kind === "operation" && (event.payload.request as Record<string, unknown>)?.clientRequestId === body.clientRequestId
      && (event.payload.result as Record<string, unknown>)?.initial_message === true);
    const initial = task.phase === "assess" || !!prior;
    if (!prior && (task.status !== "running" || (initial ? !!task.plan : task.phase !== "apply" || !task.plan?.approvedAt))) fail(409, "消息只能在初始判断或获批方案执行阶段由 Agent 决定");
    if (typeof body.message !== "string" || !body.message.trim() || body.message.length > 8000) fail(422, "重试消息格式无效");
    const key = initial ? task.request.delivery_key : `retry-${createHash("sha256").update(`${task.taskId}:${String(body.clientRequestId)}`).digest("hex").slice(0, 48)}`;
    return this.tool(task, body, "retry_message", task.level !== "debug", async markDispatched => {
      if (task.level === "debug") return { status: "not_intervened", dry_run: true, delivery_key: key, message: body.message, initial_message: initial };
      const currentTarget = await this.deps.targets.resolve({ environment: task.request.target.env, ownerId: task.request.target.entity_id, botId: task.request.target.bot_id, ...(task.request.target.runtime_identity ? { runtimeIdentity: task.request.target.runtime_identity } : {}) });
      if (recoveryTargetSnapshot(currentTarget).fingerprint !== task.runtimeTarget!.fingerprint) fail(409, "目标运行位置已变化，不能投递旧方案的重试消息");
      const originalId = this.originalSessionId(task);
      let deliverySessionKey = task.request.session_key;
      if (originalId && task.request.target.engine !== "TE") {
        const original = await this.reader.read({ target: task.runtimeTarget!.target, sessionKey: task.request.session_key,
          deliveryKey: this.latestDeliveryKey(task), expectedSessionId: originalId, requireIndex: true, limit: 1 });
        if (typeof original.session_key === "string") deliverySessionKey = original.session_key;
      }
      const marker = `[会话自愈：${key}]`;
      const startedAt = this.now();
      const message = `${marker}\n${body.message}`;
      markDispatched();
      const receipt = await this.deps.runtime.sendMessage({ target: task.runtimeTarget!.target, expectedEngine: { OC: "openclaw", CC: "claude_code", TE: "teclaw" }[task.request.target.engine] as "openclaw" | "claude_code" | "teclaw", sessionKey: deliverySessionKey,
        deliveryKey: key, messageMarker: marker, message }).catch(() => ({ status: "unknown" as const }));
      return { ...receipt, session_key: deliverySessionKey, delivery_key: key, message, initial_message: initial, started_at_ms: startedAt, finished_at_ms: this.now() };
    });
  }
  async toolGet(task: RecoveryStoredTask, id: string) {
    const call = await this.deps.audit.findToolCall(id);
    if (!call || call.taskId !== task.taskId) fail(404, "操作记录不存在");
    return { ...call, toolCallId: call.callId };
  }
  async reconcileWrite(task: RecoveryStoredTask, body: Record<string, unknown>) {
    this.requireLiveTask(task);
    const source = await this.toolGet(task, String(body.sourceToolCallId));
    if (!source.isWrite || source.status !== "unknown" || source.stepId !== task.current!.stepId) fail(409, "只能核实当前步骤的未知写入结果");
    if (!["applied", "not_applied"].includes(String(body.outcome)) || typeof body.summary !== "string" || !body.summary.trim()
      || !Array.isArray(body.evidenceToolCallIds) || !body.evidenceToolCallIds.length) fail(422, "核实需要明确结果、依据和后续读取证据");
    for (const id of body.evidenceToolCallIds) {
      const evidence = await this.toolGet(task, String(id));
      if (evidence.isWrite || evidence.status !== "succeeded" || evidence.id <= source.id
        || evidence.executionId !== task.execution!.executionId
        || !["session_read", "trajectory_read", "runtime_inspect", "logs_search"].includes(evidence.toolName)) fail(422, "必须引用本次执行中、未知操作之后的成功读取证据");
    }
    return this.tool(task, body, "reconcile_write", false, async () => ({ sourceToolCallId: source.callId,
      outcome: body.outcome, summary: body.summary, evidenceToolCallIds: body.evidenceToolCallIds }));
  }
  async conclusion(task: RecoveryStoredTask, body: Record<string, unknown>) {
    const source = await this.toolGet(task, String(body.sourceToolCallId));
    if (source.executionId !== task.execution!.executionId || !["succeeded", "failed", "unknown"].includes(source.status)) fail(409, "只能总结当前执行的终态来源");
    if (typeof body.conclusionZh !== "string" || !body.conclusionZh.trim() || body.conclusionZh.length > 1000
      || !Array.isArray(body.evidenceToolCallIds) || !body.evidenceToolCallIds.includes(source.callId)) fail(422, "结论需要来源、证据与文字判断");
    for (const id of body.evidenceToolCallIds) await this.toolGet(task, String(id));
    const current = await this.required(task.taskId);
    const existing = current.timeline.find(event => event.kind === "observation" && event.payload.sourceToolCallId === source.callId);
    if (existing) { if (JSON.stringify(existing.payload) !== JSON.stringify(body)) fail(409, "结论不能被不同内容覆盖"); return { ok: true }; }
    await this.save(current, value => ({ ...value, timeline: [...value.timeline, { event_id: randomUUID(), at_ms: this.now(), kind: "observation", actor: "agent", summary: body.conclusionZh as string, payload: body }] }));
    return { ok: true };
  }
  async report(task: RecoveryStoredTask, body: Record<string, unknown>) {
    if (body.phase !== task.phase) fail(409, "回报不对应当前阶段，请重新读取任务");
    if (task.simulation && !["assessment", "plan", "blocked", "release"].includes(String(body.kind))) fail(403, "仿真只接受拟议判断和方案，不接受执行或效果回报");
    if (body.kind === "release") {
      await this.release(task, true);
    } else if (body.kind === "assessment") {
      if (task.simulation) await this.deps.business().assessSimulation(task.taskId, task.version,
        { decision: body.decision, summary: body.summary, evidence_ids: body.evidence_ids, ...(body.knowledge === undefined ? {} : { knowledge: body.knowledge }), ...(body.message === undefined ? {} : { message: body.message }) } as RecoverySimulationAssessment);
      else await this.deps.business().assess(task.taskId, task.version, { decision: body.decision as "message" | "environment" | "observe", summary: body.summary as string, evidence_ids: body.evidence_ids as string[] });
    } else if (body.kind === "observation") {
      const judgment = body.judgment as RecoveryJudgment;
      if (!judgment || !["confirmed", "unconfirmed"].includes(judgment.delivery) || !["acted", "replied", "none", "unknown"].includes(judgment.response)
        || !["recovered", "not_recovered", "unknown"].includes(judgment.effect) || typeof judgment.summary !== "string" || !Array.isArray(judgment.evidence_ids)) fail(422, "效果回报格式无效");
      if (judgment.delivery === "confirmed" && !task.timeline.some(event => event.kind === "operation" && (event.payload.result as Record<string, unknown>)?.delivery_confirmed === true && (event.payload.result as Record<string, unknown>)?.delivery_key === this.latestDeliveryKey(task))) fail(422, "触达确认必须来自受控会话或轨迹读取核实的本次实际用户消息");
      await this.deps.business().observe(task.taskId, task.version, judgment, []);
    } else if (body.kind === "plan") {
      const plan = body.plan as RecoveryPlan;
      if (Array.isArray(plan?.actions)) for (const action of plan.actions) {
        if (!action || typeof action !== "object") fail(422, "方案操作格式无效");
        if (task.request.target.engine === "TE" && (action.type === "container_command" || action.command !== undefined)) fail(422, "TE 不提供 Shell，需使用声明接口或在方案中说明 Owner 配合事项");
        if (task.request.target.engine === "TE" && !action.engineOperation && !action.operationScope) fail(422, "TE 方案动作必须声明 operationScope 或参考 Engine 请求");
        if (action.engineOperation) BotEngineOperations.validate(task.runtimeTarget?.target.activeEngine ?? { OC: "openclaw", CC: "claude_code", TE: "teclaw" }[task.request.target.engine] as "openclaw" | "claude_code" | "teclaw", action.engineOperation, true);
      }
      await this.deps.business().plan(task.taskId, task.version, plan);
    }
    else if (body.kind === "apply") {
      if (typeof body.applicable !== "boolean" || typeof body.success !== "boolean" || typeof body.summary !== "string") fail(422, "执行回报格式无效");
      await this.deps.business().finishApply(task.taskId, task.version, { applicable: body.applicable, success: body.success, summary: body.summary });
    } else if (body.kind === "blocked" && typeof body.summary === "string") {
      if (task.simulation && body.reason === "user_continued") await this.deps.business().assessSimulation(task.taskId, task.version,
        { decision: "user_continued", summary: body.summary, evidence_ids: body.evidence_ids as string[] });
      else if (body.reason === "user_continued") await this.deps.business().finishForUserContinuation(task.taskId, task.version,
        { summary: body.summary, evidence_ids: body.evidence_ids as string[], executionId: task.execution!.executionId });
      else if (body.reason !== undefined) fail(422, "未知的结束原因");
      else await this.deps.business().executionFailure(task.taskId, redactPersistableText(body.summary, 8000), task.execution!.executionId);
    }
    else fail(422, "未知业务回报类型");
    return { ok: true };
  }
  readonly handleAisStatus: AisStatusHandler = async ({ task: row, step, report }) => {
    const task = await this.required(row.task_id);
    const owner = resolveTaskControlPlaneEnvironment(task);
    if (owner && owner !== this.deps.controlPlaneEnvironment) return { applied: false };
    if (step.step_type !== "agent_execution" || step.task_id !== task.taskId) return { applied: false };
    if (task.status === "canceled" || step.status === "canceled") return { applied: false };
    const currentExecution = task.execution?.state !== "ended" && task.aisExecution?.stepId === step.step_id && task.aisExecution.aisBase.executionId === task.execution?.executionId;
    if (["succeeded", "failed"].includes(step.status)) {
      const identical = sameAgentExecutionReport(step, report);
      return { applied: identical, duplicate: identical };
    }
    if (currentExecution && report.status !== "running") {
      if (task.status === "running") {
        if (report.errorCode === "AGENT_LEASE_EXPIRED" && task.execution!.jobId) {
          const remote = await this.deps.ais.getJobStatusDetail(task.execution!.jobId);
          if (remote.status === "running" || remote.rawStatus === "notfound") return { applied: false };
        }
        const detail = redactPersistableText(report.errorMessage ?? report.summary ?? "未取得有效运行回报", 1500);
        const reason = redactPersistableText(`自愈 AIS 执行中断${task.execution!.jobId ? `（作业 ${task.execution!.jobId}）` : ""}${report.errorCode ? ` [${report.errorCode}]` : ""}：${detail}`, 2000);
        await this.deps.business().executionFailure(task.taskId, reason, task.execution!.executionId, report.retryable !== false);
      }
      else await this.release(task);
    }
    return { applied: await this.deps.repo.updateStepStatus(step.step_id, { ...report, expectedStep: { status: step.status, output_json: step.output_json } }) };
  };
  private async tool(task: RecoveryStoredTask, body: Record<string, unknown>, name: string, write: boolean, run: (markDispatched: () => void) => Promise<unknown>) {
    if (task.simulation && (write || !simulationReadTools.has(name))) fail(403, "仿真只允许受控只读调查，不允许真实干预");
    if (typeof body.clientRequestId !== "string" || !body.clientRequestId.trim() || typeof body.purpose !== "string" || !body.purpose.trim()) fail(422, "工具调用需要稳定 clientRequestId 与 purpose");
    const scope = task.authorizationScopeDigest!, execution = task.execution!;
    const { call, created } = await this.deps.audit.createToolCall({ callId: `tc-${randomUUID()}`, taskId: task.taskId, stepId: task.current!.stepId,
      executionId: execution.executionId, authorizationScopeDigest: scope, clientRequestId: body.clientRequestId,
      toolName: name, operation: String(body.operation ?? name), actionId: typeof body.actionId === "string" ? body.actionId : null,
      request: recoveryToolRequest(body), isWrite: write, toolCallLedgerGuard: (calls, locked) => {
        const config = locked.taskConfig as RecoveryStoredTask | undefined;
        if (config?.simulation && (write || !simulationReadTools.has(name))) fail(403, "仿真只允许受控只读调查，不允许真实干预");
        if (!config || config.status === "canceled" || config.execution?.executionId !== execution.executionId || config.execution.state === "ended"
          || config.execution.leaseExpiresAt <= Math.floor(this.now() / 1000)) fail(409, "执行授权已变化");
        if (name === "reconcile_write" && calls.some(item => item.toolName === "reconcile_write" && item.status === "succeeded"
          && (item.result as Record<string, unknown>)?.sourceToolCallId === body.sourceToolCallId
          && (item.result as Record<string, unknown>)?.outcome !== body.outcome)) fail(409, "已核实的写入结果不能被相反结论覆盖");
        if (write && (config.level === "debug" || config.status !== "running")) fail(403, "当前不能执行实际干预");
        if (name === "retry_message" && config.phase === "assess") {
          if (config.plan || calls.some(item => item.toolName === "retry_message"
            && item.status !== "canceled" && !(item.status === "failed" && (item.result as Record<string, unknown>)?.dispatched === false)
            && !calls.some(check => check.toolName === "reconcile_write" && check.status === "succeeded"
              && (check.result as Record<string, unknown>)?.sourceToolCallId === item.callId
              && (check.result as Record<string, unknown>)?.outcome === "not_applied"))) fail(409, "初始消息已领取，不能再次发送");
        } else if (write && (config.phase !== "apply" || !config.plan?.approvedAt)) fail(403, "当前没有方案操作授权");
        if (write && name === "apply_action" && (config.plan?.digest !== task.plan?.digest
          || !config.plan?.actions.some(action => action.actionId === body.actionId))) fail(403, "当前操作不属于有效获批方案");
        if (write && calls.some(item => item.isWrite && item.status === "unknown"
          && !calls.some(check => check.toolName === "reconcile_write" && check.status === "succeeded"
            && (check.result as Record<string, unknown>)?.sourceToolCallId === item.callId))) fail(409, "已有写操作结果未知，不能盲目重放");
      } });
    if (!created) {
      const evidenceId = ["succeeded", "failed", "unknown"].includes(call.status)
        ? await this.recordToolEvidence(task.taskId, call.callId, name, body, call.status, call.result) : undefined;
      return { toolCallId: call.callId, status: call.status, result: call.result, evidenceId };
    }
    const leaseOwner = `worker-${randomUUID()}`;
    const claimed = await this.deps.audit.claimToolCall({ callId: call.callId, executionId: execution.executionId,
      authorizationScopeDigest: scope, leaseOwner, leaseExpiresAt: Math.floor(this.now() / 1000) + 180 });
    if (!claimed) fail(409, "工具调用已被领取");
    let dispatched = false;
    let result: unknown, status: "succeeded" | "failed" | "unknown" = "succeeded";
    try {
      const current = await this.required(task.taskId);
      if (current.status === "canceled" || current.execution?.executionId !== execution.executionId || current.execution.state === "ended") fail(409, "执行授权已变化");
      result = redactEvidenceValue(await run(() => { dispatched = true; }));
      if ((result as Record<string, unknown>)?.status === "failed") status = "failed";
      else if ((result as Record<string, unknown>)?.status === "unknown") status = "unknown";
    } catch (error) {
      status = write && dispatched ? "unknown" : "failed";
      result = { dispatched, error: error instanceof SessionRecoveryError ? { code: error.code, message: redactPersistableText(error.message, 1000) }
        : { code: "controlled_operation_unconfirmed", message: "受控操作未确认，需核对现场与审计" } };
    }
    result = { ...result as Record<string, unknown>, recorded_at_ms: this.now() };
    try {
      await this.deps.audit.completeToolCall({ callId: call.callId, executionId: execution.executionId, authorizationScopeDigest: scope,
        status, leaseOwner, result });
    } catch (error) {
      const current = await this.required(task.taskId), settled = await this.deps.audit.findToolCall(call.callId);
      if (current.status !== "canceled" || !settled || ["pending", "executing"].includes(settled.status)) throw error;
      return { toolCallId: settled.callId, status: settled.status, result: settled.result };
    }
    const evidenceId = await this.recordToolEvidence(task.taskId, call.callId, name, body, status, result);
    return { toolCallId: call.callId, status, result, evidenceId };
  }
  private async recordToolEvidence(taskId: string, callId: string, name: string, body: Record<string, unknown>, status: string, result: unknown) {
    const current = await this.required(taskId), evidenceId = `evidence-${callId}`;
    const receipt = result as Record<string, unknown>;
    const initialDelivery = name === "retry_message" && receipt.initial_message === true && receipt.dry_run !== true
      ? recoveryResult(current.request, receipt as unknown as Parameters<typeof recoveryResult>[1], Number(receipt.started_at_ms), Number(receipt.finished_at_ms)) : undefined;
    // Tool requests, results and evidence are projected from the existing audit ledger.
    const latestInitial = [...current.timeline].reverse().find(event => event.kind === "operation" && (event.payload.result as Record<string, unknown>)?.initial_message === true);
    if (initialDelivery && latestInitial?.payload.tool_call_id === callId && JSON.stringify(initialDelivery) !== JSON.stringify(current.delivery)) await this.save(current, value => ({ ...value,
      delivery: initialDelivery, observationDeadlineAt: initialDelivery.delivery.finished_at_ms + this.deps.observationMs }));
    if (name === "retry_message" && receipt.dry_run !== true) {
      try { this.deps.actionNotifier?.after({ taskId, eventId: callId, kind: "message" }); }
      catch { console.error("[session-recovery] group notification hook failed", taskId, "message"); }
    }
    return evidenceId;
  }
  private context(task: RecoveryStoredTask, target = task.runtimeTarget!.target): AgentToolContext {
    return { taskId: task.taskId,
      issue: { traceId: null, relatedTaskId: null, errorText: task.request.diagnosis,
        timeRange: { from: Math.floor(this.now() / 1000) - 86400, to: Math.floor(this.now() / 1000) } },
      target };
  }
  private requireLiveTask(task: RecoveryStoredTask): void {
    if (task.simulation) fail(403, "仿真不允许实际干预或核实真实写入");
  }
  private logicalTarget(task: RecoveryStoredTask): BotTarget {
    return { environment: task.request.target.env, ownerId: task.request.target.entity_id, botId: task.request.target.bot_id,
      ...(task.request.target.runtime_identity ? { runtimeIdentity: task.request.target.runtime_identity } : {}) };
  }
  private async currentTarget(task: RecoveryStoredTask) {
    return recoveryTargetSnapshot(await this.deps.targets.resolve(this.logicalTarget(task))).target;
  }
  private async historicalSessionTarget(task: RecoveryStoredTask) {
    const target = await this.currentTarget(task);
    const engine = { OC: 'openclaw', CC: 'claude_code', TE: 'teclaw' }[task.request.target.engine];
    if (target.activeEngine !== engine) fail(409, '当前引擎已变化，不能用于读取原会话');
    return target;
  }
  private recordAfterCutoff(timestamp: unknown, cutoffAtMs: number): boolean {
    const at = typeof timestamp === "number" ? timestamp : typeof timestamp === "string" ? Date.parse(timestamp) : NaN;
    return Number.isFinite(at) && at > cutoffAtMs;
  }
  private latestDeliveryKey(task: RecoveryStoredTask): string {
    const event = [...task.timeline].reverse().find(item => item.kind === "operation" && typeof (item.payload.result as Record<string, unknown>)?.delivery_key === "string");
    return event ? String((event.payload.result as Record<string, unknown>).delivery_key) : task.request.delivery_key;
  }
  private originalSessionId(task: RecoveryStoredTask): string | undefined {
    const requested = task.request.session_id ?? (task.request.target.engine === "OC" ? cronRunSessionId(task.request.session_key) : undefined);
    if (requested) return requested;
    const source = task.timeline.find(event => event.kind === "operation" && (event.payload.result as Record<string, unknown>)?.session_id);
    return source ? String((source.payload.result as Record<string, unknown>).session_id) : undefined;
  }
  private async required(id: string) { const task = await this.deps.tasks.find(id); if (!task) fail(404, "自愈任务不存在"); return task; }
  private async save(task: RecoveryStoredTask, change: (value: RecoveryStoredTask) => RecoveryStoredTask, executionId?: string) {
    for (let attempt = 0; attempt < 5; attempt++) {
      if (executionId && task.execution?.executionId !== executionId) return task;
      const saved = await this.deps.tasks.update(task.taskId, task.version, value => {
        if (executionId && value.execution?.executionId !== executionId) fail(409, "旧执行不能更新新执行");
        return { ...change(value), version: value.version + 1 };
      });
      if (saved) return saved;
      task = await this.required(task.taskId);
    }
    fail(409, "任务并发更新，请重试");
  }
  private event(kind: "operation" | "execution", summary: string, payload: Record<string, unknown>) {
    return { event_id: randomUUID(), at_ms: this.now(), kind, actor: "service" as const, summary, payload };
  }
}
