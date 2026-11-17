//! Admission in the fixed write order of the design:
//!
//! 1. read `admit`: same digest → accepted; different → conflict;
//! 2. create `spec` (new session) and `task` (new task), create-once;
//! 3. append the input to `inbox`;
//! 4. read the listener (delayed contract D1, see [`read_listener`]);
//! 5. register the session in `active` with the lower score;
//! 6. append to `wake` when nobody valid listens;
//! 7. write `admit` (the success point).
//!
//! An error before the first write is reported as is. Once anything was
//! written, every error is `OutcomeUnknown` (operation id = the input id) and the
//! caller retries with the same input; the retry redoes the missing steps. A
//! retry may append a second `inbox` entry with the same input id; the Worker
//! drops repeats by input id.
//!
//! The merged port separates `Accepted` (this call wrote `admit`) from
//! `Duplicate` (the answer comes from an `admit` written earlier, by a previous
//! attempt or by the Worker's compensation). Decision 24: callers treat both as
//! the same success; a different digest is `ContentConflict`.

use super::Clock;
use super::listener::{ListenerState, read_listener};
use crate::capability;
use crate::codec::{AdmitRecord, encode_inbox_entry, encode_json, encode_wake_entry};
use crate::error::{from_read, from_write};
use crate::identity::{digest_fields, digest_json, hex_of};
use crate::layout::KeyLayout;
use crate::version;
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::common::StoreError;
use bcs_service_api::port::repo::distributed::inbox::{
    AdmitOutcome, AdmitRequest, AppendAgentEvent, InboxProducerPort,
};
use bcs_store_api::json::{JsonMutation, JsonStorePlugin};
use bcs_store_api::kv::KvStorePlugin;
use bcs_store_api::stream::{AppendId, StreamId, StreamStorePlugin, TrimPolicy};
use bcs_store_api::zset::{ZAddMode, ZSetEntry, ZSetStorePlugin};
use bcs_store_api::{Expiry, StoreCapability, WriteCondition, WriteOutcome};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

/// Settings of the admission provider.
#[derive(Debug, Clone)]
pub struct InboxProducerConfig {
    /// Identifies the store in replay tokens, e.g. `tbase`.
    pub backend: BackendId,
    /// `wake` is trimmed on one append in this many (it is only a hint).
    pub wake_trim_every: u64,
    /// Entries `wake` keeps after a trim.
    pub wake_max_len: u64,
}

impl InboxProducerConfig {
    pub fn new(backend: BackendId) -> Self {
        Self {
            backend,
            wake_trim_every: 128,
            wake_max_len: 100_000,
        }
    }
}

/// Generation of `inbox` positions. Stream ids never restart, so this changes
/// only if a future rebuild procedure recreates a stream.
pub const LOG_GENERATION: &str = "1";

pub struct StoreInboxProducer {
    kv: Arc<dyn KvStorePlugin>,
    json: Arc<dyn JsonStorePlugin>,
    zset: Arc<dyn ZSetStorePlugin>,
    stream: Arc<dyn StreamStorePlugin>,
    layout: KeyLayout,
    clock: Arc<dyn Clock>,
    config: InboxProducerConfig,
    wake_appends: AtomicU64,
}

impl StoreInboxProducer {
    /// Fails when a store lacks `Kv`, `AtomicJson`, `ZSet` or `Stream`.
    pub fn new(
        kv: Arc<dyn KvStorePlugin>,
        json: Arc<dyn JsonStorePlugin>,
        zset: Arc<dyn ZSetStorePlugin>,
        stream: Arc<dyn StreamStorePlugin>,
        layout: KeyLayout,
        clock: Arc<dyn Clock>,
        config: InboxProducerConfig,
    ) -> Result<Self, StoreError> {
        capability::require(&[
            (kv.as_ref(), StoreCapability::Kv),
            (json.as_ref(), StoreCapability::AtomicJson),
            (zset.as_ref(), StoreCapability::ZSet),
            (stream.as_ref(), StoreCapability::Stream),
        ])?;
        if config.wake_trim_every == 0 {
            return Err(StoreError::Invalid {
                code: "wake_trim_every_zero".into(),
            });
        }
        Ok(Self {
            kv,
            json,
            zset,
            stream,
            layout,
            clock,
            config,
            wake_appends: AtomicU64::new(0),
        })
    }

