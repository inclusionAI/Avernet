//! The `DispatchQueuePort` provider: the fenced claim of a dispatch.
//!
//! A claim writes holder `{me, epoch + 1}` at version `(epoch + 1) × 10^9`.
//! Two Connectors claiming at once write the same version and equal is
//! rejected, so exactly one wins; every write of a former holder is below the
//! new version and is rejected from then on.
//!
//! Example: say the dispatch is still in band 0 (only Worker writes so far,
//! all below 10^9). The first claim takes epoch 1 and writes at 1 000 000 000; the holder's own
//! writes then go to 1 000 000 001, 1 000 000 002, ... A takeover (or a
//! resume) takes epoch 2 and writes at 2 000 000 000; from then on every
//! write of the epoch-1 holder (at `read + 1`, inside band 1) is rejected.
//! This is spec rules B1 (one holder generation at a time) and B18 (a stale
//! holder can change nothing).
//!
//! Where this sits: the Connector engine reads a hint from the command feed
//! (`notifications`), calls `claim`, and on `Acquired`
//! starts an actor with the returned `StoreHeldDispatch`. It acknowledges the
//! hint once the claim decided (spec rule B2).
//!
//! Keys touched:
//!
//! ```text
//! disp:       bcn:v1:c01:main:s0042:disp:s0042.7f3a9c.b21e.n2.a1
//!             claim patch: {"execution": {"holder": {"instance": {"node":
//!             "conn-03", "incarnation": "1794370950"}, "epoch": 2}}}
//! held index: bcn:v1:held:c01:main:conn-03:1794370950
//!             member "s0042.7f3a9c.b21e.n2.a1", score = claim time in ms
//! ```
//!
//! Glossary:
//! - **Claim outcome**: `Acquired` (this call now holds it, with a handle),
//!   `OwnedByMe` (this instance already holds it, under some epoch),
//!   `HeldByOther` (a live, or not provably dead, other instance holds it),
//!   `Ended`, `NotFound`.
//! - **Takeover**: a claim of a dispatch another instance holds; allowed only
//!   when membership says that instance is `Dead`.
//! - **Resume**: a re-claim, at the next epoch, of a call this instance holds
//!   but no actor runs.

use crate::doc::DispatchDoc;
use crate::feed::StoreCommandFeed;
use crate::held::StoreHeldDispatch;
use crate::held_index;
use crate::shared::{Shared, Wrote};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_dist_store::capability;
use bcs_dist_store::command::CommandStreams;
use bcs_dist_store::inbox::Clock;
use bcs_dist_store::layout::KeyLayout;
use bcs_dist_store::version;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::dispatch::{
    ClaimOutcome, CommandFeedPort, DispatchClaimRequest, DispatchQueuePort, ResumeOwnedDispatch,
};
use bcs_service_api::port::repo::distributed::ownership::{Liveness, MembershipPort};
use bcs_store_api::StoreCapability;
use bcs_store_api::json::JsonStorePlugin;
use bcs_store_api::stream::StreamStorePlugin;
use bcs_store_api::zset::ZSetStorePlugin;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

/// Settings of the dispatch-board providers (queue, held handles, feed).
///
/// Plain words for the stream terms below:
/// - a **group read** asks the store for new hints on behalf of this
///   Connector inside the consumer group `connectors`; each hint is handed to
///   exactly one Connector of the group and stays "pending" under it until it
///   is acknowledged;
/// - an **idle takeover** moves hints that sat pending under some Connector
///   (usually a crashed one) for too long over to this Connector, so they are
///   handled again.
#[derive(Debug, Clone)]
pub struct DispatchBoardConfig {
    /// Identifies the store in command deliveries, e.g. `tbase`. `ack`
    /// refuses a delivery that names another backend.
    pub backend: BackendId,
    /// Command-stream partitions; the same value the Worker posts with.
    /// No default: a mismatch would make the two sides disagree on where a
    /// dispatch's hints are.
    pub streams: CommandStreams,
    /// Longest wait of one blocking group read: how long the store holds a
    /// group read open when a partition has no new hint. Default 1 s.
    pub read_block: Duration,
    /// Entries one group read or idle takeover page may return. Default 64
    /// (`READ_BATCH`).
    pub read_batch: NonZeroU32,
    /// How often hints left pending by other consumers are taken over: one
    /// idle-takeover round at most every this long. Default 10 s.
    pub reclaim_every: Duration,
    /// A pending hint is taken over once it was idle this long, i.e. delivered
    /// and not acknowledged for this long. Default 30 s, so that a live
    /// Connector normally acknowledges first.
    pub reclaim_min_idle: Duration,
    /// Attempts of one conditional write before it reports contention.
    /// Used by the held handle's transitions (read, decide, write; a rejected
    /// write re-reads and tries again). Must be above 0. Default 5.
    pub write_attempts: u32,
}

