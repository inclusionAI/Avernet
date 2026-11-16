use bcn_runtime_model::*;

pub(crate) fn uncertain() -> ConnectorError {
    // Never forward peer diagnostics, URLs or credential material.
    ConnectorError::MayHaveBeenAccepted("agent observation unavailable".into())
}
pub(crate) fn not_sent(reason: &str) -> ConnectorError { ConnectorError::NotSent(reason.into()) }
pub(crate) fn validate_handle(binding: &FrozenTargetBinding, handle: &ExecutionHandle) -> ConnectorResult<()> {
    if handle.target != binding.target || handle.binding_version != binding.binding_version
        || handle.remote_task_id.as_ref().is_some_and(String::is_empty)
        || handle.remote_context_id.as_ref().is_some_and(String::is_empty) {
        return Err(not_sent("execution handle does not match frozen target"));
    }
    Ok(())
}
pub(crate) fn validate_request(binding: &FrozenTargetBinding, request: &AgentSendRequest) -> ConnectorResult<()> {
    let scope=&binding.target.scope;
    if &request.binding != binding || &request.dispatch_id.scope != scope || &request.operation_id.scope != scope
        || &request.message.message_id.scope != scope
        || request.message.context_id.as_ref().is_some_and(|id| &id.scope != scope)
        || request.message.task_id.as_ref().is_some_and(|id| &id.scope != scope) {
        return Err(not_sent("request does not match frozen target"));
    }
    if let Some(handle)=&request.handle { validate_handle(binding,handle)?; }
    // Runner supplies its local deadline for all targets. Unsupported idempotency
    // does not promise downstream expiry enforcement and never authorizes replay.
    Ok(())
}
pub(crate) fn output(text: &str) -> Vec<MessagePart> {
    if text.is_empty() { vec![] } else { vec![MessagePart::Text{text:text.into()}] }
}
pub(crate) fn fits(parts: &[MessagePart]) -> bool {
    serde_json::to_vec(parts).is_ok_and(|bytes|bytes.len() <= crate::MAX_OUTPUT_BYTES)
}
pub(crate) fn observation(text: &str) -> AgentEvent {
    AgentEvent::ObservationFailed { error:RuntimeError::ExecutionUnknown, output:output(text) }
}
