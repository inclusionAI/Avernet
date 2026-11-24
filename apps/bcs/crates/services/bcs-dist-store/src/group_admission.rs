//! Strict-group round admission over the merged #4245 joints.
//!
//! The checkpoint `ckpt:<sid>` exact-version CAS (owner fence + expected
//! `commit_seq`) installs generation, Tid, the original request reference,
//! the `Accepted` license and the scheduling duty in ONE commit; only that
//! commit is acceptance. The request intent row, session spec, initial task
//! snapshot and list indexes are repairable pre-writes; the Inbox Start entry
//! and the schedule wake are aftermath. A busy session installs no accepted
//! task and consumes no message key; the loser never runs. Same-message
//! retries recover the original Tid through the row + license, never a new
//! round.
//!
//! Boundaries owned by later tasks, not here: the terminal/cold-proof read
//! path (T4/T7 — a superseded round stays out of `get` until the per-Tid
//! proof transfer lands), the FSM `runner_state` rewrite (T7 processor must
//! carry the license marker), and Gateway source isolation wiring (T8).
use crate::{
    inbox::{Clock, StrictRound, StoreInboxProducer},
    layout::KeyLayout,
};
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{
    common::StoreError,
    group_round::{
        GroupRequestIdentity, GroupRequestRecord, GroupRoundRepo, GroupRoundView,
    },
    inbox::{AdmitRequest, AdmitOutcome},
    ownership::ShardClaim,
    session::{
        AcquiredSession, Checkpoint, CommitOutcome, CommitPurpose, CommitRequest, CommittedBatch,
        SessionStorePort, TaskPhase, TaskProgress, TaskRunnerState,
    },
};
use bcs_service_api::port::repo::distributed::common::ShardOwner;
use bcs_store_api::{StoreCapability, Version, WriteOutcome, document::DocumentStorePlugin};
use serde::{Deserialize, Serialize};
use std::{num::NonZeroU64, sync::Arc};

/// Bound on one encoded registry row; the S1.1 candidate value, not a frozen
/// production capacity (T4/T8 calibrate retention and budgets).
pub const MAX_GROUP_REQUEST_BYTES: usize = 128 * 1024;
const RETRIES: usize = 8;

#[derive(Debug, Clone)]
pub struct GroupAdmissionConfig {
    pub max_request_bytes: usize,
}
impl Default for GroupAdmissionConfig {
    fn default() -> Self {
        Self {
            max_request_bytes: MAX_GROUP_REQUEST_BYTES,
        }
    }
}
impl GroupAdmissionConfig {
    fn validate(&self) -> Result<(), StoreError> {
        if self.max_request_bytes == 0 || self.max_request_bytes > 1024 * 1024 {
            return Err(invalid("group_admission_configuration"));
        }
        Ok(())
    }
}

/// The strict license installed by the one acceptance CAS. It lives in the
/// checkpoint `runner_state` value under the `group_admission` key and is the
/// only public-read/execution permission. The T7 processor that later
/// rewrites `runner_state` must carry it until the terminal proof transfer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub(crate) struct StrictLicense {
    pub schema_version: u32,
    pub scope: TaskScope,
    pub group: GroupId,
    pub sid: Sid,
    pub tid: Tid,
    pub generation: NonZeroU64,
    pub request_id: String,
    pub request_digest: String,
}
impl StrictLicense {
    pub(crate) fn round_key(&self) -> RoundKey {
        RoundKey {
            scope: self.scope.clone(),
            group: self.group.clone(),
            sid: self.sid.clone(),
            tid: self.tid.clone(),
            generation: self.generation,
        }
    }
}

/// `value` is the checkpoint runner state's `value` JSON.
pub(crate) fn decode_strict_license(
    runner_value: &serde_json::Value,
) -> Option<StrictLicense> {
    serde_json::from_value(runner_value.get("group_admission")?.clone()).ok()
}

/// Read the strict license sealed in the session checkpoint document without
/// going through the checkpoint port: probe callers (inbox producer, task
/// reader) hold only the document plugin. One bounded document read.
pub(crate) async fn probe_strict_license(
    documents: &Arc<dyn DocumentStorePlugin>,
    layout: &KeyLayout,
    sid: &Sid,
) -> Result<Option<StrictLicense>, StoreError> {
    let key = layout.ckpt(sid)?;
    let raw = documents
        .read_document(&key)
        .await
        .map_err(|e| crate::error::from_read(&e))?;
    Ok(raw
        .and_then(|v| {
            v.value
                .pointer("/checkpoint/runner_state/state/value")
                .cloned()
        })
        .and_then(|value| decode_strict_license(&value))
        .filter(|license| license.sid == *sid))
}

