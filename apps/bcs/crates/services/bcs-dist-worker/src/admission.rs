//! Initialize task state once, retaining the canonical request selected by Inbox.
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    application::dist_gateway::CreationSource,
    core::dist_admission::*,
    port::repo::distributed::{
        common::StoreError,
        group_round::{
            GroupRequestIdentity, GroupRequestRecord, GroupRoundRepo, GroupStartRequest,
            decode_strict_license,
        },
        inbox::*,
        session::SessionStorePort,
    },
};
use std::{num::NonZeroU64, sync::Arc};

/// The stable key of a fresh strict round's pinned (empty) history
/// boundary: a new session's admission is the boundary of no committed
/// history yet, digest-pinned so the T3 freeze seam (`freeze_node_input`)
/// can later attach the real history store behind the same reference.
const HISTORY_CUT_KEY: &str = "group-round-history:v1";

pub struct WorkerTaskAdmission {
    inbox: Arc<dyn InboxProducerPort>,
    /// Strict-group rounds admit through the checkpoint-licensed repo; the
    /// generic inbox entry stays the old-RPC-compatible path and never
    /// installs rounds for a strict-claimed Session.
    strict: Option<StrictAdmission>,
    now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
}

struct StrictAdmission {
    rounds: Arc<dyn GroupRoundRepo>,
    sessions: Arc<dyn SessionStorePort>,
}

impl WorkerTaskAdmission {
    pub fn new(
        inbox: Arc<dyn InboxProducerPort>,
        now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
    ) -> Self {
        Self {
            inbox,
            strict: None,
            now,
        }
    }

    /// Canonical Worker-side strict-round admission entry: the only writer of
    /// group round licenses (Gateways submit trusted scope and stable intent
    /// here, they never write checkpoints).
    pub fn with_rounds(
        inbox: Arc<dyn InboxProducerPort>,
        rounds: Arc<dyn GroupRoundRepo>,
        sessions: Arc<dyn SessionStorePort>,
        now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
    ) -> Self {
        Self {
            inbox,
            strict: Some(StrictAdmission { rounds, sessions }),
            now,
        }
    }

    /// One recoverable round per context. Authorization belongs to the
    /// calling use case; the check here only pins the trusted caller scope.
    pub async fn admit_group_round(
        &self,
        scope: &TaskScope,
        request: &GroupStartRequest,
    ) -> Result<Admission, StoreError> {
        if request.input.key.scope != *scope {
            return Err(StoreError::Invalid {
                code: "group_scope_untrusted".into(),
            });
        }
        self.strict_admission()?.rounds.admit(request).await
    }

    fn strict_admission(&self) -> Result<&StrictAdmission, StoreError> {
        self.strict.as_ref().ok_or_else(|| invalid("strict_rounds_unconfigured"))
    }

    /// Freeze a new strict round from the immutable SessionSpec, not from
    /// the current directory revision of a reused context.
    fn group_round_of(
        &self,
        scope: &TaskScope,
        command: &TaskAdmissionCommand,
        spec: &SessionSpec,
        generation: NonZeroU64,
    ) -> Result<GroupStartRequest, StoreError> {
        let InputBody::User { payload } = &command.input.body else {
            return Err(invalid("group_round_body"));
        };
        let Payload::Inline(value) = payload else {
            return Err(invalid("group_round_payload_inline"));
        };
        let parts = value.get("parts").and_then(serde_json::Value::as_array)
            .filter(|parts| !parts.is_empty()).ok_or_else(|| invalid("group_round_parts"))?;
        let ordered_parts = parts.iter().map(|part| {
            let fields = part.as_object().filter(|fields| fields.len() == 1)
                .ok_or_else(|| invalid("group_round_part_unsupported"))?;
            if let Some(text) = fields.get("text").and_then(serde_json::Value::as_str) {
                Ok(RoundPart::Text { text: text.to_owned() })
            } else if let Some(data) = fields.get("data") {
                Ok(RoundPart::Data { data: data.clone() })
            } else {
                Err(invalid("group_round_part_unsupported"))
            }
        }).collect::<Result<Vec<_>, _>>()?;
        Ok(GroupStartRequest {
            input: RoundInput {
                schema_version: RoundSchemaVersion::V1,
                source: GroupRoundSource::GroupA2a,
                key: RoundKey {
                    scope: scope.clone(),
                    group: spec.group.clone(),
                    sid: command.input.sid.clone(),
                    tid: command.input.tid.clone(),
                    generation,
                },
                ordered_parts,
                workflow_ref: spec.workflow.clone(),
                history_cut: BlobRef {
                    key: HISTORY_CUT_KEY.into(),
                    digest: bcs_dist_encoding::digest_json(&serde_json::json!({
                        "entries": [],
                    })),
                },
                execution_deadline: TimestampMillis((self.now)().0.saturating_add(
                    i64::try_from(spec.limits.task_timeout_millis)
                        .unwrap_or(i64::MAX / 2),
                )),
            },
            request_id: RequestId(command.input.input_id.0.clone()),
            request_digest: command.input.business_digest.clone(),
            normalization_ref: BlobRef {
                key: format!("group-round-norm:v1:{}", command.input.input_id.0),
                digest: bcs_dist_encoding::digest_json(&serde_json::json!({
                    "schema_version": 1,
                    "defaults": {"output_mode": "text"},
                })),
            },
            return_configuration: serde_json::json!({"mode": "text"}),
        })
    }

