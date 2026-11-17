use crate::runtime::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::{ownership::*, session::*},
    },
    types::dist_runtime::*,
};
use std::time::Duration;
use tokio::time::{Instant, sleep, timeout};
#[async_trait]
impl DistRuntimeControlPort for WorkerRuntime {
    async fn snapshot(&self) -> Result<RuntimeSnapshot, RuntimeError> {
        let alive = self.self_alive().await?;
        let state = self.state()?;
        Ok(RuntimeSnapshot {
            instance: self.instance.clone(),
            ready: alive && self.ready_locked(&state),
            draining: state.draining,
            active_work: state
                .sessions
                .values()
                .filter(|s| matches!(s.phase, Phase::Running | Phase::Unknown))
                .count() as u64
                + state.acquisitions.len() as u64
                + state.incoming.len() as u64
                + state.claims.len() as u64,
            pending_effects: state.sessions.values().map(|s| s.pending_effects).sum(),
        })
    }
    async fn accept_handoff(
        &self,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        if request.target != self.instance || !self.config.shards.contains(&request.from.shard) {
            return Ok(HandoffOutcome::Fenced);
        }
        let id = request.request_id.0.clone();
        // Complete the remote liveness read before admitting a new request, so
        // the subsequent local wait timer is not started after that round trip.
        let alive = self.self_alive().await?;
        let confirm_only = {
            let mut state = self.state()?;
            if let Some(previous) = state.incoming.get(&id) {
                if *previous != request {
                    return Err(err("handoff_content_conflict"));
                }
                false
            } else if !alive || !self.ready_locked(&state) {
                true
            } else if state.incoming.len() >= self.config.max_inflight_handoffs
                || state.sessions.len() >= self.config.max_active_sessions
            {
                return Ok(HandoffOutcome::Rejected);
            } else {
                state.incoming.insert(id.clone(), request.clone());
                false
            }
        };
        if confirm_only {
            return self.confirm_handoff(&request).await;
        }
        if !alive {
            return self.confirm_handoff(&request).await;
        }
        self.finish_incoming(request).await
    }
    async fn handoff_done(
        &self,
        source: Instance,
        target: ShardClaim,
        id: RequestId,
    ) -> Result<bool, RuntimeError> {
        if target.owner.instance != self.instance {
            return Ok(false);
        }
        let Some(record) = self.bounded(self.owners.current(target.shard)).await? else {
            return Ok(false);
        };
        if record.claim != target
            || !record
                .handoff
                .as_ref()
                .is_some_and(|h| h.from.owner.instance == source && h.request_id == id)
        {
            return Ok(false);
        }
        self.bounded(self.owners.source_done(&target, &id)).await
    }
    async fn wake(&self, shard: ShardId) -> Result<(), RuntimeError> {
        if !self.config.shards.contains(&shard) {
            return Err(err("unknown_shard"));
        }
        // Rewind only the page cursor. Existing loaded page remains bounded; the
        // normal owner/pending scan guarantees eventual discovery without hints.
        if let Some(scan) = self.state()?.scans.get_mut(&shard) {
            scan.next = None;
        }
        Ok(())
    }
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        let deadline = Instant::now()
            .checked_add(grace)
            .ok_or_else(|| err("drain_budget"))?;
        // This is the first operation: close all new local admissions before I/O.
        self.state()?.draining = true;
        tracing::info!(node=%self.instance.node, incarnation=%self.instance.incarnation, grace_ms=grace.as_millis(), "Worker drain started");
        let _ = timeout(
            grace.min(self.config.request_timeout),
            self.membership.set_draining(&self.lease),
        )
        .await;
        while Instant::now() < deadline {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining <= self.config.exit_margin {
                break;
            }
            let _ = timeout(
                deadline
                    .saturating_duration_since(Instant::now())
                    .min(self.config.request_timeout),
                self.heartbeat(),
            )
            .await;
            let _ = timeout(
                deadline
                    .saturating_duration_since(Instant::now())
                    .min(self.config.request_timeout),
                self.resolve_claims(),
            )
            .await;
            let _ = timeout(
                deadline
                    .saturating_duration_since(Instant::now())
                    .min(self.config.request_timeout),
                self.resolve_acquisitions(),
            )
            .await;
            let incoming = self.state()?.incoming.values().cloned().collect::<Vec<_>>();
            for request in incoming.into_iter().take(self.config.max_shards_per_tick) {
                if Instant::now() >= deadline {
                    break;
                }
                let _ = timeout(
                    deadline
                        .saturating_duration_since(Instant::now())
                        .min(self.config.request_timeout),
                    self.finish_incoming(request),
                )
                .await;
            }
            if self.state()?.acquisitions.is_empty() && self.state()?.claims.is_empty() {
                let _ = timeout(
                    deadline
                        .saturating_duration_since(Instant::now())
                        .min(self.config.request_timeout),
                    self.drain_step(deadline.saturating_duration_since(Instant::now())),
                )
                .await;
            }
            if self.remaining()? == 0 {
                self.state()?.stopped = true;
                let budget = deadline
                    .saturating_duration_since(Instant::now())
                    .min(self.config.request_timeout);
                // Successful retirement proves no delayed renewal can revive us.
                match timeout(budget, self.membership.retire(&self.lease)).await {
                    Ok(Ok(())) => return Ok(DrainOutcome::Drained),
                    _ => return Ok(DrainOutcome::DeadlineReached { remaining: 1 }),
                }
            }
            sleep(
                self.config
                    .check_interval
                    .min(deadline.saturating_duration_since(Instant::now())),
            )
            .await;
        }
        // No retirement proof is claimed while existing processors or unknown
        // operations may still be running. Stop renewals; natural expiry and the
        // successor's checkpoint fence provide the fallback recovery path.
        self.state()?.stopped = true;
        let remaining = self.remaining()?.max(1);
        tracing::warn!(node=%self.instance.node, incarnation=%self.instance.incarnation, remaining, "Worker drain deadline reached");
        Ok(DrainOutcome::DeadlineReached { remaining })
    }
}
impl WorkerRuntime {
    async fn confirm_handoff(
        &self,
        request: &HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        let Some(record) = self
            .bounded(self.owners.current(request.from.shard))
            .await?
        else {
            return Ok(HandoffOutcome::Fenced);
        };
        if record.claim.owner.instance == self.instance
            && record
                .handoff
                .as_ref()
                .is_some_and(|h| h.request_id == request.request_id && h.from == request.from)
        {
            let mut effective = request.clone();
            effective.grace = effective.grace.saturating_sub(self.config.exit_margin);
            return self.bounded(self.owners.handoff(effective)).await;
        }
        Ok(if record.claim == request.from {
            HandoffOutcome::Rejected
        } else {
            HandoffOutcome::Fenced
        })
    }
    pub(crate) async fn finish_incoming(
        &self,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        let id = request.request_id.0.clone();
        let mut effective = request.clone();
        effective.grace = effective.grace.saturating_sub(self.config.exit_margin);
        if effective.grace.is_zero() {
            self.state()?.incoming.remove(&id);
            return Ok(HandoffOutcome::Rejected);
        }
        let outcome = timeout(self.config.request_timeout, self.owners.handoff(effective))
            .await
            .map_err(|_| {tracing::warn!(shard=request.from.shard.0, handoff_id=%id, "Worker handoff result unknown");err("handoff_unknown")})?
            .map_err(store_error)?;
        let mut state = self.state()?;
        if let HandoffOutcome::Accepted(record) = &outcome {
            tracing::info!(shard=record.claim.shard.0, epoch=record.claim.owner.epoch.0, handoff_id=%id, "Worker handoff accepted");
            state
                .shards
                .insert(record.claim.shard, record.claim.clone());
        }
        state.incoming.remove(&id);
        Ok(outcome)
    }
    fn remaining(&self) -> Result<u64, RuntimeError> {
        let state = self.state()?;
        Ok((state.shards.len()
            + state.sessions.len()
            + state.acquisitions.len()
            + state.incoming.len()
            + state.outgoing.len()
            + state.claims.len()) as u64)
    }
    async fn drain_step(&self, remaining: Duration) -> Result<(), RuntimeError> {
        let claims = self.state()?.shards.values().cloned().collect::<Vec<_>>();
        for claim in claims.into_iter().take(self.config.max_shards_per_tick) {
            let Some(record) = self.bounded(self.owners.current(claim.shard)).await? else {
                continue;
            };
            if record.claim != claim {
                self.state()?.shards.remove(&claim.shard);
                continue;
            }
            if let Some(h) = record.handoff {
                if !self
                    .bounded(self.owners.clear_handoff(&claim, &h.request_id))
                    .await?
                {
                    continue;
                }
                tracing::debug!(shard=claim.shard.0, epoch=claim.owner.epoch.0, handoff_id=%h.request_id.0, "Worker handoff cleanup confirmed");
            }
            let pending = self.state()?.outgoing.get(&claim.shard).cloned();
            let (target, request) = if let Some(pending) = pending {
                pending
            } else {
                let mut candidates = self.candidates().await?;
                candidates.retain(|m| m.instance != self.instance);
                let ranked = crate::rank_candidates(
                    claim.shard,
                    &candidates
                        .iter()
                        .map(|m| m.instance.clone())
                        .collect::<Vec<_>>(),
                    self.config.identity,
                );
                let Some(first) = ranked.first() else {
                    continue;
                };
                let Some(target) = candidates.into_iter().find(|m| m.instance == *first) else {
                    continue;
                };
                let request = HandoffRequest {
                    request_id: RequestId(uuid::Uuid::new_v4().to_string()),
                    from: claim.clone(),
                    target: target.instance.clone(),
                    grace: remaining,
                };
                self.state()?
                    .outgoing
                    .insert(claim.shard, (target.clone(), request.clone()));
                (target, request)
            };
            let outcome = self.peers.handoff(&target, request.clone()).await;
            match outcome {
                Ok(HandoffOutcome::Accepted(record)) => {
                    if record.claim.owner.instance != target.instance
                        || record.claim.shard != claim.shard
                    {
                        return Err(err("peer_handoff_response"));
                    }
                    self.transfer_quiescent(&request, &record.claim, &target)
                        .await?;
                }
                Ok(HandoffOutcome::Rejected) => {
                    self.state()?.outgoing.remove(&claim.shard);
                }
                Ok(HandoffOutcome::Fenced) => {
                    self.state()?.outgoing.remove(&claim.shard);
                    self.state()?.shards.remove(&claim.shard);
                }
                Err(_) => {}
            }
        }
        // Outgoing accepted ownership is no longer the source shard owner. Keep
        // progressing its per-session transfers independently of healthy shards.
        let outgoing = self.state()?.outgoing.values().cloned().collect::<Vec<_>>();
        for (target, request) in outgoing {
            if let Some(record) = self
                .bounded(self.owners.current(request.from.shard))
                .await?
            {
                if record.claim.owner.instance == request.target {
                    self.transfer_quiescent(&request, &record.claim, &target)
                        .await?;
                } else if record.claim.owner.epoch > request.from.owner.epoch {
                    self.state()?.outgoing.remove(&request.from.shard);
                }
            }
        }
        Ok(())
    }
    async fn transfer_quiescent(
        &self,
        request: &HandoffRequest,
        target: &ShardClaim,
        member: &MemberRecord,
    ) -> Result<(), RuntimeError> {
        let owners = self
            .state()?
            .sessions
            .values()
            .filter(|s| {
                s.acquired.ownership.shard == request.from.shard
                    && s.acquired.ownership.owner == request.from.owner
                    && s.phase == Phase::Idle
                    && !s.pending_schedule
            })
            .map(|s| s.acquired.ownership.clone())
            .collect::<Vec<_>>();
        for source in owners.into_iter().take(self.config.max_acquires_per_tick) {
            match self
                .bounded(
                    self.sessions
                        .handoff_session(&source, target, &request.request_id),
                )
                .await?
            {
                SessionTransferOutcome::Transferred
                | SessionTransferOutcome::AlreadyTransferred
                | SessionTransferOutcome::Fenced => {
                    self.bounded(self.sessions.release_local(&source)).await?;
                    self.state()?.sessions.remove(&source.sid);
                }
            }
        }
        let unfinished = self.state()?.sessions.values().any(|s| {
            s.acquired.ownership.shard == request.from.shard
                && s.acquired.ownership.owner == request.from.owner
        });
        if !unfinished {
            let _ = self
                .peers
                .source_done(
                    member,
                    self.instance.clone(),
                    target.clone(),
                    request.request_id.clone(),
                )
                .await;
            let mut state = self.state()?;
            state.shards.remove(&request.from.shard);
            state.outgoing.remove(&request.from.shard);
        }
        Ok(())
    }
}