impl DispatchBoardConfig {
    /// `streams` has no default: it must match the Worker's configuration.
    /// Every other field gets the default named on it.
    pub fn new(backend: BackendId, streams: CommandStreams) -> Self {
        Self {
            backend,
            streams,
            read_block: Duration::from_secs(1),
            read_batch: READ_BATCH,
            reclaim_every: Duration::from_secs(10),
            reclaim_min_idle: Duration::from_secs(30),
            write_attempts: 5,
        }
    }
}

/// Default page size of group reads and idle takeover (64 entries).
/// `NonZeroU32::new` returns an `Option`; the `None` arm is never taken.
const READ_BATCH: NonZeroU32 = match NonZeroU32::new(64) {
    Some(batch) => batch,
    None => NonZeroU32::MIN,
};

/// Rounds of read, decide, write one claim makes before giving up. These are
/// total rounds, not retries, and apply to every claim (a first claim and a
/// takeover alike). A round ends early when the read alone decides (ended,
/// missing, held by me, held by a live other). After the last round one more
/// read decides the answer without writing.
const CLAIM_ATTEMPTS: u32 = 3;

/// The `DispatchQueuePort` provider. Cheap to clone: all state is in the
/// shared `Shared` handle.
#[derive(Clone)]
pub struct StoreDispatchQueue {
    shared: Arc<Shared>,
}

impl StoreDispatchQueue {
    /// Fails when a store lacks `PersistentJsonMerge`, `ZSet`, `Stream` or `StreamGroups`.
    /// Checked at startup so a Connector never starts on a store that cannot
    /// fence or group-read (spec rule B21). Also fails (`dispatch_board_config`)
    /// when `write_attempts` is 0 or `read_block` is zero.
    pub fn new(
        json: Arc<dyn JsonStorePlugin>,
        zset: Arc<dyn ZSetStorePlugin>,
        stream: Arc<dyn StreamStorePlugin>,
        membership: Arc<dyn MembershipPort>,
        layout: KeyLayout,
        clock: Arc<dyn Clock>,
        config: DispatchBoardConfig,
    ) -> Result<Self, StoreError> {
        capability::require(&[
            (json.as_ref(), StoreCapability::PersistentJsonMerge),
            (zset.as_ref(), StoreCapability::ZSet),
            (stream.as_ref(), StoreCapability::Stream),
            (stream.as_ref(), StoreCapability::StreamGroups),
        ])?;
        if config.write_attempts == 0 || config.read_block.is_zero() {
            return Err(StoreError::Invalid {
                code: "dispatch_board_config".into(),
            });
        }
        Ok(Self {
            shared: Arc::new(Shared {
                json,
                zset,
                stream,
                membership,
                layout,
                clock,
                config,
            }),
        })
    }

    /// The dispatches `instance` is listed as holding (it may list too much).
    /// First page only, oldest claim first. Not used by the engine yet; it is
    /// the read a recovery of a dead Connector's calls (D3) would make.
    pub async fn held_by(
        &self,
        instance: &Instance,
        limit: NonZeroU32,
    ) -> Result<Vec<Did>, StoreError> {
        let page = held_index::page(&self.shared, instance, None, limit).await?;
        Ok(page.into_iter().map(|entry| Did(entry.member)).collect())
    }

    /// Whether `holder` may be taken over. Only a clear `Dead` allows it;
    /// `Unknown`, `Alive` and a failed check do not (with the pending
    /// membership provider every check fails, so the answer is always no).
    /// Taking over a live holder would run the call twice, so doubt means no.
    async fn holder_is_dead(&self, holder: &Holder) -> bool {
        match self.shared.membership.liveness(&holder.instance).await {
            Ok(Liveness::Dead) => true,
            Ok(Liveness::Alive | Liveness::Unknown) => false,
            Err(error) => {
                tracing::debug!(?error, "holder liveness unavailable; not taking over");
                false
            }
        }
    }

