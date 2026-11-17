//! Provider-independent membership, shard transitions and checkpoint fencing.
//! Private document versions never encode epoch or business commit sequence.
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
}

pub struct CoordinationStore {
    documents: Arc<dyn DocumentStorePlugin>,
    leases: Arc<dyn LeaseStorePlugin>,
    index: Arc<dyn ZSetStorePlugin>,
    layout: KeyLayout,
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
        for (supported, code) in [
            (
                documents.supports(StoreCapability::DocumentCas),
                "document_cas_required",
            ),
            (
                leases.supports(StoreCapability::StrictLease),
                "strict_lease_required",
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
    async fn valid_claim(&self, claim: &ShardClaim) -> Result<bool, StoreError> {
        Ok(self
            .current(claim.shard)
            .await?
            .is_some_and(|r| r.claim == *claim)
            && self.liveness(&claim.owner.instance).await? == Liveness::Alive)
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
            entry.checkpoint = Some(value);
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
            if cache.len() >= 4096 {
                return Err(not_applied("session_cache_capacity"));
            }
            cache.insert(
                sid.0.clone(),
                CachedSession {
                    holder: holder.clone(),
                    checkpoint: None,
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
            .read::<StoredCheckpoint>(&self.layout.ckpt(sid)?)
            .await?;
        if let Some(v) = &value {
            if v.value.checkpoint.format_version != 1 {
                return Err(StoreError::UnsupportedFormat {
                    format: v.value.checkpoint.format_version,
                });
            }
            if v.value
                .checkpoint
                .consumed
                .as_ref()
                .is_some_and(|p| p.sid != *sid)
            {
                return Err(invalid("stored_position"));
            }
            self.remember(sid, v.clone())?;
        } else if self.cached(sid)?.is_some() {
            return Err(not_applied("known_checkpoint_missing"));
        }
        Ok(value)
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