    /// First-session recovery before the round registry row exists. Only the
    /// generation-one bootstrap can have a staged inbox record without a
    /// registry row; successors reserve through the round repo first.
    async fn original_group_round(
        &self,
        scope: &TaskScope,
        spec: &SessionSpec,
        command: &TaskAdmissionCommand,
    ) -> Result<Option<GroupStartRequest>, StoreError> {
        let input_id = bcs_dist_store::group_admission::strict_round_input_id_parts(
            scope,
            &spec.group,
            &RequestId(command.input.input_id.0.clone()),
            &command.input.business_digest,
        )?;
        let Some(original) = self.inbox.lookup(scope, &input_id).await? else {
            return Ok(None);
        };
        let InputBody::User { payload: Payload::Inline(value) } = original.input.body else {
            return Err(invalid("group_round_encoding"));
        };
        let request = serde_json::from_value(value).map_err(|_| invalid("group_round_encoding"))?;
        Ok(Some(request))
    }

    /// First-session bootstrap only. The repo stages successor starts itself,
    /// after classifying Busy, stale generations and retained request keys.
    async fn stage_first_round(
        &self,
        mut initial: AdmitRequest,
        request: &GroupStartRequest,
    ) -> Result<StrictRoundStage, StoreError> {
        initial.input.input_id = bcs_dist_store::group_admission::strict_round_input_id(request)?;
        initial.input.body = InputBody::User {
            payload: Payload::Inline(serde_json::to_value(request)
                .map_err(|_| invalid("group_round_encoding"))?),
        };
        self.inbox.stage_strict_round(
            initial,
            StrictRoundIdentity {
                request_id: request.request_id.0.clone(),
                generation: request.input.key.generation.get(),
            },
        ).await
    }

