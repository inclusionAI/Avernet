//! Core errors as application errors. Adapters only ever see `GatewayError`.

use bcs_dist_model::RequestId;
use bcs_service_api::application::dist_gateway::GatewayError;
use bcs_service_api::core::dist_gateway::DistGatewayCoreError;

/// Mapping for reads and for checks that involve no admission.
pub(crate) fn core_error(error: DistGatewayCoreError) -> GatewayError {
    match error {
        DistGatewayCoreError::NotFound => GatewayError::NotFound,
        DistGatewayCoreError::ScopeMismatch => GatewayError::AccessDenied,
        DistGatewayCoreError::InvalidTransition => GatewayError::Invalid {
            code: "invalid_task_state".to_owned(),
        },
        DistGatewayCoreError::ContentConflict => GatewayError::Conflict,
        DistGatewayCoreError::Quarantined => GatewayError::Quarantined,
        DistGatewayCoreError::Unavailable { code } => GatewayError::Unavailable { code },
        // Without a request to name, an unknown outcome is a failed dependency.
        DistGatewayCoreError::OutcomeUnknown { .. } => GatewayError::Unavailable {
            code: "outcome_unknown".to_owned(),
        },
    }
}

/// Mapping for an admission of the client request `request_id`: an unknown
/// outcome tells the client to retry that same request.
pub(crate) fn admit_error(error: DistGatewayCoreError, request_id: &RequestId) -> GatewayError {
    match error {
        DistGatewayCoreError::OutcomeUnknown { .. } => GatewayError::OutcomeUnknown {
            request_id: request_id.clone(),
        },
        other => core_error(other),
    }
}
