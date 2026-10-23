import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "../common/task-control-plane.js";
import type { AgentRuntimeTargetSnapshot } from "../common/agent-tools/contracts.js";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import { createHash, randomUUID } from "node:crypto";
import { SessionRecoveryError } from "./errors.js";
import { parseRequest } from "./validation.js";
import { recoveryHandbook } from "./handbook.js";
import { decideRecoveryPlan, observeRecovery, recoveryTaskResult, resumeRecoveryObservation, submitRecoveryPlan, terminateRecoveryTask } from "./task-state.js";
import type { RecoveryActionNotifier, RecoveryActor, RecoveryAcknowledgment, RecoveryExecutionView, RecoveryRequest, RecoveryResult, RecoveryTaskView, RecoveryTermination, SessionRecoveryTaskApi } from "./contracts.js";
import type { RecoveryEvidence, RecoveryJudgment, RecoveryLevel, RecoveryPlan, RecoveryTaskResult, RecoveryTimelineEvent, RecoveryStoredTask, RecoveryTaskStore } from "./task-contracts.js";

const EXECUTION_RETRY_DELAYS_MS = [20000, 60000] as const;

export interface RecoveryAgentExecutor {
  start(task: RecoveryStoredTask): Promise<void>;
  release(task: RecoveryStoredTask): Promise<void>;
  terminate(task: RecoveryStoredTask): Promise<RecoveryTermination>;
  listExecutions(taskId: string): Promise<RecoveryExecutionView[]>;
}
export interface RecoveryTaskNotifier {
  /** False means notification is disabled; do not record a delivery attempt. */
  notify(input: { ownerId: string; taskId: string; kind: "plan" | "result"; summary: string }): Promise<boolean>;
}
export interface RecoveryTaskReporter { report(result: RecoveryTaskResult): Promise<void> }

/** Owns the self-healing workflow; execution and controlled tools are injected. */
export class SessionRecoveryTaskService implements SessionRecoveryTaskApi {
  private readonly now: () => number;
  private reportingCursor = "";
  constructor(private readonly deps: {
    controlPlaneEnvironment: AgentControlPlaneEnvironment;
    tasks: RecoveryTaskStore;
    agent: RecoveryAgentExecutor;
    notifier: RecoveryTaskNotifier;
    actionNotifier?: RecoveryActionNotifier;
    reporter: RecoveryTaskReporter;
    observationMs: number;
    now?: () => number;
    resolveTarget?: (request: RecoveryRequest) => Promise<AgentRuntimeTargetSnapshot>;
  }) { this.now = deps.now ?? Date.now; }

  async create(raw: unknown, actor: RecoveryActor): Promise<RecoveryAcknowledgment & { task_id: string }> {
    const request = parseRequest(raw);
    if (actor.userId !== request.target.entity_id && !actor.isAdmin) this.fail(403, "无权发起目标自愈");
    const level = this.level(raw);
    const handbook = recoveryHandbook(request.tc_fault_label);
    const taskId = `SR-${createHash("sha256").update(JSON.stringify([request.event_id, request.delivery_key])).digest("hex").slice(0, 48)}`;
    const existing = await this.deps.tasks.find(taskId);
    if (existing) {
      if (JSON.stringify(existing.request) !== JSON.stringify(request)) this.fail(409, "同一投递标识的请求内容不一致");
      return { event_id: request.event_id, delivery_key: request.delivery_key, status: "accepted", task_id: taskId };
    }
    const runtimeTarget = await this.deps.resolveTarget?.(request);
    const task: RecoveryStoredTask = { schemaVersion: "session-recovery-task/v1", taskId, request, level, controlPlaneEnvironment: this.deps.controlPlaneEnvironment,
      status: "running", phase: "assess", version: 1, shared: false, observationDeadlineAt: null, ownerDeadlineAt: null,
      ...(runtimeTarget ? { runtimeTarget, authorizationScopeDigest: createHash("sha256").update(JSON.stringify({ target: request.target, level })).digest("hex"), current: { stepId: `${taskId}-1` } } : {}),
      plan: null, judgment: null, evidence: [], pending_results: [], timeline: [this.event("created", "service", "收到诊断并创建自愈任务", { request, handbook, initial_intervention: "agent" }),
        ...(level === "debug" ? [this.event("message", "service", "Debug：仅记录干预策略，不实际发送", { initial_intervention: "agent" })] : [])] };
    try { await this.deps.tasks.create(task, `${taskId}-1`); }
    catch (error) {
      const raced = await this.deps.tasks.find(taskId);
      if (!raced) throw error;
      if (JSON.stringify(raced.request) !== JSON.stringify(request)) this.fail(409, "同一投递标识的请求内容不一致");
      return { event_id: request.event_id, delivery_key: request.delivery_key, status: "accepted", task_id: taskId };
    }
    setImmediate(() => {
      void this.start(task).catch(() => { console.error("[session-recovery] failed to persist startup result", taskId); });
    });
    return { event_id: request.event_id, delivery_key: request.delivery_key, status: "accepted", task_id: taskId };
  }

