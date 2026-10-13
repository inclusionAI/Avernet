export class SessionRecoveryError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) { super(message); this.name = "SessionRecoveryError"; }
}
