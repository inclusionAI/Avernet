use super::*;
use async_trait::async_trait;

// Dropping an acquire future during the provider write is also an unknown
// result. Preserve its reservation without changing the public port contract.
struct AcquisitionWriteGuard<'a> {
    store: &'a CoordinationStore,
    sid: &'a Sid,
    holder: &'a ShardOwner,
    definite: bool,
}
impl Drop for AcquisitionWriteGuard<'_> {
    fn drop(&mut self) {
        if !self.definite
            && let Ok(mut cache) = self.store.cache.lock()
            && let Some(entry) = cache.get_mut(&self.sid.0)
            && entry.holder == *self.holder
        {
            entry.uncertain_acquisition = true;
        }
    }
}
fn owner_valid(owner: &SessionOwnership) -> Result<(), StoreError> {
    if KeyLayout::shard_of(&owner.sid)? != owner.shard {
        return Err(invalid("session_shard"));
    }
    Ok(())
}
fn receipt(sid: &Sid, envelope: &CheckpointEnvelope) -> Option<CommitReceipt> {
    envelope
        .checkpoint
        .outbox
        .as_ref()
        .map(|batch| CommitReceipt {
            sid: sid.clone(),
            owner: envelope.owner.clone(),
            commit_id: batch.commit_id.clone(),
            content_digest: batch.content_digest.clone(),
            sequence: batch.source_commit,
            consumed: envelope.checkpoint.consumed.clone(),
            proof: format!(
                "document:{}:{}:{}",
                sid.0, batch.source_commit.0, batch.commit_id.0
            ),
        })
}
fn acquired(sid: &Sid, claim: &ShardClaim, envelope: StoredCheckpoint) -> AcquiredSession {
    AcquiredSession {
        ownership: SessionOwnership {
            sid: sid.clone(),
            shard: claim.shard,
            owner: claim.owner.clone(),
        },
        last_commit: receipt(sid, &envelope),
        envelope: envelope.envelope,
    }
}
#[async_trait]
impl SessionStorePort for CoordinationStore {
    fn local_session_capacity(&self) -> Option<usize> {
        Some(self.config.max_cached_sessions)
    }

