/**
 * Markdown formatter for DingTalk alert notifications.
 *
 * Builds consolidated markdown messages from multiple threshold breaches
 * and node failure events for DingTalk webhook delivery.
 */
import type { ThresholdBreach } from "../analysis/types.js";

/** A node failure event to include in the alert message. */
export type NodeFailureAlert = {
  nodeId: string;
  flowId: string;
  workflowId: string;
  error: string;
  attempt: number;
};

const SEVERITY_EMOJI: Record<string, string> = {
  critical: "🔴",
  warning: "🟡",
};

/**
 * Build a merged markdown message from threshold breaches and/or node failures.
 * Consolidates multiple alerts into a single message to reduce notification noise.
 */
export function buildMergedAlertMarkdown(
  breaches: ThresholdBreach[],
  nodeFailures?: NodeFailureAlert[],
): string {
  const sections: string[] = [];

  if (breaches.length > 0) {
    const lines: string[] = ["### Threshold Breaches\n"];
    for (const breach of breaches) {
      const emoji = SEVERITY_EMOJI[breach.severity] ?? "⚠️";
      lines.push(`${emoji} **${breach.metric}** — ${breach.severity.toUpperCase()}`);
      lines.push(`   - Value: ${breach.value.toFixed(3)}`);
      lines.push(`   - Threshold: ${breach.threshold.toFixed(3)}`);
      lines.push(`   - ${breach.message}`);
      lines.push("");
    }
    sections.push(lines.join("\n"));
  }

  if (nodeFailures && nodeFailures.length > 0) {
    const lines: string[] = ["### Node Failures\n"];
    for (const nf of nodeFailures) {
      lines.push(`❌ **${nf.nodeId}** (attempt ${nf.attempt})`);
      lines.push(`   - Flow: ${nf.flowId}`);
      lines.push(`   - Error: ${nf.error.substring(0, 200)}`);
      lines.push("");
    }
    sections.push(lines.join("\n"));
  }

  if (sections.length === 0) {
    return "✅ All metrics within normal ranges.";
  }

  return sections.join("\n---\n\n");
}