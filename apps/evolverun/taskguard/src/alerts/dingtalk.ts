/**
 * DingTalk webhook notification sender.
 *
 * Formats alert content as markdown and POSTs to configured webhook URLs.
 * Best-effort: network failures are logged but never thrown.
 */
import type { DingTalkConfig } from "../config/types.js";

/** Result of sending DingTalk notifications. */
export type DingTalkSendResult = {
  /** Number of webhooks that accepted the notification. */
  sent: number;
  /** Number of webhooks that failed. */
  failed: number;
  /** Per-webhook error messages. */
  errors: string[];
};

/** Payload for a single DingTalk alert message. */
export type DingTalkAlertPayload = {
  /** Workflow ID that triggered the alert. */
  workflowId: string;
  /** Flow execution ID. */
  flowId: string;
  /** Alert severity. */
  severity: "warning" | "critical";
  /** Markdown-formatted alert body. */
  markdown: string;
};

const DINGTALK_TIMEOUT_MS = 5000;

/**
 * Send a DingTalk markdown notification to all configured webhooks.
 *
 * Uses the DingTalk robot webhook API format:
 * POST with JSON body `{ msgtype: "markdown", markdown: { title, text } }`.
 */
export async function sendDingTalkAlert(
  config: DingTalkConfig,
  payload: DingTalkAlertPayload,
): Promise<DingTalkSendResult> {
  const { webhooks, keywords } = config;
  if (webhooks.length === 0) {
    return { sent: 0, failed: 0, errors: [] };
  }

  const title = `[ClawFlow Alert] ${payload.severity.toUpperCase()} — ${payload.workflowId}`;
  const keywordLine = keywords.length > 0 ? `\n\nKeywords: ${keywords.join(" ")}` : "";
  const text = `## ${title}\n\nFlow: ${payload.flowId}\n\n${payload.markdown}${keywordLine}`;

  const body = JSON.stringify({
    msgtype: "markdown",
    markdown: { title, text },
  });

  let sent = 0;
  let failed = 0;
  const errors: string[] = [];

  const results = await Promise.allSettled(
    webhooks.map(async (url) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), DINGTALK_TIMEOUT_MS);
      try {
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
          signal: controller.signal,
        });
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}: ${res.statusText}`);
        }
      } finally {
        clearTimeout(timer);
      }
    }),
  );

  for (const result of results) {
    if (result.status === "fulfilled") {
      sent++;
    } else {
      failed++;
      const msg = result.reason instanceof Error ? result.reason.message : String(result.reason);
      errors.push(msg);
      console.warn(`[dingtalk] Failed to send alert: ${msg}`);
    }
  }

  return { sent, failed, errors };
}