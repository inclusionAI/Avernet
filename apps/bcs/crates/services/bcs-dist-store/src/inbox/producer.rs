//! Admission is stored in one scoped document keyed by `(scope, input_id)`.
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
        atomic::{AtomicU64, AtomicUsize, Ordering},
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
    /// Deprecated compatibility fields. The MVP no longer charges admission
    /// against namespace-wide history or Session ledgers.
    #[deprecated(note = "admission no longer maintains shard-wide max history entries budgets")]
    pub max_history_entries: u64,
    #[deprecated(note = "admission no longer maintains shard-wide max history bytes budgets")]
    pub max_history_bytes: u64,
    #[deprecated(note = "admission no longer maintains shard-wide reserved entries budgets")]
    pub reserved_entries: u64,
    #[deprecated(note = "admission no longer maintains shard-wide reserved bytes budgets")]
    pub reserved_bytes: u64,
    #[deprecated(note = "admission no longer maintains shard-wide max sessions budgets")]
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
    #[allow(deprecated)]
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
        Ok(())
    }
}
pub const LOG_GENERATION: &str = "1";
#[derive(Clone)]
pub struct StoreInboxProducer {
    pub(super) inner: Arc<Inner>,
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
    pub slot_rotation: AtomicUsize,
    pub receipts: std::sync::Mutex<std::collections::BTreeMap<String, super::consumer::Delivery>>,
    pub receipt_sequence: AtomicU64,
    pub scan_budget: Arc<dyn bcs_service_api::port::repo::distributed::budget::ScanBudgetPort>,
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
                slot_rotation: AtomicUsize::new(0),
                receipts: std::sync::Mutex::new(Default::default()),
                receipt_sequence: AtomicU64::new(0),
                scan_budget,
            }),
        })
    }
    pub async fn admission_decision(
        &self,
        scope: &TaskScope,
        input_id: &InputId,
    ) -> Result<Option<AdmitOutcome>, StoreError> {
        let key = self.inner.layout.admission(scope, input_id)?;
        let Some((_, record)) = self.admission_record(&key).await? else {
            return Ok(None);
        };
        Ok(record
            .decision
            .as_ref()
            .map(|d| self.outcome(&record, d, false)))
    }
    pub(super) fn outcome(&self, c: &AdmissionRecord, d: &Decision, new: bool) -> AdmitOutcome {
        if *d == Decision::Rejected {
            return AdmitOutcome::Quarantined;
        }
        let i = &c.request.input;
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
        let key = self.inner.layout.admission(scope, id)?;
        self.admission_record(&key).await.map(|record| record.map(|(_, c)| c.request))
    }

    fn scan_budget_profile(
        &self,
    ) -> Option<bcs_service_api::port::repo::distributed::budget::ScanBudgetProfile> {
        Some(self.inner.scan_budget.profile())
    }
    async fn admit(&self, request: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        self.admit_scoped(request, None).await
    }
    async fn admit_connector(
        &self,
        request: ConnectorAdmission,
    ) -> Result<AdmitOutcome, StoreError> {
        let spec = self
            .load::<SessionSpec>(&self.inner.layout.spec(&request.input.sid)?)
            .await?
            .ok_or_else(|| StoreError::NotApplied {
                code: "session_not_found".into(),
            })?
            .1;
        self.admit_scoped(
            AdmitRequest {
                scope: spec.scope,
                input: request.input,
                create_session: None,
                create_task: None,
            },
            Some(request.identity),
        )
        .await
    }
    async fn append_stream(&self, request: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        self.append_intermediate(request).await
    }
}

impl StoreInboxProducer {
    async fn admit_scoped(
        &self,
        mut request: AdmitRequest,
        connector: Option<ConnectorEventIdentity>,
    ) -> Result<AdmitOutcome, StoreError> {
        if let Err(error) = self.prepare(&mut request, connector.as_ref()).await {
            if matches!(&error, StoreError::Invalid { code } if code == "frozen_spec_conflict" || code == "frozen_task_conflict") {
                return Ok(AdmitOutcome::ContentConflict);
            }
            if matches!(&error, StoreError::NotApplied { code } if code != "session_not_found")
                || matches!(&error, StoreError::OutcomeUnknown { .. })
            {
                return Err(unknown(&request.input.input_id.0));
            }
            return Err(error);
        }
        let key = self
            .inner
            .layout
            .admission(&request.scope, &request.input.input_id)?;
        let op = request.input.input_id.0.clone();
        let digest = request.input.business_digest.clone();
        let existing = match self.admission_record(&key).await.map_err(|_| unknown(&op))? {
            Some((_, existing)) => existing,
            None => {
                let (_, schedule) = self.schedule(&request.input.sid).await.map_err(|_| unknown(&op))?;
                let admission = self.new_admission(request, schedule.generation, connector)?;
                if self.save(&key, None, &admission, &op).await? {
                    return self.advance(&key, true).await.map_err(|_| unknown(&op));
                }
                // Another writer won create CAS. Use the same outcome as if
                // the first read had seen its record, so Worker can reconcile
                // differing server-generated initialization with caller intent.
                self.admission_record(&key)
                    .await
                    .map_err(|_| unknown(&op))?
                    .ok_or_else(|| unknown(&op))?
                    .1
            }
        };
        if existing.request.input.business_digest != digest {
            return Ok(AdmitOutcome::ContentConflict);
        }
        self.advance(&key, false).await.map_err(|_| unknown(&op))
    }
}
