export type RepairRecovery = {
  recoveryClass: "model_output" | "transient" | "agent_recovery";
  recoveryAction: "regenerate_final_result" | "complete_missing_conclusions";
  automatic: boolean;
};

export class RepairError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly toolCallId: string | null = null,
    public readonly recovery: RepairRecovery | null = null,
  ) {
    super(message);
    this.name = "RepairError";
  }
}

export function repairFinalizationRejected(code: string, message: string): never {
  throw new RepairError(409, code, message, null, {
    recoveryClass: "model_output",
    recoveryAction: "regenerate_final_result",
    automatic: true,
  });
}

export function repairValidation(code: string, message: string): never {
  throw new RepairError(400, code, message);
}

export function repairUnauthorized(code: string, message: string): never {
  throw new RepairError(401, code, message);
}

export function repairForbidden(code: string, message: string): never {
  throw new RepairError(403, code, message);
}

export function repairNotFound(code: string, message: string): never {
  throw new RepairError(404, code, message);
}

export function repairUnavailable(code: string, message: string): never {
  throw new RepairError(503, code, message);
}
