/**
 * AutoHeal trigger-run helper — invoked by AutoHealDeployService after a deploy.
 *
 * A re-run replays the repaired workflow on the Bot that ran the source run, so
 * the target Bot and the service-Bot lifecycle stage are both read from that
 * run. The autoHeal config Bot is the diagnosis Bot and is never a run target.
 */
import { sendIntervention } from "@avernet/clawevolve/server/services/baas-intervention";
import {
  BAAS_BOT_ID_PATTERN,
  resolveSourceStage,
} from "@avernet/workflow/server/services/auto-heal-run-service";

/** The only fields of a run row needed to address its Bot. */
export type AutoHealSourceRun = {
  workflow_id: string;
  origin_bot_id: string | null;
  credentials_json: string | null;
};

/** Narrow repository view so the helper stays independent of the full run store. */
export type AutoHealRunSourceLookup = {
  findFullByFlowId(flowId: string): Promise<AutoHealSourceRun | null>;
};

export function createAutoHealTriggerRun(
  flowRunRepo: AutoHealRunSourceLookup | null,
  getUserName: () => string = () => "ClawWeb AutoHeal",
): (workflowId: string, actorId: string, sourceFlowId?: string | null) => Promise<string | null> {
  return async (
    workflowId: string,
    actorId: string,
    sourceFlowId?: string | null,
  ): Promise<string | null> => {
    if (!flowRunRepo) {
      throw new Error("AutoHeal re-run needs run storage to resolve the source Bot");
    }
    if (!sourceFlowId?.trim()) {
      throw new Error("AutoHeal re-run needs sourceFlowId to resolve the source Bot");
    }

    const run = await flowRunRepo.findFullByFlowId(sourceFlowId);
    if (!run) {
      throw new Error(`Source run ${sourceFlowId} not found`);
    }
    if (run.workflow_id !== workflowId) {
      throw new Error("Source run does not match workflowId");
    }

    const botId = run.origin_bot_id;
    if (typeof botId !== "string" || !BAAS_BOT_ID_PATTERN.test(botId)) {
      throw new Error("Source run requires origin_bot_id in botId:ownerId format");
    }
    // bot_options.lifecycle_stage is a service-Bot option; undefined for the rest.
    const lifecycleStage = resolveSourceStage(run.credentials_json);

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

    // A re-run is a new invocation: never reuse the source session.
    const result = await sendIntervention({
      botId,
      lifecycleStage,
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