    async fn admit_strict_start(
        &self,
        scope: &TaskScope,
        command: &TaskAdmissionCommand,
    ) -> Result<AdmitOutcome, StoreError> {
        let strict = self.strict_admission()?;
        let sid = &command.input.sid;
        let existing = strict.sessions.get_spec(sid).await?;
        let initial = if existing.is_none() {
            Some(self.initialize(scope, command)?)
        } else {
            None
        };
        let spec = existing.as_ref()
            .or_else(|| initial.as_ref().and_then(|request| request.create_session.as_ref()))
            .ok_or_else(|| invalid("group_round_definition_missing"))?;
        if spec.scope != *scope || spec.sid != *sid || spec.kind != SessionKind::FsmGroup {
            return Err(invalid("group_round_session_binding"));
        }
        if command.create_session.as_ref().is_some_and(|proposed| proposed.group != spec.group) {
            return Ok(AdmitOutcome::ContentConflict);
        }
        let request_id = RequestId(command.input.input_id.0.clone());
        let identity = GroupRequestIdentity::strict(scope, &spec.group, &request_id);
        let request = match strict.rounds.lookup(&identity).await? {
            GroupRequestRecord::Accepted(original) | GroupRequestRecord::Prepared(original) => {
                if !same_round_intent(&original, spec, command) {
                    return Ok(AdmitOutcome::ContentConflict);
                }
                original
            }
            GroupRequestRecord::Expired => return Err(round_expired()),
            GroupRequestRecord::Absent => {
                let current = strict.sessions.load(sid).await?;
                let license = current.as_ref()
                    .and_then(|state| state.checkpoint.runner_state.as_ref())
                    .and_then(|state| decode_strict_license(&state.state.value));
                if license.as_ref().is_some_and(|license| {
                    license.scope != *scope || license.sid != *sid || license.group != spec.group
                }) {
                    return Err(invalid("group_round_session_binding"));
                }
                let mut request = if let Some(license) = license {
                    let generation = license.generation.checked_add(1)
                        .ok_or_else(|| invalid("group_round_generation"))?;
                    self.group_round_of(scope, command, spec, generation)?
                } else {
                    match self.original_group_round(scope, spec, command).await? {
                        Some(original) if same_round_intent(&original, spec, command) => original,
                        Some(_) => return Ok(AdmitOutcome::ContentConflict),
                        None => self.group_round_of(scope, command, spec, NonZeroU64::MIN)?,
                    }
                };
                if let Some(initial) = &initial
                    && matches!(self.stage_first_round(initial.clone(), &request).await?,
                        StrictRoundStage::Delivered(AdmitOutcome::ContentConflict))
                {
                    // A concurrent identical bootstrap may have selected
                    // its own clock-derived fields first.
                    match self.original_group_round(scope, spec, command).await? {
                        Some(original) if same_round_intent(&original, spec, command) => request = original,
                        _ => return Ok(AdmitOutcome::ContentConflict),
                    }
                }
                request
            }
        };
        let mut outcome = self.admit_group_round(scope, &request).await?;
        if outcome == Admission::Conflict {
            // Lookup is not a reservation. A concurrent same-key successor
            // may have frozen a different deadline before our bind CAS.
            if let GroupRequestRecord::Accepted(original) | GroupRequestRecord::Prepared(original) =
                strict.rounds.lookup(&identity).await?
                && original != request
                && same_round_intent(&original, spec, command)
            {
                outcome = self.admit_group_round(scope, &original).await?;
            }
        }
        match outcome {
            Admission::New { accepted } | Admission::Replay { accepted } => {
                Ok(AdmitOutcome::Accepted(AdmissionReceipt {
                    sid: accepted.input.key.sid,
                    tid: accepted.input.key.tid,
                    input_id: command.input.input_id.clone(),
                    business_digest: accepted.request_digest,
                }))
            }
            Admission::Busy => Err(StoreError::NotApplied {
                code: "group_round_busy".into(),
            }),
            Admission::Conflict => Ok(AdmitOutcome::ContentConflict),
            Admission::Expired => Err(round_expired()),
        }
    }

    fn initialize(
        &self,
        scope: &TaskScope,
        command: &TaskAdmissionCommand,
    ) -> Result<AdmitRequest, StoreError> {
        let now = (self.now)();
        let input = command.input.clone();
        let create_session = command
            .create_session
            .as_ref()
            .map(|definition| {
                let head = input
                    .sid
                    .0
                    .split_once('.')
                    .map(|(head, _)| head)
                    .unwrap_or("");
                let shard = head
                    .strip_prefix('s')
                    .and_then(|s| s.parse().ok())
                    .ok_or_else(|| invalid("session_id_format"))?;
                let digest = bcs_dist_encoding::digest_json(&serde_json::json!({
                    "group": definition.group, "workflow": definition.workflow,
                    "participants": definition.participants, "limits": definition.limits,
                }));
                Ok(SessionSpec {
                    schema_version: 1,
                    sid: input.sid.clone(),
                    shard: ShardId(shard),
                    scope: scope.clone(),
                    kind: SessionKind::FsmGroup,
                    group: definition.group.clone(),
                    workflow: definition.workflow.clone(),
                    participants: definition.participants.clone(),
                    limits: definition.limits.clone(),
                    digest,
                    created_at: now,
                })
            })
            .transpose()?;
        let create_task = command.start_task.then(|| TaskSnapshot {
            schema_version: 1,
            scope: scope.clone(),
            sid: input.sid.clone(),
            tid: input.tid.clone(),
            content: TaskContent {
                status: TaskStatus::Submitted,
                interactions: vec![],
                snapshot: Payload::Inline(
                    serde_json::json!({"format_version":DISPLAY_FORMAT_VERSION,"units":[]}),
                ),
                result_ref: None,
            },
            output_limits: PHASE_ONE_OUTPUT_LIMITS,
            output_usage: OutputUsage::default(),
            snapshot_event_id: None,
            source_commit: CommitSeq(0),
            created_at: now,
            updated_at: now,
        });
        Ok(AdmitRequest {
            scope: scope.clone(),
            input,
            create_session,
            create_task,
        })
    }

