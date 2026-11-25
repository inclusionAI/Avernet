//! Provider-independent membership, shard transitions and checkpoint fencing.
//! Private document versions never encode epoch or business commit sequence.
#[cfg(test)]
mod capacity_tests;
mod capacity;
mod control;
mod membership;
mod session;
mod shard;
use crate::{
    codec::{decode_json, encode_json},
    error,
    layout::KeyLayout,
};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{common::*, ownership::*, session::*};
use bcs_store_api::{
    StoreCapability, Version, Versioned, WriteOutcome, document::DocumentStorePlugin,
    lease::LeaseStorePlugin, zset::ZSetStorePlugin,
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Clone, Serialize, Deserialize)]
struct StoredCheckpoint {
    #[serde(flatten)]
    envelope: CheckpointEnvelope,
    #[serde(default)]
    recovery_attempts: Vec<RequestId>,
    #[serde(default)]
    control: bcs_service_api::port::repo::distributed::control::SessionControl,
}
impl std::ops::Deref for StoredCheckpoint {
    type Target = CheckpointEnvelope;
    fn deref(&self) -> &CheckpointEnvelope {
        &self.envelope
    }
}
impl std::ops::DerefMut for StoredCheckpoint {
    fn deref_mut(&mut self) -> &mut CheckpointEnvelope {
        &mut self.envelope
    }
}

struct CachedSession {
    holder: ShardOwner,
    checkpoint: Option<Versioned<StoredCheckpoint>>,
    uncertain_acquisition: bool,
    known_checkpoint: bool,
}

/// Local process bounds; no durable state is evicted to enforce these limits.
#[derive(Debug, Clone)]
pub struct CoordinationStoreConfig {
    pub max_cached_sessions: usize,
    /// Independent shard/handoff dimension, not an active-session budget.
    pub max_handoff_timers: usize,
    /// Enforced before every CAS; the host separately validates backend capacity.
    pub checkpoint_capacity: SessionCheckpointCapacity,
    /// Host-declared worst-case private DocumentCas wrapper bytes. The adapter
    /// must prove this bound; it is included in both declared byte budgets.
    pub storage_envelope_bytes: usize,
}
impl Default for CoordinationStoreConfig {
    fn default() -> Self {
        Self {
            max_cached_sessions: 10_000,
            max_handoff_timers: 4096,
            checkpoint_capacity: SessionCheckpointCapacity::default(),
            storage_envelope_bytes: 64,
        }
    }
}

