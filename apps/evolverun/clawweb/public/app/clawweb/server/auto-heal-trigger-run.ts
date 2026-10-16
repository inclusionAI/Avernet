/**
 * AutoHeal trigger-run helper — invoked by AutoHealDeployService after a deploy.
 *
 * Mirrors the POST /api/auto-heal/run behavior in the standalone clawweb:
 *   - Resolves the auto-heal botId from config.
 *   - Sends a structured run_workflow command via BaaS intervention.
 *   - Returns the BaaS messageId as the new flow run id.
 */
import { sendIntervention } from "@avernet/clawevolve/server/services/baas-intervention";
import { resolveAutoHealConfig } from "@avernet/clawweb-shared/server/db";

export function createAutoHealTriggerRun(
  getUserName: () => string = () => "ClawWeb AutoHeal",
): (workflowId: string, actorId: string) => Promise<string | null> {
  return async (workflowId: string, actorId: string): Promise<string | null> => {
    const autoHealConfig = resolveAutoHealConfig();
    if (!autoHealConfig.botId) {
      throw new Error("AUTO_HEAL_BOT_ID is not configured");
    }

    const userName = getUserName();
    const payload = {
      action: "run_workflow" as const,
      workflowId,
      params: {},
      triggeredBy: "clawweb-dashboard",
      userId: actorId,
      userName,
      source: "auto-heal",
    };

    const message = `🚀 [自动修复-重新运行] ${userName}(${actorId}) 请求运行工作流
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📁 工作流: ${workflowId}
⚡ 操作: 自动修复后重新运行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
执行命令:
${JSON.stringify(payload)}`;

    const result = await sendIntervention({
      botId: autoHealConfig.botId,
      sessionKey: `auto-heal-run:${workflowId}:${Date.now()}`,
      sessionId: null,
      message,
    });

    if (!result.ok) {
      throw new Error(result.error ?? "BaaS intervention failed");
    }

    return result.messageId ?? null;
  };
}