  private async start(task: RecoveryStoredTask): Promise<void> {
    task = await this.required(task.taskId);
    if (task.status !== "running" || !this.ownsTask(task)) return;
    try { await this.deps.agent.start(task); }
    catch (error) {
      const latest = await this.required(task.taskId);
      // A stale dispatcher must not fail a replacement that another worker already owns.
      if (latest.execution?.executionId !== task.execution?.executionId || latest.executionRetry?.retryAt) throw error;
      await this.executionFailure(task.taskId, "自愈 Agent 启动失败", task.execution?.executionId);
    }
  }

  async recordDelivery(taskId: string, result: RecoveryResult): Promise<void> {
    const task = await this.required(taskId);
    if (result.event_id !== task.request.event_id || result.delivery.delivery_key !== task.request.delivery_key) this.fail(409, "投递结果不属于该任务");
    if (task.delivery && JSON.stringify(task.delivery) === JSON.stringify(result)) return;
    if (task.delivery) this.fail(409, "投递结果不能被不同内容覆盖");
    await this.mutate(taskId, task.version, value => ({ ...value, delivery: result, version: value.version + 1,
      observationDeadlineAt: result.delivery.finished_at_ms + this.deps.observationMs,
      timeline: [...value.timeline, this.event("message", "service", "初始建议发送结果", { result })] }));
  }

  async assess(taskId: string, version: number, input: { decision: "message" | "environment" | "observe"; summary: string; evidence_ids: string[] }): Promise<void> {
    const task = await this.mutate(taskId, version, value => {
      if (value.status !== "running" || value.phase !== "assess") this.fail(409, "当前不能提交初始判断");
      if (!["message", "environment", "observe"].includes(input.decision) || typeof input.summary !== "string" || !input.summary.trim()
        || input.summary.length > 8000 || containsSecret(input.summary) || !Array.isArray(input.evidence_ids) || !input.evidence_ids.length
        || input.evidence_ids.some(id => !value.evidence.some(item => item.evidence_id === id))) this.fail(422, "初始判断需要决策、理由及已保存的证据");
      if (input.decision === "message" && !value.delivery && !(value.level === "debug" && value.timeline.some(event =>
        event.kind === "operation" && (event.payload.result as Record<string, unknown>)?.dry_run === true))) this.fail(409, "尚未留存 Agent 的初始消息或拟发送内容");
      const debugFinished = value.level === "debug" && input.decision !== "environment";
      return { ...value, version: value.version + 1, phase: input.decision === "environment" ? "plan" : "observe",
        ...(debugFinished ? { status: "completed" as const, judgment: { delivery: "unconfirmed" as const, response: "unknown" as const, effect: "not_intervened" as const, summary: input.summary, evidence_ids: input.evidence_ids } } : {}),
        observationDeadlineAt: input.decision === "environment" ? null : value.observationDeadlineAt ?? this.now() + this.deps.observationMs,
        timeline: [...value.timeline, this.event("assessment", "agent", input.summary, input)] };
    });
    if (task.status === "completed") await this.deps.agent.release(task);
    if (task.status === "completed") this.notifyGroup(task, "result");
  }

