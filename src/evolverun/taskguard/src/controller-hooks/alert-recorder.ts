/**
 * AlertRecorder — records failure events as triggered_alerts rows.
 *
 * Listens for node_failure_exhausted, output_contract_failed,
 * and required_hook_failed events and persists them via
 * TriggeredAlertRepository. Best-effort DB writes.
 */
import { recordFailure } from "../fire-and-forget/index.js";
import type { ITriggeredAlertRepository } from "../db/repositories/types.js";
import type {
  NodeLifecycleEvent,
  NodeLifecyclePayload,
} from "./types.js";

/** Alert rule names that can be triggered. */
const ALERT_RULES = {
  node_failure_exhausted: "node_failure_exhausted",
  output_contract_failed: "output_contract_failed",
  required_hook_failed: "required_hook_failed",
} as const;

export class AlertRecorder {
  constructor(private repo: ITriggeredAlertRepository) {}

  /**
   * Record a threshold breach as a triggered alert.
   * Called directly (not via onEvent) since alert conditions
   * require external evaluation (analysis/threshold checks).
   */
  recordAlert(
    flowId: string,
    workflowId: string,
    nodeId: string | undefined,
    alertRule: string,
    severity: "warning" | "critical",
    message: string,
  ): void {
    void this.repo
      .record(flowId, workflowId, nodeId ?? null, alertRule, severity, message)
      .catch((err) => recordFailure("alert-recorder.recordAlert", flowId, nodeId, err, "warn"));
  }

  /**
   * Handle lifecycle events that directly trigger alerts.
   * Currently only `node_failed` when retries are exhausted
   * triggers an alert (caller should invoke this only on final failure).
   */
  onEvent(event: NodeLifecycleEvent, payload: NodeLifecyclePayload): void {
    if (event === "node_failed") {
      const msg = payload.error
        ? `Node ${payload.nodeId} failed (attempt ${payload.attempt}): ${payload.error.substring(0, 300)}`
        : `Node ${payload.nodeId} failed (attempt ${payload.attempt})`;

      void this.repo
        .record(
          payload.flowId,
          payload.workflowId,
          payload.nodeId,
          ALERT_RULES.node_failure_exhausted,
          "warning",
          msg,
        )
        .catch((err) => recordFailure("alert-recorder.onEvent", payload.flowId, payload.nodeId, err, "warn"));
    }
  }
}