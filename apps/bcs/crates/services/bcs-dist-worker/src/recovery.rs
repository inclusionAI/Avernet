use crate::runtime::*;
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{common::*, inbox::*, ownership::*, session::*},
    types::dist_runtime::*,
};
use std::{num::NonZeroU32, time::Duration};
use tokio::time::{Instant, timeout};
enum RecoveryVisit {
    Visited,
    Deferred,
}

impl WorkerRuntime {
    pub(crate) async fn recover_owned(&self, budget: &mut usize) -> Result<(), RuntimeError> {
        let mut visited = std::collections::BTreeSet::new();
        let mut failure = None;
        for _ in 0..self.config.max_shards_per_tick {
            if *budget == 0 {
                break;
            }
            let claim = {
                let mut state = self.state()?;
                let priority = if state.recovery_turn && !state.recovery_priority.is_empty() {
                    state.recovery_turn = false;
                    state
                        .recovery_priority
                        .pop_front()
                        .and_then(|s| state.shards.get(&s).cloned())
                } else {
                    state.recovery_turn = true;
                    None
                };
                if let Some(claim) = priority {
                    Some((claim, false))
                } else {
                    let after = state.recovery_cursor;
                    state
                        .shards
                        .iter()
                        .filter(|(s, _)| after.is_none_or(|a| **s > a))
                        .chain(
                            state
                                .shards
                                .iter()
                                .filter(|(s, _)| after.is_some_and(|a| **s <= a)),
                        )
                        .find(|(s, _)| !visited.contains(*s))
                        .map(|(_, claim)| (claim.clone(), true))
                }
            };
            let Some((claim, normal)) = claim else {
                break;
            };
            if !visited.insert(claim.shard) {
                continue;
            }
            match self.recover_page(&claim, budget).await {
                // Keep both cursors so the next round starts with the first
                // shard that could not read a page.
                Ok(RecoveryVisit::Deferred) => break,
                Ok(RecoveryVisit::Visited) => {}
                Err(error) => {
                    failure.get_or_insert(error);
                }
            }
            if normal {
                self.state()?.recovery_cursor = Some(claim.shard);
            }
        }
        failure.map_or(Ok(()), Err)
    }