  async observe(taskId: string, version: number, judgment: RecoveryJudgment, evidence: RecoveryEvidence[]): Promise<void> {
    const task = await this.mutate(taskId, version, value => {
      const stored = [...value.evidence];
      for (const item of evidence) {
        const existing = stored.find(record => record.evidence_id === item.evidence_id);
        if (existing && JSON.stringify(existing) !== JSON.stringify(item)) this.fail(409, "同一证据标识的内容不能改变");
        if (!existing) stored.push(item);
      }
      return { ...value, ...observeRecovery({ ...value, evidence: stored }, judgment, this.now()),
        timeline: [...value.timeline, this.event("observation", "agent", judgment.summary, { judgment, evidence })] };
    });
    if (["completed", "failed", "waiting_effect"].includes(task.status)) await this.deps.agent.release(task);
    if (["completed", "failed"].includes(task.status)) this.notifyGroup(task, "result");
  }

  async plan(taskId: string, version: number, plan: RecoveryPlan): Promise<void> {
    const task = await this.mutate(taskId, version, value => ({ ...value, ...submitRecoveryPlan(value, plan, this.now()),
      timeline: [...value.timeline, this.event("plan", "agent", plan.summary, { plan })] }));
    this.notifyGroup(task, "plan");
    if (task.status === "waiting_owner") await this.notify(task, "plan", plan.summary);
    if (task.status === "completed") await this.deps.agent.release(task);
    if (task.status === "completed") this.notifyGroup(task, "result");
  }

  async decide(taskId: string, digest: string, approved: boolean, actor: RecoveryActor, cooperationDone = false, feedback = ""): Promise<void> {
    const previous = await this.required(taskId); this.owner(previous, actor);
    const decision = previous.timeline.find(event => event.kind === "owner_decision" && event.payload.digest === digest);
    if (decision) {
      if (decision.payload.approved !== approved || decision.payload.cooperation_done !== cooperationDone || decision.payload.feedback !== feedback) this.fail(409, "方案决策不能被不同内容覆盖");
      if (approved && previous.status === "running") await this.deps.agent.start(previous);
      return;
    }
    if (approved && previous.plan?.owner_cooperation.length && !cooperationDone) this.fail(422, "请先处理方案中的 Owner 配合事项");
    if (typeof feedback !== "string" || feedback.length > 4000 || containsSecret(feedback)) this.fail(422, "反馈格式无效");
    const task = await this.mutate(taskId, previous.version, value => ({ ...value, ...decideRecoveryPlan(value, digest, approved, this.now()),
      timeline: [...value.timeline, this.event("owner_decision", "owner", approved ? "确认方案并完成配合事项" : "拒绝方案", { digest, approved, cooperation_done: cooperationDone, feedback })] }));
    if (approved) await this.deps.agent.start(task);
    else await this.deps.agent.release(task);
    if (!approved) this.notifyGroup(task, "result");
  }

  async resume(taskId: string): Promise<void> {
    const previous = await this.required(taskId);
    const task = await this.mutate(taskId, previous.version, value => ({ ...value, ...resumeRecoveryObservation(value),
      timeline: [...value.timeline, this.event("resumed", "service", "恢复原观察阶段", {})] }));
    await this.deps.agent.start(task);
  }

