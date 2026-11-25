//! Mapping of `bcs-store-api` errors onto the port `StoreError`.
//!
//! Only `OutcomeUnknown` may become `OutcomeUnknown`; every other store error
//! means "this attempt changed nothing". No provider message text is forwarded:
//! callers get a machine code only.

use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_store_api::StoreError as PluginError;

/// Code of a method whose cross-component contract is not final yet
/// (walkthrough §16). It always fails with this code, never panics.
pub const NEED_CONTRACT: &str = "need_contract_not_implemented";

/// The error a delayed-contract stub returns.
pub fn need_contract() -> StoreError {
    StoreError::NotApplied {
        code: NEED_CONTRACT.to_owned(),
    }
}

/// Maps an error of a write. `operation_id` names the logical operation (e.g.
/// the input id) so a caller can resolve an unknown outcome by identity.
pub fn from_write(error: &PluginError, operation_id: &str) -> StoreError {
    match error {
        PluginError::OutcomeUnknown(_) => StoreError::OutcomeUnknown {
            operation_id: operation_id.to_owned(),
        },
        other => definite(other),
    }
}

/// Maps an error of a read. A read changes nothing, so even an unknown outcome
/// is reported as "not applied".
pub fn from_read(error: &PluginError) -> StoreError {
    match error {
        PluginError::OutcomeUnknown(_) => StoreError::NotApplied {
            code: "store_read_unknown".to_owned(),
        },
        other => definite(other),
    }
}

fn definite(error: &PluginError) -> StoreError {
    let invalid = |code: &str| StoreError::Invalid {
        code: code.to_owned(),
    };
    let not_applied = |code: &str| StoreError::NotApplied {
        code: code.to_owned(),
    };
    match error {
        PluginError::InvalidInput(_) => invalid("store_invalid_input"),
        PluginError::WrongType => invalid("store_wrong_type"),
        PluginError::LimitExceeded(_) => invalid("store_limit_exceeded"),
        PluginError::Unsupported(_) => not_applied("store_unsupported"),
        PluginError::VersionExhausted => not_applied("store_version_exhausted"),
        PluginError::IdNotIncreasing => not_applied("store_id_not_increasing"),
        PluginError::GroupMissing => not_applied("store_group_missing"),
        PluginError::NotApplied(_) => not_applied("store_not_applied"),
        PluginError::OutcomeUnknown(_) => not_applied("store_outcome_unknown"),
    }
}
