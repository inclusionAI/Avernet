use super::*;
use async_trait::async_trait;
use serde::Deserialize;
#[derive(Clone, Serialize, Deserialize)]
struct StoredShard {
    record: ShardRecord,
    request_id: RequestId,
    request_digest: Digest,
}
fn digest<T: Serialize>(value: &T) -> Result<Digest, StoreError> {
    Ok(crate::identity::digest_json(&encode_json(value)?))
}
#[async_trait]
impl ShardLeasePort for CoordinationStore {
    async fn current(&self, shard: ShardId) -> Result<Option<ShardRecord>, StoreError> {
        Ok(self
            .read::<StoredShard>(&self.owner_key(shard)?)
            .await?
            .map(|x| x.value.record))
    }
    async fn claim(&self, request: ClaimShard) -> Result<LeaseClaim, StoreError> {
        if !self.confirmed_alive(&request.me, &request.request_id.0).await? {
            return Ok(LeaseClaim::Conflict);
        }
        let key = self.owner_key(request.shard)?;
        let fingerprint = digest(&(
            request.shard,
            &request.me,
            request.expected_epoch,
            &request.request_id,
        ))?;
        let old = self.read::<StoredShard>(&key).await?;
        if let Some(old) = &old {
            if old.value.request_id == request.request_id {
                if old.value.request_digest != fingerprint {
                    return Err(invalid("claim_content_conflict"));
                }
                return Ok(LeaseClaim::AlreadyClaimed(old.value.record.claim.clone()));
            }
            if request.expected_epoch != Some(old.value.record.claim.owner.epoch)
                || self.confirmed_alive(&old.value.record.claim.owner.instance,
                    &request.request_id.0).await?
            {
                return Ok(LeaseClaim::Conflict);
            }
        } else if request.expected_epoch.is_some() {
            return Ok(LeaseClaim::Conflict);
        }
        let epoch = request
            .expected_epoch
            .map_or(Some(1), |e| e.0.checked_add(1))
            .ok_or_else(|| invalid("epoch_exhausted"))?;
        let claim = ShardClaim {
            shard: request.shard,
            owner: ShardOwner {
                instance: request.me,
                epoch: WorkerEpoch(epoch),
            },
        };
        let value = StoredShard {
            record: ShardRecord {
                claim: claim.clone(),
                handoff: None,
            },
            request_id: request.request_id.clone(),
            request_digest: fingerprint,
        };
        if self
            .write(&key, old.map(|x| x.version), &value, &request.request_id.0)
            .await?
            .is_none()
        {
            return Ok(LeaseClaim::Conflict);
        }
        if !self
            .valid_claim(&claim)
            .await
            .map_err(|_| unknown(&request.request_id.0))?
        {
            return Ok(LeaseClaim::Conflict);
        }
        self.timers
            .lock()
            .map_err(|_| unknown(&request.request_id.0))?
            .remove(&claim.shard.0);
        Ok(LeaseClaim::Claimed(claim))
    }
    async fn handoff(&self, request: HandoffRequest) -> Result<HandoffOutcome, StoreError> {
        let received_at = Instant::now();
        if request.grace.is_zero() || request.target == request.from.owner.instance {
            return Err(invalid("handoff_input"));
        }
        let key = self.owner_key(request.from.shard)?;
        let fingerprint = digest(&request)?;
        let epoch = request
            .from
            .owner
            .epoch
            .0
            .checked_add(1)
            .ok_or_else(|| invalid("epoch_exhausted"))?;
        let target = ShardClaim {
            shard: request.from.shard,
            owner: ShardOwner {
                instance: request.target.clone(),
                epoch: WorkerEpoch(epoch),
            },
        };
        // This entry is installed before the first write and never extended by retries.
        let timer_key = target.shard.0;
        let mut observed = false;
        for _ in 0..RETRIES {
            let Some(old) = self.read::<StoredShard>(&key).await? else {
                return Ok(HandoffOutcome::Fenced);
            };
            if old.value.request_id == request.request_id {
                if old.value.request_digest != fingerprint {
                    return Err(invalid("handoff_content_conflict"));
                }
                return Ok(HandoffOutcome::Accepted(old.value.record));
            }
            if old.value.record.claim != request.from {
                return Ok(HandoffOutcome::Fenced);
            }
            if old.value.record.handoff.is_some() {
                return Ok(HandoffOutcome::Rejected);
            }
            if !self.confirmed_alive(&request.from.owner.instance, &request.request_id.0).await?
                || !self.confirmed_alive(&request.target, &request.request_id.0).await?
            {
                return Ok(HandoffOutcome::Rejected);
            }
            if !observed {
                let mut timers = self
                    .timers
                    .lock()
                    .map_err(|_| not_applied("timer_poisoned"))?;
                if let Some((prior, end)) = timers.get(&timer_key) {
                    if prior.request_id == request.request_id && prior != &request {
                        return Err(invalid("handoff_content_conflict"));
                    }
                    if prior != &request && prior.from == request.from && Instant::now() < *end {
                        return Ok(HandoffOutcome::Rejected);
                    }
                }
                if !timers
                    .get(&timer_key)
                    .is_some_and(|(prior, _)| prior == &request)
                {
                    if !timers.contains_key(&timer_key) && timers.len() >= self.config.max_handoff_timers {
                        return Err(not_applied("handoff_capacity"));
                    }
                    timers.insert(
                        timer_key,
                        (
                            request.clone(),
                            received_at
                                .checked_add(request.grace)
                                .ok_or_else(|| invalid("handoff_budget"))?,
                        ),
                    );
                }
                observed = true;
            }
            let deadline = TimestampMillis(
                now()
                    .0
                    .saturating_add(i64::try_from(request.grace.as_millis()).unwrap_or(i64::MAX)),
            );
            let value = StoredShard {
                record: ShardRecord {
                    claim: target.clone(),
                    handoff: Some(ShardHandoff {
                        request_id: request.request_id.clone(),
                        from: request.from.clone(),
                        deadline,
                        source_done: false,
                    }),
                },
                request_id: request.request_id.clone(),
                request_digest: fingerprint.clone(),
            };
            if self
                .write(&key, Some(old.version), &value, &request.request_id.0)
                .await?
                .is_some()
            {
                if !self
                    .valid_claim(&target)
                    .await
                    .map_err(|_| unknown(&request.request_id.0))?
                {
                    return Ok(HandoffOutcome::Fenced);
                }
                return Ok(HandoffOutcome::Accepted(value.record));
            }
        }
        Err(not_applied("handoff_contention"))
    }
    async fn source_done(&self, target: &ShardClaim, id: &RequestId) -> Result<bool, StoreError> {
        self.change_handoff(target, id, false).await
    }
    async fn clear_handoff(&self, target: &ShardClaim, id: &RequestId) -> Result<bool, StoreError> {
        self.change_handoff(target, id, true).await
    }
}
impl CoordinationStore {
    async fn change_handoff(
        &self,
        target: &ShardClaim,
        id: &RequestId,
        clear: bool,
    ) -> Result<bool, StoreError> {
        let key = self.owner_key(target.shard)?;
        for _ in 0..RETRIES {
            if !self.valid_claim(target).await? {
                return Ok(false);
            }
            let Some(old) = self.read::<StoredShard>(&key).await? else {
                return Ok(false);
            };
            let mut value = old.value;
            if value.record.claim != *target {
                return Ok(false);
            }
            let Some(handoff) = value.record.handoff.as_ref() else {
                return Ok(clear && value.request_id == *id);
            };
            if handoff.request_id != *id {
                return Ok(false);
            }
            if clear {
                if !handoff.source_done
                    && self.liveness(&handoff.from.owner.instance).await? != Liveness::Dead
                    && self.waiting(&value.record)?
                {
                    return Ok(false);
                }
                value.record.handoff = None;
            } else {
                if handoff.source_done {
                    self.release_handoff_timer(target.shard, id)?;
                    return Ok(true);
                }
                if let Some(handoff) = value.record.handoff.as_mut() {
                    handoff.source_done = true;
                }
            }
            if self
                .write(&key, Some(old.version), &value, &id.0)
                .await?
                .is_some()
            {
                // Both source_done and clearing end source protection. Remove
                // only this exact request; a concurrent later handoff may have
                // already installed the next timer for the same shard.
                self.release_handoff_timer(target.shard, id)?;
                return Ok(true);
            }
        }
        Err(not_applied("handoff_contention"))
    }
    pub(super) async fn protected(
        &self,
        record: &ShardRecord,
        owner: &ShardOwner,
    ) -> Result<bool, StoreError> {
        let Some(h) = &record.handoff else {
            return Ok(false);
        };
        if h.from.owner != *owner {
            return Ok(false);
        }
        if !h.source_done
            && self.waiting(record)?
            && self.liveness(&h.from.owner.instance).await? != Liveness::Dead
        {
            return Ok(true);
        }
        if !self.clear_handoff(&record.claim, &h.request_id).await? {
            return Err(unknown(&h.request_id.0));
        }
        Ok(false)
    }
}
