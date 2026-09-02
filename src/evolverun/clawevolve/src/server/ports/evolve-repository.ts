import type { EvolveBotRuntime } from "./evolve-dispatcher.js";

export type EvolveTaskRow = {
  id: number;
  task_id: string;
  task_type: string;
  user_id: string;
  bot_id: string;
  task_name: string | null;
  remark: string | null;
  status: string;
  config_json: string;
  error_message: string | null;
  created_by: string;
  gmt_create: number;
  gmt_modified: number;
  bot_name?: string | null;
};

export type EvolveStepRow = {
  id: number;
  step_id: string;
  task_id: string;
  step_type: string;
  step_no: number;
  round_no: number | null;
  command: string;
  status: string;
  bot_run_id: string | null;
  bot_session_id: string | null;
  bot_response_json: string | null;
  output_json: string | null;
  summary: string | null;
  error_code: string | null;
  error_message: string | null;
  retryable: number | null;
  started_at: number | string | null;
  completed_at: number | string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type EvolveTaskRepositoryPort = {
  markDispatched(
    stepId: string,
    runId: string | null,
    sessionId: string | null,
    platformResponse?: unknown,
  ): Promise<void>;
  markDispatchFailed(stepId: string, error: string): Promise<void>;
  findStep(stepId: string): Promise<EvolveStepRow | null>;
  claimCreatedBusinessStep(taskId: string): Promise<EvolveStepRow | null>;
  resolveEvolveBotRuntime(
    userId: string,
    botId: string,
    env?: string,
  ): Promise<EvolveBotRuntime | null>;
};

export type StaleRunAnalysisStep = {
  step_id: string;
  task_id: string;
  flow_id: string;
  gmt_create: number | string;
};

export type RunAnalysisTimeoutRepositoryPort = {
  findStaleRunAnalysisSteps(staleMs: number): Promise<StaleRunAnalysisStep[]>;
  updateStepStatus(stepId: string, input: {
    status: string;
    errorCode?: string;
    errorMessage?: string;
  }): Promise<void>;
  failFlowAnalysis(flowId: string): Promise<void>;
};