/// A strict-group start staged by the group repo. `Staged` writes only the
/// repairable pre-writes; `Delivered` means the Start entry is durable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StrictOutcome {
    Staged,
    Delivered(AdmitOutcome),
}

/// True when the session holds a staged (not yet licensed) strict admission
/// slot. One bounded document read; used by the visual cut before a license
/// exists.
pub(crate) async fn probe_strict_staging(
    documents: &Arc<dyn DocumentStorePlugin>,
    layout: &KeyLayout,
    sid: &Sid,
) -> Result<bool, StoreError> {
    let raw = documents
        .read_document(&layout.schedule(sid)?)
        .await
        .map_err(|e| crate::error::from_read(&e))?;
    Ok(raw
        .and_then(|v| {
            v.value["slots"]
                .as_object()
                .map(|slots| slots.values().any(|slot| slot["strict"] == true))
        })
        .unwrap_or(false))
}

/// Stable strict-round inbox identity: the same message key always repairs
/// the same staged record, retries across connections included. Public for
/// adapter tooling and conformance probes that must locate one round's
/// durable admission record without re-deriving the digest algorithm.
pub fn strict_round_input_id(request: &AcceptedRound) -> Result<InputId, StoreError> {
    let key = &request.input.key;
    let digest = crate::identity::digest_fields(&[
        b"group_input:v1",
        key.scope.tenant.0.as_bytes(),
        key.scope.principal.0.as_bytes(),
        key.group.0.as_bytes(),
        request.request_id.0.as_bytes(),
        request.request_digest.0.as_bytes(),
    ]);
    Ok(InputId(format!("ga:{}", crate::identity::hex_of(&digest))))
}

/// Request intent row: the unique durable binding of a message key to its
/// original normalization. `abandoned` rows keep fence evidence and are only
/// re-bound by the same request identity; `expired` tombstones answer Expired.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
struct GroupRequestRow {
    schema_version: u32,
    request_id: String,
    state: String,
    request: AcceptedRound,
    /// Checkpoint head bound at prepare, for diagnostics and fencing review.
    head: u64,
    owner: ShardOwner,
}

/// The real `GroupRoundRepo` provider: checkpoint-CAS acceptance, per-request
/// registry rows, inbox aftermath through the merged producer. The claim is
/// the Worker's sealed shard identity; Gateways never write checkpoints.
#[derive(Clone)]
pub struct StoreGroupRoundAdmission {
    sessions: Arc<dyn SessionStorePort>,
    producer: Arc<StoreInboxProducer>,
    documents: Arc<dyn DocumentStorePlugin>,
    layout: KeyLayout,
    claim: ShardClaim,
    config: GroupAdmissionConfig,
    clock: Arc<dyn Clock>,
}

impl StoreGroupRoundAdmission {
    #[allow(clippy::too_many_arguments, reason = "injected joint storage")]
    pub fn new(
        sessions: Arc<dyn SessionStorePort>,
        producer: Arc<StoreInboxProducer>,
        documents: Arc<dyn DocumentStorePlugin>,
        claim: ShardClaim,
        layout: KeyLayout,
        config: GroupAdmissionConfig,
        clock: Arc<dyn Clock>,
    ) -> Result<Self, StoreError> {
        config.validate()?;
        if !documents.supports(StoreCapability::DocumentCas) {
            return Err(StoreError::NotApplied {
                code: "document_cas_required".into(),
            });
        }
        Ok(Self {
            sessions,
            producer,
            documents,
            layout,
            claim,
            config,
            clock,
        })
    }

    // ---- bounded storage helpers ----

    async fn load<T: serde::de::DeserializeOwned>(
        &self,
        key: &str,
    ) -> Result<Option<(Version, T)>, StoreError> {
        self.documents
            .read_document(key)
            .await
            .map_err(|e| crate::error::from_read(&e))?
            .map(|v| {
                Ok((
                    v.version,
                    serde_json::from_value::<T>(v.value)
                        .map_err(|_| StoreError::UnsupportedFormat { format: 0 })?,
                ))
            })
            .transpose()
    }

