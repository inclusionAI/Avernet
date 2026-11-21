use super::*;
#[async_trait]
impl DispatchQueuePort for StoreDispatch {
    async fn claim(&self, request: DispatchClaimRequest) -> Result<ClaimOutcome, StoreError> {
        self.acquire(
            request.did,
            Claim {
                request: request.request_id,
                me: request.me,
                previous: None,
            },
        )
        .await
    }
    async fn resume_owned(&self, request: ResumeOwnedDispatch) -> Result<ClaimOutcome, StoreError> {
        self.acquire(
            request.did,
            Claim {
                request: request.request_id,
                me: request.previous.instance.clone(),
                previous: Some(request.previous),
            },
        )
        .await
    }
    async fn notifications(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn CommandFeedPort>, StoreError> {
        self.command_feed(pool, me).await
    }
}
impl StoreDispatch {
    async fn acquire(&self, did: Did, claim: Claim) -> Result<ClaimOutcome, StoreError> {
        if claim.request.0.is_empty() || claim.request.0.len() > 256 || bytes(&claim.request)? > 512
        {
            return Err(invalid("dispatch_claim_id"));
        }
        self.layout.held(&claim.me)?;
        let key = self.layout.disp(&did)?;
        let mut budget = Budget::new();
        loop {
            budget.next(&key).await?;
            if budget.io(&key, self.live(&claim.me)).await? != Liveness::Alive {
                return Err(unavailable("dispatch_claim_membership"));
            }
            let Some(mut stored) = budget.io(&key, self.document(&did)).await? else {
                return Ok(ClaimOutcome::NotFound);
            };
            if stored.value.ended() {
                return Ok(ClaimOutcome::Ended);
            }
            if let Some(old) = &stored.value.claim
                && old.request == claim.request
                && old != &claim
            {
                return Err(invalid("dispatch_claim_content"));
            }
            let current = stored.value.view.execution.holder.clone();
            if stored.value.claim.as_ref() == Some(&claim) {
                return current
                    .map(ClaimOutcome::OwnedByMe)
                    .ok_or_else(|| invalid("dispatch_claim_holder"));
            }
            if let Some(previous) = &claim.previous {
                if current.as_ref() != Some(previous) {
                    return Ok(match current {
                        Some(h) if h.instance == claim.me => ClaimOutcome::OwnedByMe(h),
                        Some(h) => ClaimOutcome::HeldByOther(h),
                        None => ClaimOutcome::NotFound,
                    });
                }
            } else if let Some(holder) = &current {
                if holder.instance == claim.me {
                    return Ok(ClaimOutcome::OwnedByMe(holder.clone()));
                }
                if budget.io(&key, self.live(&holder.instance)).await? != Liveness::Dead {
                    return Ok(ClaimOutcome::HeldByOther(holder.clone()));
                }
            }
            let epoch = current
                .map_or(Some(1), |h| h.epoch.0.checked_add(1))
                .ok_or_else(|| invalid("dispatch_epoch_exhausted"))?;
            let holder = Holder {
                instance: claim.me.clone(),
                epoch: HolderEpoch(epoch),
            };
            stored.value.view.execution.holder = Some(holder.clone());
            stored.value.claim = Some(claim.clone());
            stored.value.due = Some(TimestampMillis(0));
            if stored.value.view.execution.phase == DispatchPhase::Created {
                stored.value.view.execution.phase = DispatchPhase::Claimed;
            }
            if budget
                .io(&key, self.write(&key, Some(stored.version), &stored.value))
                .await?
            {
                let index = self.layout.held(&claim.me)?;
                let _ = self
                    .io(
                        async {
                            self.stores
                                .index
                                .add(
                                    &index,
                                    &[ZSetEntry {
                                        member: did.0.clone(),
                                        score: 0.0,
                                    }],
                                    ZAddMode::Upsert,
                                )
                                .await
                                .map(|_| ())
                                .map_err(|e| crate::error::from_write(&e, &index))
                        },
                        true,
                        &index,
                    )
                    .await;
                return Ok(ClaimOutcome::Acquired(Box::new(held::Held {
                    store: self.clone(),
                    holder,
                    view: stored.value.view,
                })));
            }
        }
    }
}
