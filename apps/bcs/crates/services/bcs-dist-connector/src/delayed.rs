//! Steps whose cross-component contract is not final (walkthrough §16). Each
//! has its intended signature and fails with `need_contract_not_implemented`.

use async_trait::async_trait;
use bcs_dist_model::{Did, Holder, Instance};
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::inbox::AdmitOutcome;

/// Code of a method that waits for a contract.
pub const NEED_CONTRACT: &str = "need_contract_not_implemented";

/// Why a call cannot be resolved by the Connector.
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
/// the dispatch document keeps the truth and the Worker's deadline still fires.
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