    fn row_key(&self, request: &AcceptedRound) -> Result<String, StoreError> {
        self.layout.group_request(
            &request.input.key.scope,
            &request.input.key.group,
            &request.request_id,
        )
    }

    async fn row(
        &self,
        request: &AcceptedRound,
    ) -> Result<Option<(Version, GroupRequestRow)>, StoreError> {
        let key = self.row_key(request)?;
        self.load(&key).await
    }

    async fn save_row(
        &self,
        key: &str,
        expected: Option<Version>,
        row: &GroupRequestRow,
    ) -> Result<bool, StoreError> {
        let doc = serde_json::to_value(row).map_err(|_| invalid("group_request_encoding"))?;
        if serde_json::to_vec(&doc).map_err(|_| invalid("group_request_encoding"))?.len()
            > self.config.max_request_bytes
        {
            return Err(StoreError::NotApplied {
                code: "group_request_budget".into(),
            });
        }
        let outcome = match expected {
            Some(v) => self.documents.replace_document(key, v, &doc).await,
            None => self.documents.create_document(key, &doc).await,
        }
        .map_err(|e| crate::error::from_write(&e, "group_request_row"))?;
        Ok(matches!(outcome, WriteOutcome::Applied { .. }))
    }

    /// Spec binding: scope, group and workflow must name this session. A
    /// missing spec never creates the session; unknown client Sids are not
    /// instantiated by round admission. One bounded read, zero writes.
    async fn check_binding(&self, request: &AcceptedRound) -> Result<(), StoreError> {
        let key = self.layout.spec(&request.input.key.sid)?;
        let Some((_, spec)) = self.load::<SessionSpec>(&key).await? else {
            return Err(StoreError::NotApplied {
                code: "session_not_found".into(),
            });
        };
        if spec.scope != request.input.key.scope {
            return Err(invalid("group_scope_mismatch"));
        }
        if spec.group != request.input.key.group {
            return Err(invalid("group_identity_mismatch"));
        }
        if spec.workflow != request.input.workflow_ref {
            return Err(invalid("group_workflow_mismatch"));
        }
        Ok(())
    }

    /// Sealed session snapshot; the protected first create rides the claim
    /// fence inside the checkpoint port.
    async fn acquire(&self, sid: &Sid) -> Result<AcquiredSession, StoreError> {
        let initial = Checkpoint {
            format_version: 1,
            commit_seq: CommitSeq(0),
            consumed: None,
            tasks: Default::default(),
            dispatch_seq: Default::default(),
            deadlines: vec![],
            deadline_history: Default::default(),
            view_drafts: Default::default(),
            runner_state: None,
            last_event_id: None,
            outbox: None,
        };
        use bcs_service_api::port::repo::distributed::session::AcquireOutcome;
        match self.sessions.acquire(sid, &self.claim, initial).await? {
            AcquireOutcome::Acquired(a) => Ok(a),
            AcquireOutcome::Fenced => Err(StoreError::NotApplied {
                code: "strict_session_fenced".into(),
            }),
            _ => Err(StoreError::NotApplied {
                code: "strict_session_deferred".into(),
            }),
        }
    }

    async fn license(&self, snapshot: &AcquiredSession) -> Option<StrictLicense> {
        snapshot
            .envelope
            .checkpoint
            .runner_state
            .as_ref()
            .and_then(|r| decode_strict_license(&r.state.value))
            .filter(|l| l.sid == snapshot.ownership.sid)
    }

    fn replay(original: &AcceptedRound, request: &AcceptedRound) -> Admission {
        if original.request_digest == request.request_digest
            && original.input.key.sid == request.input.key.sid
        {
            Admission::Replay {
                accepted: original.clone(),
            }
        } else {
            Admission::Conflict
        }
    }

    /// Busy while the licensed round's task is not terminally Done in the
    /// checkpoint queue (the terminal commit itself is the T7 processor).
    fn round_busy(license: &StrictLicense, snapshot: &AcquiredSession) -> bool {
        snapshot
            .envelope
            .checkpoint
            .tasks
            .iter()
            .find(|t| t.tid == license.tid)
            .is_some_and(|t| t.phase != TaskPhase::Done)
    }

    // ---- inbox entry for one round, keyed by the request identity ----

    async fn aftermath(&self, request: &AcceptedRound) -> Result<StrictOutcome, StoreError> {
        let inbox = self.inbox_request(request)?;
        self.producer
            .admit_strict_round(
                inbox,
                StrictRound {
                    request_id: request.request_id.0.clone(),
                    generation: request.input.key.generation.get(),
                },
            )
            .await
    }

