import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "../../services/repair/common/task-control-plane.js";
import { createHash } from "node:crypto";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { nowForDb } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { ObjectStore } from "../../services/object-storage/oss-object-store.js";
import type { RecoveryEffect, RecoveryEffectConfigCheck, RecoveryEffectResumeConfig, RecoveryEffectResumeSlot, RecoveryMaintenanceTask, RecoveryStoredTask, RecoveryTaskChange, RecoveryTaskResult, RecoveryTaskStore, RecoveryTimelineEvent } from "../../services/repair/session-recovery/task-contracts.js";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import { AgentToolCallRepository, type AgentToolCall } from "./agent-tool-call-repository.js";
import { recoveryInterventionStatus } from "../../services/repair/session-recovery/result.js";
import { sessionReadCoverage } from "../../services/repair/session-recovery/session-read-coverage.js";
import { createAgentExecutionStep } from "../../services/repair/common/agent-execution.js";
import { parseEffectResumeConfig } from "../../services/repair/session-recovery/effect-resume.js";
import { trajectoryIndexReads } from "../../services/repair/session-recovery/bootstrap-evidence.js";
import { presentRecoveryTask } from "../../services/repair/session-recovery/task-list.js";

export type { RecoveryStoredTask } from "../../services/repair/session-recovery/task-contracts.js";
export type RecoveryArtifactStore = Required<Pick<ObjectStore, "getObject" | "putObject">>;
type ArtifactRef = { objectKey: string; sha256: string };
// Read-only compatibility for failed callback audit written by older versions.
type LegacyStoppedResult = ArtifactRef & { resultVersion: number; attempts: number; stoppedAtMs: number };
const businessEvent = (event: RecoveryTimelineEvent) => !event.payload.raw_tool_call_id;
const phases = ["assess", "observe", "plan", "apply", "verify"] as const;

/** Same ownership as Repair: Task state, Step reports, object artifacts and tool audit. */
export class SessionRecoveryTaskRepository implements RecoveryTaskStore {
  constructor(private readonly db: IDatabase, private readonly tasks: EvolveRepository, private readonly artifacts: RecoveryArtifactStore) {}

  async create(value: RecoveryStoredTask, stepId: string): Promise<void> {
    const details = await this.put(value.taskId, this.details({ ...value, interventionAuditComplete: true }));
    const report = await this.put(value.taskId, { events: value.timeline, details });
    await this.db.transaction(async tx => {
      await new EvolveRepository(tx).createTaskWithStep({
        task: { taskId: value.taskId, taskType: "session_recovery", userId: value.request.target.entity_id,
          botId: value.request.target.bot_id, taskName: "会话自愈", remark: this.eventRemark(value.request.event_id), createdBy: "session-recovery-service", configJson: this.config(value, details) },
        step: { stepId, stepType: "session_recovery_assess", stepNo: 1, command: "session_recovery" },
      });
      await tx.exec("UPDATE ce_steps SET output_json = ?, status = 'running' WHERE step_id = ?", [JSON.stringify(report), stepId]);
    });
  }
  async listForMaintenance(limit: number, afterTaskId = "", environment?: AgentControlPlaneEnvironment): Promise<Array<RecoveryStoredTask | RecoveryMaintenanceTask>> {
    this.limit(limit);
    const rows = await this.db.query<{ task_id: string; config_json: string }>(
      `SELECT task_id, config_json FROM ce_tasks WHERE task_type = 'session_recovery' AND task_id > ?
       AND status IN ('pending','running','waiting_owner','waiting_effect','settling') ORDER BY task_id ASC LIMIT ?`,
      [afterTaskId, limit]);
    return Promise.all(rows.map(row => {
      const state = JSON.parse(row.config_json);
      if (!environment || (resolveTaskControlPlaneEnvironment(state) === environment
        && (state.status === "running" || state.ownerRequest))) return this.hydrate(this.db, row.config_json);
      const { status, phase, controlPlaneEnvironment, publicBaseUrl, observationDeadlineAt, effectWait, effectResumeSlot } = state;
      return { compact: true as const, taskId: row.task_id, status, phase, controlPlaneEnvironment, publicBaseUrl, observationDeadlineAt, effectWait, effectResumeSlot };
    }));
  }

