/** Preserve an original stack exactly; otherwise label a diagnostic observation, never inspect Error extensions. */
export function errorStack(exception: unknown): string {
  return apiErrorDiagnostic(exception).stack;
}

export type TaskGuardApiDiagnostic = { message: string; stack: string; stackSource: "exception" | "observation" };
export type ApiDiagnosticResponse = { locals: Record<string, unknown> };

/** Only stack/message cross this logging boundary, never enumerable Error/client metadata. */
export function apiErrorDiagnostic(exception: unknown): TaskGuardApiDiagnostic {
  const error = exception && typeof exception === "object" ? exception as { message?: unknown; stack?: unknown } : undefined;
  const message = typeof error?.message === "string" ? error.message
    : typeof exception === "string" ? exception : "Unknown error (non-Error exception)";
  if (typeof error?.stack === "string" && error.stack) return { message, stack: error.stack, stackSource: "exception" };
  const observation = new Error(`Workflow API failure observed: ${message}`);
  return { message, stack: observation.stack || observation.message, stackSource: "observation" };
}

/** Capture before sending a 5xx response so HTTP file logging can retain the original stack. */
export function captureApiError(response: ApiDiagnosticResponse, exception: unknown): string {
  const diagnostic = apiErrorDiagnostic(exception);
  response.locals ??= {};
  response.locals.taskGuardApiDiagnostic = diagnostic;
  return diagnostic.stack;
}

/** Protected Pack/editor/AI operations must not reveal even the original message or stack. */
export function captureSensitiveApiError(response: ApiDiagnosticResponse): string {
  const observation = new Error("Workflow protected operation failure observed");
  const diagnostic: TaskGuardApiDiagnostic = {
    message: "Protected workflow operation failed", stack: observation.stack || observation.message, stackSource: "observation",
  };
  response.locals ??= {};
  response.locals.taskGuardApiDiagnostic = diagnostic;
  return diagnostic.stack;
}
