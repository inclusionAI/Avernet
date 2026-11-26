//! Partition ownership: which command partitions this process owns, and
//! handing them over on drain.
//!
//! Every command partition of the pool has one owner record
//! (`bcn:v1:<c>:<ns>:<pool>:p<nnnn>:owner`, through `ShardLeasePort`; a
//! partition is a `ShardId`, its owner epoch a `WorkerEpoch`). The owner reads
//! the partition's hints and runs its calls; its claims carry the owner
//! record's claim as proof, which lets it take a call over from any other
//! holder (see `StoreDispatchQueue`).
//!
//! - **Ownership round** (`own_partitions`, every `ownership_interval`):
//!   for each partition, a record naming this process is kept; a partition
//!   with no owner, or whose owner is `Dead`, is claimed by the best
//!   HRW-ranked live Connector (others wait `rank x candidate_backoff`). A
//!   live owner is never displaced; `Unknown` is not `Dead`.
//! - **Recovery**: a partition this process just acquired is recovered at
//!   once: the calls its held index lists are claimed, instead of waiting for
//!   the Worker to re-post their hints.
//! - **Outgoing handoff** (`hand_off`, on drain, after every call stopped):
//!   each owned partition is offered to the best HRW-ranked live,
//!   non-draining peer over its control endpoint; the peer commits the
//!   transfer into the owner record. A refusal moves on to the next peer.
//! - **Incoming handoff** (`accept`): the peer side of that offer.
//!
//! The Worker's `source_done` is not used: the source stops every call before
//! it offers a partition and retires right after, which makes it `Dead`; the
//! new owner clears the handoff mark on its next round.

use super::{ConnectorEngine, failed, lock};
use crate::config::Backoff;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::{PageRequest, StoreError};
use bcs_service_api::port::repo::distributed::ownership::{
    ClaimShard, HandoffOutcome, HandoffRequest, LeaseClaim, Liveness, MemberRecord, RuntimeRole, ShardClaim,
};
use bcs_service_api::types::dist_runtime::RuntimeError;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::num::NonZeroU32;
use std::sync::atomic::Ordering;
use tokio::time::{Instant, timeout};

/// Member pages read per candidate list (100 members each).
const MEMBER_PAGES: usize = 10;

/// Highest-random-weight order of `candidates` for `partition`, best first.
/// Every Connector computes the same order from the same member list.
pub fn rank_candidates(partition: u32, candidates: &[Instance]) -> Vec<Instance> {
    let score = |instance: &Instance| {
        let mut h = Sha256::new();
        h.update(b"bcn-connector-hrw-v1");
        h.update(partition.to_be_bytes());
        h.update((instance.node.len() as u32).to_be_bytes());
        h.update(instance.node.as_bytes());
        h.update((instance.incarnation.len() as u32).to_be_bytes());
        h.update(instance.incarnation.as_bytes());
        <[u8; 32]>::from(h.finalize())
    };
    let mut ranked: Vec<([u8; 32], Instance)> = candidates.iter().map(|i| (score(i), i.clone())).collect();
    ranked.sort_by(|(a, x), (b, y)| b.cmp(a).then_with(|| (&x.node, &x.incarnation).cmp(&(&y.node, &y.incarnation))));
    ranked.dedup_by(|(_, a), (_, b)| a == b);
    ranked.into_iter().map(|(_, i)| i).collect()
}

/// This process's view of partition ownership.
#[derive(Default)]
pub(super) struct Ownership {
    /// The partitions this process owns, with the owner record's claim (the
    /// proof its dispatch claims carry).
    pub owned: BTreeMap<u32, ShardClaim>,
    /// Bumped whenever `owned` changes; the main loop re-assigns its feed
    /// when it differs from the value it last applied.
    pub version: u64,
    /// Partitions acquired and not recovered yet.
    pub recover: Vec<u32>,
    /// When a partition was first seen claimable (the HRW stagger starts).
    pub claimable_since: BTreeMap<u32, Instant>,
}

impl Ownership {
    fn keep(&mut self, partition: u32, claim: ShardClaim) {
        if self.owned.get(&partition) != Some(&claim) {
            if !self.owned.contains_key(&partition) {
                self.recover.push(partition);
            }
            self.owned.insert(partition, claim);
            self.version += 1;
        }
        self.claimable_since.remove(&partition);
    }