  async effectResumeConfig(environment: AgentControlPlaneEnvironment, db = this.db, lock = false): Promise<RecoveryEffectResumeConfig | null> {
    const key = `session_recovery_effect_resume_${environment}`;
    const suffix = lock && db.dbType !== "sqlite" ? " FOR UPDATE" : "";
    const [row] = await db.query<{ id: number; version: number; config_json: string; enabled: number }>(
      `SELECT id, version, config_json, enabled FROM ce_app_config WHERE config_key = ?${suffix}`, [key]);
    if (!row?.enabled) return null;
    return { id: row.id, version: row.version, ...parseEffectResumeConfig(JSON.parse(row.config_json)) };
  }

  async latestDiagnosisEffect(task: RecoveryStoredTask, db = this.db, lock = false): Promise<{ version: number; effect: RecoveryEffect } | null> {
    const [row] = await db.query<{ self_healing_results: string | null }>(
      `SELECT self_healing_results FROM insight_monitoring_diagnose WHERE event_id = ?${lock && db.dbType !== "sqlite" ? " FOR UPDATE" : ""}`, [task.request.event_id]);
    if (!row) return null;
    const results = JSON.parse(row.self_healing_results ?? "[]") as RecoveryTaskResult[];
    return results.reduce<{ version: number; effect: RecoveryEffect }>((latest, item) =>
      item.task_id === task.taskId && item.result_version > latest.version
        ? { version: item.result_version, effect: item.effect } : latest, { version: 0, effect: "unknown" });
  }

  async claimRecheckDispatch(stepId: string): Promise<boolean> {
    const result = await this.db.exec(
      "UPDATE ce_steps SET status = 'dispatching', gmt_modified = ? WHERE step_id = ? AND status = 'created'",
      [nowForDb(this.db.dbType), stepId]);
    return result.affectedRows === 1;
  }

  async recordRecheckJob(stepId: string, jobId: string, receipt: Record<string, string>): Promise<void> {
    const result = await this.db.exec(
      `UPDATE ce_steps SET status = CASE WHEN status IN ('created','dispatching') THEN 'dispatched' ELSE status END,
        bot_run_id = ?, bot_response_json = ?, gmt_modified = ? WHERE step_id = ? AND step_type = 'agent_execution'`,
      [jobId, JSON.stringify(receipt), nowForDb(this.db.dbType), stepId]);
    if (result.affectedRows !== 1) throw new Error("Recovery recheck execution not found");
  }

  async claimEffectResumeDispatch(stepId: string): Promise<boolean> {
    const result = await this.db.exec(
      `UPDATE ce_steps SET status = 'dispatching', gmt_modified = ?
       WHERE step_id = ? AND step_type = 'agent_execution' AND status = 'created'
       AND EXISTS (SELECT 1 FROM ce_tasks WHERE ce_tasks.task_id = ce_steps.task_id AND ce_tasks.status = 'running')`,
      [this.db.dialect.now(), stepId],
    );
    return result.affectedRows === 1;
  }

