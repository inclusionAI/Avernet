import { logRepairEvent, repairLogError } from "../common/logging.js";
import type { AgentControlPlaneEnvironment } from "../common/task-control-plane.js";
import type { RecoveryTaskReporter } from "./task-service.js";
import type { RecoveryTaskResult, RecoveryTaskStore } from "./task-contracts.js";
import { RecoveryResultCallbackError } from "./errors.js";

const RETRY_DELAYS_MS = [60_000, 300_000, 900_000] as const;
type Delivery = { key: string; result: RecoveryTaskResult; attempt: number; timer?: ReturnType<typeof setTimeout> };

/** Immediate post-commit delivery with finite, process-local retries. No database polling or restart replay. */
export class RecoveryResultDelivery {
  private readonly pending = new Map<string, Delivery>();
  private readonly inFlight = new Set<Promise<void>>();
  private disposed = false;
  constructor(private readonly deps: { reporter: RecoveryTaskReporter; tasks: Pick<RecoveryTaskStore, "appendResultOutcome">;
    environment: AgentControlPlaneEnvironment; now: () => number }) {}

  send(result: RecoveryTaskResult): void {
    const key = `${result.task_id}:${result.result_version}`;
    if (this.disposed || this.pending.has(key)) return;
    const delivery: Delivery = { key, result: structuredClone(result), attempt: 0 };
    this.pending.set(key, delivery);
    this.start(delivery);
  }
  private start(delivery: Delivery): void {
    delivery.timer = undefined;
    const work = this.attempt(delivery).finally(() => { this.inFlight.delete(work); });
    this.inFlight.add(work);
  }
  private async attempt(delivery: Delivery): Promise<void> {
    const { result } = delivery;
    const attempt = ++delivery.attempt;
    const startedAt = this.deps.now();
    const context = { environment: this.deps.environment, taskId: result.task_id, eventId: result.event_id,
      stateVersion: result.result_version, attempt };
    let accepted = false;
    try {
      await this.deps.reporter.report(result);
      accepted = true;
      logRepairEvent("session-recovery", "session_recovery_reporting", "callback_succeeded", {
        ...context, outcome: "succeeded", elapsedMs: this.deps.now() - startedAt });
    } catch (error) {
      const delay = RETRY_DELAYS_MS[attempt - 1];
      const retry = !this.disposed && delay !== undefined;
      const failure = error instanceof RecoveryResultCallbackError ? error.logFields
        : { errorCode: "RECOVERY_RESULT_CALLBACK_FAILED", reason: "结果回传失败（非 HTTP 适配器异常）" };
      logRepairEvent("session-recovery", "session_recovery_reporting", "callback_failed", {
        ...context, ...failure, outcome: "failed", elapsedMs: this.deps.now() - startedAt,
        ...(retry ? { retryAtMs: this.deps.now() + delay } : {}) });
      if (retry) {
        delivery.timer = setTimeout(() => this.start(delivery), delay).unref();
        return;
      }
      // Shutdown cancels remaining retry work; it does not pretend the retry budget was exhausted.
      if (this.disposed && delay !== undefined) { this.pending.delete(delivery.key); return; }
    }
    try {
      await this.deps.tasks.appendResultOutcome(result.task_id, result.result_version, attempt, accepted);
    } catch (error) {
      // HTTP acceptance and audit persistence are separate facts. Do not resend an accepted callback.
      logRepairEvent("session-recovery", "session_recovery_reporting", "callback_audit_failed", {
        ...context, status: accepted ? "accepted" : "exhausted", outcome: "failed", ...repairLogError(error) });
    } finally { this.pending.delete(delivery.key); }
  }
  async dispose(): Promise<void> {
    this.disposed = true;
    for (const delivery of this.pending.values()) if (delivery.timer) clearTimeout(delivery.timer);
    await Promise.all(this.inFlight);
    this.pending.clear();
  }
}
