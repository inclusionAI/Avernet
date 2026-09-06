import type {
  EvolveBotRuntime,
  EvolveRepository,
  EvolveStepRow,
  EvolveTaskRow,
} from "../../repositories/evolve-repository.js";
import {
  dispatchEvolveCommand,
  type EvolveDispatchInput,
} from "../evolve-dispatcher.js";

type Dispatch = typeof dispatchEvolveCommand;

export type StartInitialEvolveStepInput = {
  repo: EvolveRepository;
  dispatch: Dispatch;
  task: Pick<EvolveTaskRow, "task_id" | "user_id" | "bot_id">;
  businessStep: EvolveStepRow;
  businessDispatch: Omit<EvolveDispatchInput, "stepPk">;
  runtime?: EvolveBotRuntime | null;
  clawwebUrl: string;
  callbackUrl: (stepId: string) => string;
  initStepNo?: number;
};

async function dispatchAndRecord(
  repo: EvolveRepository,
  dispatch: Dispatch,
  input: EvolveDispatchInput,
): Promise<void> {
  try {
    const result = await dispatch(input);
    await repo.markDispatched(input.stepId, result.runId, result.sessionId, result.platformResponse);
  } catch (error) {
    await repo.markDispatchFailed(input.stepId, error instanceof Error ? error.message : String(error));
  }
}

export async function startInitialEvolveStep(input: StartInitialEvolveStepInput): Promise<{
  deferredForInit: boolean;
  initStep?: EvolveStepRow;
  businessStep?: EvolveStepRow;
}> {
  await dispatchAndRecord(input.repo, input.dispatch, {
    ...input.businessDispatch,
    stepPk: input.businessStep.id,
  });
  return {
    deferredForInit: false,
    businessStep: await input.repo.findStep(input.businessStep.step_id) ?? input.businessStep,
  };
}

export async function dispatchPendingBusinessStep(input: {
  repo: EvolveRepository;
  dispatch: Dispatch;
  task: EvolveTaskRow;
  callbackUrl: (stepId: string) => string;
}): Promise<EvolveStepRow | null> {
  const step = await input.repo.claimCreatedBusinessStep(input.task.task_id);
  if (!step) return null;
  const config = (() => {
    try { return JSON.parse(input.task.config_json) as Record<string, unknown>; }
    catch { return {}; }
  })();
  const runtime = await input.repo.resolveEvolveBotRuntime(
    input.task.user_id,
    input.task.bot_id,
    String(config.botEnv ?? ""),
  );
  const mode = config.dispatchMode === "run" ? "run" : "message";
  await dispatchAndRecord(input.repo, input.dispatch, {
    taskId: input.task.task_id,
    stepPk: step.id,
    stepId: step.step_id,
    stepType: step.step_type,
    userId: input.task.user_id,
    botId: input.task.bot_id,
    command: step.command,
    mode,
    callbackUrl: input.callbackUrl(step.step_id),
    runtime,
    forceMessage: config.forceMessage === true,
    runtimeMaintenance: config.runtimeMaintenance !== false,
    ...(step.step_type === "optimize" && step.round_no != null ? {
      optimizeArgs: {
        round: step.round_no,
        trainBenchDomainId: String(config.trainBenchDomainId ?? "") || undefined,
        testBenchDomainId: String(config.testBenchDomainId ?? "") || undefined,
      },
    } : {}),
  });
  return input.repo.findStep(step.step_id);
}
