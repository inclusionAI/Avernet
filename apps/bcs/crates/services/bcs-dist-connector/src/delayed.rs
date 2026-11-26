//! Steps whose cross-component contract is not final (walkthrough §16). Each
//! has its intended signature and fails with `need_contract_not_implemented`.
//!
//! These are deliberate gaps, not bugs; callers handle the stub error and
//! fall back to behaviour that is safe without the contract:
//!
//! - D2 [`report_unresolved`]: the Worker is not told that a call is stuck.
//!   The `disp` document keeps the truth (the call stays in its phase and
//!   held) and the Worker's own deadline later becomes a cancel.
//!
//! Two former stubs are gone. D3 (re-post a dead Connector's calls) is done
//! by the Worker's supervisor, which re-posts a hint for every open dispatch
//! about every second, and by partition ownership: the next owner of a dead
//! Connector's partitions claims their calls. D8 (hand calls over on drain)
//! is `drain` itself: calls stop at a safe point and each partition is handed
//! to a peer, which takes its calls over at once.

use bcs_dist_model::Did;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::inbox::AdmitOutcome;

/// Code of a method that waits for a contract.
pub const NEED_CONTRACT: &str = "need_contract_not_implemented";

/// Why a call cannot be resolved by the Connector. Applies to the start
/// (first message) and to a human reply alike.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UnresolvedKind {
    /// The send may or may not have reached the agent, and may not be repeated.
    Uncertain,
    /// The send definitely did not reach the agent, after bounded retries.
    NotSent,
}

/// Delayed contract D2 (walkthrough §16, Gap 6): tell the Worker that a call
/// is uncertain or was not sent. No `AgentEventKind` exists for it yet.
///
/// Until it does, the caller logs the error and leaves the call in its phase:
/// the dispatch document keeps the truth and the Worker's deadline still fires
/// and reaches the call as a cancel (B13, B17). The Worker is not told
/// anything by this stub.
pub async fn report_unresolved(_call: &Did, _kind: UnresolvedKind) -> Result<AdmitOutcome, StoreError> {
    Err(StoreError::NotApplied {
        code: NEED_CONTRACT.to_owned(),
    })
}
