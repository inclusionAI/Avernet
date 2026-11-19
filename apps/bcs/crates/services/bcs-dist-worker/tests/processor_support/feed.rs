use super::*;
pub struct FaultConsumer {
    pub inner: Arc<StoreInboxProducer>,
    pub fail: Arc<AtomicBool>,
    pub opens: Arc<AtomicUsize>,
    pub failed_watch: Arc<std::sync::Mutex<Option<Sid>>>,
    pub mixed_failure: Arc<std::sync::Mutex<Option<Sid>>>,
}
struct FaultFeed {
    failed_watch: Arc<std::sync::Mutex<Option<Sid>>>,
    mixed_failure: Arc<std::sync::Mutex<Option<Sid>>>,
    inner: Box<dyn ShardFeedPort>,
    fail: Arc<AtomicBool>,
}
#[async_trait]
impl ShardFeedPort for FaultFeed {
    async fn watch(&mut self, s: Sid, a: Option<InboxPos>) -> Result<(), StoreError> {
        if self.failed_watch.lock().unwrap().as_ref() == Some(&s) {
            return Err(StoreError::NotApplied {
                code: "injected_watch_failure".into(),
            });
        }
        self.inner.watch(s, a).await
    }
    async fn unwatch(&mut self, s: &Sid) -> Result<(), StoreError> {
        self.inner.unwatch(s).await
    }
    async fn next(&mut self, l: ReadLimits, w: Duration) -> Result<FeedBatch, StoreError> {
        if self.fail.swap(false, Ordering::SeqCst) {
            return Err(StoreError::NotApplied {
                code: "injected_reader_failure".into(),
            });
        }
        let mut batch = self.inner.next(l, w).await?;
        if let Some(sid) = self.mixed_failure.lock().unwrap().clone() {
            batch.failures.push(SessionFeedFailure {
                sid,
                error: StoreError::NotApplied {
                    code: "injected_watch_failure".into(),
                },
            });
        }
        Ok(batch)
    }
}
#[async_trait]
impl InboxConsumerPort for FaultConsumer {
    fn reader_resources(&self) -> Option<InboxReaderResources> {
        self.inner.reader_resources()
    }
    fn scan_budget_profile(
        &self,
    ) -> Option<bcs_service_api::port::repo::distributed::budget::ScanBudgetProfile> {
        InboxConsumerPort::scan_budget_profile(self.inner.as_ref())
    }
    async fn open(&self, s: ShardId) -> Result<Box<dyn ShardFeedPort>, StoreError> {
        let inner = self.inner.open(s).await?;
        self.opens.fetch_add(1, Ordering::SeqCst);
        Ok(Box::new(FaultFeed {
            inner,
            fail: self.fail.clone(),
            failed_watch: self.failed_watch.clone(),
            mixed_failure: self.mixed_failure.clone(),
        }))
    }
    async fn has_unconsumed(&self, s: &Sid, a: Option<&InboxPos>) -> Result<bool, StoreError> {
        self.inner.has_unconsumed(s, a).await
    }
    async fn confirm_committed(
        &self,
        d: &DeliveryReceipt,
        p: &CommitReceipt,
    ) -> Result<(), StoreError> {
        self.inner.confirm_committed(d, p).await
    }
    async fn trim_committed(&self, p: &CommitReceipt) -> Result<(), StoreError> {
        self.inner.trim_committed(p).await
    }
    async fn enumerate_pending(&self, s: ShardId, p: PageRequest) -> Result<Page<Sid>, StoreError> {
        self.inner.enumerate_pending(s, p).await
    }
}
