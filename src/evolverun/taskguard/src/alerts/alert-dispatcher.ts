/**
 * AlertDispatcher — orchestrates alert persistence and DingTalk notifications.
 *
 * Receives analysis results (breaches) and node failure events,
 * persists them via TriggeredAlertRepository, and sends consolidated
 * DingTalk notifications when configured.
 */
import type { AlertingConfig } from "../config/types.js";
import type { HealthReport, ThresholdBreach } from "../analysis/types.js";
import type { ITriggeredAlertRepository } from "../db/repositories/types.js";
import { sendDingTalkAlert } from "./dingtalk.js";
import { buildMergedAlertMarkdown } from "./markdown-formatter.js";

/** A node failure event to dispatch. */
export type NodeFailureEvent = {
  nodeId: string;
  flowId: string;
  workflowId: string;
  error: string;
  attempt: number;
};

/** Result of dispatching alerts. */
export type DispatchResult = {
  /** Number of DB alert records created. */
  dbRecords: number;
  /** Number of DingTalk webhooks notified. */
  dingtalkSent: number;
  /** Number of DingTalk sends that failed. */
  dingtalkFailed: number;
};

export class AlertDispatcher {
  constructor(
    private repo: ITriggeredAlertRepository | null,
    private config: AlertingConfig,
  ) {}

  /**
   * Dispatch alerts from a health report (produced by WorkflowAnalyzer + ThresholdChecker).
   *
   * 1. Persists each breach as a triggered_alert via the repository.
   * 2. Sends a consolidated DingTalk notification with all breach details.
   */
  async dispatchBreaches(report: HealthReport): Promise<DispatchResult> {
    if (!this.config.enabled) {
      return { dbRecords: 0, dingtalkSent: 0, dingtalkFailed: 0 };
    }

    let dbRecords = 0;

    // Persist each breach
    if (this.repo && report.hasBreaches) {
      for (const breach of report.breaches) {
        try {
          const ok = await this.repo.record(
            report.result.flowId,
            report.result.workflowId,
            undefined,
            `threshold_${breach.metric}`,
            breach.severity,
            breach.message,
          );
          if (ok) dbRecords++;
        } catch (error) {
          const msg = error instanceof Error ? error.message : String(error);
          console.warn(`[alerts] Failed to persist breach alert: ${msg}`);
        }
      }
    }

    // Send DingTalk notification
    let dingtalkSent = 0;
    let dingtalkFailed = 0;
    if (report.hasBreaches && this.config.dingtalk.webhooks.length > 0) {
      const markdown = buildMergedAlertMarkdown(report.breaches);
      const maxSeverity = report.breaches.find((b) => b.severity === "critical")
        ? "critical" as const
        : "warning" as const;

      const result = await sendDingTalkAlert(this.config.dingtalk, {
        workflowId: report.result.workflowId,
        flowId: report.result.flowId,
        severity: maxSeverity,
        markdown,
      });
      dingtalkSent = result.sent;
      dingtalkFailed = result.failed;
    }

    return { dbRecords, dingtalkSent, dingtalkFailed };
  }

  /**
   * Dispatch a node failure alert.
   *
   * Persists the failure as a triggered_alert and optionally sends
   * a DingTalk notification for critical node failures.
   */
  async dispatchNodeFailure(event: NodeFailureEvent): Promise<DispatchResult> {
    if (!this.config.enabled || !this.config.onNodeFailure) {
      return { dbRecords: 0, dingtalkSent: 0, dingtalkFailed: 0 };
    }

    let dbRecords = 0;
    if (this.repo) {
      try {
        const msg = `Node ${event.nodeId} failed (attempt ${event.attempt}): ${event.error.substring(0, 300)}`;
        const ok = await this.repo.record(
          event.flowId,
          event.workflowId,
          event.nodeId,
          "node_failure_exhausted",
          "warning",
          msg,
        );
        if (ok) dbRecords++;
      } catch (error) {
        const emsg = error instanceof Error ? error.message : String(error);
        console.warn(`[alerts] Failed to persist node failure alert: ${emsg}`);
      }
    }

    let dingtalkSent = 0;
    let dingtalkFailed = 0;
    if (this.config.dingtalk.webhooks.length > 0) {
      const markdown = buildMergedAlertMarkdown([], [event]);
      const result = await sendDingTalkAlert(this.config.dingtalk, {
        workflowId: event.workflowId,
        flowId: event.flowId,
        severity: "warning",
        markdown,
      });
      dingtalkSent = result.sent;
      dingtalkFailed = result.failed;
    }

    return { dbRecords, dingtalkSent, dingtalkFailed };
  }

  /** Update the alerting config (e.g., on hot-reload). */
  updateConfig(config: AlertingConfig): void {
    this.config = config;
  }
}