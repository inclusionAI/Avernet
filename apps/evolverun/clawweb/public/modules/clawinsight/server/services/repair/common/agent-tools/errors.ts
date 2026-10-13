export type AgentToolRecovery = {
  recoveryClass: "model_output" | "transient" | "agent_recovery";
  recoveryAction: "regenerate_final_result" | "complete_missing_conclusions";
  automatic: boolean;
};

export class AgentToolError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly toolCallId: string | null = null,
    public readonly recovery: AgentToolRecovery | null = null,
  ) {
    super(message);
    this.name = "AgentToolError";
  }
}

export function agentToolFinalizationRejected(code: string, message: string): never {
  throw new AgentToolError(409, code, message, null, {
    recoveryClass: "model_output",
    recoveryAction: "regenerate_final_result",
    automatic: true,
  });
}

export function agentToolValidation(code: string, message: string): never {
  throw new AgentToolError(400, code, message);
}

export function agentToolUnauthorized(code: string, message: string): never {
  throw new AgentToolError(401, code, message);
}

export function agentToolForbidden(code: string, message: string): never {
  throw new AgentToolError(403, code, message);
}

export function agentToolNotFound(code: string, message: string): never {
  throw new AgentToolError(404, code, message);
}

export function agentToolUnavailable(code: string, message: string): never {
  throw new AgentToolError(503, code, message);
}