  async list(eventId: string, actor: RecoveryActor) {
    if (!eventId || eventId.length > 128) this.fail(422, "event_id 无效");
    const tasks = await this.deps.tasks.listForEvent(eventId, actor.userId, actor.isAdmin);
    return tasks.filter(task => this.canOperate(task, actor) || task.shared === true)
      .map(task => ({ task_id: task.taskId, status: task.status, phase: task.phase, effect: task.judgment?.effect ?? "unknown",
        ...(task.completionReason ? { completion_reason: task.completionReason } : {}) }));
  }
  async detail(taskId: string, actor: RecoveryActor): Promise<RecoveryTaskView> {
    const task = await this.required(taskId);
    const canOperate = this.canOperate(task, actor);
    if (!canOperate && task.shared !== true) this.fail(403, "权限不足，请联系任务 Owner 开启分享");
    const { pending_results: _pending, execution: _execution, authorizationScopeDigest: _scope, aisExecution: _ais, interventionStatus: _intervention, interventionAuditComplete: _auditCoverage, ...view } = task;
    const executions = await this.deps.agent.listExecutions(taskId);
    return { ...view, pending_results: [], shared: task.shared === true, canOperate, canManageShare: canOperate,
      executions, ...(task.status === "canceled" ? { termination: executions.at(-1)?.termination ?? { status: "stop_pending" as const, aisJobId: executions.at(-1)?.jobId ?? null } } : {}) };
  }
  async setTaskShared(taskId: string, shared: boolean, actor: RecoveryActor): Promise<RecoveryTaskView> {
    if (typeof shared !== "boolean") this.fail(422, "shared 必须为布尔值");
    const task = await this.required(taskId); this.owner(task, actor);
    if (task.shared !== shared) await this.mutate(taskId, task.version, value => ({ ...value, shared, version: value.version + 1 }));
    return this.detail(taskId, actor);
  }

  async terminate(taskId: string, actor: RecoveryActor, reason: unknown = "用户手动停止任务"): Promise<RecoveryTaskView & { termination: RecoveryTermination }> {
    const previous = await this.required(taskId); this.owner(previous, actor);
    if (!this.ownsTask(previous)) this.fail(409, "请在任务所属的 ClawWeb 环境停止任务");
    if (typeof reason !== "string" || !reason.trim() || reason.length > 500 || containsSecret(reason)) this.fail(422, "停止原因格式无效");
    let task = previous;
    if (previous.status !== "canceled") {
      try {
        task = await this.mutate(taskId, previous.version, value => ({ ...value, ...terminateRecoveryTask(value),
          timeline: [...value.timeline, this.event("termination", actor.isAdmin ? "service" : "owner",
            `${actor.isAdmin ? "管理员" : "Owner"}停止本次自愈：${reason.trim()}`, { actor_id: actor.userId, actor_role: actor.isAdmin ? "admin" : "owner",
              reason: reason.trim(), phase: value.phase, execution_id: value.execution?.executionId })] }));
        this.notifyGroup(task, "result");
      } catch (error) {
        if (!(error instanceof SessionRecoveryError) || error.status !== 409) throw error;
        task = await this.required(taskId);
        if (task.status !== "canceled") throw error;
      }
    }
    const termination = await this.deps.agent.terminate(task);
    return { ...await this.detail(taskId, actor), termination };
  }