    /// Writes holder `{me, epoch}` at the claim version, then reads the
    /// document back: the handle starts from what is stored now, not from the
    /// claimant's earlier read (a former holder may have written in between).
    /// `None` = lost a race.
    ///
    /// Claims of one dispatch by one instance are made one at a time (the
    /// engine handles hints sequentially), so a document that names this
    /// instance at `epoch` after an unknown write is this call's own claim.
    ///
    /// `Unknown` write = the store cannot tell whether it applied (e.g. the
    /// connection broke). If the read-back does not show this claim, the
    /// answer is `None` but the dispatch stays listed in the held index: the
    /// write may still land later and name this instance, and the index must
    /// never list too little (B6).
    ///
    /// A claim that applied but cannot be read back (read failed, or another
    /// claim already followed it) is `OutcomeUnknown`: no handle is made from
    /// a state this call has not seen.
    async fn take(
        &self,
        doc: &DispatchDoc,
        me: &Instance,
        epoch: HolderEpoch,
    ) -> Result<Option<ClaimOutcome>, StoreError> {
        let did = &doc.view.spec.did;
        let holder = Holder {
            instance: me.clone(),
            epoch,
        };
        // Listed as held right before the claim, so the index never misses a call.
        held_index::add(&self.shared, me, did).await?;
        let first_claim = doc.view.execution.phase == DispatchPhase::Created;
        let mutation = crate::doc::claim_write(&holder, first_claim, !doc.has_connector)?;
        let wrote = self.shared.mutate(did, version::claim(epoch)?, mutation).await?;
        let unknown = matches!(wrote, Wrote::Unknown);
        let claimed = match wrote {
            Wrote::Rejected => None,
            Wrote::Applied(_) | Wrote::Unknown => match self.shared.read(did).await {
                Ok(Some(now)) if now.view.execution.holder.as_ref() == Some(&holder) => Some(now),
                Ok(_) if unknown => None,
                // Applied, but the document cannot be read back: not a handle.
                Ok(_) | Err(_) => {
                    return Err(StoreError::OutcomeUnknown {
                        operation_id: did.0.clone(),
                    });
                }
            },
        };
        match claimed {
            // The former holder ended the call just before the claim landed.
            Some(now) if now.view.execution.phase == DispatchPhase::Ended => {
                self.unlist(me, did).await;
                Ok(Some(ClaimOutcome::Ended))
            }
            Some(now) => Ok(Some(ClaimOutcome::Acquired(Box::new(StoreHeldDispatch::new(
                self.shared.clone(),
                holder,
                now.view,
                now.version,
            ))))),
            // The write may still land after this read-back. The call then
            // names this instance, so it must stay listed: the index may list
            // too much, never too little.
            None if unknown => Ok(None),
            None => {
                self.unlist(me, did).await;
                Ok(None)
            }
        }
    }

    /// Removes `did` from this instance's held index because there is
    /// nothing to hold. A failure only leaves the index listing too much.
    async fn unlist(&self, me: &Instance, did: &Did) {
        if let Err(error) = held_index::remove(&self.shared, me, did).await {
            tracing::warn!(?error, did = %did.0, "held index cleanup failed");
        }
    }

    /// The claim decision for one hint: up to [`CLAIM_ATTEMPTS`] rounds of
    /// read, decide, write. Per round:
    /// - missing -> `NotFound`; ended -> `Ended`;
    /// - held by this instance (any epoch) -> `OwnedByMe`; the engine then
    ///   pokes the running actor or resumes the call;
    /// - held by another instance that is not proven dead -> `HeldByOther`;
    /// - otherwise (no holder, or a dead one) -> claim at the next epoch.
    ///
    /// A lost race goes to the next round. When every round lost, a final
    /// read answers with whoever holds it now (or the dead holder last seen);
    /// with no holder at all it is `NotApplied` (`disp_claim_contended`).
    async fn decide_claim(&self, request: &DispatchClaimRequest) -> Result<ClaimOutcome, StoreError> {
        let mut last_holder = None;
        for _ in 0..CLAIM_ATTEMPTS {
            let Some(doc) = self.shared.read(&request.did).await? else {
                return Ok(ClaimOutcome::NotFound);
            };
            if doc.view.execution.phase == DispatchPhase::Ended {
                return Ok(ClaimOutcome::Ended);
            }
            if let Some(holder) = &doc.view.execution.holder {
                if holder.instance == request.me {
                    return Ok(ClaimOutcome::OwnedByMe(holder.clone()));
                }
                if !self.holder_is_dead(holder).await {
                    return Ok(ClaimOutcome::HeldByOther(holder.clone()));
                }
                last_holder = Some(holder.clone());
            }
            if let Some(outcome) = self.take(&doc, &request.me, next_epoch(&doc)?).await? {
                return Ok(outcome);
            }
        }
        // Lost every race: someone else holds it, or will be seen on a re-post.
        match self.shared.read(&request.did).await? {
            None => Ok(ClaimOutcome::NotFound),
            Some(doc) if doc.view.execution.phase == DispatchPhase::Ended => Ok(ClaimOutcome::Ended),
            Some(doc) => match doc.view.execution.holder.or(last_holder) {
                Some(holder) if holder.instance == request.me => Ok(ClaimOutcome::OwnedByMe(holder)),
                Some(holder) => Ok(ClaimOutcome::HeldByOther(holder)),
                None => Err(StoreError::NotApplied {
                    code: "disp_claim_contended".into(),
                }),
            },
        }
    }
}

