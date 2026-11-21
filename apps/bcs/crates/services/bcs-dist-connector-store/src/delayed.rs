//! Storage steps whose cross-component contract is not final (walkthrough §16).
//! Each has its intended signature and fails with
//! `need_contract_not_implemented`; nothing here panics or pretends success.

use bcs_dist_model::{Did, Holder, Instance};
use bcs_dist_store::error::need_contract;
use bcs_service_api::port::repo::distributed::common::{Page, PageRequest, StoreError};

/// Delayed contract D3 (walkthrough §16, Gap 5): re-post the held dispatches
/// of a dead Connector so another one claims them. Who supervises (a Worker or
/// the Connectors themselves) is open.
///
/// The held index is maintained regardless; until this exists, recovery falls
/// back to the Worker's deadline re-post.
pub async fn repost_held(_dead: &Instance, _page: PageRequest) -> Result<Page<Did>, StoreError> {
    Err(need_contract())
}

/// Delayed contract D8 (walkthrough §16): hand an in-flight call over on drain
/// by writing it back to unclaimed and re-notifying. The merged contract has
/// no "release this call" operation and it would touch the dispatch document
/// the Worker also writes.
///
/// Until this exists, `drain` waits for the grace period and leftover calls
/// are recovered through D3 or the Worker's deadline re-post.
pub async fn release(_did: &Did, _holder: &Holder) -> Result<(), StoreError> {
    Err(need_contract())
}