  async annotate(taskId: string, actor: RecoveryActor, input: {
    layer: "delivery" | "response" | "effect"; correct: boolean | null; reason: string; evidence_ids: string[];
  }): Promise<void> {
    const task = await this.required(taskId); this.owner(task, actor);
    if (!input || typeof input !== "object" || !["delivery", "response", "effect"].includes(input.layer)
      || (input.correct !== true && input.correct !== false && input.correct !== null)
      || typeof input.reason !== "string" || !input.reason.trim() || input.reason.length > 4000
      || !Array.isArray(input.evidence_ids) || input.evidence_ids.some(id => !task.evidence.some(item => item.evidence_id === id))) {
      this.fail(422, "标注需要判断层次、结论、理由及已留存的证据引用");
    }
    await this.mutate(taskId, task.version, value => ({ ...value, version: value.version + 1,
      timeline: [...value.timeline, this.event("annotation", "owner", "人工标注", { ...input, reviewer_id: actor.userId })] }));
  }
  async finishApply(taskId: string, version: number, input: { applicable: boolean; success: boolean; summary: string }): Promise<void> {
    const task = await this.mutate(taskId, version, value => {
      if (value.status !== "running" || value.phase !== "apply" || !value.plan?.approvedAt) this.fail(409, "当前没有获批执行方案");
      const failed = !input.applicable || !input.success;
      return { ...value, version: value.version + 1, status: failed ? "failed" : "running", phase: failed ? "apply" : "verify",
        observationDeadlineAt: failed ? null : this.now() + this.deps.observationMs,
        judgment: failed ? this.failureJudgment(value, input.summary) : value.judgment,
        timeline: [...value.timeline, this.event("operation", "agent", input.summary, { applicable: input.applicable, success: input.success })] };
    });
    if (task.status === "failed") await this.deps.agent.release(task);
    this.notifyGroup(task, "execution");
    if (task.status === "failed") this.notifyGroup(task, "result");
  }

  async finishForUserContinuation(taskId: string, version: number, input: { summary: string; evidence_ids: string[]; executionId: string }): Promise<void> {
    const task = await this.mutate(taskId, version, value => {
      if (value.status !== "running" || value.execution?.executionId !== input.executionId || value.execution.state === "ended") this.fail(409, "当前不能结束自愈任务");
      const reads = new Set(value.timeline.filter(event => event.kind === "operation" && event.payload.status === "succeeded")
        .map(event => event.payload.raw_tool_call_id ?? event.payload.tool_call_id));
      if (typeof input.summary !== "string" || !input.summary.trim() || input.summary.length > 8000
        || containsSecret(input.summary) || !Array.isArray(input.evidence_ids) || !input.evidence_ids.length
        || input.evidence_ids.some(id => !value.evidence.some(item => item.evidence_id === id
          && ["session_read", "trajectory_read"].includes(item.source) && reads.has(item.tool_call_id)))) this.fail(422, "用户续聊让路需要已保存的原会话或轨迹读取证据");
      return { ...value, status: "completed", completionReason: "user_continued", version: value.version + 1,
        observationDeadlineAt: null, ownerDeadlineAt: null, requiresFreshObservation: false,
        ...(value.executionRetry ? { executionRetry: { ...value.executionRetry, retryAt: 0 } } : {}),
        judgment: { delivery: value.judgment?.delivery ?? "unconfirmed", response: value.judgment?.response ?? "unknown",
          effect: value.judgment?.effect ?? "unknown", summary: input.summary,
          evidence_ids: [...new Set([...(value.judgment?.evidence_ids ?? []), ...input.evidence_ids])] },
        timeline: [...value.timeline, this.event("termination", "agent", input.summary,
          { completion_reason: "user_continued", evidence_ids: input.evidence_ids })] };
    });
    await this.deps.agent.release(task);
    this.notifyGroup(task, "result");
  }

  async executionFailure(taskId: string, reason: string, executionId?: string, retryable = false): Promise<void> {
    const previous = await this.required(taskId);
    if (!this.ownsTask(previous) || previous.status !== "running" || (executionId && (previous.execution?.executionId !== executionId || previous.execution.state === "ended"))) return;
    const task = await this.mutate(taskId, previous.version, value => {
      if (value.status !== "running") this.fail(409, "任务已离开执行阶段");
      if (executionId && (value.execution?.executionId !== executionId || value.execution.state === "ended")) this.fail(409, "旧执行已结束");
      const failedExecutionId = executionId ?? value.execution?.executionId;
      // The retry marker is committed before release; another report may arrive in between.
      if (failedExecutionId && value.executionRetry?.executionId === failedExecutionId) return { ...value, version: value.version + 1 };
      const failures = (value.executionRetry?.failures ?? 0) + 1;
      const delay = retryable ? EXECUTION_RETRY_DELAYS_MS[failures - 1] : undefined;
      const summary = delay !== undefined ? `${reason}；已保存进度，${delay / 1000} 秒后重试 AIS 执行（第 ${failures} 次重试）`
        : retryable ? `${reason}；AIS 执行重试次数已用尽（失败 ${failures} 次），原任务效果未确认` : reason;
      return { ...value, status: delay !== undefined ? "running" : "failed", version: value.version + 1,
        executionRetry: { failures, retryAt: delay !== undefined ? this.now() + delay : 0,
          ...(failedExecutionId ? { executionId: failedExecutionId } : {}) },
        judgment: delay !== undefined ? value.judgment : this.failureJudgment(value, summary),
        timeline: [...value.timeline, this.event("execution", "service", summary, { execution_id: executionId, retryable, failures, retry_at_ms: delay !== undefined ? this.now() + delay : null })] };
    });
    await this.deps.agent.release(task);
    if (task.status === "failed") this.notifyGroup(task, "result");
  }

