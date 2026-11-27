//! Port errors as core errors. Only machine codes are forwarded.

use bcs_dist_model::InputId;
use bcs_service_api::core::dist_gateway::DistGatewayCoreError;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::STRICT_CANCEL_NOT_ACTIVE};

/// Error of a read: nothing changed, so it is never "outcome unknown".
pub(crate) fn read_error(error: StoreError) -> DistGatewayCoreError {
    DistGatewayCoreError::Unavailable { code: code_of(error) }
}

/// Error of an admission: an unknown outcome names the input to retry.
pub(crate) fn admit_error(error: StoreError, input_id: &InputId) -> DistGatewayCoreError {
    match error {
        StoreError::OutcomeUnknown { .. } => DistGatewayCoreError::OutcomeUnknown {
            input_id: input_id.clone(),
        },
        StoreError::NotApplied { code } if code == STRICT_CANCEL_NOT_ACTIVE => {
            DistGatewayCoreError::InvalidTransition
        }
        other => DistGatewayCoreError::Unavailable { code: code_of(other) },
    }
}

fn code_of(error: StoreError) -> String {
    match error {
        StoreError::NotApplied { code } | StoreError::Invalid { code } => code,
        StoreError::OutcomeUnknown { .. } => "store_outcome_unknown".to_owned(),
        StoreError::UnsupportedFormat { format } => format!("unsupported_stored_format:{format}"),
    }
}