  async settleEffectResumeSlot(taskId: string, slot: RecoveryEffectResumeSlot, jobEnded: (jobId: string) => Promise<boolean>): Promise<boolean> {
    if (slot.lastExecutionNo === undefined) return false;
    const steps = await this.db.query<{ status: string; error_code: string | null; bot_run_id: string | null; bot_response_json: string | null }>(
      "SELECT status, error_code, bot_run_id, bot_response_json FROM ce_steps WHERE task_id = ? AND step_type = 'agent_execution' AND step_no BETWEEN ? AND ?",
      [taskId, -slot.lastExecutionNo, -slot.firstExecutionNo]);
    for (const step of steps) {
      if (step.status === "succeeded" || step.status === "failed") continue;
      if (step.status !== "canceled") return false;
      if (step.error_code === "RECOVERY_TERMINATED_BEFORE_DISPATCH") continue;
      const cancellation = JSON.parse(step.bot_response_json ?? "{}").evolve_cancel?.status;
      if (cancellation === "remote_stopped") continue;
      if (cancellation !== "remote_stop_failed" || !step.bot_run_id || !await jobEnded(step.bot_run_id)) return false;
    }
    return this.db.transaction(async tx => {
      const raw = await this.lock(tx, taskId);
      if (!raw) return false;
      const state = JSON.parse(raw);
      const current = state.effectResumeSlot as RecoveryEffectResumeSlot | undefined;
      if (current?.firstExecutionNo !== slot.firstExecutionNo || current.lastExecutionNo !== slot.lastExecutionNo) return false;
      // Scheduler bookkeeping only. A concurrent business update reads the same locked row.
      delete state.effectResumeSlot;
      // Invalidate older business snapshots so they cannot restore the released slot.
      state.version++;
      await tx.exec("UPDATE ce_tasks SET config_json = ?, status = ? WHERE task_id = ?", [JSON.stringify(state), state.status, taskId]);
      return true;
    });
  }
  async find(taskId: string, options: { forBootstrap?: boolean } = {}): Promise<RecoveryStoredTask | null> {
    const row = await this.tasks.findTask(taskId);
    return row?.task_type === "session_recovery" ? this.hydrate(this.db, row.config_json, options.forBootstrap) : null;
  }
  async listTasks(query: import('../../services/repair/session-recovery/task-list.js').RecoveryTaskListQuery, ownerId: string) {
    const where = ["task_type = 'session_recovery'", 'user_id = ?'];
    const params: unknown[] = [ownerId];
    if (query.botId) { where.push('bot_id = ?'); params.push(query.botId); }
    if (query.env) { where.push("JSON_EXTRACT(config_json, '$.request.target.env') = ?"); params.push(query.env); }
    if (query.status) { where.push("JSON_EXTRACT(config_json, '$.status') = ?"); params.push(query.status); }
    const clause = where.join(' AND ');
    const [count] = await this.db.query<{ total: number }>(`SELECT COUNT(*) AS total FROM ce_tasks WHERE ${clause}`, params);
    const rows = await this.db.query<{ task_id: string; config_json: string; gmt_create: number | string }>(
      `SELECT task_id, config_json, gmt_create FROM ce_tasks WHERE ${clause} ORDER BY id DESC LIMIT ? OFFSET ?`,
      [...params, query.pageSize, (query.page - 1) * query.pageSize]);
    const page = rows.map(row => ({ row, value: JSON.parse(row.config_json) }));
    const phaseReports = query.includePresentation
      ? await this.phaseReportRefs(this.db, page.filter(({ value }) => value.artifactStorage === 2).map(({ row }) => row.task_id))
      : new Map<string, ArtifactRef[]>();
    // The opt-in projection reads phase reports without hydrating the tool audit.
    const items = await Promise.all(page.map(async ({ row, value }) => {
      const details = value.artifactStorage === 2 ? await this.get(value.detailsRef) as { judgment?: RecoveryStoredTask['judgment'] } : value;
      const presentation = query.includePresentation ? presentRecoveryTask(value, details.judgment,
        value.artifactStorage === 2 ? await this.readPhaseEvents(phaseReports.get(row.task_id) ?? []) : value.timeline ?? []) : {};
      return { taskId: value.taskId as string, botId: value.request.target.bot_id as string, entityId: value.request.target.entity_id as string,
        env: value.request.target.env as string, sessionKey: value.request.session_key as string,
        status: value.status as RecoveryStoredTask['status'], phase: value.phase as RecoveryStoredTask['phase'],
        effect: details.judgment?.effect ?? 'unknown' as const, createdAt: row.gmt_create, ...presentation };
    }));
    return { items, page: query.page, pageSize: query.pageSize, total: Number(count?.total ?? 0) };
  }
  private storageStatus(task: RecoveryStoredTask): string {
    // Preserve the business result in JSON while the scheduler still owns a permit.
    return task.effectResumeSlot && ["completed", "failed", "canceled"].includes(task.status) ? "settling" : task.status;
  }
  private async phaseReportRefs(db: IDatabase, taskIds: string[]): Promise<Map<string, ArtifactRef[]>> {
    const refs = new Map<string, ArtifactRef[]>();
    if (!taskIds.length) return refs;
    const placeholders = taskIds.map(() => "?").join(", ");
    const rows = await db.query<{ task_id: string; output_json: string }>(
      `SELECT task_id, output_json FROM ce_steps WHERE task_id IN (${placeholders}) AND step_type LIKE 'session_recovery%' AND output_json IS NOT NULL ORDER BY step_no`,
      taskIds);
    for (const row of rows) {
      const ref = JSON.parse(row.output_json);
      if (!ref.objectKey) continue;
      const task = refs.get(row.task_id) ?? [];
      task.push(ref);
      refs.set(row.task_id, task);
    }
    return refs;
  }
  private async readPhaseEvents(refs: ArtifactRef[]): Promise<RecoveryTimelineEvent[]> {
    const events: RecoveryTimelineEvent[] = [];
    for (const ref of refs) events.push(...(await this.get(ref) as { events: RecoveryTimelineEvent[] }).events);
    return events;
  }
  private eventRemark(eventId: string): string { return `session-recovery-event:${createHash("sha256").update(eventId).digest("hex")}`; }
  async listForEvent(eventId: string, ownerId: string, isAdmin: boolean): Promise<RecoveryStoredTask[]> {
    const rows = await this.db.query<{ task_id: string }>(
      "SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery' AND remark = ? AND (? = 1 OR user_id = ? OR JSON_EXTRACT(config_json, '$.shared') = true) ORDER BY id DESC LIMIT 100", [this.eventRemark(eventId), isAdmin ? 1 : 0, ownerId]);
    return (await Promise.all(rows.map(row => this.find(row.task_id)))).filter((task): task is RecoveryStoredTask => !!task);
  }
  async update(taskId: string, expectedVersion: number, change: (current: RecoveryStoredTask) => RecoveryTaskChange,
    effectResumeClaim?: RecoveryEffectConfigCheck): Promise<RecoveryStoredTask | null> {
    return this.db.transaction(async tx => {
      if (effectResumeClaim) {
        const current = await this.effectResumeConfig(effectResumeClaim.environment, tx, true);
        const expected = effectResumeClaim.config;
        if ("recheck" in effectResumeClaim) {
          // Scheduler slot reservations change the row version without changing policy.
          const policy = (config: RecoveryEffectResumeConfig | null) => config && [config.id, config.concurrency,
            config.intervalSeconds, config.windowSeconds, config.maxResumes].join(":");
          if (policy(current) !== policy(expected)) return null;
        } else if (!current || current.id !== effectResumeClaim.config.id || current.version !== effectResumeClaim.config.version) return null;
      }
      const raw = await this.lock(tx, taskId);
      if (!raw) return null;
      const current = await this.hydrate(tx, raw);
      if (current.version !== expectedVersion) return null;
      if (effectResumeClaim && "recheck" in effectResumeClaim) {
        const reported = await this.latestDiagnosisEffect(current, tx, true);
        if (!reported || reported.version !== effectResumeClaim.recheck.diagnosisVersion) return null;
        if (effectResumeClaim.recheck.register) {
          const [active] = await tx.query(
            "SELECT call_id FROM ce_repair_tool_calls WHERE task_id = ? AND status IN ('pending','executing') LIMIT 1", [taskId]);
          if (active) return null;
        }
      }
      const { resultToReport, ...updated } = change(structuredClone(current));
      if (updated.taskId !== taskId || updated.version !== expectedVersion + 1) throw new Error("Invalid recovery task transition");
      const priorEvents = current.timeline.filter(businessEvent);
      const events = updated.timeline.filter(businessEvent);
      if (priorEvents.some(event => !events.some(item => item.event_id === event.event_id && JSON.stringify(item) === JSON.stringify(event)))) throw new Error("Recovery timeline facts are immutable");
      const stored = JSON.parse(raw);
      const details = await this.put(taskId, this.details(updated), stored.detailsRef);
      const legacy = stored.artifactStorage !== 2;
      // Older failures were projected from config rather than saved as Step events.
      const stoppedIds = new Set((stored.stoppedResultDelivery ?? []).map((item: LegacyStoppedResult) => `result-stopped-${item.resultVersion}`));
      const fresh = legacy ? events : events.filter(event => stoppedIds.has(event.event_id) || !priorEvents.some(item => item.event_id === event.event_id));
      const snapshots: RecoveryTaskResult[] = legacy ? stored.pending_results ?? [] : [];
      if (resultToReport) {
        if (resultToReport.task_id !== taskId || resultToReport.event_id !== updated.request.event_id
          || resultToReport.result_version !== updated.version) throw new Error("Invalid recovery result snapshot");
        snapshots.push(resultToReport);
      }
      const results: Array<ArtifactRef & { resultVersion: number }> = [];
      for (const result of snapshots) {
        if (["waiting_effect", "completed", "failed", "canceled"].includes(result.status)
          && !results.some(item => item.resultVersion === result.result_version)) {
          results.push({ ...await this.put(taskId, result), resultVersion: result.result_version });
        }
      }
      if (effectResumeClaim && "recheck" in effectResumeClaim && effectResumeClaim.recheck.register) {
        const execution = updated.execution!;
        await createAgentExecutionStep(new EvolveRepository(tx), { taskId, executionId: execution.executionId,
          executionNo: updated.executionNo!, handler: "session_recovery", metadata: { effectRecheckRequestId: execution.effectRecheck!.requestId } });
      }
      await this.savePhase(tx, updated, details, fresh, results);
      await tx.exec("UPDATE ce_tasks SET config_json = ?, status = ?, gmt_modified = ? WHERE task_id = ?", [this.config(updated, details), this.storageStatus(updated), nowForDb(tx.dbType), taskId]);
      // Another dispatcher that scanned before this claim must discard its older slot count.
      if (effectResumeClaim && !("recheck" in effectResumeClaim)) await tx.exec("UPDATE ce_app_config SET version = version + 1 WHERE id = ?", [effectResumeClaim.config.id]);
      return updated;
    });
  }
  async appendNotification(taskId: string, event: RecoveryTimelineEvent): Promise<boolean> {
    if (event.kind !== "notification" || containsSecret(JSON.stringify(event))) throw new Error("Invalid recovery notification event");
    return this.appendEvent(taskId, event);
  }
  async appendResultOutcome(taskId: string, resultVersion: number, attempts: number, accepted: boolean): Promise<void> {
    if (!Number.isSafeInteger(resultVersion) || resultVersion < 1 || !Number.isSafeInteger(attempts) || attempts < 1) throw new Error("Invalid recovery callback outcome");
    await this.appendEvent(taskId, { event_id: accepted ? `result-${resultVersion}` : `result-stopped-${resultVersion}`,
      at_ms: Date.now(), kind: "result", actor: "service",
      summary: accepted ? "诊断服务已接收任务结果" : "诊断结果回传失败，已达重试上限，停止自动回传",
      payload: { result_version: resultVersion, attempts, ...(!accepted ? { delivery_status: "exhausted" } : {}) } });
  }
  private async appendEvent(taskId: string, event: RecoveryTimelineEvent): Promise<boolean> {
    return this.db.transaction(async tx => {
      const raw = await this.lock(tx, taskId);
      if (!raw) throw new Error("Recovery task not found during audit");
      const task = await this.hydrate(tx, raw);
      if (task.timeline.some(item => item.event_id === event.event_id)) return false;
      const stored = JSON.parse(raw);
      if (stored.artifactStorage !== 2) throw new Error("Recovery task requires artifact storage before audit");
      await this.savePhase(tx, task, stored.detailsRef, [event]);
      return true;
    });
  }
  private async lock(tx: IDatabase, taskId: string): Promise<string | undefined> {
    return (await tx.query<{ config_json: string }>(`SELECT config_json FROM ce_tasks WHERE task_id = ? AND task_type = 'session_recovery'${tx.dbType === "sqlite" ? "" : " FOR UPDATE"}`, [taskId]))[0]?.config_json;
  }
  private details(value: RecoveryStoredTask) {
    const auditedEvidence = new Set(value.timeline.filter(event => event.payload.raw_tool_call_id).map(event => event.payload.evidence_id));
    return { plan: value.plan, judgment: value.judgment, evidence: value.evidence.filter(item => !auditedEvidence.has(item.evidence_id)),
      ...(value.ownerRequest ? { ownerRequest: value.ownerRequest } : {}),
      ...(value.delivery ? { delivery: value.delivery } : {}), ...(value.interventionAuditComplete ? { interventionAuditComplete: true } : {}) };
  }
  private config(value: RecoveryStoredTask, detailsRef: ArtifactRef): string {
    const { timeline: _timeline, evidence: _evidence, interventionStatus: _intervention, interventionAuditComplete: _auditCoverage, sessionReadCoverage: _readCoverage, trajectoryIndexReads: _indexReads, plan, delivery: _delivery, judgment: _judgment, ownerRequest, ...state } = value;
    const json = JSON.stringify({ ...state, artifactStorage: 2, detailsRef,
      ...(ownerRequest ? { ownerRequest: { requestId: ownerRequest.requestId, revision: ownerRequest.revision, status: ownerRequest.status, reason: ownerRequest.reason, resumePhase: ownerRequest.resumePhase, createdAtMs: ownerRequest.createdAtMs } } : {}),
      plan: plan ? { digest: plan.digest, approvedAt: plan.approvedAt, actions: plan.actions.map(action => ({ actionId: action.actionId })) } : null });
    this.validateConfig(json);
    return json;
  }
  private validateConfig(json: string): void {
    if (Buffer.byteLength(json, "utf8") > 60000) throw new Error("Recovery current state exceeds Task config capacity");
    if (containsSecret(json)) throw new Error("Recovery task state must not contain credentials");
  }
  private async savePhase(tx: IDatabase, value: RecoveryStoredTask, details: ArtifactRef, added: RecoveryTimelineEvent[], results: Array<ArtifactRef & { resultVersion: number }> = []) {
    const stepNo = phases.indexOf(value.phase) + 1, stepId = `${value.taskId}-${stepNo}`;
    const row = (await tx.query<{ output_json: string | null }>("SELECT output_json FROM ce_steps WHERE step_id = ?", [stepId]))[0];
    const oldRef = row?.output_json ? JSON.parse(row.output_json) : null;
    const previous = oldRef?.objectKey ? await this.get(oldRef) as { events: RecoveryTimelineEvent[]; results?: ArtifactRef[] } : { events: [] };
    const report = await this.put(value.taskId, { events: [...previous.events, ...added.filter(event => !previous.events.some(item => item.event_id === event.event_id))], details, results: [...previous.results ?? [], ...results.map(({ objectKey, sha256, resultVersion }) => ({ objectKey, sha256, resultVersion }))] }, oldRef);
    if (!row) await new EvolveRepository(tx).createStep({ taskId: value.taskId, stepId, stepType: `session_recovery_${value.phase}`, stepNo, command: "session_recovery" });
    const status = value.status === "completed" ? "succeeded" : value.status === "failed" ? "failed" : value.status;
    await tx.exec("UPDATE ce_steps SET output_json = ?, summary = ?, status = ?, gmt_modified = ? WHERE step_id = ?",
      [JSON.stringify(report), value.judgment?.summary ?? value.plan?.summary ?? null, status, nowForDb(tx.dbType), stepId]);
    await tx.exec("UPDATE ce_steps SET status = 'succeeded' WHERE task_id = ? AND step_no > 0 AND step_no < ? AND status NOT IN ('succeeded','failed','canceled')", [value.taskId, stepNo]);
  }
  private async hydrate(db: IDatabase, raw: string, forBootstrap = false): Promise<RecoveryStoredTask> {
    const value = JSON.parse(raw);
    delete value.ownerCardSyncPending;
    const legacy = value.artifactStorage !== 2;
    const { artifactStorage: _storage, detailsRef, resultDelivery: _oldQueue, pending_results: _oldResults, stoppedResultDelivery, ...state } = value;
    const details = legacy ? { ...value, interventionAuditComplete: undefined } : await this.get(detailsRef) as Pick<RecoveryStoredTask, "plan" | "judgment" | "delivery" | "evidence" | "interventionAuditComplete">;
    const { pending_results: _legacyQueue, resultDelivery: _legacyDelivery, stoppedResultDelivery: _legacyStopped, ...businessDetails } = details as typeof details & Record<string, unknown>;
    const task = { ...state, ...businessDetails, timeline: legacy ? [...value.timeline ?? []] : [], evidence: details.evidence ?? [] } as RecoveryStoredTask;
    if (!legacy) {
      const refs = await this.phaseReportRefs(db, [value.taskId]);
      task.timeline.push(...await this.readPhaseEvents(refs.get(value.taskId) ?? []));
    }
    const audit = new AgentToolCallRepository(db, "session_recovery");
    const interventionCalls: AgentToolCall[] = [];
    let afterId = 0;
    for (;;) {
      const calls = await audit.listToolCalls(value.taskId, { ...(afterId ? { afterId } : {}), limit: 500 });
      interventionCalls.push(...calls);
      for (const call of calls) {
        afterId = call.id;
        if (!["succeeded", "failed", "unknown", "canceled"].includes(call.status)) continue;
        const result = (call.result ?? {}) as Record<string, unknown>, request = (call.request ?? {}) as Record<string, unknown>;
        const evidenceId = `evidence-${call.callId}`;
        if (!task.evidence.some(item => item.evidence_id === evidenceId)) task.evidence.push({ evidence_id: evidenceId, tool_call_id: call.callId, source: call.toolName, location: typeof result.source === "string" ? result.source : `tool-call:${call.callId}`, excerpt: JSON.stringify(result).slice(0, 2000) });
        const timestamp = typeof result.recorded_at_ms === "number" ? result.recorded_at_ms : typeof call.gmtCreate === "number" ? call.gmtCreate * 1000 : Date.parse(call.gmtCreate);
        if (legacy && task.timeline.some(event => event.event_id === call.callId || event.payload.tool_call_id === call.callId)) continue;
        task.timeline.push({ event_id: call.callId, at_ms: timestamp, kind: "operation", actor: "agent", summary: String(request.purpose ?? call.toolName), payload: {
          tool_call_id: call.callId, tool_name: call.toolName, request, result: { status: result.status, delivery_confirmed: result.delivery_confirmed, session_id: result.session_id, delivery_key: result.delivery_key,
            message: result.message, initial_message: result.initial_message, finished_at_ms: result.finished_at_ms, owner_request_id: result.owner_request_id, dry_run: result.dry_run, dispatched: result.dispatched, error: result.error }, status: call.status, evidence_id: evidenceId, raw_tool_call_id: call.callId } });
      }
      if (calls.length < 500) break;
    }
    const intervention = recoveryInterventionStatus(interventionCalls, task.delivery?.delivery, task.timeline);
    task.sessionReadCoverage = sessionReadCoverage(interventionCalls);
    if (forBootstrap) task.trajectoryIndexReads = trajectoryIndexReads(interventionCalls);
    task.interventionStatus = intervention === "none" && !task.interventionAuditComplete ? "unknown" : intervention;
    for (const result of (stoppedResultDelivery ?? []) as LegacyStoppedResult[]) {
      if (task.timeline.some(event => event.event_id === `result-stopped-${result.resultVersion}`)) continue;
      task.timeline.push({ event_id: `result-stopped-${result.resultVersion}`, at_ms: result.stoppedAtMs, kind: "result", actor: "service",
        summary: "诊断结果回传失败，已达重试上限，停止自动回传", payload: { result_version: result.resultVersion,
          attempts: result.attempts, delivery_status: "exhausted", result_ref: { objectKey: result.objectKey, sha256: result.sha256 } } });
    }
    task.timeline.sort((a, b) => a.at_ms - b.at_ms);
    return task;
  }
  private async put(taskId: string, payload: unknown, previous?: ArtifactRef | null): Promise<ArtifactRef> {
    const content = Buffer.from(JSON.stringify(payload));
    if (content.length > 1024 * 1024 || containsSecret(content.toString())) throw new Error("Invalid recovery artifact: capacity or credential constraint");
    const sha256 = createHash("sha256").update(content).digest("hex");
    if (previous?.sha256 === sha256) return previous;
    // The shared Evolve artifact credentials grant access under evolution/.
    const objectKey = `evolution/session-recovery/${taskId}/${sha256}.json`;
    await this.artifacts.putObject(objectKey, content, "application/json");
    return { objectKey, sha256 };
  }
  private async get(ref: ArtifactRef): Promise<unknown> {
    const object = await this.artifacts.getObject(ref.objectKey);
    if (object.content.length > 1024 * 1024 || createHash("sha256").update(object.content).digest("hex") !== ref.sha256) throw new Error("Recovery artifact digest mismatch");
    return JSON.parse(object.content.toString());
  }
  private limit(value: number): void { if (!Number.isSafeInteger(value) || value < 1 || value > 500) throw new Error("Invalid recovery query limit"); }
}