    pub(super) fn lose(&mut self, partition: u32) {
        if self.owned.remove(&partition).is_some() {
            self.version += 1;
        }
        self.recover.retain(|p| *p != partition);
    }
}

impl ConnectorEngine {
    /// The partition proof for a dispatch, if this process owns its partition.
    pub(super) fn proof_for(&self, did: &Did) -> Option<ShardClaim> {
        let partition = self.inner.ports.queue.partition_of(did);
        lock(&self.inner.ownership).owned.get(&partition).cloned()
    }

    /// The owned partitions, for the feed.
    pub(super) fn owned_partitions(&self) -> (u64, Vec<u32>) {
        let ownership = lock(&self.inner.ownership);
        (ownership.version, ownership.owned.keys().copied().collect())
    }

    /// Live, non-draining Connectors, this process included.
    ///
    /// `None` when the list cannot be read (no claim this round). With the
    /// stub membership (`need_contract_not_implemented`) this process is the
    /// only candidate. This process is added when the list does not show it
    /// yet (a registration not listed yet), unless it is draining.
    async fn candidates(&self) -> Option<Vec<MemberRecord>> {
        let inner = &self.inner;
        let me = &inner.config.me;
        let mut members = Vec::new();
        let mut after = None;
        for _ in 0..MEMBER_PAGES {
            let page = inner
                .ports
                .membership
                .members(
                    RuntimeRole::Connector,
                    PageRequest {
                        after,
                        limit: NonZeroU32::new(100).unwrap_or(NonZeroU32::MIN),
                    },
                )
                .await;
            match page {
                Ok(page) => {
                    members.extend(page.items);
                    after = page.next;
                    if after.is_none() {
                        break;
                    }
                }
                Err(StoreError::NotApplied { code }) if code == crate::delayed::NEED_CONTRACT => {
                    members.clear();
                    break;
                }
                Err(error) => {
                    tracing::warn!(?error, "Connector member list unavailable; no partition claim this round");
                    return None;
                }
            }
        }
        if !inner.draining.load(Ordering::SeqCst) && !members.iter().any(|m| m.instance == *me) {
            members.push(MemberRecord {
                instance: me.clone(),
                role: RuntimeRole::Connector,
                control_address: inner.config.control_address.clone(),
                draining: false,
            });
        }
        Some(members)
    }

    /// One ownership round over every partition; then recovers the
    /// partitions acquired by it (or by an accepted handoff).
    pub(super) async fn own_partitions(&self) {
        let inner = &self.inner;
        let me = inner.config.me.clone();
        let count = inner.ports.queue.command_partitions();
        let candidates = self.candidates().await;
        let candidates: Option<Vec<Instance>> = candidates.map(|c| c.into_iter().map(|m| m.instance).collect());
        let mut liveness: BTreeMap<(String, String), Liveness> = BTreeMap::new();
        for partition in 0..count {
            if inner.draining.load(Ordering::SeqCst) {
                return;
            }
            let shard = ShardId(partition);
            let current = match timeout(inner.config.peer_timeout, inner.ports.partitions.current(shard)).await {
                Ok(Ok(current)) => current,
                outcome => {
                    tracing::warn!(partition, ?outcome, "partition owner unreadable; kept as it was");
                    continue;
                }
            };
            if let Some(record) = &current {
                if record.claim.owner.instance == me {
                    lock(&inner.ownership).keep(partition, record.claim.clone());
                    if let Some(handoff) = &record.handoff {
                        // Allowed once the former owner is `Dead` (it retires
                        // right after a handoff); until then this is a no-op.
                        let cleared = inner.ports.partitions.clear_handoff(&record.claim, &handoff.request_id).await;
                        tracing::debug!(partition, ?cleared, "handoff mark cleanup");
                    }
                    continue;
                }
                lock(&inner.ownership).lose(partition);
                let owner = &record.claim.owner.instance;
                let key = (owner.node.clone(), owner.incarnation.clone());
                let live = match liveness.get(&key) {
                    Some(live) => *live,
                    None => {
                        let live = inner.ports.membership.liveness(owner).await.unwrap_or(Liveness::Unknown);
                        liveness.insert(key, live);
                        live
                    }
                };
                if live != Liveness::Dead {
                    lock(&inner.ownership).claimable_since.remove(&partition);
                    continue;
                }
            } else {
                lock(&inner.ownership).lose(partition);
            }
            let Some(candidates) = &candidates else {
                continue;
            };
            let Some(rank) = rank_candidates(partition, candidates).iter().position(|i| *i == me) else {
                continue;
            };
            let since = *lock(&inner.ownership).claimable_since.entry(partition).or_insert_with(Instant::now);
            if since.elapsed() < inner.config.candidate_backoff.saturating_mul(u32::try_from(rank).unwrap_or(u32::MAX)) {
                continue;
            }
            let expected = current.map(|r| r.claim.owner.epoch);
            let request = ClaimShard {
                shard,
                me: me.clone(),
                expected_epoch: expected,
                request_id: RequestId(format!(
                    "claim:{}:{}:{}:{}",
                    me.node,
                    me.incarnation,
                    partition,
                    expected.map_or(0, |e| e.0)
                )),
            };
            match timeout(inner.config.peer_timeout, inner.ports.partitions.claim(request)).await {
                Ok(Ok(LeaseClaim::Claimed(claim) | LeaseClaim::AlreadyClaimed(claim))) => {
                    tracing::info!(partition, epoch = claim.owner.epoch.0, "partition claimed");
                    lock(&inner.ownership).keep(partition, claim);
                }
                Ok(Ok(LeaseClaim::Conflict)) => {}
                // Unknown: the next round reads the record and resolves it.
                outcome => tracing::warn!(partition, ?outcome, "partition claim outcome unknown"),
            }
        }
        self.recover_acquired().await;
    }

