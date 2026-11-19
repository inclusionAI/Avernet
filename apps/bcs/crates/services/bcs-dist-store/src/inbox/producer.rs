//! Admission is journaled before selecting a scope-wide canonical operation.
//! Schedule EQ is the unique accept/close linearization point. All durable
//! records are retained; the active set and wake stream are disposable hints.
use super::{Clock, state::*, storage::*};
use crate::{capability, codec::encode_wake_entry, layout::KeyLayout};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::StoreError, inbox::*};
use bcs_store_api::{
    StoreCapability, document::DocumentStorePlugin, kv::KvStorePlugin, stream::*, zset::*,
};
use std::{
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
    time::Duration,
};

#[derive(Debug, Clone)]
pub struct InboxProducerConfig {
    pub backend: BackendId,
    pub wake_trim_every: u64,
    pub wake_max_len: u64,
    pub max_input_bytes: usize,
    pub max_document_bytes: usize,
    /// Namespace-lifetime charge units, including physical append attempts.
    /// A larger profile requires coordinated rollout and capacity validation.
    pub max_history_entries: u64,
    pub max_history_bytes: u64,
    pub reserved_entries: u64,
    pub reserved_bytes: u64,
    pub max_sessions: u64,
    pub max_inbox_entries: u64,
    pub max_inbox_bytes: u64,
    pub max_watches: usize,
    pub max_readers: usize,
    /// Local monotonic progress window; may be tightened below the 60s profile.
    pub maintenance_stale_after: Duration,
    pub max_receipts: usize,
    /// Shared store connection-pool capacity; scan IO is at most one quarter.
    pub connection_pool_size: usize,
    /// New Starts yield after this duration of continuous shared-pool congestion.
    pub pool_pressure_after: Duration,
}
impl InboxProducerConfig {
    pub fn new(backend: BackendId) -> Self {
        Self {
            backend,
            wake_trim_every: 128,
            wake_max_len: 4096,
            max_input_bytes: 64 * 1024,
            max_document_bytes: 960 * 1024,
            max_history_entries: 4096,
            max_history_bytes: 64 * 1024 * 1024,
            reserved_entries: 512,
            reserved_bytes: 8 * 1024 * 1024,
            max_sessions: 1024,
            max_inbox_entries: 4096,
            max_inbox_bytes: 16 * 1024 * 1024,
            max_watches: 128,
            max_readers: 64,
            maintenance_stale_after: Duration::from_secs(60),
            max_receipts: 256,
            connection_pool_size: 32,
            pool_pressure_after: Duration::from_millis(200),
        }
    }
    fn validate(&self) -> Result<(), StoreError> {
        if self.wake_trim_every == 0
            || self.backend.0.is_empty()
            || serde_json::to_vec(&self.backend.0).map_or(true, |b| b.len() > 256)
            || self.max_input_bytes == 0
            || self.max_input_bytes > 64 * 1024
            || self.max_document_bytes > 1024 * 1024
            || self.max_document_bytes < 256 * 1024
            || self.max_history_entries == 0
            || self.reserved_entries == 0
            || self.reserved_entries.saturating_add(64) >= self.max_history_entries
            || self.reserved_bytes < 64 * 1024
            || self.reserved_bytes.saturating_add(128 * 1024) >= self.max_history_bytes
            || self.max_sessions == 0
            // Preserve the previous effective bound independently of retained
            // history. The distinct-Session ledger lives in one root document.
            || self.max_sessions > 4096
            || self.max_sessions > self.max_history_entries
            || self.max_watches == 0
            || self.max_watches > 128
            || self.max_readers == 0
            || self.max_readers > 128
            || self.maintenance_stale_after.is_zero()
            || self.maintenance_stale_after > Duration::from_secs(60)
            || self.max_receipts == 0
            || self.max_receipts > 1024
            || self.connection_pool_size < 4
            || !(Duration::from_millis(5)..=Duration::from_secs(5)).contains(&self.pool_pressure_after)
            || self.max_inbox_entries <= 128
            || self.max_inbox_bytes <= 256 * 1024
        {
            return Err(invalid("admission_configuration"));
        }
        if super::capacity::maximum_scan_bytes(self.max_history_entries)
            .is_none_or(|bytes| bytes > self.max_document_bytes as u64)
        {
            return Err(invalid("admission_scan_capacity"));
        }
        Ok(())
    }
}
pub const LOG_GENERATION: &str = "1";
#[derive(Clone)]
pub struct StoreInboxProducer {
    pub(super) inner: Arc<Inner>,
    pub(super) scans: super::scan_scheduler::ScanHandle,
}
pub(super) struct Inner {
    pub projection: Arc<dyn bcs_service_api::core::dist_projection::OutputProjectionCoreService>,
    pub documents: Arc<dyn DocumentStorePlugin>,
    pub zset: Arc<dyn ZSetStorePlugin>,
    pub stream: Arc<dyn StreamStorePlugin>,
    pub layout: KeyLayout,
    pub clock: Arc<dyn Clock>,
    pub config: InboxProducerConfig,
    pub io: tokio::sync::Semaphore,
    pub readers: Arc<tokio::sync::Semaphore>,
    pub watches: Arc<tokio::sync::Semaphore>,
    pub wake_appends: AtomicU64,
    pub receipts: std::sync::Mutex<std::collections::BTreeMap<String, super::consumer::Delivery>>,
    pub receipt_sequence: AtomicU64,
    pub scan_budget: Arc<dyn bcs_service_api::port::repo::distributed::budget::ScanBudgetPort>,
    pub health: std::sync::Mutex<super::health::Health>,
}
impl StoreInboxProducer {
    #[allow(
        clippy::too_many_arguments,
        reason = "injected storage and pure validation boundaries"
    )]
    pub fn new(
        scan_budget: Arc<dyn bcs_service_api::port::repo::distributed::budget::ScanBudgetPort>,
        projection: Arc<dyn bcs_service_api::core::dist_projection::OutputProjectionCoreService>,
        kv: Arc<dyn KvStorePlugin>,
        documents: Arc<dyn DocumentStorePlugin>,
        zset: Arc<dyn ZSetStorePlugin>,
        stream: Arc<dyn StreamStorePlugin>,
        layout: KeyLayout,
        clock: Arc<dyn Clock>,
        config: InboxProducerConfig,
    ) -> Result<Self, StoreError> {
        capability::require(&[
            (kv.as_ref(), StoreCapability::Kv),
            (documents.as_ref(), StoreCapability::DocumentCas),
            (zset.as_ref(), StoreCapability::ZSet),
            (stream.as_ref(), StoreCapability::Stream),
        ])?;
        config.validate()?;
        crate::budget::validate_profile(&scan_budget.profile(), config.connection_pool_size)?;
        super::pressure::validate_observation(documents.pool_observation(), config.connection_pool_size)?;
        Ok(Self {
            scans: super::scan_scheduler::ScanHandle::new(),
            inner: Arc::new(Inner {
                projection,
                documents,
                zset,
                stream,
                layout,
                clock,
                io: tokio::sync::Semaphore::new((config.connection_pool_size / 4).min(8)),
                readers: Arc::new(tokio::sync::Semaphore::new(config.max_readers)),
                watches: Arc::new(tokio::sync::Semaphore::new(config.max_watches)),
                config,
                wake_appends: AtomicU64::new(0),
                receipts: std::sync::Mutex::new(Default::default()),
                receipt_sequence: AtomicU64::new(0),
                scan_budget,
                health: std::sync::Mutex::new(Default::default()),
            }),
        })
    }
    pub async fn admission_decision(
        &self,
        scope: &TaskScope,
        input_id: &InputId,
    ) -> Result<Option<AdmitOutcome>, StoreError> {
        let key = self.inner.layout.canonical_admission(scope, input_id)?;
        let Some((_, record)) = self.canonical(&key).await? else {
            return Ok(None);
        };
        Ok(record
            .decision
            .as_ref()
            .map(|d| self.outcome(&record, d, false)))
    }
    pub(super) fn outcome(&self, c: &Canonical, d: &Decision, new: bool) -> AdmitOutcome {
        if *d == Decision::Rejected {
            return AdmitOutcome::Quarantined;
        }
        let i = &c.intent.request.input;
        let receipt = AdmissionReceipt {
            sid: i.sid.clone(),
            tid: i.tid.clone(),
            input_id: i.input_id.clone(),
            business_digest: i.business_digest.clone(),
        };
        if new {
            AdmitOutcome::Accepted(receipt)
        } else {
            AdmitOutcome::Duplicate(receipt)
        }
    }
    pub(super) fn position(&self, sid: &Sid, id: StreamId) -> InboxPos {
        InboxPos {
            sid: sid.clone(),
            token: ReplayToken {
                backend: self.inner.config.backend.clone(),
                generation: LogGeneration(LOG_GENERATION.into()),
                value: format!("{}-{}", id.major, id.minor),
            },
        }
    }
    pub(super) async fn hint(
        &self,
        sid: &Sid,
        due: Option<TimestampMillis>,
    ) -> Result<(), StoreError> {
        let shard = KeyLayout::shard_of(sid)?;
        let _permit = self.io_permit().await?;
        let action = async {
            if let Some(due) = due {
                self.inner
                    .zset
                    .add(
                        &self.inner.layout.active(shard),
                        &[ZSetEntry {
                            member: sid.0.clone(),
                            score: due.0 as f64,
                        }],
                        ZAddMode::Upsert,
                    )
                    .await?;
                self.inner
                    .stream
                    .append(
                        &self.inner.layout.wake(shard),
                        AppendId::Auto,
                        &encode_wake_entry(sid),
                    )
                    .await?;
                if self
                    .inner
                    .wake_appends
                    .fetch_add(1, Ordering::Relaxed)
                    .is_multiple_of(self.inner.config.wake_trim_every)
                {
                    self.inner
                        .stream
                        .trim(
                            &self.inner.layout.wake(shard),
                            TrimPolicy::MaxLen(self.inner.config.wake_max_len),
                        )
                        .await?;
                }
            } else {
                self.inner
                    .zset
                    .remove(
                        &self.inner.layout.active(shard),
                        std::slice::from_ref(&sid.0),
                    )
                    .await?;
            }
            Ok::<_, bcs_store_api::StoreError>(())
        };
        tokio::time::timeout(Duration::from_millis(100), action)
            .await
            .map_err(|_| unknown("scheduling_hint"))?
            .map_err(|e| crate::error::from_write(&e, "scheduling_hint"))
    }
}
#[async_trait]
impl InboxProducerPort for StoreInboxProducer {
    async fn lookup(&self, scope: &TaskScope, id: &InputId) -> Result<Option<AdmitRequest>, StoreError> {
        let key = self.inner.layout.canonical_admission(scope, id)?;
        self.canonical(&key).await.map(|record| record.map(|(_, c)| c.intent.request))
    }