    /// Forfeit this round's staged claim after a lost acceptance CAS: the
    /// admission record is durably fenced and its schedule slot freed, so a
    /// duel loser never keeps a resident slot (each duel would otherwise
    /// permanently burn one of the session's bounded slots).
    async fn abandon_start(&self, request: &AcceptedRound) -> Result<(), StoreError> {
        let inbox = self.inbox_request(request)?;
        self.producer
            .abandon_strict_start(
                inbox,
                StrictRound {
                    request_id: request.request_id.0.clone(),
                    generation: request.input.key.generation.get(),
                },
            )
            .await
    }

    fn inbox_request(&self, request: &AcceptedRound) -> Result<AdmitRequest, StoreError> {
        let key = &request.input.key;
        let input_id = strict_round_input_id(request)?;
        let payload = serde_json::to_value(request).map_err(|_| invalid("group_round_encoding"))?;
        let now = self.clock.now();
        Ok(AdmitRequest {
            scope: key.scope.clone(),
            input: InputEnvelope {
                schema_version: 1,
                sid: key.sid.clone(),
                tid: key.tid.clone(),
                input_id,
                business_digest: request.request_digest.clone(),
                body: InputBody::User {
                    payload: Payload::Inline(payload),
                },
            },
            create_session: None,
            create_task: Some(self.initial_task(request, now)?),
        })
    }

    fn initial_task(
        &self,
        request: &AcceptedRound,
        now: TimestampMillis,
    ) -> Result<TaskSnapshot, StoreError> {
        let key = &request.input.key;
        Ok(TaskSnapshot {
            schema_version: 1,
            scope: key.scope.clone(),
            sid: key.sid.clone(),
            tid: key.tid.clone(),
            content: TaskContent {
                status: TaskStatus::Submitted,
                interactions: vec![],
                snapshot: Payload::Inline(
                    serde_json::to_value(DisplaySnapshot {
                        format_version: 1,
                        units: vec![],
                    })
                    .map_err(|_| invalid("empty_snapshot_encoding"))?,
                ),
                result_ref: None,
            },
            output_limits: PHASE_ONE_OUTPUT_LIMITS,
            output_usage: OutputUsage::default(),
            snapshot_event_id: None,
            source_commit: CommitSeq(0),
            created_at: now,
            updated_at: now,
        })
    }

    // ---- the acceptance CAS ----

    async fn license_commit(
        &self,
        snapshot: &AcquiredSession,
        request: &AcceptedRound,
    ) -> Result<CommitOutcome, StoreError> {
        let key = &request.input.key;
        let head = snapshot.envelope.checkpoint.commit_seq.0;
        let sequence = head
            .checked_add(1)
            .ok_or_else(|| invalid("commit_sequence_exhausted"))?;
        let mut candidate = snapshot.envelope.checkpoint.clone();
        candidate.commit_seq = CommitSeq(sequence);
        // Scheduling duty: one queued task entry in the same atomic commit.
        candidate.tasks.push_back(TaskProgress {
            steps: 0,
            tid: key.tid.clone(),
            phase: TaskPhase::Queued,
            input: Payload::Inline(serde_json::json!({
                "request_id": request.request_id.0,
                "generation": key.generation.get(),
            })),
            result_ref: None,
            output_limits: PHASE_ONE_OUTPUT_LIMITS,
            output_usage: OutputUsage::default(),
            publication: Default::default(),
            finalization: None,
        });
        let license = StrictLicense {
            schema_version: 1,
            scope: key.scope.clone(),
            group: key.group.clone(),
            sid: key.sid.clone(),
            tid: key.tid.clone(),
            generation: key.generation,
            request_id: request.request_id.0.clone(),
            request_digest: request.request_digest.0.clone(),
        };
        candidate.runner_state = Some(TaskRunnerState {
            tid: key.tid.clone(),
            state: RunnerState {
                format_version: 1,
                value: serde_json::json!({ "group_admission": license }),
            },
        });
        let commit_id = CommitId(format!(
            "strict-admit:{}:{}:{}",
            request.request_id.0,
            key.generation.get(),
            head
        ));
        let content_digest = Digest(
            serde_json::to_string(&candidate.runner_state)
                .map_err(|_| invalid("license_encoding"))?,
        );
        candidate.outbox = Some(CommittedBatch {
            created_at: None,
            commit_id: commit_id.clone(),
            source_commit: CommitSeq(sequence),
            content_digest: content_digest.clone(),
            effects: vec![],
        });
        Ok(self
            .sessions
            .commit(CommitRequest {
                purpose: CommitPurpose::Ordinary { control_revision: 0 },
                owner: snapshot.ownership.clone(),
                expected_commit_seq: CommitSeq(head),
                commit_id,
                content_digest,
                candidate,
            })
            .await?)
    }

