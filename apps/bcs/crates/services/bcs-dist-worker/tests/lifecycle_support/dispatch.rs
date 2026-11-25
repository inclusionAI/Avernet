use crate::processor_support::Fixture;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    budget::ScanBudgetProfile,
    common::*,
    dispatch::*,
    session::{EffectKind, SessionStorePort},
};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicU8, Ordering},
};

pub struct Board {
    inner: Arc<bcs_dist_store::dispatch::StoreDispatch>,
    sessions: Arc<bcs_dist_store::coordination::CoordinationStore>,
    pub requests: Mutex<Vec<CommittedCancelIntent>>,
    /// 1 loses the first applied reply, then 2 holds later attempts unavailable.
    pub cancel_fault: AtomicU8,
    pub conflict: AtomicBool,
    pub reschedules: Mutex<Vec<Did>>,
}
impl Board {
    pub fn new(f: &Fixture) -> Arc<Self> {
        Arc::new(Self {
            inner: f.dispatch.clone(),
            sessions: f.coordination.clone(),
            requests: Mutex::new(vec![]),
            cancel_fault: AtomicU8::new(0),
            conflict: AtomicBool::new(false),
            reschedules: Mutex::new(vec![]),
        })
    }
}
#[async_trait]
impl DispatchControlPort for Board {
    fn scan_budget_profile(&self) -> Option<ScanBudgetProfile> {
        self.inner.scan_budget_profile()
    }
    async fn create(&self, r: CreateCommittedDispatch) -> Result<IdempotentWrite, StoreError> {
        self.inner.create(r).await
    }
    async fn request_cancel(&self, r: CommittedCancelIntent) -> Result<ControlOutcome, StoreError> {
        let durable = self
            .sessions
            .load(&r.source.commit.sid)
            .await?
            .unwrap()
            .checkpoint;
        let batch = durable.outbox.as_ref().unwrap();
        assert_eq!(batch.source_commit, r.source.commit.sequence);
        assert!(
            batch.effects.iter().any(|e| e.id == r.source.effect_id
                && e.content_digest == r.source.content_digest
                && e.kind
                    == EffectKind::RequestCancel {
                        did: r.did.clone(),
                        intent: r.intent.clone()
                    }),
            "cancel must be replayed from the durable exact effect"
        );
        self.requests.lock().unwrap().push(r.clone());
        if self.conflict.load(Ordering::SeqCst) {
            return Ok(ControlOutcome::ContentConflict);
        }
        if self.cancel_fault.load(Ordering::SeqCst) == 2 {
            return Err(StoreError::NotApplied {
                code: "cancel_unavailable".into(),
            });
        }
        let result = self.inner.request_cancel(r.clone()).await?;
        if self
            .cancel_fault
            .compare_exchange(1, 2, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
        {
            return Err(StoreError::OutcomeUnknown {
                operation_id: r.intent.request_id.0,
            });
        }
        Ok(result)
    }
    async fn push_input(&self, r: CommittedDispatchInput) -> Result<ControlOutcome, StoreError> {
        self.inner.push_input(r).await
    }
    async fn notify(&self, p: &PoolId, d: &Did) -> Result<(), StoreError> {
        self.inner.notify(p, d).await
    }
    async fn get(&self, d: &Did) -> Result<Option<DispatchRecord>, StoreError> {
        self.inner.get(d).await
    }
    async fn scan_due(
        &self,
        s: ShardId,
        n: TimestampMillis,
        p: PageRequest,
    ) -> Result<Page<Did>, StoreError> {
        self.inner.scan_due(s, n, p).await
    }
    async fn reschedule(
        &self,
        d: &Did,
        r: Revision,
        n: TimestampMillis,
    ) -> Result<RecheckOutcome, StoreError> {
        let result = self.inner.reschedule(d, r, n).await?;
        if result == RecheckOutcome::Applied {
            self.reschedules.lock().unwrap().push(d.clone());
        }
        Ok(result)
    }
}