    fn scan_budget_profile(
        &self,
    ) -> Option<bcs_service_api::port::repo::distributed::budget::ScanBudgetProfile> {
        Some(self.inner.scan_budget.profile())
    }
    async fn admit(&self, mut request: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        if let Err(error) = self.prepare(&mut request).await {
            if matches!(&error,StoreError::Invalid{code} if code=="frozen_spec_conflict" || code=="frozen_task_conflict")
            {
                return Ok(AdmitOutcome::ContentConflict);
            }
            if matches!(&error,StoreError::NotApplied{code} if code!="session_not_found")
                || matches!(&error, StoreError::OutcomeUnknown { .. })
            {
                return Err(unknown(&request.input.input_id.0));
            }
            return Err(error);
        }
        let key = self
            .inner
            .layout
            .canonical_admission(&request.scope, &request.input.input_id)?;
        if let Some((_, existing)) = self
            .canonical(&key)
            .await
            .map_err(|_| unknown(&request.input.input_id.0))?
        {
            if existing.intent.request.input.business_digest != request.input.business_digest {
                return Ok(AdmitOutcome::ContentConflict);
            }
            return self
                .advance(&key, false)
                .await
                .map_err(|_| unknown(&request.input.input_id.0));
        }
        let (_, schedule) = self
            .schedule(&request.input.sid)
            .await
            .map_err(|_| unknown(&request.input.input_id.0))?;
        let op = request.input.input_id.0.clone();
        let digest = request.input.business_digest.clone();
        let intent = match self.reserve_intent(request, schedule.generation).await {
            Ok(intent) => intent,
            Err(error @ StoreError::NotApplied { .. }) => {
                // Capacity can fill while another copy of this identity wins
                // canonical selection. A definite refusal must confirm that the
                // previous missing-canonical read is still authoritative.
                let Some((_, existing)) = self.canonical(&key).await.map_err(|_| unknown(&op))?
                else {
                    return Err(error);
                };
                if existing.intent.request.input.business_digest != digest {
                    return Ok(AdmitOutcome::ContentConflict);
                }
                return self.advance(&key, false).await.map_err(|_| unknown(&op));
            }
            Err(error) => return Err(error),
        };
        self.materialize(&intent).await.map_err(|_| unknown(&op))?;
        self.select(&intent).await.map_err(|_| unknown(&op))?;
        let Some((_, selected)) = self.canonical(&key).await.map_err(|_| unknown(&op))? else {
            return Err(unknown(&op));
        };
        if selected.intent.request.input.business_digest != intent.request.input.business_digest {
            return Ok(AdmitOutcome::ContentConflict);
        }
        self.advance(&key, true).await.map_err(|_| unknown(&op))
    }
    async fn append_stream(&self, request: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        self.append_intermediate(request).await
    }
}
