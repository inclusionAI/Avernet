use crate::runtime::*;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    port::{
        dist_runtime::DistRuntimeControlPort,
        repo::distributed::{common::StoreError, ownership::*},
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
                .filter(|s| {
                    matches!(
                        s.phase,
                        Phase::Running | Phase::Unknown | Phase::Transferring
                    )
                })
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
                if previous.request != request {
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
                state.incoming.insert(
                    id.clone(),
                    Incoming {
                        request: request.clone(),
                        uncertain: false,
                        gate: std::sync::Arc::new(tokio::sync::Mutex::new(())),
                    },
                );
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
        let done = self.bounded(self.owners.source_done(&target, &id)).await?;
        if done {
            self.prioritize(target.shard)?;
        }
        Ok(done)
    }
    async fn wake(&self, shard: ShardId) -> Result<(), RuntimeError> {
        if !self.config.shards.contains(&shard) {
            return Err(err("unknown_shard"));
        }
        self.prioritize(shard)
    }
    async fn drain(&self, grace: Duration) -> Result<DrainOutcome, RuntimeError> {
        let deadline = Instant::now()
            .checked_add(grace)
            .ok_or_else(|| err("drain_budget"))?;
        // This is the first operation: close all new local admissions before I/O.
        self.state()?.draining = true;
        tracing::info!(node=%self.instance.node, incarnation=%self.instance.incarnation, grace_ms=grace.as_millis(), "Worker drain started");
        // Finish or cancel the existing scan before retrying its in-flight
        // records. A drain must not concurrently resolve the same acquisition.
        let guard = tokio::time::timeout_at(deadline, self.tick_guard.lock()).await;
        let Ok(_guard) = guard else {
            self.state()?.stopped = true;
            return Ok(DrainOutcome::DeadlineReached {
                remaining: self.remaining()?.max(1),
            });
        };
        let _ = timeout(
            deadline
                .saturating_duration_since(Instant::now())
                .min(self.config.request_timeout),
            self.membership.set_draining(&self.lease),
        )
        .await;
        while Instant::now() < deadline {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining <= self.config.exit_margin {
                break;
            }
            let work_until = deadline - self.config.exit_margin;
            let step = self.drain_round(work_until);
            tokio::pin!(step);
            let mut heartbeat = tokio::time::interval(self.config.heartbeat_interval);
            loop {
                tokio::select! {
                    result = &mut step => {
                        if let Err(error) = result { tracing::warn!(code=%error.code, "Worker drain step deferred"); }
                        break;
                    }
                    _ = tokio::time::sleep_until(work_until) => break,
                    _ = heartbeat.tick() => {
                        if let Ok(Err(error)) = tokio::time::timeout_at(work_until, self.heartbeat()).await {
                            tracing::warn!(code=%error.code, "Worker drain heartbeat unavailable");
                        }
                    }
                }
            }
            self.close_unused_feeds()?;
            tracing::debug!(
                remaining = self.remaining()?,
                budget_ms = deadline
                    .saturating_duration_since(Instant::now())
                    .as_millis(),
                "Worker drain progress"
            );
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
    async fn confirm_retired_incoming(
        &self,
        request: &HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        // A stale waiter cannot validate a replacement entry's private content
        // fingerprint. It may only confirm whether the old source still owns
        // the shard; normal retry admission handles acceptance reconciliation.
        let current = self
            .bounded(self.owners.current(request.from.shard))
            .await?;
        Ok(
            if current.is_some_and(|record| record.claim == request.from) {
                HandoffOutcome::Rejected
            } else {
                HandoffOutcome::Fenced
            },
        )
    }
    pub(crate) async fn finish_incoming(
        &self,
        request: HandoffRequest,
    ) -> Result<HandoffOutcome, RuntimeError> {
        let id = request.request_id.0.clone();
        let gate = self
            .state()?
            .incoming
            .get(&id)
            .map(|pending| pending.gate.clone());
        let Some(gate) = gate else {
            return self.confirm_retired_incoming(&request).await;
        };
        let _guard = timeout(self.config.request_timeout, gate.lock())
            .await
            .map_err(|_| err("handoff_confirmation_busy"))?;
        let same_entry = {
            let state = self.state()?;
            match state.incoming.get(&id) {
                Some(pending) if std::sync::Arc::ptr_eq(&pending.gate, &gate) => {
                    if pending.request != request {
                        return Err(err("handoff_content_conflict"));
                    }
                    true
                }
                _ => false,
            }
        };
        if !same_entry {
            // This queued call belongs to an entry already removed/replaced.
            // It must never issue a fresh write under the replacement's ID.
            return self.confirm_retired_incoming(&request).await;
        }
        let mut effective = request.clone();
        effective.grace = effective.grace.saturating_sub(self.config.exit_margin);
        let prior_unknown = self
            .state()?
            .incoming
            .get(&id)
            .is_some_and(|pending| pending.uncertain);
        if effective.grace.is_zero() {
            if !prior_unknown {
                self.state()?.incoming.remove(&id);
                return Ok(HandoffOutcome::Rejected);
            }
            return Err(err("handoff_unknown"));
        }
        if let Some(pending) = self.state()?.incoming.get_mut(&id) {
            pending.uncertain = true;
        }
        let outcome = match timeout(self.config.request_timeout, self.owners.handoff(effective))
            .await
        {
            Ok(Ok(outcome)) => outcome,
            Ok(Err(error)) => {
                if !prior_unknown && !matches!(error, StoreError::OutcomeUnknown { .. }) {
                    self.state()?.incoming.remove(&id);
                }
                return Err(store_error(error));
            }
            Err(_) => {
                tracing::warn!(shard=request.from.shard.0, handoff_id=%id, "Worker handoff result unknown");
                return Err(err("handoff_unknown"));
            }
        };
        if prior_unknown && !matches!(outcome, HandoffOutcome::Accepted(_)) {
            // A source that has since died can make this retry be rejected while
            // the first EQ is still in flight. Rejection alone cannot cancel it.
            let current = self
                .bounded(self.owners.current(request.from.shard))
                .await?;
            let Some(current) = current.filter(|r| r.claim.owner.epoch > request.from.owner.epoch)
            else {
                return Err(err("handoff_unknown"));
            };
            let mut state = self.state()?;
            if current.claim.owner.instance == self.instance {
                state.shards.insert(current.claim.shard, current.claim);
            }
            state.incoming.remove(&id);
            return Ok(HandoffOutcome::Fenced);
        }
        let mut state = self.state()?;
        if let HandoffOutcome::Accepted(record) = &outcome {
            tracing::info!(shard=record.claim.shard.0, epoch=record.claim.owner.epoch.0, handoff_id=%id, "Worker handoff accepted");
            state
                .shards
                .insert(record.claim.shard, record.claim.clone());
            if !state.priority_shards.contains(&record.claim.shard) {
                state.priority_shards.push_back(record.claim.shard);
            }
            if !state.recovery_priority.contains(&record.claim.shard) {
                state.recovery_priority.push_back(record.claim.shard);
            }
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
}
