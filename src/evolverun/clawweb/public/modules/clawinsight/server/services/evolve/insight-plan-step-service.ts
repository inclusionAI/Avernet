import { randomUUID } from "node:crypto";
import { getClawWebPublicBaseUrl } from "@avernet/clawweb-shared/server/env";
import type { EvolveRepository, EvolveTaskRow } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { dispatchEvolveCommand } from "@avernet/clawevolve";
import { renderCommand, type NodeCommandYamls } from "@avernet/clawevolve/server/services/evolve/command";
import { startInitialEvolveStep } from "./task-start.js";

type Dispatch = typeof dispatchEvolveCommand;

function stepId(): string {
  return `STEP-${randomUUID().replaceAll("-", "").slice(0, 16).toUpperCase()}`;
}

type InsightPlanTaskConfig = {
  dispatchMode?: "message" | "run";
  nodeCommands?: NodeCommandYamls;
  forceMessage?: boolean;
  runtimeMaintenance?: boolean;
  clawwebUrl?: string;
  botEnv?: string;
  input?: { type?: string };
};

function parseConfig(value: string): InsightPlanTaskConfig {
  try { return JSON.parse(value) as InsightPlanTaskConfig; }
  catch { return {}; }
}

/** Starts the Plan step for an Insight Improvement task only. */
export class InsightPlanStepService {
  constructor(
    private readonly repo: EvolveRepository,
    private readonly dispatch: Dispatch,
  ) {}

  async start(
    task: Pick<EvolveTaskRow, "task_id" | "user_id" | "bot_id" | "config_json">,
    previousStepNo: number,
    callbackUrl: (stepId: string) => string,
  ) {
    const taskConfig = parseConfig(task.config_json);
    if (taskConfig.input?.type !== "insight_improvement") {
      throw new Error("InsightPlanStepService 只允许启动 Insight Improvement 任务");
    }
    const dispatchMode = taskConfig.dispatchMode
      ?? await this.repo.resolveBotDispatchMode(task.user_id, task.bot_id, taskConfig.botEnv);
    const nextStepId = stepId();
    const planTemplate = taskConfig.nodeCommands?.plan ?? "/clawevolve-plan";
    const command = renderCommand(planTemplate, {}, [
      ["task-id", task.task_id],
      ["step-id", nextStepId],
      ["owner-id", task.user_id],
      ["bot-id", task.bot_id],
      ["clawweb-url", taskConfig.clawwebUrl ?? getClawWebPublicBaseUrl()],
    ]);
    const next = await this.repo.createStep({
      stepId: nextStepId,
      taskId: task.task_id,
      stepType: "plan",
      stepNo: previousStepNo + 1,
      command,
    });
    const runtime = await this.repo.resolveEvolveBotRuntime(task.user_id, task.bot_id, taskConfig.botEnv);
    await startInitialEvolveStep({
      repo: this.repo,
      dispatch: this.dispatch,
      task,
      businessStep: next,
      runtime,
      clawwebUrl: taskConfig.clawwebUrl ?? getClawWebPublicBaseUrl(),
      callbackUrl,
      businessDispatch: {
        taskId: task.task_id,
        stepId: nextStepId,
        stepType: "plan",
        userId: task.user_id,
        botId: task.bot_id,
        command,
        mode: dispatchMode,
        runtime,
        forceMessage: taskConfig.forceMessage === true,
        runtimeMaintenance: taskConfig.runtimeMaintenance !== false,
        callbackUrl: callbackUrl(nextStepId),
      },
    });
    return { stepId: nextStepId, stepType: "plan" as const };
  }
}