    /// Claims the calls listed for each partition acquired since the last
    /// recovery, up to the free capacity. The rest arrive as re-posted hints.
    async fn recover_acquired(&self) {
        let inner = &self.inner;
        let partitions = std::mem::take(&mut lock(&inner.ownership).recover);
        for partition in partitions {
            let free = inner.config.max_active_calls.saturating_sub(self.live());
            let Some(limit) = u32::try_from(free).ok().and_then(NonZeroU32::new) else {
                return;
            };
            let listed = inner.ports.queue.held_in(&inner.config.pool, partition, limit).await;
            match listed {
                Ok(dids) => {
                    tracing::info!(partition, calls = dids.len(), "recovering an acquired partition");
                    for did in dids {
                        if inner.draining.load(Ordering::SeqCst) {
                            return;
                        }
                        // A failure is left to the re-posted hints.
                        let _ = self.decide(&did).await;
                    }
                }
                Err(error) => tracing::warn!(partition, ?error, "held index of an acquired partition unreadable"),
            }
        }
    }

    /// Hands every owned partition to a peer before `deadline` (drain only,
    /// after every call of this process stopped). Returns how many were not
    /// handed over; those are claimed by others once this process is `Dead`.
    pub(super) async fn hand_off(&self, deadline: Instant) -> usize {
        let owned: Vec<(u32, ShardClaim)> = lock(&self.inner.ownership)
            .owned
            .iter()
            .map(|(p, c)| (*p, c.clone()))
            .collect();
        let mut left = 0;
        for (partition, claim) in owned {
            if self.hand_off_one(partition, &claim, deadline).await {
                lock(&self.inner.ownership).lose(partition);
            } else {
                left += 1;
            }
        }
        left
    }