    /// Stage pre-writes (registry row + inbox/snapshot/index scaffolding),
    /// then take the acceptance CAS, then deliver the aftermath. Called only
    /// after classify left an empty/terminal authority at `snapshot`.
    async fn install(
        &self,
        request: &AcceptedRound,
        snapshot: AcquiredSession,
        row: Option<(Version, GroupRequestRow)>,
    ) -> Result<Admission, StoreError> {
        let row_key = self.row_key(request)?;
        let head = snapshot.envelope.checkpoint.commit_seq.0;
        let owner = snapshot.ownership.owner.clone();
        if !request.input.key.tid.0.starts_with(&format!("{}.", request.input.key.sid.0)) {
            // Installable rounds only install Tids inside their session;
            // classification above has already answered busy or replay for
            // malformed candidates without touching durable state.
            return Err(invalid("tid_not_in_session"));
        }
        if let Some((_, row)) = &row {
            if row.state == "expired" {
                return Ok(Admission::Expired);
            }
            if row.request.request_id == request.request_id
                && row.request.request_digest != request.request_digest
            {
                // Same message key, changed normalization: the first kept it.
                return Ok(Admission::Conflict);
            }
        }
        if !self.bind_row(request, &row_key, head, &owner).await? {
            return Ok(Admission::Conflict);
        }
        // A rejected staged entry cannot grant an execution license.
        match self.aftermath(request).await? {
            StrictOutcome::Staged
            | StrictOutcome::Delivered(AdmitOutcome::Accepted(_) | AdmitOutcome::Duplicate(_)) => {}
            StrictOutcome::Delivered(AdmitOutcome::ContentConflict) => return Ok(Admission::Conflict),
            StrictOutcome::Delivered(AdmitOutcome::Quarantined) => {
                return Err(invalid("strict_start_rejected"));
            }
        }
        match self.license_commit(&snapshot, request).await? {
            CommitOutcome::Committed(_) | CommitOutcome::AlreadyCommitted(_) => {}
            CommitOutcome::Fenced => {
                return Err(StoreError::NotApplied {
                    code: "strict_session_fenced".into(),
                });
            }
            // CommitSeqConflict / ContentConflict: read authority again. A
            // re-read can still observe OutcomeUnknown if the concurrent
            // write itself was uncertain; that stays an error, recoverable by
            // the same message key.
            _ => {
                let now = self.acquire(&request.input.key.sid).await?;
                let license = self.license(&now).await;
                let Some(license) = license else {
                    return Err(StoreError::NotApplied {
                        code: "strict_admission_unresolved".into(),
                    });
                };
                if license.request_id == request.request_id.0 {
                    if license.request_digest != request.request_digest.0 {
                        return Ok(Admission::Conflict);
                    }
                    self.aftermath(request).await?;
                    return Ok(Admission::Replay {
                        accepted: request.clone(),
                    });
                }
                self.abandon(&row_key).await?;
                self.abandon_start(request).await?;
                if license.generation.get() >= request.input.key.generation.get() {
                    return Ok(Admission::Busy);
                }
                return Err(invalid("stale_generation"));
            }
        }
        self.mark_accepted(request, &row_key).await?;
        match self.aftermath(request).await? {
            StrictOutcome::Delivered(AdmitOutcome::Accepted(_) | AdmitOutcome::Duplicate(_)) => Ok(Admission::New {
                accepted: request.clone(),
            }),
            StrictOutcome::Delivered(_) => Err(invalid("strict_start_rejected")),
            StrictOutcome::Staged => Err(StoreError::NotApplied {
                code: "strict_round_superseded".into(),
            }),
        }
    }