    async fn release_local(&self, owner: &SessionOwnership) -> Result<(), StoreError> {
        owner_valid(owner)?;
        let mut cache = self
            .cache
            .lock()
            .map_err(|_| not_applied("cache_poisoned"))?;
        if cache
            .get(&owner.sid.0)
            .is_some_and(|entry| entry.holder == owner.owner)
        {
            cache.remove(&owner.sid.0);
        }
        Ok(())
    }
    async fn get_spec(&self, sid: &Sid) -> Result<Option<SessionSpec>, StoreError> {
        Ok(self.read(&self.layout.spec(sid)?).await?.map(|x| x.value))
    }
    async fn acquire(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
        initial: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError> {
        let result = self.acquire_inner(sid, claim, initial).await;
        // Definite non-acquisitions release observation slots. Unknown writes
        // and confirmed existence survive errors, especially a missing known
        // checkpoint: forgetting it would make a retry initialize lost state.
        let uncertain = {
            let mut cache = self.cache.lock().map_err(|_| not_applied("cache_poisoned"))?;
            if let Some(entry) = cache.get_mut(&sid.0) {
                if entry.holder == claim.owner && matches!(&result, Ok(AcquireOutcome::Acquired(_))) {
                    entry.uncertain_acquisition = false;
                }
                entry.uncertain_acquisition
            } else { false }
        };
        let release = match &result {
            Ok(AcquireOutcome::Acquired(_)) | Err(StoreError::OutcomeUnknown { .. }) => false,
            Ok(AcquireOutcome::Fenced) => true,
            Ok(AcquireOutcome::Deferred { .. }) => !uncertain,
            Err(_) => !uncertain && self.cached(sid)?.is_none(),
        };
        if release {
            self.release_local(&SessionOwnership {
                sid: sid.clone(),
                shard: claim.shard,
                owner: claim.owner.clone(),
            })
            .await?;
        }
        result
    }
    async fn handoff_session(
        &self,
        source: &SessionOwnership,
        target: &ShardClaim,
        id: &RequestId,
    ) -> Result<SessionTransferOutcome, StoreError> {
        owner_valid(source)?;
        if source.shard != target.shard || target.owner.epoch <= source.owner.epoch {
            return Err(invalid("session_handoff_input"));
        }
        let key = self.layout.ckpt(&source.sid)?;
        for _ in 0..RETRIES {
            let Some(record) = self.current(target.shard).await? else {
                return Ok(SessionTransferOutcome::Fenced);
            };
            if record.claim != *target
                || !self.confirmed_alive(&target.owner.instance, &id.0).await?
            {
                return Ok(SessionTransferOutcome::Fenced);
            }
            let Some(old) = self.checkpoint(&source.sid).await? else {
                return Err(not_applied("handoff_checkpoint_missing"));
            };
            if old.value.owner == target.owner {
                return Ok(SessionTransferOutcome::AlreadyTransferred);
            }
            if old.value.owner != source.owner {
                return Ok(SessionTransferOutcome::Fenced);
            }
            if !record.handoff.as_ref().is_some_and(|h| {
                h.request_id == *id
                    && h.from.owner == source.owner
                    && h.from.shard == source.shard
                    && !h.source_done
            }) {
                return Ok(SessionTransferOutcome::Fenced);
            }
            let mut value = old.value;
            value.owner = target.owner.clone();
            if let Some(version) = self.write(&key, Some(old.version), &value, &id.0).await? {
                self.remember(&source.sid, Versioned { version, value })?;
                if !self.valid_claim(target).await.map_err(|_| unknown(&id.0))? {
                    return Ok(SessionTransferOutcome::Fenced);
                }
                let confirmed = self
                    .checkpoint(&source.sid)
                    .await
                    .map_err(|_| unknown(&id.0))?
                    .ok_or_else(|| unknown(&id.0))?;
                return Ok(if confirmed.value.owner == target.owner {
                    SessionTransferOutcome::Transferred
                } else {
                    SessionTransferOutcome::Fenced
                });
            }
        }
        Err(not_applied("session_handoff_contention"))
    }
    async fn load(&self, sid: &Sid) -> Result<Option<CheckpointEnvelope>, StoreError> {
        Ok(self.checkpoint(sid).await?.map(|x| x.value.envelope))
    }
    async fn commit(&self, request: CommitRequest) -> Result<CommitOutcome, StoreError> {
        owner_valid(&request.owner)?;
        if request.expected_commit_seq.0.checked_add(1) != Some(request.candidate.commit_seq.0)
            || request.candidate.format_version != 1
        {
            return Err(invalid("commit_sequence_or_format"));
        }
        if !request.candidate.outbox.as_ref().is_some_and(|b| {
            b.commit_id == request.commit_id
                && b.content_digest == request.content_digest
                && b.source_commit == request.candidate.commit_seq
        }) {
            return Err(invalid("commit_batch"));
        }
        if request
            .candidate
            .consumed
            .as_ref()
            .is_some_and(|p| p.sid != request.owner.sid)
        {
            return Err(invalid("commit_position"));
        }
        let sid = &request.owner.sid;
        let key = self.layout.ckpt(sid)?;
        let mut old = match self.cached(sid)? {
            Some(v) => Some(v),
            None => self.checkpoint(sid).await?,
        };
        // A cached receipt cannot authorize an idempotent success after an
        // external seal. Only a new business sequence uses the one-write path.
        if old
            .as_ref()
            .is_some_and(|v| v.value.checkpoint.commit_seq >= request.candidate.commit_seq)
        {
            old = self.checkpoint(sid).await?;
        }
        for _ in 0..RETRIES {
            let Some(current) = old else {
                return Err(not_applied("commit_checkpoint_missing"));
            };
            if current.value.owner != request.owner.owner {
                return Ok(CommitOutcome::Fenced);
            }
            if let Some(previous) = receipt(sid, &current.value)
                && previous.commit_id == request.commit_id
            {
                return Ok(
                    if previous.content_digest == request.content_digest
                        && current.value.checkpoint == request.candidate
                    {
                        CommitOutcome::AlreadyCommitted(previous)
                    } else {
                        CommitOutcome::ContentConflict
                    },
                );
            }
            if current.value.checkpoint.commit_seq != request.expected_commit_seq {
                return Ok(CommitOutcome::CommitSeqConflict);
            }
            let mut value = current.value;
            value.checkpoint = request.candidate.clone();
            value.recovery_attempts.clear();
            value.recovery.takeover_streak = 0;
            value.recovery.last_attempt = None;
            value.recovery.last_active_at = now();
            if let Some(version) = self
                .write(&key, Some(current.version), &value, &request.commit_id.0)
                .await?
            {
                let proof = receipt(sid, &value).ok_or_else(|| invalid("commit_receipt"))?;
                self.remember(sid, Versioned { value, version })?;
                return Ok(CommitOutcome::Committed(proof));
            }
            old = self.checkpoint(sid).await?;
        }
        Err(not_applied("commit_contention"))
    }
    async fn resolve_commit(
        &self,
        owner: &SessionOwnership,
        id: &CommitId,
        digest: &Digest,
    ) -> Result<CommitResolution, StoreError> {
        owner_valid(owner)?;
        let Some(current) = self.checkpoint(&owner.sid).await? else {
            return Ok(CommitResolution::OutcomeUnknown);
        };
        if current.value.owner != owner.owner {
            return Ok(CommitResolution::Fenced);
        }
        if let Some(receipt) = receipt(&owner.sid, &current.value)
            && receipt.commit_id == *id
        {
            return Ok(if receipt.content_digest == *digest {
                CommitResolution::Committed(receipt)
            } else {
                CommitResolution::ContentConflict
            });
        }
        // An earlier timed-out write may still arrive. A read alone cannot cancel it.
        Ok(CommitResolution::OutcomeUnknown)
    }
    async fn record_recovery_attempt(
        &self,
        owner: &SessionOwnership,
        expected: CommitSeq,
        attempt: RequestId,
    ) -> Result<RecoveryUpdate, StoreError> {
        owner_valid(owner)?;
        let key = self.layout.ckpt(&owner.sid)?;
        for _ in 0..RETRIES {
            let Some(old) = self.checkpoint(&owner.sid).await? else {
                return Err(not_applied("recovery_checkpoint_missing"));
            };
            let mut value = old.value;
            if value.owner != owner.owner {
                return Ok(RecoveryUpdate::Fenced);
            }
            if value.checkpoint.commit_seq != expected {
                return Ok(RecoveryUpdate::Conflict);
            }
            if value.recovery_attempts.contains(&attempt) {
                return Ok(RecoveryUpdate::Recorded {
                    metadata: value.recovery.clone(),
                });
            }
            if value.recovery_attempts.len() >= 64 {
                return Err(not_applied("recovery_attempt_limit"));
            }
            value.recovery_attempts.push(attempt.clone());
            value.recovery.takeover_streak = value
                .recovery
                .takeover_streak
                .checked_add(1)
                .ok_or_else(|| invalid("recovery_exhausted"))?;
            value.recovery.last_attempt = Some(attempt.clone());
            if let Some(version) = self
                .write(&key, Some(old.version), &value, &attempt.0)
                .await?
            {
                let metadata = value.recovery.clone();
                self.remember(&owner.sid, Versioned { value, version })?;
                return Ok(RecoveryUpdate::Recorded { metadata });
            }
        }
        Err(not_applied("recovery_contention"))
    }
}

impl CoordinationStore {
    async fn acquire_inner(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
        initial: Checkpoint,
    ) -> Result<AcquireOutcome, StoreError> {
        if KeyLayout::shard_of(sid)? != claim.shard
            || initial.commit_seq != CommitSeq(0)
            || initial.outbox.is_some()
            || initial.format_version != 1
            || initial.consumed.as_ref().is_some_and(|p| p.sid != *sid)
        {
            return Err(invalid("acquire_input"));
        }
        if !self.reserve_session(sid, &claim.owner)? {
            return Ok(AcquireOutcome::Fenced);
        }
        let key = self.layout.ckpt(sid)?;
        for _ in 0..RETRIES {
            if !self.valid_claim(claim).await? {
                return Ok(AcquireOutcome::Fenced);
            }
            let Some(record) = self.current(claim.shard).await? else {
                return Ok(AcquireOutcome::Fenced);
            };
            if record.claim != *claim {
                return Ok(AcquireOutcome::Fenced);
            }
            let old = self.checkpoint(sid).await?;
            let mut envelope = if let Some(old) = &old {
                if old.value.owner.epoch > claim.owner.epoch
                    || (old.value.owner.epoch == claim.owner.epoch
                        && old.value.owner != claim.owner)
                {
                    return Ok(AcquireOutcome::Fenced);
                }
                if self.protected(&record, &old.value.owner).await? {
                    return Ok(AcquireOutcome::Deferred {
                        check_after: Duration::from_millis(500),
                    });
                }
                old.value.clone()
            } else {
                StoredCheckpoint {
                    envelope: CheckpointEnvelope {
                        owner: claim.owner.clone(),
                        checkpoint: initial.clone(),
                        recovery: RecoveryMetadata {
                            takeover_streak: 0,
                            last_attempt: None,
                            quarantined: false,
                            last_active_at: now(),
                        },
                    },
                    recovery_attempts: vec![],
                }
            };
            if old
                .as_ref()
                .is_some_and(|old| old.value.owner == claim.owner)
            {
                if self.valid_claim(claim).await? {
                    return Ok(AcquireOutcome::Acquired(acquired(sid, claim, envelope)));
                }
                return Ok(AcquireOutcome::Fenced);
            }
            envelope.owner = claim.owner.clone();
            let mut attempt = AcquisitionWriteGuard {
                store: self, sid, holder: &claim.owner, definite: false,
            };
            let outcome = self.write(&key, old.map(|x| x.version), &envelope, &sid.0).await;
            attempt.definite = !matches!(&outcome, Err(StoreError::OutcomeUnknown { .. }));
            drop(attempt);
            if let Some(version) = outcome? {
                self.remember(
                    sid,
                    Versioned {
                        value: envelope,
                        version,
                    },
                )?;
                if !self.valid_claim(claim).await.map_err(|_| unknown(&sid.0))? {
                    return Ok(AcquireOutcome::Fenced);
                }
                let confirmed = self
                    .checkpoint(sid)
                    .await
                    .map_err(|_| unknown(&sid.0))?
                    .ok_or_else(|| unknown(&sid.0))?;
                if confirmed.value.owner != claim.owner {
                    return Ok(AcquireOutcome::Fenced);
                }
                return Ok(AcquireOutcome::Acquired(acquired(
                    sid,
                    claim,
                    confirmed.value,
                )));
            }
        }
        Err(not_applied("acquire_contention"))
    }
}