    async fn recover_page(
        &self,
        claim: &ShardClaim,
        budget: &mut usize,
    ) -> Result<RecoveryVisit, RuntimeError> {
        if self.state()?.draining || *budget == 0 {
            return Ok(RecoveryVisit::Visited);
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
            let page = timeout(
                self.config.request_timeout,
                self.inbox.enumerate_pending(
                    claim.shard,
                    PageRequest {
                        after,
                        limit: NonZeroU32::new(self.config.page_size)
                            .ok_or_else(|| err("page_size"))?,
                    },
                ),
            )
            .await
            .map_err(|_| err("store_outcome_unknown"))?;
            let page = match page {
                Err(StoreError::NotApplied { code }) if code == PENDING_SCAN_DEFERRED => {
                    return Ok(RecoveryVisit::Deferred);
                }
                result => result.map_err(store_error)?,
            };
            self.state()?.scans.insert(
                claim.shard,
                Scan {
                    queue: page.items.into(),
                    next: page.next,
                },
            );
        }
        let mut failure = None;
        // A bounded page inspection can skip backoff/active items without wasting
        // the operation budget or revisiting a corrupt prefix forever.
        for _ in 0..self.config.page_size {
            if *budget == 0 {
                break;
            }
            let sid = {
                let mut state = self.state()?;
                if !self.ready_locked(&state)
                    || state.sessions.len() + state.acquisitions.len()
                        >= self.config.max_active_sessions
                {
                    break;
                }
                state
                    .scans
                    .get_mut(&claim.shard)
                    .and_then(|s| s.queue.pop_front())
            };
            let Some(sid) = sid else { break };
            let skip = {
                let state = self.state()?;
                state.sessions.contains_key(&sid)
                    || state.acquisitions.contains_key(&sid)
                    || state
                        .retries
                        .get(&sid)
                        .is_some_and(|(_, due)| *due > Instant::now())
            };
            if skip {
                continue;
            }
            *budget -= 1;
            if let Err(error) = self.acquire_session(&sid, claim).await {
                tracing::warn!(sid=%sid.0, shard=claim.shard.0, code=%error.code, "Worker session recovery will retry");
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(RecoveryVisit::Visited), Err)
    }
    fn retry_later(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
        delay: Duration,
    ) -> Result<(), RuntimeError> {
        let mut state = self.state()?;
        // Only a scheduling hint. Overflow entries remain discoverable in the
        // persistent pending index; never evict uncertain in-flight operations.
        if state.retries.contains_key(sid) || state.retries.len() < self.config.max_active_sessions
        {
            state
                .retries
                .insert(sid.clone(), (claim.clone(), Instant::now() + delay));
        }
        Ok(())
    }
    pub(crate) async fn acquire_session(
        &self,
        sid: &Sid,
        claim: &ShardClaim,
    ) -> Result<(), RuntimeError> {
        let Some(_scan_visit) = self.begin_session_scan()? else {
            self.retry_later(sid, claim, self.config.check_interval)?;
            return Ok(());
        };
        if !self.state()?.acquisitions.contains_key(sid) {
            // Nothing that can change ownership has been issued yet. Missing
            // spec or initializer failure must not become an in-flight write.
            let prepare = async {
                let spec = self
                    .bounded(self.sessions.get_spec(sid))
                    .await?
                    .ok_or_else(|| err("pending_session_spec_missing"))?;
                (self.initialize)(&spec)
            }
            .await;
            let initial = match prepare {
                Ok(initial) => initial,
                Err(error) => {
                    self.retry_later(sid, claim, self.config.check_interval)?;
                    return Err(error);
                }
            };
            let mut state = self.state()?;
            if state.draining
                || state.sessions.len() + state.acquisitions.len()
                    >= self.config.max_active_sessions
            {
                return Ok(());
            }
            state.retries.remove(sid);
            state.acquisitions.insert(
                sid.clone(),
                Acquisition {
                    claim: claim.clone(),
                    stage: AcquisitionStage::Acquire {
                        initial: Box::new(initial),
                        uncertain: false,
                    },
                    retry_at: Instant::now(),
                },
            );
        }
        let pending = self
            .state()?
            .acquisitions
            .get(sid)
            .cloned()
            .ok_or_else(|| err("acquisition_missing"))?;
        match pending.stage {
            AcquisitionStage::Acquire { initial, uncertain } => {
                // Set uncertainty BEFORE issuing: cancellation can occur at any
                // await. Retries reuse the exact initial state and original claim.
                if let Some(pending) = self.state()?.acquisitions.get_mut(sid) {
                    pending.stage = AcquisitionStage::Acquire {
                        initial: initial.clone(),
                        uncertain: true,
                    };
                    pending.retry_at = Instant::now() + self.config.check_interval;
                }
                match timeout(
                    self.config.request_timeout,
                    self.sessions.acquire(sid, &pending.claim, *initial),
                )
                .await
                {
                    Ok(Ok(AcquireOutcome::Acquired(acquired))) => {
                        let mut state = self.state()?;
                        state
                            .acquisitions
                            .get_mut(sid)
                            .ok_or_else(|| err("acquisition_missing"))?
                            .stage = AcquisitionStage::Observe(Box::new(acquired));
                        state.made_progress();
                    }
                    Ok(Ok(AcquireOutcome::Deferred { check_after })) if !uncertain => {
                        self.state()?.acquisitions.remove(sid);
                        self.retry_later(
                            sid,
                            &pending.claim,
                            check_after.min(self.config.check_interval),
                        )?;
                        return Ok(());
                    }
                    Ok(Ok(AcquireOutcome::Fenced)) => {
                        // Fenced is durable evidence that this exact claim may
                        // no longer acquire; its late write cannot regain rights.
                        let mut state = self.state()?;
                        state.acquisitions.remove(sid);
                        state.made_progress();
                        return Ok(());
                    }
                    Ok(Ok(AcquireOutcome::Deferred { .. })) => {
                        return Err(err("session_acquire_unknown"));
                    }
                    Ok(Err(StoreError::OutcomeUnknown { .. })) | Err(_) => {
                        return Err(err("session_acquire_unknown"));
                    }
                    Ok(Err(
                        error @ (StoreError::Invalid { .. } | StoreError::UnsupportedFormat { .. }),
                    )) => {
                        let code = store_error(error).code;
                        if let Some(acquisition) = self.state()?.acquisitions.get_mut(sid) {
                            acquisition.stage = AcquisitionStage::Diagnostic { code: code.clone() };
                        }
                        return Err(err(&code));
                    }
                    Ok(Err(error)) => {
                        if !uncertain {
                            self.state()?.acquisitions.remove(sid);
                            self.retry_later(sid, &pending.claim, self.config.check_interval)?;
                        }
                        return Err(store_error(error));
                    }
                }
            }
            AcquisitionStage::Diagnostic { .. } => return Ok(()),
            AcquisitionStage::Observe(_) | AcquisitionStage::Release(_) => {}
        }
        let stage = self.state()?.acquisitions.get(sid).map(|a| a.stage.clone());
        if let Some(AcquisitionStage::Observe(acquired)) = stage {
            match self
                .bounded(self.scheduling.observe(&acquired.ownership))
                .await
            {
                Ok(ScheduleObservation::Observed(_)) => {
                    let pending = acquired
                        .envelope
                        .checkpoint
                        .outbox
                        .as_ref()
                        .map_or(0, |b| b.effects.len() as u64);
                    let mut state = self.state()?;
                    state.sessions.insert(
                        sid.clone(),
                        Session {
                            acquired: *acquired,
                            phase: Phase::Recovering,
                            pending_effects: pending,
                            pending_schedule: false,
                            drain_retry_at: Instant::now(),
                        },
                    );
                    state.acquisitions.remove(sid);
                    state.made_progress();
                    return Ok(());
                }
                Ok(ScheduleObservation::Fenced) => {
                    let mut state = self.state()?;
                    state
                        .acquisitions
                        .get_mut(sid)
                        .ok_or_else(|| err("acquisition_missing"))?
                        .stage = AcquisitionStage::Release(acquired.ownership);
                    state.made_progress();
                }
                Err(error) => return Err(error),
            }
        }
        let release = self
            .state()?
            .acquisitions
            .get(sid)
            .and_then(|a| match &a.stage {
                AcquisitionStage::Release(owner) => Some(owner.clone()),
                _ => None,
            });
        if let Some(owner) = release {
            self.bounded(self.sessions.release_local(&owner)).await?;
            let mut state = self.state()?;
            state.acquisitions.remove(sid);
            state.made_progress();
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
            let mut due = state
                .acquisitions
                .iter()
                .filter(|(_, a)| a.retry_at <= Instant::now())
                .map(|(sid, a)| (sid.clone(), a.claim.clone()))
                .collect::<std::collections::BTreeMap<_, _>>();
            if self.ready_locked(&state)
                && state.sessions.len() + state.acquisitions.len() < self.config.max_active_sessions
            {
                due.extend(
                    state
                        .retries
                        .iter()
                        .filter(|(_, (claim, at))| {
                            *at <= Instant::now() && state.shards.get(&claim.shard) == Some(claim)
                        })
                        .map(|(sid, (claim, _))| (sid.clone(), claim.clone())),
                );
            }
            due.iter()
                .filter(|(sid, _)| after.is_none_or(|after| *sid > after))
                .chain(
                    due.iter()
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
            if let Some(a) = self.state()?.acquisitions.get_mut(&sid) {
                a.retry_at = Instant::now() + self.config.check_interval;
            }
            if let Err(error) = self.acquire_session(&sid, &claim).await {
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(()), Err)
    }
}
