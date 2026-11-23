//! Incremental restoration keeps a failed/slow watch from blocking sibling reads.
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, inbox::*};
use std::{collections::VecDeque, time::Duration};
pub(crate) struct RebuildingFeed {
    inner: Box<dyn ShardFeedPort>,
    pending: VecDeque<(Sid, Option<InboxPos>)>,
    timeout: Duration,
}
impl RebuildingFeed {
    pub(crate) fn new(
        inner: Box<dyn ShardFeedPort>,
        pending: Vec<(Sid, Option<InboxPos>)>,
        timeout: Duration,
    ) -> Self {
        Self {
            inner,
            pending: pending.into(),
            timeout: timeout.min(Duration::from_millis(100)),
        }
    }
}
#[async_trait]
impl ShardFeedPort for RebuildingFeed {
    async fn watch(&mut self, sid: Sid, after: Option<InboxPos>) -> Result<(), StoreError> {
        self.inner.watch(sid.clone(), after).await?;
        self.pending.retain(|(s, _)| *s != sid);
        Ok(())
    }
    async fn unwatch(&mut self, sid: &Sid) -> Result<(), StoreError> {
        self.inner.unwatch(sid).await?;
        self.pending.retain(|(s, _)| s != sid);
        Ok(())
    }
    async fn next(&mut self, limits: ReadLimits, wait: Duration) -> Result<FeedBatch, StoreError> {
        let mut failure = None;
        if let Some((sid, after)) = self.pending.pop_front() {
            // Rotate before awaiting, so even canceled IO does not pin one prefix.
            self.pending.push_back((sid.clone(), after.clone()));
            let result =
                tokio::time::timeout(self.timeout, self.inner.watch(sid.clone(), after)).await;
            match result {
                Ok(Ok(())) => self.pending.retain(|(s, _)| *s != sid),
                Ok(Err(error)) => failure = Some(SessionFeedFailure { sid, error }),
                Err(_) => {
                    failure = Some(SessionFeedFailure {
                        sid,
                        error: StoreError::NotApplied {
                            code: "watch_restore_timeout".into(),
                        },
                    })
                }
            }
        }
        let mut batch = self.inner.next(limits, wait).await?;
        if let Some(failure) = failure {
            batch.failures.push(failure);
        }
        Ok(batch)
    }
}