  private failureJudgment(task: RecoveryStoredTask, summary: string): RecoveryJudgment {
    return { delivery: task.judgment?.delivery ?? "unconfirmed", response: task.judgment?.response ?? "unknown",
      effect: "unknown", summary, evidence_ids: task.evidence.map(item => item.evidence_id) };
  }

  async maintenance(isActive: () => Promise<boolean> = async () => true): Promise<void> {
    let cursor = "";
    while (await isActive()) {
      const batch = await this.deps.tasks.listForMaintenance?.(200, cursor, this.deps.controlPlaneEnvironment, this.now());
      if (!batch) return;
      for (const task of batch.tasks) {
      try {
        if (!await isActive()) return;
        if (!this.ownsTask(task)) continue;
        // Persisted retry scheduling also survives a process restart between state save and release.
        if (task.status === "running" && task.executionRetry?.retryAt && task.execution?.state !== "ended") {
          // A retained retry marker must never retire a successor launched by another worker.
          if (task.executionRetry.executionId !== task.execution?.executionId) continue;
          await this.deps.agent.release(task);
        }
        if (task.status === "running" && ["observe", "verify"].includes(task.phase)
          && task.observationDeadlineAt !== null && task.observationDeadlineAt <= this.now() && !task.requiresFreshObservation) {
          await this.observe(task.taskId, task.version, { delivery: task.judgment?.delivery ?? "unconfirmed", response: task.judgment?.response ?? "unknown",
            effect: "unknown", summary: "本轮观察到期，保存进度并等待诊断服务继续", evidence_ids: task.judgment?.evidence_ids ?? [] }, []);
          continue;
        }
        if (task.status === "running" && (!task.execution || task.execution.state === "ended" || task.executionRetry?.retryAt)
          && (task.executionRetry?.retryAt ?? 0) <= this.now()) await this.start(task);
      } catch { console.error("[session-recovery] maintenance failed", task.taskId); }
      }
      if (!batch.nextTaskId) return;
      cursor = batch.nextTaskId;
    }
  }

  private async notify(task: RecoveryStoredTask, kind: "plan" | "result", summary: string): Promise<void> {
    // Notification completion is a timeline fact, independent of Owner approval.
    let sent = false;
    try {
      const result = await this.deps.notifier.notify({ ownerId: task.request.target.entity_id, taskId: task.taskId, kind, summary });
      if (result === false) return;
      sent = true;
    }
    catch { console.error("[session-recovery] notification not confirmed", task.taskId, kind); }
    const current = await this.required(task.taskId);
    await this.mutate(task.taskId, current.version, value => ({ ...value, version: value.version + 1,
      timeline: [...value.timeline, this.event("notification", "service", sent ? "钉钉通知已发送" : "钉钉通知未确认，请在任务页面处理", { kind, sent })] }));
  }
  private notifyGroup(task: RecoveryStoredTask, kind: Parameters<RecoveryActionNotifier["after"]>[0]["kind"]): void {
    const event = task.timeline.at(-1);
    if (!event) return;
    try { this.deps.actionNotifier?.after({ taskId: task.taskId, eventId: event.event_id, kind }); }
    catch { console.error("[session-recovery] group notification hook failed", task.taskId, kind); }
  }

