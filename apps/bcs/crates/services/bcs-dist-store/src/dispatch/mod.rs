//! Durable DispatchBoard over public document, index and stream plugins.
mod control;
mod journal;
mod reclaim;
mod scan;
mod scan_scheduler;
mod storage;
use crate::{
    codec::{decode_json, encode_json},
    evidence::{EffectVerifier, completion_identity},
    layout::KeyLayout,
};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::*, dispatch::*, ownership::*, session::EffectKind,
};
use bcs_store_api::{
    StoreCapability, Version, Versioned, WriteOutcome, document::DocumentStorePlugin,
    json::JsonStorePlugin, stream::*, zset::*,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    collections::BTreeMap,
    num::NonZeroU32,
    sync::Arc,
    time::{Duration, Instant},
};

/// Worker journals use exact document CAS; shared dispatch records use persistent object merge.
pub trait DispatchDocuments: DocumentStorePlugin + JsonStorePlugin {}
impl<T: DocumentStorePlugin + JsonStorePlugin> DispatchDocuments for T {}

#[derive(Clone)]
pub struct DispatchStores {
    pub scan_budget: Arc<dyn bcs_service_api::port::repo::distributed::budget::ScanBudgetPort>,
    pub documents: Arc<dyn DispatchDocuments>,
    pub streams: Arc<dyn StreamStorePlugin>,
    pub index: Arc<dyn ZSetStorePlugin>,
    pub membership: Arc<dyn MembershipPort>,
}
#[derive(Debug, Clone)]
pub struct DispatchConfig {
    pub command_streams: crate::command::CommandStreams,
    pub max_dispatches: usize,
    pub max_receipts: usize,
    pub receipt_bytes: usize,
    pub max_pending: usize,
    pub pending_bytes: usize,
    pub pool_size: usize,
    pub local_io: usize,
    pub io_timeout: Duration,
}
impl Default for DispatchConfig {
    fn default() -> Self {
        Self {
            command_streams: crate::command::CommandStreams::new(NonZeroU32::MIN),
            max_dispatches: 1024,
            max_receipts: 256,
            receipt_bytes: 512 * 1024,
            max_pending: 64,
            pending_bytes: 512 * 1024,
            pool_size: 32,
            local_io: 8,
            io_timeout: Duration::from_millis(500),
        }
    }
}
#[derive(Clone)]
pub struct StoreDispatch {
    stores: DispatchStores,
    layout: KeyLayout,
    verifier: EffectVerifier,
    config: DispatchConfig,
    io_slots: Arc<tokio::sync::Semaphore>,
    scans: scan_scheduler::ScanHandle,
}
impl StoreDispatch {
    pub fn new(
        stores: DispatchStores,
        layout: KeyLayout,
        config: DispatchConfig,
    ) -> Result<Self, StoreError> {
        if config.max_dispatches == 0
            || config.max_dispatches > 1024
            || config.max_receipts < 16
            || config.max_receipts > 256
            || config.receipt_bytes < 128 * 1024
            || config.receipt_bytes > 512 * 1024
            || config.max_pending < 8
            || config.max_pending > 64
            || config.pending_bytes < 128 * 1024
            || config.pending_bytes > 512 * 1024
            || config.local_io == 0
            || config.local_io > 8
            || config.local_io > config.pool_size / 4
            || config.io_timeout.is_zero()
            || config.io_timeout > Duration::from_millis(500)
        {
            return Err(invalid("dispatch_config"));
        }
        crate::budget::validate_profile(&stores.scan_budget.profile(), config.pool_size)?;
        if !stores.documents.supports(StoreCapability::DocumentCas)
            || !stores.documents.supports(StoreCapability::PersistentJsonMerge)
            || !stores.index.supports(StoreCapability::ZSet)
            || !stores.streams.supports(StoreCapability::Stream)
        {
            return Err(unavailable("dispatch_capabilities"));
        }
        Ok(Self {
            verifier: EffectVerifier::new(stores.documents.clone(), layout.clone()),
            stores,
            layout,
            io_slots: Arc::new(tokio::sync::Semaphore::new(config.local_io)),
            scans: scan_scheduler::ScanHandle::new(),
            config,
        })
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct Intent {
    identity: String,
    effect: EffectId,
    kind: EffectKind,
}
impl Intent {
    fn did(&self) -> Result<&Did, StoreError> {
        match &self.kind {
            EffectKind::CreateDispatch { spec } => Ok(&spec.did),
            EffectKind::RequestCancel { did, .. } | EffectKind::PushInput { did, .. } => Ok(did),
            _ => Err(invalid("dispatch_effect_kind")),
        }
    }
    fn critical(&self) -> bool {
        matches!(self.kind, EffectKind::RequestCancel { .. })
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
enum ReceiptResult {
    Applied,
    Conflict,
    Superseded {
        outcome: FinalDispatchOutcome,
        evidence: String,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Receipt {
    intent: Intent,
    result: ReceiptResult,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Directory {
    did: Did,
    receipts: usize,
    bytes: usize,
    /// Stable monotonic per-shard key. Rows never change key, deletes never
    /// move other rows, and scan cursors address the directory by key, not
    /// array position. 0 marks a legacy row persisted before keys existed.
    #[serde(default)]
    seq: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Journal {
    schema_version: u32,
    directory: Vec<Directory>,
    pending: Vec<Intent>,
    /// Next stable directory key of the shard (exclusive). Survives an
    /// emptied directory so reclaim can never re-key a removed did.
    #[serde(default)]
    next_seq: u64,
    /// Reclaim cursor: highest stable key a reclaim batch already examined.
    /// Batches only examine strictly greater keys and wrap to 0 when a batch
    /// runs past the directory end without exhausting its bounds.
    #[serde(default)]
    reclaim_next: u64,
}
impl Default for Journal {
    fn default() -> Self {
        Self {
            schema_version: 1,
            directory: vec![],
            pending: vec![],
            next_seq: 1,
            reclaim_next: 0,
        }
    }
}
impl Journal {
    /// The protected normalization funnel for legacy journals: zero-key rows
    /// (persisted before stable keys) receive fresh monotone keys, and the
    /// shard key counter stays strictly above every row. Never re-keys a row
    /// that already has one, so normalization is idempotent and safe at any
    /// read point; the normalized value persists with the next journal write.
    fn normalized(mut self) -> Self {
        let mut next = self
            .next_seq
            .max(1)
            .max(
                self.directory
                    .iter()
                    .map(|entry| entry.seq)
                    .max()
                    .unwrap_or(0),
            );
        for entry in &mut self.directory {
            if entry.seq == 0 {
                entry.seq = next;
                next = next.saturating_add(1);
            }
        }
        self.next_seq = next;
        self
    }
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct ConnectorEvidence {
    finish_evidence: Option<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Document {
    #[serde(flatten)]
    view: DispatchView,
    receipts: Vec<Receipt>,
    due: Option<TimestampMillis>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    connector: Option<ConnectorEvidence>,
}
impl Document {
    fn discard_confirmed(&mut self) {
        if let Some(last) = self.view.execution.confirmed_input_through {
            self.view.control.pending_input.retain(|p| p.seq > last);
        }
    }
    fn receipt(&self, intent: &Intent) -> Option<ReceiptResult> {
        self.receipts
            .iter()
            .find(|r| r.intent.effect == intent.effect)
            .map(|r| {
                if r.intent == *intent {
                    r.result.clone()
                } else {
                    ReceiptResult::Conflict
                }
            })
    }
    fn ended(&self) -> bool {
        self.view.execution.phase == DispatchPhase::Ended
    }
    fn final_outcome(&self) -> Option<FinalDispatchOutcome> {
        match self.view.execution.outcome {
            Some(DispatchOutcome::Completed) => Some(FinalDispatchOutcome::Completed),
            Some(DispatchOutcome::Failed) => Some(FinalDispatchOutcome::Failed),
            Some(DispatchOutcome::Canceled) => Some(FinalDispatchOutcome::Canceled),
            _ => None,
        }
    }
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
fn unavailable(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.into() }
}
fn unknown(operation: &str) -> StoreError {
    StoreError::OutcomeUnknown {
        operation_id: operation.into(),
    }
}
fn after_write(error: StoreError, operation: &str) -> StoreError {
    match error {
        StoreError::NotApplied { .. } => unknown(operation),
        other => other,
    }
}
fn bytes<T: Serialize>(value: &T) -> Result<usize, StoreError> {
    serde_json::to_vec(value)
        .map(|v| v.len())
        .map_err(|_| invalid("dispatch_encoding"))
}
fn format(version: u32) -> Result<(), StoreError> {
    if version == 1 {
        Ok(())
    } else {
        Err(StoreError::UnsupportedFormat { format: version })
    }
}
struct Budget {
    start: Instant,
    attempts: u8,
}
impl Budget {
    fn new() -> Self {
        Self {
            start: Instant::now(),
            attempts: 0,
        }
    }
    async fn next(&mut self, id: &str) -> Result<(), StoreError> {
        if self.attempts >= 8 {
            return Err(unknown(id));
        }
        if self.attempts > 0 {
            tokio::time::sleep(
                Duration::from_millis((5u64 << (self.attempts - 1)).min(200)).min(self.remaining()),
            )
            .await;
        }
        if self.remaining().is_zero() {
            return Err(unknown(id));
        }
        self.attempts += 1;
        Ok(())
    }
    fn remaining(&self) -> Duration {
        Duration::from_millis(500).saturating_sub(self.start.elapsed())
    }
    async fn io<T>(
        &self,
        id: &str,
        f: impl Future<Output = Result<T, StoreError>>,
    ) -> Result<T, StoreError> {
        tokio::time::timeout(self.remaining(), f)
            .await
            .map_err(|_| unknown(id))?
            .map_err(|e| after_write(e, id))
    }
}
