//! Durable DispatchBoard over public document, index and stream plugins.
mod commands;
mod control;
mod held;
mod journal;
mod queue;
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
    StoreCapability, Version, Versioned, WriteOutcome, document::DocumentStorePlugin, stream::*,
    zset::*,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    collections::BTreeMap,
    num::NonZeroU32,
    sync::Arc,
    time::{Duration, Instant},
};

#[derive(Clone)]
pub struct DispatchStores {
    pub scan_budget: Arc<dyn bcs_service_api::port::repo::distributed::budget::ScanBudgetPort>,
    pub documents: Arc<dyn DocumentStorePlugin>,
    pub streams: Arc<dyn StreamStorePlugin>,
    pub index: Arc<dyn ZSetStorePlugin>,
    pub membership: Arc<dyn MembershipPort>,
}
#[derive(Debug, Clone)]
pub struct DispatchConfig {
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
            || !stores.index.supports(StoreCapability::ZSet)
            || !stores.streams.supports(StoreCapability::StreamGroups)
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
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Journal {
    schema_version: u32,
    directory: Vec<Directory>,
    pending: Vec<Intent>,
}
impl Default for Journal {
    fn default() -> Self {
        Self {
            schema_version: 1,
            directory: vec![],
            pending: vec![],
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct Claim {
    request: RequestId,
    me: Instance,
    previous: Option<Holder>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Send {
    request: RequestId,
    remote: Option<RemoteTaskRef>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Document {
    schema_version: u32,
    view: DispatchView,
    receipts: Vec<Receipt>,
    claim: Option<Claim>,
    sends: BTreeMap<String, Send>,
    due: Option<TimestampMillis>,
    terminal_evidence: Option<String>,
}
impl Document {
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
fn operation_key(operation: &SendOperation) -> (String, &RequestId) {
    match operation {
        SendOperation::Start { request_id } => ("start".into(), request_id),
        SendOperation::Input { seq, request_id } => (format!("input:{}", seq.0), request_id),
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