  private async mutate(taskId: string, version: number, change: (value: RecoveryStoredTask) => RecoveryStoredTask): Promise<RecoveryStoredTask> {
    for (let attempt = 0; attempt < 5; attempt++) {
      const updated = await this.deps.tasks.update(taskId, version, value => {
        const changed = change(value);
        const reportable = changed.status !== value.status
          && ["waiting_effect", "completed", "failed", "canceled"].includes(changed.status);
        return { ...changed, pending_results: reportable
          ? [...value.pending_results, recoveryTaskResult(changed, changed.status === "canceled"
            ? [...changed.timeline].reverse().find(event => event.kind === "termination")?.summary : undefined)] : value.pending_results };
      });
      if (updated) return updated;
      version = (await this.required(taskId)).version;
    }
    this.fail(409, "任务已变化，请读取当前版本");
  }

  /** Independently scheduled delivery; never holds up state transitions or expiry handling. */
  async reportResults(isActive: () => Promise<boolean> = async () => true): Promise<void> {
    if (!await isActive()) return;
    // A bounded page and serial HTTP calls cap each maintenance tick's work.
    const batch = await this.deps.tasks.listForReporting?.(5, this.reportingCursor, this.deps.controlPlaneEnvironment);
    if (!batch) return;
    this.reportingCursor = batch.nextTaskId;
    let remaining = 5;
    for (const task of batch.tasks) {
      remaining -= await this.reportPending(task, remaining, isActive);
      if (!remaining) { this.reportingCursor = task.taskId; break; }
    }
  }

  async reportPending(task: RecoveryStoredTask, limit = 5, isActive: () => Promise<boolean> = async () => true): Promise<number> {
    if (!this.ownsTask(task)) return 0;
    let sent = 0;
    for (const result of task.pending_results) {
      if (sent >= limit || !await isActive()) break;
      const attempt = await this.deps.tasks.claimResult(task.taskId, result.result_version);
      if (attempt === null) break;
      sent += 1;
      try {
        await this.deps.reporter.report(result);
        await this.deps.tasks.acknowledgeResult(task.taskId, result.result_version, attempt);
      } catch {
        await this.deps.tasks.failResult(task.taskId, result.result_version, attempt);
        console.error("[session-recovery] result callback failed", task.taskId, result.result_version);
        break;
      }
    }
    return sent;
  }
  private ownsTask(task: RecoveryStoredTask): boolean {
    return resolveTaskControlPlaneEnvironment(task) === this.deps.controlPlaneEnvironment;
  }
  private async required(taskId: string): Promise<RecoveryStoredTask> {
    const task = await this.deps.tasks.find(taskId); if (!task) this.fail(404, "自愈任务不存在"); return task;
  }
  private owner(task: RecoveryStoredTask, actor: RecoveryActor): void {
    if (!this.canOperate(task, actor)) this.fail(403, "无权查看或操作该任务");
  }
  private canOperate(task: RecoveryStoredTask, actor: RecoveryActor): boolean {
    return task.request.target.entity_id === actor.userId || actor.isAdmin;
  }
  private level(raw: unknown): RecoveryLevel {
    const value = (raw as Record<string, unknown>).execution_level ?? "approval";
    if (!["debug", "approval", "automatic"].includes(String(value))) this.fail(422, "execution_level 无效");
    return value as RecoveryLevel;
  }
  private event(kind: RecoveryTimelineEvent["kind"], actor: RecoveryTimelineEvent["actor"], summary: string, payload: Record<string, unknown>): RecoveryTimelineEvent {
    return { event_id: randomUUID(), at_ms: this.now(), kind, actor, summary, payload };
  }
  private fail(status: number, message: string): never { throw new SessionRecoveryError(status, "invalid_session_recovery", message); }
}