/// The epoch a new claim takes: one above the stored holder epoch and above
/// the version band, which keeps the epoch even while `holder` is null.
///
/// Example: holder epoch 2 stored at version 2 000 000 005 -> epoch 3. No
/// holder at version 7 (only Worker writes so far) -> epoch 1.
fn next_epoch(doc: &DispatchDoc) -> Result<HolderEpoch, StoreError> {
    let stored = doc.view.execution.holder.as_ref().map_or(0, |h| h.epoch.0);
    stored
        .max(version::band_of(doc.version))
        .checked_add(1)
        .map(HolderEpoch)
        .ok_or_else(|| StoreError::Invalid {
            code: "holder_epoch_overflow".into(),
        })
}

#[async_trait]
impl DispatchQueuePort for StoreDispatchQueue {
    /// Claims `request.did` for `request.me`; see `decide_claim`. The caller
    /// acknowledges the hint after any `Ok` answer (B2).
    async fn claim(&self, request: DispatchClaimRequest) -> Result<ClaimOutcome, StoreError> {
        // Validate the id before anything is read or written.
        self.shared.layout.disp(&request.did)?;
        self.decide_claim(&request).await
    }

    /// Restarts a call this process holds but no actor runs (the actor died,
    /// or a claim answer was lost). The caller stops and joins the old actor
    /// first. It re-claims at `epoch + 1`, so a late write of the old actor is
    /// fenced. `Acquired` comes back from `take`.
    ///
    /// Early returns, before any write:
    /// - missing -> `NotFound`; ended -> `Ended`;
    /// - this instance under another epoch -> `OwnedByMe` (an earlier resume
    ///   landed; the caller resumes from that generation instead);
    /// - another instance -> `HeldByOther` (the call was taken over);
    /// - no holder -> `Invalid` (`resume_without_holder`): there is nothing of
    ///   this process's to resume.
    async fn resume_owned(&self, request: ResumeOwnedDispatch) -> Result<ClaimOutcome, StoreError> {
        let me = &request.previous.instance;
        let Some(doc) = self.shared.read(&request.did).await? else {
            return Ok(ClaimOutcome::NotFound);
        };
        if doc.view.execution.phase == DispatchPhase::Ended {
            return Ok(ClaimOutcome::Ended);
        }
        match &doc.view.execution.holder {
            Some(holder) if *holder == request.previous => {}
            Some(holder) if holder.instance == *me => {
                return Ok(ClaimOutcome::OwnedByMe(holder.clone()));
            }
            Some(holder) => return Ok(ClaimOutcome::HeldByOther(holder.clone())),
            None => {
                return Err(StoreError::Invalid {
                    code: "resume_without_holder".into(),
                });
            }
        }
        match self.take(&doc, me, next_epoch(&doc)?).await? {
            Some(outcome) => Ok(outcome),
            None => match self.shared.read(&request.did).await? {
                None => Ok(ClaimOutcome::NotFound),
                Some(now) if now.view.execution.phase == DispatchPhase::Ended => Ok(ClaimOutcome::Ended),
                Some(now) => match now.view.execution.holder {
                    Some(holder) if holder.instance == *me => Ok(ClaimOutcome::OwnedByMe(holder)),
                    Some(holder) => Ok(ClaimOutcome::HeldByOther(holder)),
                    None => Err(StoreError::NotApplied {
                        code: "disp_claim_contended".into(),
                    }),
                },
            },
        }
    }

    /// Opens this instance's command feed for `pool`: joins the consumer
    /// group on every partition. One feed per Connector process run.
    async fn notifications(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn CommandFeedPort>, StoreError> {
        let feed = StoreCommandFeed::open(self.shared.clone(), pool, &me).await?;
        Ok(Box::new(feed))
    }
}