    /// Once the license committed, the row records that the message key's
    /// round installed. Classification failures of this bookkeeping fall back
    /// to the same-key license recovery path.
    async fn mark_accepted(&self, request: &AcceptedRound, key: &str) -> Result<(), StoreError> {
        for _ in 0..RETRIES {
            let Some((version, mut row)) = self.load::<GroupRequestRow>(key).await? else {
                return Ok(());
            };
            if row.state != "prepared" {
                return Ok(());
            }
            if row.request.request_id != request.request_id {
                return Ok(());
            }
            row.state = "accepted".into();
            if self.save_row(key, Some(version), &row).await? {
                return Ok(());
            }
        }
        Err(StoreError::OutcomeUnknown {
            operation_id: key.into(),
        })
    }

    async fn bind_row(
        &self,
        request: &AcceptedRound,
        key: &str,
        head: u64,
        owner: &ShardOwner,
    ) -> Result<bool, StoreError> {
        let fresh = GroupRequestRow {
            schema_version: 1,
            request_id: request.request_id.0.clone(),
            state: "prepared".into(),
            request: request.clone(),
            head,
            owner: owner.clone(),
        };
        for _ in 0..RETRIES {
            match self.load::<GroupRequestRow>(key).await? {
                None => {
                    if self.save_row(key, None, &fresh).await? {
                        return Ok(true);
                    }
                }
                Some((version, row)) => match row.state.as_str() {
                    "expired" => return Ok(false),
                    "abandoned" => {
                        if self.save_row(key, Some(version), &fresh).await? {
                            return Ok(true);
                        }
                    }
                    "prepared" | "accepted" => {
                        // A scope-wide message reservation is not transferable
                        // to another session, task, generation or normalization.
                        if row.request != *request {
                            return Ok(false);
                        }
                        let mut rebound = row;
                        rebound.head = head;
                        rebound.owner = owner.clone();
                        if self.save_row(key, Some(version), &rebound).await? {
                            return Ok(true);
                        }
                    }
                    _ => return Err(invalid("group_request_state")),
                },
            }
        }
        Err(StoreError::OutcomeUnknown {
            operation_id: key.into(),
        })
    }

    async fn abandon(&self, key: &str) -> Result<(), StoreError> {
        for _ in 0..RETRIES {
            let Some((version, mut row)) = self.load::<GroupRequestRow>(key).await? else {
                return Ok(());
            };
            if row.state != "prepared" {
                return Ok(());
            }
            row.state = "abandoned".into();
            if self.save_row(key, Some(version), &row).await? {
                return Ok(());
            }
        }
        Err(StoreError::OutcomeUnknown {
            operation_id: key.into(),
        })
    }
}

#[async_trait]
impl GroupRoundRepo for StoreGroupRoundAdmission {
    async fn lookup(
        &self,
        identity: &GroupRequestIdentity<'_>,
    ) -> Result<GroupRequestRecord, StoreError> {
        let parts = identity.components();
        if parts.len() != 5 || parts[0] != "group_a2a:v1" {
            return Err(invalid("group_request_identity"));
        }
        let scope = TaskScope {
            tenant: TenantId(parts[1].into()),
            principal: PrincipalId(parts[2].into()),
        };
        let group = GroupId(parts[3].into());
        let request_id = RequestId(parts[4].into());
        let key = self.layout.group_request(&scope, &group, &request_id)?;
        let Some((_, row)) = self.load::<GroupRequestRow>(&key).await? else {
            return Ok(GroupRequestRecord::Absent);
        };
        // Abandonment is checkout by the fence, not a fresh message key; the
        // row stays as evidence. Expired tombstones answer before everything.
        if row.state == "abandoned" {
            return Ok(GroupRequestRecord::Absent);
        }
        if row.state == "expired" {
            return Ok(GroupRequestRecord::Expired);
        }
        if row.state != "prepared" && row.state != "accepted" {
            return Err(invalid("group_request_state"));
        }
        if row.state == "accepted" {
            // The round installed under this message key once; supersession
            // does not rewrite first identity.
            return Ok(GroupRequestRecord::Accepted(row.request.clone()));
        }
        let license =
            probe_strict_license(&self.documents, &self.layout, &row.request.input.key.sid)
                .await?;
        if license.is_some_and(|l| {
            l.request_id == row.request.request_id.0
                && l.request_digest == row.request.request_digest.0
        }) {
            return Ok(GroupRequestRecord::Accepted(row.request.clone()));
        }
        Ok(GroupRequestRecord::Prepared(row.request.clone()))
    }

