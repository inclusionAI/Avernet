export type EvolveBotRuntime = {
  activeEngine: string | null;
  botType: string | null;
  hasServiceBot: boolean;
  botStatus: string | null;
  bindingId: string | number | null;
  provider: string | null;
  deviceId: string | null;
  bindingStatus: string | null;
  env: string | null;
  ownerId?: string;
  accessType?: "owner" | "collaborator";
};

export type EvolveDispatchInput = {
  taskId: string;
  stepPk: number;
  stepId: string;
  stepType: string;
  userId: string;
  botId: string;
  command: string;
  mode: "message" | "run";
  callbackUrl?: string;
  runtime?: EvolveBotRuntime | null;
  optimizeArgs?: {
    round: number;
    trainBenchDomainId?: string;
    testBenchDomainId?: string;
  };
  forceMessage?: boolean;
  runtimeMaintenance?: boolean;
  secrets?: { diagnoseApiKey?: string };
};

export type EvolveDispatchResult = {
  runId: string | null;
  sessionId: string | null;
  platformResponse: unknown;
};

export type EvolveDispatcher = (
  input: EvolveDispatchInput,
) => Promise<EvolveDispatchResult>;