    /// Validates the request and builds every key and document it writes.
    fn prepare(&self, request: &AdmitRequest) -> Result<Prepared, StoreError> {
        validate(request)?;
        let input = &request.input;
        self.layout.inbox(&input.sid)?;
        encode_inbox_entry(input)?;
        let spec_doc = request.create_session.as_ref().map(encode_json).transpose()?;
        let task = match &request.create_task {
            Some(task) => Some((self.layout.task(&task.tid)?, encode_json(task)?)),
            None => None,
        };
        Ok(Prepared {
            spec_key: self.layout.spec(&input.sid)?,
            spec_doc,
            task,
            admit_key: self.layout.admit(&input.sid, &input.input_id)?,
        })
    }

    /// Reads and decodes a stored JSON document.
    async fn stored<T: serde::de::DeserializeOwned>(&self, key: &str) -> Result<Option<T>, StoreError> {
        match self.json.get_json(key).await.map_err(|e| from_read(&e))? {
            Some(doc) => crate::codec::decode_json(doc.value).map(Some),
            None => Ok(None),
        }
    }

    /// Steps 1–2 of `admit`: `None` = not admitted yet.
    async fn existing(&self, input: &InputEnvelope) -> Result<Option<AdmitOutcome>, StoreError> {
        let key = self.layout.admit(&input.sid, &input.input_id)?;
        let Some(bytes) = self.kv.get(&key).await.map_err(|e| from_read(&e))? else {
            return Ok(None);
        };
        Ok(Some(compare(input, &AdmitRecord::decode(&bytes)?)))
    }

    /// Creates a once-only document; `Ok(false)` = it already existed.
    async fn create_once(&self, key: &str, doc: serde_json::Value) -> Result<bool, bcs_store_api::StoreError> {
        let outcome = self
            .json
            .mutate_json(
                key,
                WriteCondition::Newer(version::CREATE),
                JsonMutation::Replace(doc),
                Expiry::Persistent,
            )
            .await?;
        Ok(matches!(outcome, WriteOutcome::Applied { .. }))
    }

    /// `true` when the stored spec belongs to the same scope and group.
    async fn same_spec(&self, key: &str, spec: &SessionSpec) -> Result<bool, StoreError> {
        Ok(self
            .stored::<SessionSpec>(key)
            .await?
            .is_some_and(|stored| stored.scope == spec.scope && stored.group == spec.group))
    }

    /// Steps 3–6 shared by `admit` and `append_stream`: inbox, listener, active,
    /// wake. Returns the inbox entry id.
    async fn deliver(&self, input: &InputEnvelope, op: &str) -> Result<Delivered, Failure> {
        let shard = KeyLayout::shard_of(&input.sid).map_err(Failure::Before)?;
        let inbox = self.layout.inbox(&input.sid).map_err(Failure::Before)?;
        let fields = encode_inbox_entry(input).map_err(Failure::Before)?;
        let id = self
            .stream
            .append(&inbox, AppendId::Auto, &fields)
            .await
            .map_err(|e| Failure::Before(from_write(&e, op)))?;

        let listener = match read_listener(self.json.as_ref(), &self.layout, &input.sid).await {
            Ok(state) => state,
            Err(error) => {
                tracing::debug!(code = ?error, "listener read failed; writing wake");
                ListenerState::NotListening
            }
        };
        if listener == ListenerState::Quarantined {
            return Ok(Delivered::Quarantined);
        }
        let entry = ZSetEntry {
            member: input.sid.0.clone(),
            score: self.clock.now().0 as f64,
        };
        self.zset
            .add(&self.layout.active(shard), &[entry], ZAddMode::Lower)
            .await
            .map_err(|_| Failure::After)?;
        if listener == ListenerState::NotListening {
            self.wake(shard, &input.sid).await?;
        }
        Ok(Delivered::Appended(id))
    }