pub struct CoordinationStore {
    documents: Arc<dyn DocumentStorePlugin>,
    leases: Arc<dyn LeaseStorePlugin>,
    index: Arc<dyn ZSetStorePlugin>,
    layout: KeyLayout,
    config: CoordinationStoreConfig,
    cache: Mutex<HashMap<String, CachedSession>>,
    timers: Mutex<HashMap<u32, (HandoffRequest, Instant)>>,
}
impl CoordinationStore {
    pub fn new(
        documents: Arc<dyn DocumentStorePlugin>,
        leases: Arc<dyn LeaseStorePlugin>,
        index: Arc<dyn ZSetStorePlugin>,
        layout: KeyLayout,
    ) -> Result<Self, StoreError> {
        Self::with_config(
            documents,
            leases,
            index,
            layout,
            CoordinationStoreConfig::default(),
        )
    }
    pub fn with_config(
        documents: Arc<dyn DocumentStorePlugin>,
        leases: Arc<dyn LeaseStorePlugin>,
        index: Arc<dyn ZSetStorePlugin>,
        layout: KeyLayout,
        config: CoordinationStoreConfig,
    ) -> Result<Self, StoreError> {
        if config.max_cached_sessions == 0 || config.max_handoff_timers == 0
            || config.checkpoint_capacity.max_document_bytes < 256 * 1024
            || config.checkpoint_capacity.max_document_bytes > 1024 * 1024
            || config.checkpoint_capacity.max_envelope_bytes < 64 * 1024
            || config.checkpoint_capacity.max_envelope_bytes > 128 * 1024
            || config.checkpoint_capacity.max_envelope_bytes >= config.checkpoint_capacity.max_document_bytes
            || config.storage_envelope_bytes >= config.checkpoint_capacity.max_envelope_bytes
        {
            return Err(invalid("coordination_capacity"));
        }
        capacity::validate_config(&config)?;
        for (supported, code) in [
            (
                documents.supports(StoreCapability::DocumentCas),
                "document_cas_required",
            ),
            (
                leases.supports(StoreCapability::RetainedLease),
                "retained_lease_required",
            ),
            (index.supports(StoreCapability::ZSet), "zset_required"),
        ] {
            if !supported {
                return Err(not_applied(code));
            }
        }
        Ok(Self {
            documents,
            leases,
            index,
            layout,
            config,
            cache: Mutex::new(HashMap::new()),
            timers: Mutex::new(HashMap::new()),
        })
    }
    async fn read<T: DeserializeOwned>(
        &self,
        key: &str,
    ) -> Result<Option<Versioned<T>>, StoreError> {
        self.documents
            .read_document(key)
            .await
            .map_err(|e| error::from_read(&e))?
            .map(|v| {
                Ok(Versioned {
                    value: decode_json(v.value)?,
                    version: v.version,
                })
            })
            .transpose()
    }
    async fn write<T: Serialize>(
        &self,
        key: &str,
        expected: Option<Version>,
        value: &T,
        operation: &str,
    ) -> Result<Option<Version>, StoreError> {
        let data = encode_json(value)?;
        capacity::validate_row(&data, &self.config)?;
        let outcome = match expected {
            Some(v) => self.documents.replace_document(key, v, &data).await,
            None => self.documents.create_document(key, &data).await,
        }
        .map_err(|e| error::from_write(&e, operation))?;
        Ok(match outcome {
            WriteOutcome::Applied { version } => Some(version),
            WriteOutcome::ConditionNotMet => None,
        })
    }
    async fn confirmed_alive(
        &self,
        instance: &Instance,
        operation: &str,
    ) -> Result<bool, StoreError> {
        match self.liveness(instance).await? {
            Liveness::Alive => Ok(true),
            Liveness::Dead => Ok(false),
            // A missing lease or read failure cannot prove an earlier mutation
            // was fenced. Keep its identity/reservation for reconciliation.
            Liveness::Unknown => Err(unknown(operation)),
        }
    }
    async fn valid_claim(&self, claim: &ShardClaim) -> Result<bool, StoreError> {
        if !self
            .current(claim.shard)
            .await?
            .is_some_and(|r| r.claim == *claim)
        {
            return Ok(false);
        }
        self.confirmed_alive(&claim.owner.instance, "claim_liveness")
            .await
    }
    fn cached(&self, sid: &Sid) -> Result<Option<Versioned<StoredCheckpoint>>, StoreError> {
        Ok(self
            .cache
            .lock()
            .map_err(|_| not_applied("cache_poisoned"))?
            .get(&sid.0)
            .and_then(|entry| entry.checkpoint.clone()))
    }
    fn remember(&self, sid: &Sid, value: Versioned<StoredCheckpoint>) -> Result<(), StoreError> {
        let mut cache = self
            .cache
            .lock()
            .map_err(|_| not_applied("cache_poisoned"))?;
        // Observation alone must not allocate a local session. Keep the holder
        // separate from the observed document owner, including after transfer.
        if let Some(entry) = cache.get_mut(&sid.0)
            && entry
                .checkpoint
                .as_ref()
                .is_none_or(|old| old.version < value.version)
        {
            entry.known_checkpoint = true;
            entry.checkpoint = Some(value);
        }
        Ok(())
    }
    fn known_checkpoint(&self, sid: &Sid) -> Result<bool, StoreError> {
        Ok(self
            .cache
            .lock()
            .map_err(|_| not_applied("cache_poisoned"))?
            .get(&sid.0)
            .is_some_and(|entry| entry.known_checkpoint || entry.checkpoint.is_some()))
    }
    fn observe_checkpoint(&self, sid: &Sid, invalidate: bool) -> Result<(), StoreError> {
        if let Some(entry) = self
            .cache
            .lock()
            .map_err(|_| not_applied("cache_poisoned"))?
            .get_mut(&sid.0)
        {
            entry.known_checkpoint = true;
            if invalidate {
                entry.checkpoint = None;
            }
        }
        Ok(())
    }
    fn reserve_session(&self, sid: &Sid, holder: &ShardOwner) -> Result<bool, StoreError> {
        let mut cache = self
            .cache
            .lock()
            .map_err(|_| not_applied("cache_poisoned"))?;
        if let Some(entry) = cache.get_mut(&sid.0) {
            if entry.holder == *holder {
                return Ok(true);
            }
            if entry.holder.epoch >= holder.epoch {
                return Ok(false);
            }
            entry.holder = holder.clone();
        } else {
            if cache.len() >= self.config.max_cached_sessions {
                return Err(not_applied("session_cache_capacity"));
            }
            cache.insert(
                sid.0.clone(),
                CachedSession {
                    holder: holder.clone(),
                    checkpoint: None,
                    uncertain_acquisition: false,
                    known_checkpoint: false,
                },
            );
        }
        Ok(true)
    }
    async fn checkpoint(
        &self,
        sid: &Sid,
    ) -> Result<Option<Versioned<StoredCheckpoint>>, StoreError> {
        let value = self
            .read::<serde_json::Value>(&self.layout.ckpt(sid)?)
            .await?
            .map(|raw| {
                decode_checkpoint(raw.value).map(|value| Versioned {
                    version: raw.version,
                    value,
                })
            })
            .transpose()?;
        if let Some(v) = &value {
            if v.value
                .checkpoint
                .consumed
                .as_ref()
                .is_some_and(|p| p.sid != *sid)
            {
                return Err(invalid("stored_position"));
            }
            self.remember(sid, v.clone())?;
        } else if self.known_checkpoint(sid)? {
            return Err(not_applied("known_checkpoint_missing"));
        }
        Ok(value)
    }
    fn release_handoff_timer(&self, shard: ShardId, id: &RequestId) -> Result<(), StoreError> {
        let mut timers = self
            .timers
            .lock()
            .map_err(|_| not_applied("timer_poisoned"))?;
        if timers
            .get(&shard.0)
            .is_some_and(|(request, _)| request.request_id == *id)
        {
            timers.remove(&shard.0);
        }
        Ok(())
    }
    fn waiting(&self, record: &ShardRecord) -> Result<bool, StoreError> {
        let Some(h) = &record.handoff else {
            return Ok(false);
        };
        Ok(self
            .timers
            .lock()
            .map_err(|_| not_applied("timer_poisoned"))?
            .get(&record.claim.shard.0)
            .is_some_and(|(request, end)| {
                request.request_id == h.request_id
                    && request.from == h.from
                    && request.target == record.claim.owner.instance
                    && Instant::now() < *end
            }))
    }
}

fn decode_checkpoint(value: serde_json::Value) -> Result<StoredCheckpoint, StoreError> {
    let checkpoint = &value["checkpoint"];
    let format: u32 = decode_json(checkpoint["format_version"].clone())?;
    // Inspect the header before decoding a body whose layout may be unknown.
    // The #4145 coordination prototype reused v1 for an unscoped RunnerState;
    // it is not a phase-one runtime namespace and has no implicit migration.
    let legacy_runner = checkpoint["runner_state"].as_object().is_some_and(|runner| {
        runner.contains_key("format_version") && runner.contains_key("value")
            && !runner.contains_key("tid") && !runner.contains_key("state")
    });
    if format != 1 || legacy_runner {
        return Err(StoreError::UnsupportedFormat { format });
    }
    decode_json(value)
}
fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
fn not_applied(code: &str) -> StoreError {
    StoreError::NotApplied { code: code.into() }
}
fn unknown(id: &str) -> StoreError {
    StoreError::OutcomeUnknown {
        operation_id: id.into(),
    }
}
fn now() -> TimestampMillis {
    TimestampMillis(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_millis().min(i64::MAX as u128) as i64)
            .unwrap_or(0),
    )
}
const RETRIES: usize = 8;
