//! The `DispatchQueuePort` provider: the fenced claim of a dispatch.
//!
//! A claim writes holder `{me, epoch + 1}` at version `(epoch + 1) × 10^9`.
//! Two Connectors claiming at once write the same version and equal is
//! rejected, so exactly one wins; every write of a former holder is below the
//! new version and is rejected from then on.

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

/// Settings of the dispatch-board providers.
#[derive(Debug, Clone)]
pub struct DispatchBoardConfig {
    /// Identifies the store in command deliveries, e.g. `tbase`.
    pub backend: BackendId,
    /// Command-stream partitions; the same value the Worker posts with.
    pub streams: CommandStreams,
    /// Longest wait of one blocking group read.
    pub read_block: Duration,
    /// Entries one group read or idle takeover page may return.
    pub read_batch: NonZeroU32,
    /// How often hints left pending by other consumers are taken over.
    pub reclaim_every: Duration,
    /// A pending hint is taken over once it was idle this long.
    pub reclaim_min_idle: Duration,
    /// Attempts of one conditional write before it reports contention.
    pub write_attempts: u32,
}

impl DispatchBoardConfig {
    /// `streams` has no default: it must match the Worker's configuration.
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

const READ_BATCH: NonZeroU32 = match NonZeroU32::new(64) {
    Some(batch) => batch,
    None => NonZeroU32::MIN,
};

/// How many times a claim re-reads after losing a race before giving up.
const CLAIM_ATTEMPTS: u32 = 3;

#[derive(Clone)]
pub struct StoreDispatchQueue {
    shared: Arc<Shared>,
}

impl StoreDispatchQueue {
    /// Fails when a store lacks `AtomicJson`, `ZSet`, `Stream` or `StreamGroups`.
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
            (json.as_ref(), StoreCapability::AtomicJson),
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
            None => {
                self.unlist(me, did).await;
                Ok(None)
            }
        }
    }

    /// Nothing to hold. A failure only leaves the index listing too much.
    async fn unlist(&self, me: &Instance, did: &Did) {
        if let Err(error) = held_index::remove(&self.shared, me, did).await {
            tracing::warn!(?error, did = %did.0, "held index cleanup failed");
        }
    }

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
    async fn claim(&self, request: DispatchClaimRequest) -> Result<ClaimOutcome, StoreError> {
        // Validate the id before anything is read or written.
        self.shared.layout.disp(&request.did)?;
        self.decide_claim(&request).await
    }

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

    async fn notifications(
        &self,
        pool: PoolId,
        me: Instance,
    ) -> Result<Box<dyn CommandFeedPort>, StoreError> {
        let feed = StoreCommandFeed::open(self.shared.clone(), pool, &me).await?;
        Ok(Box::new(feed))
    }
}