    async fn recover(
        &self,
        scope: &TaskScope,
        command: &TaskAdmissionCommand,
        original: AdmitRequest,
    ) -> Result<AdmitOutcome, StoreError> {
        // Caller intent and initialized request integrity are distinct checks.
        // Never compare a newly generated timestamp/config with the original.
        let same = original.scope == *scope
            && original.input.schema_version == command.input.schema_version
            && original.input.input_id == command.input.input_id
            && original.input.sid == command.input.sid
            && original.input.tid == command.input.tid
            && original.input.body == command.input.body
            && original.create_task.is_some() == command.start_task
            && command.create_session.as_ref().is_none_or(|proposed| {
                original
                    .create_session
                    .as_ref()
                    .is_some_and(|frozen| frozen.group == proposed.group)
            });
        if !same {
            return Ok(AdmitOutcome::ContentConflict);
        }
        self.inbox.admit(original).await
    }
}

fn same_round_intent(
    original: &GroupStartRequest,
    spec: &SessionSpec,
    command: &TaskAdmissionCommand,
) -> bool {
    let key = &original.input.key;
    key.scope == spec.scope
        && key.group == spec.group
        && key.sid == command.input.sid
        && key.tid == command.input.tid
        && original.request_id.0 == command.input.input_id.0
        && original.request_digest == command.input.business_digest
}

fn round_expired() -> StoreError {
    StoreError::NotApplied { code: "group_round_expired".into() }
}

fn invalid(code: &str) -> StoreError {
    StoreError::Invalid { code: code.into() }
}
#[async_trait]
impl TaskAdmissionCoreService for WorkerTaskAdmission {
    async fn admit(
        &self,
        scope: &TaskScope,
        command: TaskAdmissionCommand,
    ) -> Result<AdmitOutcome, StoreError> {
        if command.start_task != matches!(command.input.body, InputBody::User { .. })
            || (!command.start_task && command.create_session.is_some())
        {
            return Err(invalid("invalid_task_creation"));
        }
        // Route by the durable entrance, not by whether this start also
        // creates a session. Neither initial nor successor Group REST rounds
        // may use the generic JSON-RPC inbox path.
        if command.start_task
            && CreationSource::of_sid(&command.input.sid) == Some(CreationSource::GroupA2a)
        {
            return self.admit_strict_start(scope, &command).await;
        }
        // The Gateway has checked the request's entrance and grants. Only
        // a GroupA2a Cancel can use the licensed control entry; legacy and
        // other controls retain the generic Inbox path and its source fence.
        if self.strict.is_some()
            && matches!(command.input.body, InputBody::Cancel { .. })
            && CreationSource::of_sid(&command.input.sid) == Some(CreationSource::GroupA2a)
        {
            return self.inbox.admit_strict_cancel(self.initialize(scope, &command)?).await;
        }
        if let Some(original) = self.inbox.lookup(scope, &command.input.input_id).await? {
            return self.recover(scope, &command, original).await;
        }
        let outcome = self.inbox.admit(self.initialize(scope, &command)?).await?;
        if outcome == AdmitOutcome::ContentConflict {
            // A missing lookup is observational, not a reservation. Another
            // concurrent producer may have selected its initialization first.
            if let Some(original) = self.inbox.lookup(scope, &command.input.input_id).await? {
                return self.recover(scope, &command, original).await;
            }
        }
        Ok(outcome)
    }
}
