/** Translate only proven BaaS signatures into public codes. Never return diagnostics. */
type WorkflowAiDraftBaasFailureCode =
  | 'AUTO_HEAL_BOT_UNAVAILABLE'
  | 'BAAS_AUTH_FAILED'
  | 'BAAS_GENERATION_TIMEOUT'
  | 'BAAS_GENERATION_CANCELLED'
  | 'BAAS_SESSION_UNAVAILABLE'
  | 'BAAS_RESULT_PERSISTENCE_FAILED'
  | 'BAAS_GENERATION_FAILED';

function diagnosticText(error: unknown): string {
  if (typeof error !== 'string' || error.length > 4096) return '';
  // These are the wrappers emitted by ResultGuardExecutor and BaasBotService.
  // Do not search inside arbitrary prose or recursively unwrap unknown errors.
  return error.trim().replace(/^executor safety-net: /i, '')
    .replace(/^Failed to send message(?: stream)?: /i, '');
}
function bindingUnavailable(error: string): boolean {
  // _executor.py and api/bot_runtime/_exceptions.py; 60001 alone is NOT a Bot code.
  return /^(?:BotBindingNotFoundError(?::|$)|Bot binding not found:|binding not found:)/.test(error);
}

export function classifyWorkflowAiDraftBaasFailure(status: string, error: unknown): WorkflowAiDraftBaasFailureCode {
  // The native terminal status is stronger evidence than diagnostic text.
  // The caller supplies error only from enveloped data.error, after identity validation.
  if (status === 'time_out') return 'BAAS_GENERATION_TIMEOUT';
  if (['aborted', 'cancelled', 'canceled', 'stopped'].includes(status)) return 'BAAS_GENERATION_CANCELLED';
  const diagnostic = diagnosticText(error);
  if (bindingUnavailable(diagnostic)) return 'AUTO_HEAL_BOT_UNAVAILABLE';
  // _executor._rebuild_context, api/api_gateway/_jwt.py and OpenAPI auth messages.
  // Token budgets, token files and arbitrary occurrences of "token" prove nothing.
  if (/^(?:Token (?:missing|expired|invalid)|Token (?:invalid|verification failed): [\s\S]*|api key not found: [\s\S]*)$/i.test(diagnostic)) {
    return 'BAAS_AUTH_FAILED';
  }
  // _executor.py / _task_message_dispatcher.py record these exact timeout/cancel causes.
  if (/^(?:Task execution timeout|time out|execution cancelled \(timeout\))$/i.test(diagnostic)) return 'BAAS_GENERATION_TIMEOUT';
  if (/^execution cancelled \(not timeout\)$/i.test(diagnostic)) return 'BAAS_GENERATION_CANCELLED';
  // _executor.py's missing session, SessionNotFoundError, and
  // _baas_service._materialize_session: match only their anchored signatures.
  if (/^session_id missing$/i.test(diagnostic)
    || /^Session not found: /i.test(diagnostic)
    || /^tenant is required for materialize session, session_id=/i.test(diagnostic)
    || /^Failed to resolve WS connection for materialize: /i.test(diagnostic)
    || /^Failed to materialize session \S+: /i.test(diagnostic)
    || /^No engine adapter registered for engine_type=[^\r\n]+, cannot materialize session /i.test(diagnostic)) {
    return 'BAAS_SESSION_UNAVAILABLE';
  }
  // _executor._do_send_persist may have generated content but failed to store it.
  if (/^result persistence failed: /i.test(diagnostic)) return 'BAAS_RESULT_PERSISTENCE_FAILED';
  return 'BAAS_GENERATION_FAILED';
}