    async fn wake(&self, shard: ShardId, sid: &Sid) -> Result<(), Failure> {
        let key = self.layout.wake(shard);
        self.stream
            .append(&key, AppendId::Auto, &encode_wake_entry(sid))
            .await
            .map_err(|_| Failure::After)?;
        let n = self.wake_appends.fetch_add(1, Ordering::Relaxed);
        if n % self.config.wake_trim_every == 0 {
            // `wake` is only a hint: a failed trim is retried on a later sample.
            let trimmed = self
                .stream
                .trim(&key, TrimPolicy::MaxLen(self.config.wake_max_len))
                .await;
            if let Err(error) = trimmed {
                tracing::debug!(?error, "wake trim failed");
            }
        }
        Ok(())
    }

    fn position(&self, sid: &Sid, id: StreamId) -> InboxPos {
        InboxPos {
            sid: sid.clone(),
            token: ReplayToken {
                backend: self.config.backend.clone(),
                generation: LogGeneration(LOG_GENERATION.into()),
                value: format!("{}-{}", id.major, id.minor),
            },
        }
    }
}

struct Prepared {
    spec_key: String,
    spec_doc: Option<serde_json::Value>,
    task: Option<(String, serde_json::Value)>,
    admit_key: String,
}

enum Delivered {
    Appended(StreamId),
    Quarantined,
}

/// Where a step failed: before anything of this call was written (the error is
/// reported as is) or after (the outcome is unknown).
enum Failure {
    Before(StoreError),
    After,
}

#[async_trait]
impl InboxProducerPort for StoreInboxProducer {
    async fn admit(&self, request: AdmitRequest) -> Result<AdmitOutcome, StoreError> {
        let input = &request.input;
        // Every key and document is built before the first read, so a bad id
        // can never surface after something was written.
        let prepared = self.prepare(&request)?;
        if let Some(outcome) = self.existing(input).await? {
            return Ok(outcome);
        }
        let op = input.input_id.0.as_str();
        let unknown = || StoreError::OutcomeUnknown {
            operation_id: op.to_owned(),
        };
        if let (Some(task), None) = (&request.create_task, &request.create_session) {
            // A new task of an existing session must belong to its frozen scope.
            match self.stored::<SessionSpec>(&prepared.spec_key).await? {
                Some(spec) if spec.scope == task.scope => {}
                Some(_) => return Ok(AdmitOutcome::ContentConflict),
                None => {
                    return Err(StoreError::NotApplied {
                        code: "session_not_found".into(),
                    });
                }
            }
        }
        let mut wrote = false;

        if let (Some(spec), Some(doc)) = (&request.create_session, prepared.spec_doc) {
            match self.create_once(&prepared.spec_key, doc).await {
                Ok(true) => wrote = true,
                Ok(false) => {
                    if !self.same_spec(&prepared.spec_key, spec).await? {
                        return Ok(AdmitOutcome::ContentConflict);
                    }
                }
                Err(e) => return Err(from_write(&e, op)),
            }
        }
        if let (Some(task), Some((key, doc))) = (&request.create_task, prepared.task) {
            // "Already exists" is fine: never replace a task the Worker advanced.
            match self.create_once(&key, doc).await {
                Ok(true) => wrote = true,
                Ok(false) if !wrote => match self.stored::<TaskSnapshot>(&key).await? {
                    Some(stored) if stored.scope != task.scope || stored.sid != task.sid => {
                        return Ok(AdmitOutcome::ContentConflict);
                    }
                    _ => {}
                },
                Ok(false) => {}
                Err(_) if wrote => return Err(unknown()),
                Err(e) => return Err(from_write(&e, op)),
            }
        }
        match self.deliver(input, op).await {
            Ok(Delivered::Appended(_)) => {}
            Ok(Delivered::Quarantined) => return Ok(AdmitOutcome::Quarantined),
            Err(Failure::Before(e)) if !wrote => return Err(e),
            Err(_) => return Err(unknown()),
        }

        let record = AdmitRecord {
            digest: input.business_digest.clone(),
            sid: input.sid.clone(),
            tid: input.tid.clone(),
        };
        let created = self
            .kv
            .put_if_absent(&prepared.admit_key, &record.encode()?, Expiry::Persistent)
            .await
            .map_err(|_| unknown())?;
        if created {
            return Ok(AdmitOutcome::Accepted(receipt(input, &record)));
        }
        // Written meanwhile (a concurrent retry, or the Worker's compensation).
        match self.existing(input).await {
            Ok(Some(outcome)) => Ok(outcome),
            _ => Err(unknown()),
        }
    }

