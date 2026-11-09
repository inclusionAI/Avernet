//! Compile-time consumer contracts: no repository implementation or successful stub.
//! Runtime fencing/durability assertions belong to Task 3's concrete store conformance.
use std::sync::Arc;
use std::future::Future;
use bcn_runtime_api::port::repo::{DispatchStore, TaskEventStore};
use bcn_runtime_model::*;

async fn claim_and_recover_cancel(
    store: Arc<dyn DispatchStore>, operation: DispatchOperation, owner: NodeIdentity, next: NodeIdentity,
) -> RuntimeResult<DispatchRecord> {
    let claimed = store.claim(operation, owner).await?;
    store.takeover(&claimed.fence, next).await
}

async fn persist_cancel_outcome(
    store: Arc<dyn DispatchStore>, fence: DispatchFence, revision: Revision, outcome: CancelOutcome,
    session: SessionId, input: SessionInput,
) -> RuntimeResult<DispatchRecord> {
    store.save_receipt(&fence, revision, DispatchReceipt::Cancellation { outcome }, &session, input).await
}

async fn publish_committed_worker_event(
    store: Arc<dyn TaskEventStore>, ownership: OwnershipFence, event: CommittedEventRef,
) -> RuntimeResult<EventCursor> {
    // A first HumanInput node can call this before any agent DispatchFence exists.
    store.publish_hard_event(&ownership, event).await
}

#[test]
fn trait_object_consumers_accept_typed_cancel_and_worker_ownership() {
    // Type-check and Send-check the real consumer futures without inventing store behavior.
    fn check_claim<F: Future<Output = RuntimeResult<DispatchRecord>> + Send>(_: F) {}
    fn check_publish<F: Future<Output = RuntimeResult<EventCursor>> + Send>(_: F) {}
    let _claim = |store, operation, owner, next| check_claim(claim_and_recover_cancel(store, operation, owner, next));
    let _receipt = |store, fence, revision, outcome, session, input| check_claim(persist_cancel_outcome(store, fence, revision, outcome, session, input));
    let _publish = |store, ownership, event| check_publish(publish_committed_worker_event(store, ownership, event));
}
