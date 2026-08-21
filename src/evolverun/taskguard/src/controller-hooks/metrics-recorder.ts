/**
 * MetricsRecorder — records node lifecycle events as flow_metrics rows.
 *
 * Subscribes to node lifecycle events and writes metric rows via
 * FlowMetricsRepository. Best-effort: DB failures are logged but
 * never propagate to the caller.
 */
import { recordFailure } from "../fire-and-forget/index.js";
import type { IFlowMetricsRepository } from "../db/repositories/types.js";
import type {
  NodeLifecycleEvent,
  NodeLifecyclePayload,
} from "./types.js";

export class MetricsRecorder {
  constructor(private repo: IFlowMetricsRepository) {}

  onEvent(event: NodeLifecycleEvent, payload: NodeLifecyclePayload): void {
    switch (event) {
      case "node_duration_ms":
        if (payload.durationMs != null) {
          void this.repo
            .record(payload.flowId, payload.workflowId, payload.nodeId, "node_duration_ms", payload.durationMs, {
              executor: payload.executorType,
              attempt: String(payload.attempt),
            })
            .catch((err) => recordFailure("metrics-recorder.node_duration_ms", payload.flowId, payload.nodeId, err, "warn"));
        }
        break;

      case "node_token_usage_total":
        if (payload.usage?.totalTokens != null) {
          void this.repo
            .record(payload.flowId, payload.workflowId, payload.nodeId, "node_token_usage_total", payload.usage.totalTokens, {
              executor: payload.executorType,
            })
            .catch((err) => recordFailure("metrics-recorder.node_token_usage_total", payload.flowId, payload.nodeId, err, "warn"));
        }
        break;

      case "node_failed":
        void this.repo
          .record(payload.flowId, payload.workflowId, payload.nodeId, "node_failed", 1, {
            executor: payload.executorType,
            attempt: String(payload.attempt),
            error: payload.error?.substring(0, 200) ?? "unknown",
          })
          .catch((err) => recordFailure("metrics-recorder.node_failed", payload.flowId, payload.nodeId, err, "warn"));
        break;

      case "node_succeeded":
        void this.repo
          .record(payload.flowId, payload.workflowId, payload.nodeId, "node_succeeded", 1, {
            executor: payload.executorType,
            attempt: String(payload.attempt),
          })
          .catch((err) => recordFailure("metrics-recorder.node_succeeded", payload.flowId, payload.nodeId, err, "warn"));
        break;

      case "node_retry":
        void this.repo
          .record(payload.flowId, payload.workflowId, payload.nodeId, "node_retry", 1, {
            executor: payload.executorType,
            attempt: String(payload.attempt),
          })
          .catch((err) => recordFailure("metrics-recorder.node_retry", payload.flowId, payload.nodeId, err, "warn"));
        break;

      default:
        // node_started is informational; no metric row needed
        break;
    }
  }
}