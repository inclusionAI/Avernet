/**
 * Community default alert notifier — basic DingTalk webhook.
 * Corp extensions can override with enterprise DingTalk or other channels.
 */
import { sendDingTalkAlert } from "../alerts/dingtalk.js";
import type { DingTalkConfig } from "../config/types.js";

export function createCommunityNotifier(config: unknown) {
  const cfg = config as { dingtalk?: { webhooks?: string[] } };
  const dingtalkConfig: DingTalkConfig = {
    webhooks: cfg?.dingtalk?.webhooks ?? [],
    keywords: [],
  };

  return {
    async send(message: string, _webhookUrl?: string) {
      if (dingtalkConfig.webhooks.length === 0) return;
      return sendDingTalkAlert(
        dingtalkConfig,
        {
          workflowId: "taskguard",
          flowId: "notification",
          severity: "warning" as const,
          markdown: message,
        },
      );
    },
  };
}
