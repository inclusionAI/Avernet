use crate::runtime::*;
use bcs_dist_model::*;
use bcs_service_api::{
    port::repo::distributed::{common::*, ownership::*},
    types::dist_runtime::*,
};
use futures::{StreamExt, stream};
use tokio::time::{Instant, timeout};

impl WorkerRuntime {
    pub(crate) async fn drain_round(&self, deadline: Instant) -> Result<(), RuntimeError> {
        for result in [
            tokio::time::timeout_at(deadline, self.resolve_claims()).await,
            tokio::time::timeout_at(deadline, self.resolve_acquisitions()).await,
        ] {
            if let Ok(Err(error)) = result {
                tracing::warn!(code=%error.code, "Worker drain confirmation deferred");
            }
        }
        let incoming = self
            .state()?
            .incoming
            .values()
            .map(|p| p.request.clone())
            .collect::<Vec<_>>();
        for request in incoming.into_iter().take(self.config.max_shards_per_tick) {
            if Instant::now() >= deadline {
                break;
            }
            if let Ok(Err(error)) =
                tokio::time::timeout_at(deadline, self.finish_incoming(request)).await
            {
                tracing::warn!(code=%error.code, "Worker incoming handoff confirmation deferred");
            }
        }
        if Instant::now() < deadline {
            self.drain_step(deadline).await?;
        }
        Ok(())
    }

