use async_trait::async_trait;
use bcs_dist_model::Sid;
use bcs_dist_store::task_feed::StoreTaskFeed;
use bcs_service_api::port::repo::distributed::{common::*, task_feed::*};
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicUsize, Ordering},
};
pub struct FaultPublisher {
    pub inner: Arc<StoreTaskFeed>,
    pub lose_append: AtomicBool,
    pub append_calls: AtomicUsize,
    /// The bool injects lost responses after applying, otherwise NotApplied.
    pub fail_session: std::sync::Mutex<Option<(Sid, bool)>>,
    pub append_requests: std::sync::Mutex<Vec<AppendCommittedEvents>>,
    pub pause_failure: AtomicBool,
    pub failure_entered: tokio::sync::Notify,
    pub failure_resume: tokio::sync::Notify,
}
#[async_trait]
impl TaskPublisherPort for FaultPublisher {
    async fn append(&self, r: AppendCommittedEvents) -> Result<AppendOutcome, StoreError> {
        self.append_calls.fetch_add(1, Ordering::SeqCst);
        self.append_requests.lock().unwrap().push(r.clone());
        let failure = self.fail_session.lock().unwrap().clone();
        let failure = failure.filter(|(sid, _)| *sid == r.source.commit.sid);
        if failure.as_ref().is_some_and(|(_, unknown)| !unknown) {
            return Err(StoreError::NotApplied {
                code: "injected_append_unavailable".into(),
            });
        }
        let id = r.source.commit.commit_id.0.clone();
        let result = self.inner.append(r).await?;
        if failure.is_some() || self.lose_append.swap(false, Ordering::SeqCst) {
            if self.pause_failure.swap(false, Ordering::SeqCst) {
                self.failure_entered.notify_one();
                self.failure_resume.notified().await;
            }
            Err(StoreError::OutcomeUnknown { operation_id: id })
        } else {
            Ok(result)
        }
    }
    async fn publish(&self, r: PublishCommittedSnapshot) -> Result<PublishOutcome, StoreError> {
        self.inner.publish(r).await
    }
    async fn trim(&self, r: RetentionRequest) -> Result<TrimOutcome, StoreError> {
        self.inner.trim(r).await
    }
}
