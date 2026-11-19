use super::super::session::AcquisitionWriteGuard;
use super::*;
struct Reservation<'a> {
    store: &'a CoordinationStore,
    sid: &'a Sid,
    holder: &'a ShardOwner,
    keep: bool,
    discard_observation: bool,
}
impl Drop for Reservation<'_> {
    fn drop(&mut self) {
        if !self.keep
            && let Ok(mut cache) = self.store.cache.lock()
            && cache.get(&self.sid.0).is_some_and(|entry| {
                entry.holder == *self.holder
                    && !entry.uncertain_acquisition
                    && (self.discard_observation
                        || (!entry.known_checkpoint && entry.checkpoint.is_none()))
            })
        {
            cache.remove(&self.sid.0);
        }
    }
}
impl CoordinationStore {
    pub(super) async fn control_acquire(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
    ) -> Result<ControlAcquireOutcome, StoreError> {
        if KeyLayout::shard_of(sid)? != claim.shard {
            return Err(invalid("control_acquire_shard"));
        }
        let prior_known = self.known_checkpoint(sid)?;
        if !self.reserve_session(sid, &claim.owner)? {
            return Ok(ControlAcquireOutcome::Fenced);
        }
        let mut reservation = Reservation {
            store: self,
            sid,
            holder: &claim.owner,
            keep: false,
            discard_observation: false,
        };
        let result = budget("acquire_control", async {
            for retry in 0..8 {
                backoff(retry).await?;
                if !self.valid_claim(claim).await? {
                    return Ok(ControlAcquireOutcome::Fenced);
                }
                let Some(record) = self.current(claim.shard).await? else {
                    return Ok(ControlAcquireOutcome::Fenced);
                };
                if record.claim != *claim {
                    return Ok(ControlAcquireOutcome::Fenced);
                }
                let Some(mut header) = self.control_header(sid).await? else {
                    return Ok(ControlAcquireOutcome::Missing);
                };
                let old = &header.snapshot.owner.owner;
                if old.epoch > claim.owner.epoch
                    || (old.epoch == claim.owner.epoch && old != &claim.owner)
                {
                    return Ok(ControlAcquireOutcome::Fenced);
                }
                if self.protected(&record, old).await? {
                    return Ok(ControlAcquireOutcome::Deferred {
                        check_after: Duration::from_millis(500),
                    });
                }
                if old == &claim.owner {
                    return Ok(if self.valid_claim(claim).await? {
                        ControlAcquireOutcome::Acquired(header.snapshot)
                    } else {
                        ControlAcquireOutcome::Fenced
                    });
                }
                header.raw["owner"] = encode_json(&claim.owner)?;
                self.observe_checkpoint(sid, true)?;
                let mut write_guard = AcquisitionWriteGuard {
                    store: self,
                    sid,
                    holder: &claim.owner,
                    definite: false,
                };
                let result = self
                    .write(
                        &self.layout.ckpt(sid)?,
                        Some(header.version),
                        &header.raw,
                        "acquire_control",
                    )
                    .await;
                write_guard.definite = matches!(
                    &result,
                    Ok(None) | Err(StoreError::NotApplied { .. }) | Err(StoreError::Invalid { .. })
                );
                drop(write_guard);
                if result?.is_some() {
                    if !self
                        .valid_claim(claim)
                        .await
                        .map_err(|_| unknown("acquire_control"))?
                    {
                        return Ok(ControlAcquireOutcome::Fenced);
                    }
                    let confirmed = self
                        .control_header(sid)
                        .await
                        .map_err(|_| unknown("acquire_control"))?
                        .ok_or_else(|| unknown("acquire_control"))?;
                    return Ok(if confirmed.snapshot.owner.owner == claim.owner {
                        ControlAcquireOutcome::Acquired(confirmed.snapshot)
                    } else {
                        ControlAcquireOutcome::Fenced
                    });
                }
            }
            Err(unknown("acquire_control"))
        })
        .await;
        reservation.discard_observation = !prior_known
            && matches!(
                result,
                Ok(ControlAcquireOutcome::Deferred { .. } | ControlAcquireOutcome::Fenced)
            );
        if let Ok(mut cache) = self.cache.lock()
            && let Some(entry) = cache.get_mut(&sid.0)
            && entry.holder == claim.owner
        {
            if matches!(
                result,
                Ok(ControlAcquireOutcome::Acquired(_) | ControlAcquireOutcome::Fenced)
            ) {
                entry.uncertain_acquisition = false;
            }
            reservation.keep = matches!(result, Ok(ControlAcquireOutcome::Acquired(_)));
        }
        result
    }
}