    pub(crate) async fn drain_step(&self, deadline: Instant) -> Result<(), RuntimeError> {
        let mut budget = self.config.max_acquires_per_tick;
        let shards = {
            let state = self.state()?;
            state
                .shards
                .keys()
                .chain(state.outgoing.keys())
                .copied()
                .chain(state.sessions.values().map(|s| s.acquired.ownership.shard))
                .chain(state.acquisitions.values().map(|a| a.claim.shard))
                .collect::<std::collections::BTreeSet<_>>()
        };
        let cursor = self.state()?.drain_cursor;
        let ordered = shards
            .iter()
            .filter(|s| cursor.is_none_or(|cursor| **s > cursor))
            .chain(
                shards
                    .iter()
                    .filter(|s| cursor.is_some_and(|cursor| **s <= cursor)),
            )
            .copied()
            .take(self.config.max_shards_per_tick)
            .collect::<Vec<_>>();
        let (known, new): (Vec<_>, Vec<_>) = {
            let state = self.state()?;
            ordered.into_iter().partition(|shard| {
                state.outgoing.contains_key(shard) || !state.shards.contains_key(shard)
            })
        };
        for shard in known {
            self.progress_drain_shard(shard, &[], deadline, &mut budget)
                .await?;
        }
        // No discovery is necessary to finish accepted transfers or local
        // cleanup. Load one view only when opening new handoffs is possible.
        if !new.is_empty() && Instant::now() < deadline {
            let candidates = match self.candidates().await {
                Ok(candidates) => candidates,
                Err(error) => {
                    tracing::warn!(code=%error.code, "Worker candidate discovery deferred");
                    vec![]
                }
            };
            for shard in new {
                self.progress_drain_shard(shard, &candidates, deadline, &mut budget)
                    .await?;
            }
        }
        Ok(())
    }
    async fn progress_drain_shard(
        &self,
        shard: ShardId,
        candidates: &[MemberRecord],
        deadline: Instant,
        budget: &mut usize,
    ) -> Result<(), RuntimeError> {
        if Instant::now() < deadline {
            self.state()?.drain_cursor = Some(shard);
            if let Err(error) = self.drain_shard(shard, candidates, deadline, budget).await {
                tracing::warn!(shard=shard.0, code=%error.code, "Worker shard drain will retry");
            }
        }
        Ok(())
    }
    async fn drain_shard(
        &self,
        shard: ShardId,
        candidates: &[MemberRecord],
        deadline: Instant,
        budget: &mut usize,
    ) -> Result<(), RuntimeError> {
        let Some(record) = self.bounded(self.owners.current(shard)).await? else {
            return Ok(());
        };
        let pending = self.state()?.outgoing.get(&shard).cloned();
        if let Some(pending) = &pending
            && record.claim.owner.epoch > pending.request.from.owner.epoch
        {
            // A successor (possibly beyond the original target) is durable
            // proof of lost shard rights. The session port checks whether
            // transfer still applies or is now fenced, independently.
            self.state()?.shards.remove(&shard);
            return self
                .transfer_quiescent(&pending.request, &record.claim, &pending.target, budget)
                .await;
        }
        if record.claim.owner.instance != self.instance {
            self.state()?.shards.remove(&shard);
            return self.release_quiescent_shard(shard, budget).await;
        }
        let claim = record.claim;
        // Block only this shard on unresolved acquire/claim. Other shards and
        // already accepted transfers continue during confirmation retries.
        if self
            .state()?
            .acquisitions
            .values()
            .any(|a| a.claim.shard == shard)
            || self.state()?.claims.contains_key(&shard)
        {
            return Ok(());
        }
        if let Some(handoff) = record.handoff
            && !self
                .bounded(self.owners.clear_handoff(&claim, &handoff.request_id))
                .await?
        {
            return Ok(());
        }
        let pending = if let Some(pending) = pending {
            pending
        } else {
            if deadline.saturating_duration_since(Instant::now()) <= self.config.request_timeout {
                return Ok(());
            }
            let rejected = self
                .state()?
                .rejected_targets
                .get(&shard)
                .cloned()
                .unwrap_or_default();
            let instances = candidates
                .iter()
                .filter(|m| m.instance != self.instance && !rejected.contains(&m.instance))
                .map(|m| m.instance.clone())
                .collect::<Vec<_>>();
            let ranked = crate::rank_candidates(shard, &instances, self.config.identity);
            let Some(first) = ranked.first() else {
                return Ok(());
            };
            let Some(target) = candidates.iter().find(|m| m.instance == *first).cloned() else {
                return Ok(());
            };
            let pending = Outgoing {
                request: HandoffRequest {
                    request_id: RequestId(uuid::Uuid::new_v4().to_string()),
                    from: claim,
                    target: target.instance.clone(),
                    grace: deadline.saturating_duration_since(Instant::now()),
                },
                target,
                uncertain: false,
            };
            self.state()?.outgoing.insert(shard, pending.clone());
            pending
        };
        // Cancellation is uncertain too. Retain identity/content before I/O.
        if let Some(outgoing) = self.state()?.outgoing.get_mut(&shard) {
            outgoing.uncertain = true;
        }
        let outcome = timeout(
            self.config.request_timeout,
            self.peers.handoff(&pending.target, pending.request.clone()),
        )
        .await;
        match outcome {
            Ok(Ok(HandoffOutcome::Accepted(record))) => {
                if record.claim.owner.instance != pending.target.instance
                    || record.claim.shard != shard
                    || !record.handoff.as_ref().is_some_and(|h| {
                        h.from == pending.request.from && h.request_id == pending.request.request_id
                    })
                {
                    return Err(err("peer_handoff_response"));
                }
                self.state()?.shards.remove(&shard);
                self.transfer_quiescent(&pending.request, &record.claim, &pending.target, budget)
                    .await?;
            }
            Ok(Ok(HandoffOutcome::Rejected)) if !pending.uncertain => {
                let mut state = self.state()?;
                let rejected = state.rejected_targets.entry(shard).or_default();
                if !rejected.contains(&pending.target.instance) {
                    rejected.push(pending.target.instance);
                }
                state.outgoing.remove(&shard);
                tracing::info!(shard=shard.0, handoff_id=%pending.request.request_id.0, "Worker target rejected handoff; try another eligible target");
            }
            Ok(Ok(HandoffOutcome::Rejected | HandoffOutcome::Fenced)) => {
                // Read-only rejection of a retry cannot cancel a prior unknown
                // write. Only a newer durable owner or same-ID acceptance closes it.
                tracing::warn!(shard=shard.0, handoff_id=%pending.request.request_id.0, "Worker handoff still requires durable confirmation");
            }
            Ok(Err(error)) => {
                tracing::warn!(shard=shard.0, handoff_id=%pending.request.request_id.0, code=%error.code, "Worker handoff response unknown")
            }
            Err(_) => {
                tracing::warn!(shard=shard.0, handoff_id=%pending.request.request_id.0, "Worker handoff request timed out")
            }
        }
        Ok(())
    }
    async fn release_quiescent_shard(
        &self,
        shard: ShardId,
        budget: &mut usize,
    ) -> Result<(), RuntimeError> {
        let owners = self.quiescent_owners(shard, None, *budget)?;
        *budget -= owners.len();
        let mut releases = stream::iter(owners)
            .map(|owner| async move { self.release_session(&owner).await })
            .buffer_unordered(self.config.handoff_concurrency);
        let mut failure = None;
        while let Some(result) = releases.next().await {
            if let Err(error) = result {
                failure.get_or_insert(error);
            }
        }
        failure.map_or(Ok(()), Err)
    }
    fn quiescent_owners(
        &self,
        shard: ShardId,
        owner: Option<&ShardOwner>,
        limit: usize,
    ) -> Result<Vec<SessionOwnership>, RuntimeError> {
        let mut state = self.state()?;
        let after = state.transfer_cursors.get(&shard).cloned();
        let eligible = |s: &&Session| {
            s.acquired.ownership.shard == shard
                && owner.is_none_or(|owner| s.acquired.ownership.owner == *owner)
                && (matches!(s.phase, Phase::Idle | Phase::Releasing)
                    || (owner.is_some() && s.phase == Phase::Transferring))
                && !s.pending_schedule
                && s.pending_effects == 0
        };
        let owners = state
            .sessions
            .values()
            .filter(eligible)
            .filter(|s| after.as_ref().is_none_or(|a| s.acquired.ownership.sid > *a))
            .chain(state.sessions.values().filter(eligible).filter(|s| {
                after
                    .as_ref()
                    .is_some_and(|a| s.acquired.ownership.sid <= *a)
            }))
            .take(limit)
            .map(|s| s.acquired.ownership.clone())
            .collect::<Vec<_>>();
        if let Some(last) = owners.last() {
            state.transfer_cursors.insert(shard, last.sid.clone());
        }
        Ok(owners)
    }
    async fn transfer_quiescent(
        &self,
        request: &HandoffRequest,
        target: &ShardClaim,
        member: &MemberRecord,
        budget: &mut usize,
    ) -> Result<(), RuntimeError> {
        let owners =
            self.quiescent_owners(request.from.shard, Some(&request.from.owner), *budget)?;
        *budget -= owners.len();
        let mut transfers = stream::iter(owners).map(|source| async move {
            let releasing = {
                let mut state = self.state()?;
                let session = state.sessions.get_mut(&source.sid).ok_or_else(|| err("session_not_active"))?;
                if session.acquired.ownership != source || session.pending_schedule || session.pending_effects != 0 { return Err(err("session_not_quiescent")); }
                match session.phase {
                    Phase::Releasing => true,
                    Phase::Idle | Phase::Transferring => { session.phase = Phase::Transferring; false },
                    _ => return Err(err("session_not_quiescent")),
                }
            };
            if !releasing {
                let result = self.bounded(self.sessions.handoff_session(&source, target, &request.request_id)).await?;
                tracing::debug!(sid=%source.sid.0, shard=source.shard.0, ?result, "Worker session transfer confirmed");
                let mut state = self.state()?;
                let session = state.sessions.get_mut(&source.sid).ok_or_else(|| err("session_not_active"))?;
                if session.acquired.ownership != source || session.phase != Phase::Transferring { return Err(err("session_transfer_state")); }
                session.phase = Phase::Releasing;
            }
            self.release_session(&source).await
        }).buffer_unordered(self.config.handoff_concurrency);
        let mut failure = None;
        while let Some(result) = transfers.next().await {
            if let Err(error) = result {
                failure.get_or_insert(error);
            }
        }
        if let Some(error) = failure {
            return Err(error);
        }
        let unfinished = {
            let state = self.state()?;
            state.sessions.values().any(|s| {
                s.acquired.ownership.shard == request.from.shard
                    && s.acquired.ownership.owner == request.from.owner
            }) || state.acquisitions.values().any(|a| a.claim == request.from)
        };
        if !unfinished {
            // Best-effort acceleration; durable handoff grace and pending scan
            // remain the fallback if the completion notification is lost.
            match timeout(
                self.config.request_timeout,
                self.peers.source_done(
                    member,
                    self.instance.clone(),
                    target.clone(),
                    request.request_id.clone(),
                ),
            )
            .await
            {
                Ok(Ok(true)) => {}
                _ => {
                    tracing::debug!(shard=target.shard.0, handoff_id=%request.request_id.0, "Worker source_done unconfirmed; durable scan remains active")
                }
            }
            let mut state = self.state()?;
            state.shards.remove(&request.from.shard);
            state.outgoing.remove(&request.from.shard);
            state.rejected_targets.remove(&request.from.shard);
            state.transfer_cursors.remove(&request.from.shard);
        }
        Ok(())
    }
}
