import { resolveTaskControlPlaneEnvironment, type AgentControlPlaneEnvironment } from "../../services/repair/common/task-control-plane.js";
import { createHash } from "node:crypto";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { nowForDb } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import type { ObjectStore } from "../../services/object-storage/oss-object-store.js";
import type { RecoveryStoredTask, RecoveryTaskStore, RecoveryTimelineEvent } from "../../services/repair/session-recovery/task-contracts.js";
import { containsSecret } from "@avernet/clawweb-shared/server/services/redaction";
import { AgentToolCallRepository, type AgentToolCall } from "./agent-tool-call-repository.js";
import { recoveryInterventionStatus } from "../../services/repair/session-recovery/result.js";
import { sessionReadCoverage } from "../../services/repair/session-recovery/session-read-coverage.js";

export type { RecoveryStoredTask } from "../../services/repair/session-recovery/task-contracts.js";
export type RecoveryArtifactStore = Required<Pick<ObjectStore, "getObject" | "putObject">>;
type ArtifactRef = { objectKey: string; sha256: string };
type ResultDelivery = ArtifactRef & { resultVersion: number; attempts: number; retryAfterMs: number };
type StoppedResultDelivery = ResultDelivery & { stoppedAtMs: number };
type ResultDeliveryState = { resultDelivery: ResultDelivery[]; stoppedResultDelivery?: StoppedResultDelivery[] };
const RESULT_RETRY_DELAYS_MS = [60000, 300000, 900000] as const;
const MAX_RESULT_ATTEMPTS = RESULT_RETRY_DELAYS_MS.length + 1;
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
  async listForMaintenance(limit: number, afterTaskId = ""): Promise<RecoveryStoredTask[]> {
    this.limit(limit);
    const rows = await this.db.query<{ task_id: string }>(
      "SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery' AND task_id > ? AND status IN ('pending','running','waiting_owner','waiting_effect') ORDER BY task_id ASC LIMIT ?", [afterTaskId, limit]);
    return (await Promise.all(rows.map(row => this.find(row.task_id)))).filter((task): task is RecoveryStoredTask => !!task);
  }
  async listForReporting(limit: number, afterTaskId: string, controlPlaneEnvironment: AgentControlPlaneEnvironment): Promise<{ tasks: RecoveryStoredTask[]; nextTaskId: string }> {
    this.limit(limit);
    const result: RecoveryStoredTask[] = [];
    // One compact candidate page per tick, not an unbounded scan to fill the batch.
    const rows = await this.db.query<{ task_id: string; config_json: string }>(
      `SELECT task_id, config_json FROM ce_tasks WHERE task_type = 'session_recovery' AND task_id > ? AND (
        (config_json LIKE '%"resultDelivery":%' AND config_json NOT LIKE '%"resultDelivery":[]%')
        OR (config_json NOT LIKE '%"artifactStorage":2%' AND config_json NOT LIKE '%"pending_results":[]%'))
        ORDER BY task_id ASC LIMIT ?`, [afterTaskId, limit]);
    for (const row of rows) {
      let state = JSON.parse(row.config_json);
      if (resolveTaskControlPlaneEnvironment(state) !== controlPlaneEnvironment) continue;
      if (state.artifactStorage !== 2) {
        await this.update(row.task_id, state.version, value => ({ ...value, version: value.version + 1 }));
        state = JSON.parse((await this.tasks.findTask(row.task_id))!.config_json);
      }
      const first = (state.resultDelivery as ResultDelivery[] | undefined)?.[0];
      if (!first || first.retryAfterMs > Date.now()) continue;
      if (first.attempts >= MAX_RESULT_ATTEMPTS) {
        await this.claimResult(row.task_id, first.resultVersion); // Retire historical/crashed exhausted attempts without HTTP or OSS.
        continue;
      }
      const task = await this.find(row.task_id);
      if (task) result.push(task);
    }
    return { tasks: result, nextTaskId: rows.length === limit ? rows[rows.length - 1].task_id : "" };
  }
  async find(taskId: string): Promise<RecoveryStoredTask | null> {
    const row = await this.tasks.findTask(taskId);
    return row?.task_type === "session_recovery" ? this.hydrate(this.db, row.config_json) : null;
  }
  private eventRemark(eventId: string): string { return `session-recovery-event:${createHash("sha256").update(eventId).digest("hex")}`; }
  async listForEvent(eventId: string, ownerId: string, isAdmin: boolean): Promise<RecoveryStoredTask[]> {
    const rows = await this.db.query<{ task_id: string }>(
      "SELECT task_id FROM ce_tasks WHERE task_type = 'session_recovery' AND remark = ? AND (? = 1 OR user_id = ? OR JSON_EXTRACT(config_json, '$.shared') = true) ORDER BY id DESC LIMIT 100", [this.eventRemark(eventId), isAdmin ? 1 : 0, ownerId]);
    return (await Promise.all(rows.map(row => this.find(row.task_id)))).filter((task): task is RecoveryStoredTask => !!task);
  }
  async update(taskId: string, expectedVersion: number, change: (current: RecoveryStoredTask) => RecoveryStoredTask): Promise<RecoveryStoredTask | null> {
    return this.db.transaction(async tx => {
      const raw = await this.lock(tx, taskId);
      if (!raw) return null;
      const current = await this.hydrate(tx, raw);
      if (current.version !== expectedVersion) return null;
      const updated = change(structuredClone(current));
      if (updated.taskId !== taskId || updated.version !== expectedVersion + 1) throw new Error("Invalid recovery task transition");
      const priorEvents = current.timeline.filter(businessEvent);
      const events = updated.timeline.filter(businessEvent);
      if (priorEvents.some(event => !events.some(item => item.event_id === event.event_id && JSON.stringify(item) === JSON.stringify(event)))) throw new Error("Recovery timeline facts are immutable");
      const stored = JSON.parse(raw);
      const details = await this.put(taskId, this.details(updated), stored.detailsRef);
      const legacy = stored.artifactStorage !== 2;
      const fresh = legacy ? events : events.filter(event => !priorEvents.some(item => item.event_id === event.event_id));
      const deliveries: ResultDelivery[] = stored.resultDelivery ?? [];
      const added: ResultDelivery[] = [];
      for (const result of updated.pending_results) {
        if (!["waiting_effect", "completed", "failed", "canceled"].includes(result.status)
          || deliveries.some(item => item.resultVersion === result.result_version)) continue;
        added.push({ ...await this.put(taskId, result), resultVersion: result.result_version, attempts: 0, retryAfterMs: 0 });
      }
      deliveries.push(...added);
      deliveries.sort((a, b) => a.resultVersion - b.resultVersion);
      await this.savePhase(tx, updated, details, fresh, added);
      await tx.exec("UPDATE ce_tasks SET config_json = ?, status = ?, gmt_modified = ? WHERE task_id = ?", [this.config(updated, details, deliveries, stored.stoppedResultDelivery), updated.status, nowForDb(tx.dbType), taskId]);
      return updated;
    });
  }
  async claimResult(taskId: string, resultVersion: number): Promise<number | null> {
    const row = (await this.db.query<{ config_json: string }>("SELECT config_json FROM ce_tasks WHERE task_id = ? AND task_type = 'session_recovery'", [taskId]))[0];
    if (!row) return null;
    const stored = JSON.parse(row.config_json), entry = (stored.resultDelivery as ResultDelivery[] | undefined)?.[0];
    if (!entry || entry.resultVersion !== resultVersion || entry.retryAfterMs > Date.now()) return null;
    const version = stored.version;
    const exhausted = entry.attempts >= MAX_RESULT_ATTEMPTS;
    if (exhausted) this.stopResult(stored, entry);
    else {
      entry.attempts += 1;
      // Fence an in-flight/crashed sender beyond the reporter's maximum 60s timeout.
      // A known failure below replaces this grace period with its retry delay.
      entry.retryAfterMs = Date.now() + Math.max(120000, RESULT_RETRY_DELAYS_MS[entry.attempts - 1] ?? 0);
    }
    stored.version += 1;
    const json = JSON.stringify(stored); this.validateConfig(json);
    // The task's unique key bounds this update to one row; no network/object I/O in the reservation.
    const changed = await this.db.exec("UPDATE ce_tasks SET config_json = ?, gmt_modified = ? WHERE task_id = ? AND JSON_EXTRACT(config_json, '$.version') = ?",
      [json, nowForDb(this.db.dbType), taskId, version]);
    return changed.affectedRows === 1 && !exhausted ? entry.attempts : null;
  }
  async acknowledgeResult(taskId: string, resultVersion: number, attempt: number): Promise<void> {
    await this.updateDelivery(taskId, resultVersion, attempt, true);
  }
  async appendNotification(taskId: string, event: RecoveryTimelineEvent): Promise<boolean> {
    if (event.kind !== "notification" || containsSecret(JSON.stringify(event))) throw new Error("Invalid recovery notification event");
    return this.db.transaction(async tx => {
      const raw = await this.lock(tx, taskId);
      if (!raw) throw new Error("Recovery task not found during notification");
      const task = await this.hydrate(tx, raw);
      if (task.timeline.some(item => item.event_id === event.event_id)) return false;
      const stored = JSON.parse(raw);
      if (stored.artifactStorage !== 2) throw new Error("Recovery task requires artifact storage before notification");
      await this.savePhase(tx, task, stored.detailsRef, [event]);
      return true;
    });
  }
  async failResult(taskId: string, resultVersion: number, attempt: number): Promise<void> {
    await this.updateDelivery(taskId, resultVersion, attempt, false);
  }
  private async updateDelivery(taskId: string, resultVersion: number, attempt: number, accepted: boolean): Promise<void> {
    await this.db.transaction(async tx => {
      const raw = await this.lock(tx, taskId);
      if (!raw) throw new Error("Recovery task not found during result delivery");
      const stored = JSON.parse(raw), deliveries: ResultDelivery[] = stored.resultDelivery ?? [];
      const entry = deliveries.find(item => item.resultVersion === resultVersion);
      if (!entry) return; // An identical successful acknowledgment is idempotent.
      if (entry.attempts !== attempt) return; // A late response cannot settle a successor's reservation.
      if (deliveries[0] !== entry) throw new Error("Recovery results must be delivered in order");
      if (accepted) {
        const task = await this.hydrate(tx, raw);
        await this.savePhase(tx, task, stored.detailsRef, [{ event_id: `result-${resultVersion}`, at_ms: Date.now(),
          kind: "result", actor: "service", summary: "诊断服务已接收任务结果", payload: { result_version: resultVersion, attempts: entry.attempts } }]);
        deliveries.shift();
      } else if (entry.attempts >= MAX_RESULT_ATTEMPTS) this.stopResult(stored, entry);
      else entry.retryAfterMs = Date.now() + RESULT_RETRY_DELAYS_MS[entry.attempts - 1];
      stored.resultDelivery = deliveries;
      stored.version += 1;
      const json = JSON.stringify(stored);
      this.validateConfig(json);
      await tx.exec("UPDATE ce_tasks SET config_json = ?, gmt_modified = ? WHERE task_id = ?", [json, nowForDb(tx.dbType), taskId]);
    });
  }
  private stopResult(stored: ResultDeliveryState, entry: ResultDelivery): void {
    stored.stoppedResultDelivery = [...stored.stoppedResultDelivery ?? [], { ...entry, stoppedAtMs: Date.now() }];
    stored.resultDelivery.splice(stored.resultDelivery.indexOf(entry), 1);
  }
  private async lock(tx: IDatabase, taskId: string): Promise<string | undefined> {
    return (await tx.query<{ config_json: string }>(`SELECT config_json FROM ce_tasks WHERE task_id = ? AND task_type = 'session_recovery'${tx.dbType === "sqlite" ? "" : " FOR UPDATE"}`, [taskId]))[0]?.config_json;
  }
  private details(value: RecoveryStoredTask) {
    const auditedEvidence = new Set(value.timeline.filter(event => event.payload.raw_tool_call_id).map(event => event.payload.evidence_id));
    return { plan: value.plan, judgment: value.judgment, evidence: value.evidence.filter(item => !auditedEvidence.has(item.evidence_id)),
      ...(value.delivery ? { delivery: value.delivery } : {}), ...(value.interventionAuditComplete ? { interventionAuditComplete: true } : {}) };
  }
  private config(value: RecoveryStoredTask, detailsRef: ArtifactRef, resultDelivery: ResultDelivery[] = [], stoppedResultDelivery: StoppedResultDelivery[] = []): string {
    const { timeline: _timeline, evidence: _evidence, pending_results: _results, interventionStatus: _intervention, interventionAuditComplete: _auditCoverage, sessionReadCoverage: _readCoverage, plan, delivery: _delivery, judgment: _judgment, ...state } = value;
    const json = JSON.stringify({ ...state, artifactStorage: 2, detailsRef, resultDelivery,
      ...(stoppedResultDelivery.length ? { stoppedResultDelivery } : {}), plan: plan ? { digest: plan.digest, approvedAt: plan.approvedAt, actions: plan.actions.map(action => ({ actionId: action.actionId })) } : null });
    this.validateConfig(json);
    return json;
  }
  private validateConfig(json: string): void {
    if (Buffer.byteLength(json, "utf8") > 60000) throw new Error("Recovery current state exceeds Task config capacity");
    if (containsSecret(json)) throw new Error("Recovery task state must not contain credentials");
  }
  private async savePhase(tx: IDatabase, value: RecoveryStoredTask, details: ArtifactRef, added: RecoveryTimelineEvent[], results: ResultDelivery[] = []) {
    const stepNo = phases.indexOf(value.phase) + 1, stepId = `${value.taskId}-${stepNo}`;
    const row = (await tx.query<{ output_json: string | null }>("SELECT output_json FROM ce_steps WHERE step_id = ?", [stepId]))[0];
    const oldRef = row?.output_json ? JSON.parse(row.output_json) : null;
    const previous = oldRef?.objectKey ? await this.get(oldRef) as { events: RecoveryTimelineEvent[]; results?: ArtifactRef[] } : { events: [] };
    const report = await this.put(value.taskId, { events: [...previous.events, ...added], details, results: [...previous.results ?? [], ...results.map(({ objectKey, sha256, resultVersion }) => ({ objectKey, sha256, resultVersion }))] }, oldRef);
    if (!row) await new EvolveRepository(tx).createStep({ taskId: value.taskId, stepId, stepType: `session_recovery_${value.phase}`, stepNo, command: "session_recovery" });
    const status = value.status === "completed" ? "succeeded" : value.status === "failed" ? "failed" : value.status;
    await tx.exec("UPDATE ce_steps SET output_json = ?, summary = ?, status = ?, gmt_modified = ? WHERE step_id = ?",
      [JSON.stringify(report), value.judgment?.summary ?? value.plan?.summary ?? null, status, nowForDb(tx.dbType), stepId]);
    await tx.exec("UPDATE ce_steps SET status = 'succeeded' WHERE task_id = ? AND step_no > 0 AND step_no < ? AND status NOT IN ('succeeded','failed','canceled')", [value.taskId, stepNo]);
  }
  private async hydrate(db: IDatabase, raw: string): Promise<RecoveryStoredTask> {
    const value = JSON.parse(raw);
    if (value.artifactStorage !== 2) return { ...value, interventionStatus: undefined, interventionAuditComplete: undefined };
    const { artifactStorage: _storage, detailsRef, resultDelivery, stoppedResultDelivery, ...state } = value;
    const details = await this.get(detailsRef) as Pick<RecoveryStoredTask, "plan" | "judgment" | "delivery" | "evidence" | "interventionAuditComplete">;
    const task = { ...state, ...details, timeline: [], evidence: details.evidence ?? [], pending_results: [] } as RecoveryStoredTask;
    const steps = await db.query<{ output_json: string }>("SELECT output_json FROM ce_steps WHERE task_id = ? AND step_type LIKE 'session_recovery%' AND output_json IS NOT NULL ORDER BY step_no", [value.taskId]);
    for (const step of steps) {
      const ref = JSON.parse(step.output_json);
      if (ref.objectKey) task.timeline.push(...(await this.get(ref) as { events: RecoveryTimelineEvent[] }).events);
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
        task.timeline.push({ event_id: call.callId, at_ms: timestamp, kind: "operation", actor: "agent", summary: String(request.purpose ?? call.toolName), payload: {
          tool_call_id: call.callId, request, result: { delivery_confirmed: result.delivery_confirmed, session_id: result.session_id, delivery_key: result.delivery_key,
            message: result.message, initial_message: result.initial_message, dry_run: result.dry_run, dispatched: result.dispatched, error: result.error }, status: call.status, evidence_id: evidenceId, raw_tool_call_id: call.callId } });
      }
      if (calls.length < 500) break;
    }
    const intervention = recoveryInterventionStatus(interventionCalls, task.delivery?.delivery);
    task.sessionReadCoverage = sessionReadCoverage(interventionCalls);
    task.interventionStatus = intervention === "none" && !task.interventionAuditComplete ? "unknown" : intervention;
    for (const result of (resultDelivery ?? []) as ResultDelivery[]) {
      task.pending_results.push(await this.get(result) as RecoveryStoredTask["pending_results"][number]);
    }
    for (const result of (stoppedResultDelivery ?? []) as StoppedResultDelivery[]) {
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
