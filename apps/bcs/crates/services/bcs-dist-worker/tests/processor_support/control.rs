use super::*;
use bcs_service_api::port::repo::distributed::admission::*;
use bcs_service_api::port::repo::distributed::control::*;
pub struct FaultControl {
    pub inner: Arc<CoordinationStore>,
    pub isolate_after_runner_success: AtomicBool,
    pub isolate_after_begin: AtomicBool,
    pub block_after_replay_begin: AtomicBool,
}
#[async_trait]
impl SessionControlPort for FaultControl {
    async fn acquire_control(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
    ) -> Result<ControlAcquireOutcome, StoreError> {
        self.inner.acquire_control(sid, claim).await
    }
    async fn load_control(&self, sid: &Sid) -> Result<Option<ControlSnapshot>, StoreError> {
        self.inner.load_control(sid).await
    }
    async fn get_isolation(
        &self,
        sid: &Sid,
        id: &RequestId,
    ) -> Result<Option<IsolationRecord>, StoreError> {
        self.inner.get_isolation(sid, id).await
    }
    async fn get_attempt(
        &self,
        sid: &Sid,
        id: &RequestId,
    ) -> Result<Option<RiskAttempt>, StoreError> {
        self.inner.get_attempt(sid, id).await
    }
    async fn begin_attempt(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
    ) -> Result<ControlOutcome, StoreError> {
        let result = self
            .inner
            .begin_attempt(guard.clone(), attempt.clone())
            .await?;
        if self.isolate_after_begin.swap(false, Ordering::SeqCst) {
            self.race(&result, false).await?;
            return self.inner.begin_attempt(guard, attempt).await;
        }
        Ok(result)
    }
    async fn begin_replay_attempt(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        effect: EffectId,
        content_digest: Digest,
    ) -> Result<ControlOutcome, StoreError> {
        let result = self
            .inner
            .begin_replay_attempt(
                guard.clone(),
                attempt.clone(),
                effect.clone(),
                content_digest.clone(),
            )
            .await?;
        if self.block_after_replay_begin.swap(false, Ordering::SeqCst) {
            self.race(&result, true).await?;
            return self
                .inner
                .begin_replay_attempt(guard, attempt, effect, content_digest)
                .await;
        }
        Ok(result)
    }
    async fn finish_attempt(
        &self,
        guard: ControlGuard,
        attempt: RequestId,
        outcome: AttemptOutcome,
    ) -> Result<ControlOutcome, StoreError> {
        let result = self
            .inner
            .finish_attempt(guard.clone(), attempt.clone(), outcome.clone())
            .await?;
        if self
            .isolate_after_runner_success
            .swap(false, Ordering::SeqCst)
            && matches!(outcome, AttemptOutcome::Succeeded { .. })
        {
            let c = match &result {
                ControlOutcome::Applied(c) | ControlOutcome::AlreadyApplied(c) => c,
                _ => return Ok(result),
            };
            if c.control
                .active_attempt
                .as_ref()
                .is_some_and(|a| a.replay.is_none())
            {
                let identity = AttemptIdentity {
                    id: RequestId("racing-isolation-attempt".into()),
                    head: c.head,
                    state_digest: c.checkpoint_digest.clone(),
                    input_digest: Digest("race".into()),
                    context_digest: Digest("race".into()),
                    frozen_now: TimestampMillis(1000),
                };
                self.inner
                    .isolate(
                        ControlGuard {
                            owner: c.owner.clone(),
                            expected_head: c.head,
                            expected_revision: c.control.revision,
                        },
                        identity,
                        AttemptOutcome::Deterministic {
                            code: "race".into(),
                            isolation_id: RequestId("racing-isolation".into()),
                            safe_to_finalize: true,
                        },
                    )
                    .await?;
                return self.inner.finish_attempt(guard, attempt, outcome).await;
            }
        }
        Ok(result)
    }
    async fn isolate(
        &self,
        guard: ControlGuard,
        attempt: AttemptIdentity,
        outcome: AttemptOutcome,
    ) -> Result<ControlOutcome, StoreError> {
        self.inner.isolate(guard, attempt, outcome).await
    }
    async fn record_closure(
        &self,
        guard: ControlGuard,
        closure: AdmissionClosure,
    ) -> Result<ControlOutcome, StoreError> {
        self.inner.record_closure(guard, closure).await
    }
    async fn record_closure_complete(
        &self,
        guard: ControlGuard,
        certificate: ClosureCertificate,
    ) -> Result<ControlOutcome, StoreError> {
        self.inner.record_closure_complete(guard, certificate).await
    }
    async fn begin_recovery(
        &self,
        authorization: RecoveryAuthorization,
    ) -> Result<ControlOutcome, StoreError> {
        self.inner.begin_recovery(authorization).await
    }
    async fn confirm_recovery(
        &self,
        guard: ControlGuard,
        receipt: GateRecoveryReceipt,
    ) -> Result<ControlOutcome, StoreError> {
        self.inner.confirm_recovery(guard, receipt).await
    }
}

impl FaultControl {
    async fn race(&self, result: &ControlOutcome, blocked: bool) -> Result<(), StoreError> {
        let c = match result {
            ControlOutcome::Applied(c) | ControlOutcome::AlreadyApplied(c) => c,
            _ => return Ok(()),
        };
        let attempt = AttemptIdentity {
            id: RequestId("racing-begin-attempt".into()),
            head: c.head,
            state_digest: c.checkpoint_digest.clone(),
            input_digest: Digest("race".into()),
            context_digest: Digest("race".into()),
            frozen_now: TimestampMillis(1000),
        };
        self.inner
            .isolate(
                ControlGuard {
                    owner: c.owner.clone(),
                    expected_head: c.head,
                    expected_revision: c.control.revision,
                },
                attempt,
                AttemptOutcome::Deterministic {
                    code: "race".into(),
                    isolation_id: RequestId("racing-begin-isolation".into()),
                    safe_to_finalize: !blocked,
                },
            )
            .await?;
        Ok(())
    }
}
