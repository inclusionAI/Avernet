//! Steps whose cross-component contract is not final (walkthrough §16). Each
//! has its intended signature and fails with `need_contract_not_implemented`.
//!
//! These are deliberate gaps, not bugs; callers handle the stub error and
//! fall back to behaviour that is safe without the contract:
//!
//! - D2 [`report_unresolved`]: the Worker is not told that a call is stuck.
//!   The `disp` document keeps the truth (the call stays in its phase and
//!   held) and the Worker's own deadline later becomes a cancel.
//! - D3 [`HeldCallRecovery::repost_held`]: nobody re-posts the calls of a
//!   dead Connector. Their dispatches stay held by it; another Connector
//!   takes one over only when a hint for it arrives and the membership proves
//!   the holder dead, which the stub membership never does.
//! - D8 [`HeldCallRecovery::release`]: drain does not hand calls back; calls
//!   still running when the grace period ends are reported as remaining.

use async_trait::async_trait;
use bcs_dist_model::{Did, Holder, Instance};
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};
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

/// Recovery steps of held calls, local to the Connector until their contracts
/// (D3, D8) are final. The storage stubs live in the Connector's store crate;
/// a thin wrapper joins them at composition.
#[async_trait]
pub trait HeldCallRecovery: Send + Sync {
    /// Delayed contract D3 (walkthrough §16, Gap 5): re-post the held
    /// dispatches of a dead Connector. Not called by any loop in the MVP.
    async fn repost_held(&self, dead: &Instance, page: PageRequest) -> Result<Page<Did>, StoreError>;
    /// Delayed contract D8 (walkthrough §16): hand a call over on drain by
    /// writing it back to unclaimed and re-notifying.
    async fn release(&self, did: &Did, holder: &Holder) -> Result<(), StoreError>;
}
