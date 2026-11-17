use crate::runtime::*;
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{common::*, inbox::*, ownership::*, session::*},
    types::dist_runtime::*,
};
use std::num::NonZeroU32;
use tokio::time::timeout;
impl WorkerRuntime {
    pub(crate) async fn recover_page(
        &self,
        claim: &ShardClaim,
        budget: &mut usize,
    ) -> Result<(), RuntimeError> {
        if self.state()?.draining || *budget == 0 {
            return Ok(());
        }
        let need_page = self
            .state()?
            .scans
            .get(&claim.shard)
            .is_none_or(|s| s.queue.is_empty());
        if need_page {
            let after = self
                .state()?
                .scans
                .get(&claim.shard)
                .and_then(|s| s.next.clone());
            let page = self
                .bounded(
                    self.inbox.enumerate_pending(
                        claim.shard,
                        PageRequest {
                            after,
                            limit: NonZeroU32::new(self.config.page_size)
                                .ok_or_else(|| err("page_size"))?,
                        },
                    ),
                )
                .await?;
            let mut state = self.state()?;
            state.scans.insert(
                claim.shard,
                Scan {
                    queue: page.items.into(),
                    next: page.next,
                },
            );
        }
        for _ in 0..*budget {
            let sid = {
                let mut state = self.state()?;
                if !self.ready_locked(&state)
                    || state.sessions.len() + state.acquisitions.len()
                        >= self.config.max_active_sessions
                {
                    return Ok(());
                }
                state
                    .scans
                    .get_mut(&claim.shard)
                    .and_then(|s| s.queue.pop_front())
            };
            let Some(sid) = sid else { break };
            if self
                .state()?
                .sessions
                .get(&sid)
                .is_some_and(|s| s.acquired.ownership.owner == claim.owner)
            {
                continue;
            }
            *budget -= 1;
            self.acquire_session(&sid, claim).await?;
        }
        Ok(())
    }
    pub(crate) async fn acquire_session(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
    ) -> Result<(), RuntimeError> {
        // Registration is under the same local lock as shutdown's admission gate.
        // A dropped/timeout future leaves its acquisition in the in-flight set.
        {
            let mut state = self.state()?;
            if state.draining && !state.acquisitions.contains_key(sid) {
                return Ok(());
            }
            state.acquisitions.insert(sid.clone(), claim.clone());
        }
        let Some(spec) = self.bounded(self.sessions.get_spec(sid)).await? else {
            return Err(err("pending_session_spec_missing"));
        };
        let initial = (self.initialize)(&spec)?;
        match timeout(
            self.config.request_timeout,
            self.sessions.acquire(sid, claim, initial),
        )
        .await
        {
            Ok(Ok(AcquireOutcome::Acquired(acquired))) => {
                // The durable claim and session are confirmed by acquire. Install
                // the separate scheduling fence before exposing the session.
                let observed = self
                    .bounded(self.scheduling.observe(&acquired.ownership))
                    .await;
                match observed {
                    Ok(ScheduleObservation::Observed(_)) => {
                        let mut state = self.state()?;
                        let pending = acquired
                            .envelope
                            .checkpoint
                            .outbox
                            .as_ref()
                            .map_or(0, |b| b.effects.len() as u64);
                        state.sessions.insert(
                            sid.clone(),
                            Session {
                                acquired,
                                phase: Phase::Recovering,
                                pending_effects: pending,
                                pending_schedule: false,
                            },
                        );
                        state.acquisitions.remove(sid);
                    }
                    Ok(ScheduleObservation::Fenced) => {
                        // The store acquired a local cache slot, but no processor
                        // has seen this session. Keep the pending acquisition if
                        // cleanup fails so a normal tick can retry it.
                        self.bounded(self.sessions.release_local(&acquired.ownership))
                            .await?;
                        self.state()?.acquisitions.remove(sid);
                    }
                    Err(_) => return Err(err("schedule_observe_unknown")),
                }
            }
            Ok(Ok(AcquireOutcome::Deferred { check_after })) => {
                tracing::debug!(sid=%sid.0, shard=claim.shard.0, epoch=claim.owner.epoch.0, check_after_ms=check_after.as_millis(), "Worker session waits for graceful handoff");
                self.state()?.acquisitions.remove(sid);
            }
            Ok(Ok(AcquireOutcome::Fenced)) => {
                tracing::debug!(sid=%sid.0, shard=claim.shard.0, epoch=claim.owner.epoch.0, "Worker session acquisition fenced");
                self.state()?.acquisitions.remove(sid);
            }
            Ok(Err(StoreError::OutcomeUnknown { .. })) | Err(_) => {
                return Err(err("session_acquire_unknown"));
            }
            Ok(Err(error)) => {
                // Definite operation failure has no outstanding write from this
                // attempt. A previous unknown remains until confirmed separately.
                return Err(store_error(error));
            }
        }
        Ok(())
    }
    pub(crate) async fn resolve_acquisitions(&self) -> Result<(), RuntimeError> {
        let mut budget = self.config.max_acquires_per_tick;
        self.resolve_acquisition_budget(&mut budget).await
    }
    pub(crate) async fn resolve_acquisition_budget(
        &self,
        budget: &mut usize,
    ) -> Result<(), RuntimeError> {
        let pending = {
            let state = self.state()?;
            let after = state.acquisition_cursor.as_ref();
            // Resume after the last attempted sid, then wrap. One unavailable
            // session must not permanently monopolize a full retry page.
            state
                .acquisitions
                .iter()
                .filter(|(sid, _)| after.is_none_or(|after| *sid > after))
                .chain(
                    state
                        .acquisitions
                        .iter()
                        .filter(|(sid, _)| after.is_some_and(|after| *sid <= after)),
                )
                .take(*budget)
                .map(|(sid, claim)| (sid.clone(), claim.clone()))
                .collect::<Vec<_>>()
        };
        let mut failure = None;
        for (sid, claim) in pending {
            *budget -= 1;
            self.state()?.acquisition_cursor = Some(sid.clone());
            // Re-running the same acquire preserves latest data and cannot
            // upgrade its claim. Cancellation retains the in-flight record.
            if let Err(error) = self.acquire_session(&sid, &claim).await {
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(()), Err)
    }
}
