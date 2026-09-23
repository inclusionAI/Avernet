export class BotRuntimeError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message); this.name = "BotRuntimeError";
  }
}
export function runtimeValidation(code: string, message: string): never {
  throw new BotRuntimeError(422, code, message);
}
