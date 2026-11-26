export class SessionRecoveryError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(message); this.name = "SessionRecoveryError"; }
}

type CallbackFailure = "http" | "timeout" | "network" | "invalid_ack";

/** Fixed diagnostics only; never retain provider bodies, credentials, URLs or raw exception text. */
export class RecoveryResultCallbackError extends SessionRecoveryError {
  constructor(readonly logFields: { errorCode: string; reason: string; failureKind: CallbackFailure;
    httpStatus?: number; upstreamCode?: string; transportCode?: string }) {
    super(503, "recovery_result_unavailable", logFields.reason);
  }
}
