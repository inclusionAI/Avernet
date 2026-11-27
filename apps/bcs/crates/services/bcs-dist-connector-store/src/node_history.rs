use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::dispatch::HolderError;

/// Only actual node replies belong to this bounded ledger. Deltas, thinking,
/// tool traces and generic snapshots are not conversation turns.
pub(super) fn record_response(
    view: &DispatchView,
    response: &NodeResponseEvidence,
) -> Result<Option<DispatchExecution>, HolderError> {
    if view.spec.node_history.is_none()
        || !matches!(view.execution.phase, DispatchPhase::Sending | DispatchPhase::Accepted)
        || !matches!(response.kind, AgentEventKind::Final | AgentEventKind::Error
            | AgentEventKind::InputRequired { .. } | AgentEventKind::AuthRequired { .. })
        || response.id.0.is_empty() || response.id.0.len() > 128
        || response.content.key.is_empty() || response.content.key.len() > 1024
        || response.content.digest.0.len() > 128
    {
        return Err(invalid("node_response_evidence"));
    }
    let mut next = view.execution.clone();
    if next.node_responses.iter().any(|previous| previous.id == response.id) {
        // The first verified observation owns this stable semantic slot.
        // Remote reads may later differ in projection or auxiliary fields.
        return Ok(None);
    }
    if next.node_responses.len() >= 3 {
        return Err(invalid("node_response_evidence_limit"));
    }
    next.node_input_delivery = Some(DeliveryState::Delivered);
    next.node_responses.push(response.clone());
    Ok(Some(next))
}

fn invalid(code: &str) -> HolderError {
    HolderError::InvalidTransition { code: code.into() }
}