    async fn admit(&self, request: &AcceptedRound) -> Result<Admission, StoreError> {
        let key = &request.input.key;
        if request.input.schema_version != RoundSchemaVersion::V1 {
            return Err(StoreError::UnsupportedFormat { format: 1 });
        }
        if !matches!(request.input.source, GroupRoundSource::GroupA2a) {
            return Err(invalid("group_round_source"));
        }
        if key.generation.get() == 0 {
            return Err(invalid("group_round_generation"));
        }
        KeyLayout::shard_of(&key.sid)?;
        self.layout.ckpt(&key.sid)?;
        self.layout.group_request(&key.scope, &key.group, &request.request_id)?;
        if request.request_id.0.is_empty() {
            return Err(invalid("group_request_identity"));
        }
        self.check_binding(request).await?;
        // Zero-write classification against the sealed authority.
        let snapshot = self.acquire(&key.sid).await?;
        let license = self.license(&snapshot).await;
        let row = self.row(request).await?;
        let row_lane = match &row {
            Some((_, row)) => Some((row.state.as_str(), row.request.clone())),
            None => None,
        };
        if matches!(row_lane, Some(("expired", _))) {
            return Ok(Admission::Expired);
        }
        match &license {
            Some(license) if license.request_id == request.request_id.0 => {
                let original = match row_lane {
                    Some(("prepared", original)) | Some(("accepted", original)) => original,
                    // The row is destructible only via the T4 lifecycle; the
                    // license still pins the first identity.
                    None if license.request_digest == request.request_digest.0 => {
                        request.clone()
                    }
                    None => return Ok(Admission::Conflict),
                    Some(_) => return Ok(Admission::Conflict),
                };
                let admitted = Self::replay(&original, request);
                if matches!(admitted, Admission::Replay { .. }) {
                    // Recovery/aftermath repair for the same accepted round;
                    // the Start entry stays exactly one durable record.
                    self.aftermath(request).await?;
                }
                return Ok(admitted);
            }
            Some(license) => {
                // A stored, installed round with this message id answers by
                // its own first identity even after a superseding round took
                // the session: the retry keeps reusing the original Tid. An
                // uninstalled (pending) row never rides this path — it may
                // still be busy or re-attempted once the session frees.
                if let Some(("accepted", original)) = &row_lane
                    && original.request_id == request.request_id
                    && original.input.key.tid == request.input.key.tid
                {
                    return if original.request_digest != request.request_digest {
                        Ok(Admission::Conflict)
                    } else {
                        Ok(Self::replay(original, request))
                    };
                }
                if Self::round_busy(license, &snapshot) {
                    return Ok(Admission::Busy);
                }
                // Every new-generation install pins the license chain: a
                // stale (old) generation of any message id — including a
                // re-sent forfeited round — never installs and never
                // resurrects; only the exact expected next generation does.
                if request.input.key.generation.get() != license.generation.get().saturating_add(1)
                {
                    return Err(invalid("stale_generation"));
                }
                // Install re-checks the row identity: a stored row with this
                // message id and changed normalization answers Conflict.
                return self.install(request, snapshot, row).await;
            }
            None => {
                // No license yet: legacy or queued leftovers keep it busy.
                if snapshot
                    .envelope
                    .checkpoint
                    .tasks
                    .iter()
                    .any(|t| t.phase != TaskPhase::Done)
                {
                    return Ok(Admission::Busy);
                }
                if let Some((_, original)) = &row_lane {
                    if original.request_id == request.request_id
                        && original.request_digest != request.request_digest
                    {
                        return Ok(Admission::Conflict);
                    }
                }
                if request.input.key.generation.get() != 1 {
                    return Err(invalid("stale_generation"));
                }
                return self.install(request, snapshot, row).await;
            }
        }
    }

    async fn get(&self, key: &RoundKey) -> Result<Option<GroupRoundView>, StoreError> {
        let license = probe_strict_license(&self.documents, &self.layout, &key.sid).await?;
        let Some(license) = license.filter(|l| l.round_key() == *key) else {
            return Ok(None);
        };
        let row_key = self.layout.group_request(
            &key.scope,
            &key.group,
            &RequestId(license.request_id.clone()),
        )?;
        let Some((_, row)) = self.load::<GroupRequestRow>(&row_key).await? else {
            return Err(StoreError::NotApplied {
                code: "group_request_row_missing".into(),
            });
        };
        if row.request.input.key != *key {
            return Ok(None);
        }
        Ok(Some(GroupRoundView {
            accepted: row.request.clone(),
            terminal: None,
            result: None,
        }))
    }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