    async fn append_stream(&self, request: AppendAgentEvent) -> Result<InboxPos, StoreError> {
        let input = stream_envelope(&request)?;
        let op = input.input_id.0.clone();
        match self.deliver(&input, &op).await {
            Ok(Delivered::Appended(id)) => Ok(self.position(&input.sid, id)),
            Ok(Delivered::Quarantined) => Err(StoreError::NotApplied {
                code: "session_quarantined".into(),
            }),
            Err(Failure::Before(e)) => Err(e),
            Err(Failure::After) => Err(StoreError::OutcomeUnknown { operation_id: op }),
        }
    }
}

/// Answer from a stored `admit` record: the same digest is the same input
/// admitted earlier (`Duplicate`, which callers treat as success, decision 24);
/// a different digest is a conflict.
fn compare(input: &InputEnvelope, stored: &AdmitRecord) -> AdmitOutcome {
    if stored.digest == input.business_digest {
        AdmitOutcome::Duplicate(receipt(input, stored))
    } else {
        AdmitOutcome::ContentConflict
    }
}

fn receipt(input: &InputEnvelope, stored: &AdmitRecord) -> AdmissionReceipt {
    AdmissionReceipt {
        sid: stored.sid.clone(),
        tid: stored.tid.clone(),
        input_id: input.input_id.clone(),
        business_digest: stored.digest.clone(),
    }
}

/// The ids of a request must agree with each other before anything is read.
fn validate(request: &AdmitRequest) -> Result<(), StoreError> {
    let input = &request.input;
    let bad = |code: &str| StoreError::Invalid { code: code.into() };
    if !input.tid.0.starts_with(&format!("{}.", input.sid.0)) {
        return Err(bad("tid_not_in_session"));
    }
    if let Some(spec) = &request.create_session {
        if spec.sid != input.sid {
            return Err(bad("spec_session_mismatch"));
        }
        if KeyLayout::shard_of(&spec.sid)? != spec.shard {
            return Err(bad("spec_shard_mismatch"));
        }
    }
    if let Some(task) = &request.create_task
        && (task.sid != input.sid || task.tid != input.tid)
    {
        return Err(bad("task_identity_mismatch"));
    }
    if let (Some(spec), Some(task)) = (&request.create_session, &request.create_task)
        && spec.scope != task.scope
    {
        return Err(bad("task_scope_mismatch"));
    }
    Ok(())
}

/// An intermediate agent event as an inbox input. Its identity comes from the
/// scoped `(did, holder_epoch, seq)`, so a retry of the same event repeats it.
fn stream_envelope(request: &AppendAgentEvent) -> Result<InputEnvelope, StoreError> {
    let event = &request.event;
    let identity = digest_fields(&[
        b"agent_stream",
        event.did.0.as_bytes(),
        &event.holder_epoch.0.to_be_bytes(),
        &event.seq.0.to_be_bytes(),
    ]);
    let body = InputBody::Agent {
        event: event.clone(),
    };
    let input = InputEnvelope {
        schema_version: 1,
        sid: request.sid.clone(),
        tid: request.tid.clone(),
        input_id: InputId(format!("as-{}", hex_of(&identity))),
        business_digest: digest_json(&encode_json(&body)?),
        body,
    };
    if !input.tid.0.starts_with(&format!("{}.", input.sid.0)) {
        return Err(StoreError::Invalid {
            code: "tid_not_in_session".into(),
        });
    }
    Ok(input)
}