    /// Offers one partition to the peers in HRW order until one takes it, it
    /// is no longer this process's, or time runs out.
    async fn hand_off_one(&self, partition: u32, claim: &ShardClaim, deadline: Instant) -> bool {
        let inner = &self.inner;
        let me = &inner.config.me;
        let mut refused: Vec<Instance> = Vec::new();
        let mut attempt = 0u32;
        let mut backoff = Backoff::new(&inner.config);
        loop {
            let now = Instant::now();
            if now + inner.config.peer_timeout > deadline {
                tracing::warn!(partition, "no time left to hand the partition over");
                return false;
            }
            // Bounded by the drain's deadline, like every step here: a slow
            // store must not keep the drain from retiring on time.
            let Ok(candidates) = tokio::time::timeout_at(deadline, self.candidates()).await else {
                tracing::warn!(partition, "member list too slow; partition not handed over");
                return false;
            };
            let peers: Vec<MemberRecord> = candidates
                .unwrap_or_default()
                .into_iter()
                .filter(|m| m.instance != *me && !m.draining && !refused.contains(&m.instance))
                .collect();
            let instances: Vec<Instance> = peers.iter().map(|m| m.instance.clone()).collect();
            let Some(target) = rank_candidates(partition, &instances)
                .first()
                .and_then(|best| peers.iter().find(|m| m.instance == *best))
                .cloned()
            else {
                if refused.is_empty() {
                    tracing::warn!(partition, "no peer to hand the partition to");
                    return false;
                }
                // Everyone refused: ask them again after a pause.
                refused.clear();
                backoff.wait_at_most(deadline.saturating_duration_since(Instant::now())).await;
                continue;
            };
            attempt += 1;
            let request = HandoffRequest {
                request_id: RequestId(format!(
                    "handoff:{}:{}:{}:{}:{attempt}",
                    me.node, me.incarnation, partition, claim.owner.epoch.0
                )),
                from: claim.clone(),
                target: target.instance.clone(),
                grace: deadline.saturating_duration_since(now),
            };
            let outcome = timeout(inner.config.peer_timeout, inner.ports.peers.handoff(&target, request.clone())).await;
            match outcome {
                Ok(Ok(HandoffOutcome::Accepted(record)))
                    if record.claim.owner.instance == target.instance && record.claim.shard == claim.shard =>
                {
                    tracing::info!(partition, target = %target.instance.node, "partition handed over");
                    return true;
                }
                Ok(Ok(HandoffOutcome::Rejected)) => {
                    tracing::info!(partition, target = %target.instance.node, "peer refused the partition; trying the next");
                    refused.push(target.instance);
                }
                // Fenced, an unexpected answer, an error or no answer: the
                // owner record tells what happened. A transfer to this peer
                // that lands later is still safe: it needs this exact claim,
                // which a transfer to another peer replaces.
                other => {
                    tracing::warn!(partition, target = %target.instance.node, ?other, "handoff answer unclear");
                    match tokio::time::timeout_at(deadline, inner.ports.partitions.current(claim.shard)).await {
                        Ok(Ok(Some(record))) if record.claim != *claim => return true,
                        Ok(Ok(None)) => return true,
                        Ok(_) => refused.push(target.instance),
                        Err(_) => return false,
                    }
                }
            }
        }
    }

    /// The target side of a handoff: commits the transfer of a partition to
    /// this process when it is ready and not draining. Otherwise it only
    /// confirms a transfer already committed (a retried request).
    pub(super) async fn accept(&self, request: HandoffRequest) -> Result<HandoffOutcome, RuntimeError> {
        let inner = &self.inner;
        let partition = request.from.shard.0;
        if request.target != inner.config.me || partition >= inner.ports.queue.command_partitions() {
            return Ok(HandoffOutcome::Fenced);
        }
        let ready = inner.ready.load(Ordering::SeqCst) && !inner.draining.load(Ordering::SeqCst);
        if !ready || request.grace.is_zero() {
            let current = timeout(inner.config.peer_timeout, inner.ports.partitions.current(request.from.shard))
                .await
                .map_err(|_| failed("partition_owner_unknown"))?
                .map_err(|_| failed("partition_owner_unknown"))?;
            return Ok(match current {
                Some(record)
                    if record.claim.owner.instance == inner.config.me
                        && record.handoff.as_ref().is_some_and(|h| h.request_id == request.request_id) =>
                {
                    HandoffOutcome::Accepted(record)
                }
                Some(record) if record.claim == request.from => HandoffOutcome::Rejected,
                _ => HandoffOutcome::Fenced,
            });
        }
        let outcome = timeout(inner.config.peer_timeout, inner.ports.partitions.handoff(request))
            .await
            .map_err(|_| failed("partition_handoff_unknown"))?
            .map_err(|error| {
                tracing::warn!(partition, ?error, "partition handoff not committed");
                failed("partition_handoff_failed")
            })?;
        if let HandoffOutcome::Accepted(record) = &outcome {
            tracing::info!(partition, epoch = record.claim.owner.epoch.0, "partition taken over by handoff");
            lock(&inner.ownership).keep(partition, record.claim.clone());
        }
        Ok(outcome)
    }
}
