//! Initialize task state once, retaining the canonical request selected by Inbox.
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    application::dist_gateway::CreationSource,
    core::dist_admission::*,
    port::repo::distributed::{
        common::StoreError,
        group_round::{GroupRoundRepo, GroupStartRequest},
        inbox::*,
    },
};
use std::sync::Arc;

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
    rounds: Option<Arc<dyn GroupRoundRepo>>,
    now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
}
impl WorkerTaskAdmission {
    pub fn new(
        inbox: Arc<dyn InboxProducerPort>,
        now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
    ) -> Self {
        Self {
            inbox,
            rounds: None,
            now,
        }
    }

    /// Canonical Worker-side strict-round admission entry: the only writer of
    /// group round licenses (Gateways submit trusted scope and stable intent
    /// here, they never write checkpoints).
    pub fn with_rounds(
        inbox: Arc<dyn InboxProducerPort>,
        rounds: Arc<dyn GroupRoundRepo>,
        now: Arc<dyn Fn() -> TimestampMillis + Send + Sync>,
    ) -> Self {
        Self {
            inbox,
            rounds: Some(rounds),
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
        let rounds = self.rounds.as_ref().ok_or_else(|| StoreError::Invalid {
            code: "strict_rounds_unconfigured".into(),
        })?;
        rounds.admit(request).await
    }

    /// The strict round a GroupA2a-source start command carries (plan T2
    /// bridge): the Gateway resolved and froze the published group's
    /// definition (W resolve) and submitted the trusted scope plus its
    /// stable intent; this side turns it into the round the licensed
    /// admission installs. Only the worker builds rounds and licenses —
    /// the Gateway never touches the store, so no second execution
    /// authority exists.
    fn group_round_of(
        &self,
        now: TimestampMillis,
        scope: &TaskScope,
        command: &TaskAdmissionCommand,
    ) -> Result<(GroupStartRequest, InputId), StoreError> {
        let definition = command
            .create_session
            .as_ref()
            .ok_or_else(|| invalid("group_round_definition_missing"))?;
        let InputBody::User { payload } = &command.input.body else {
            return Err(invalid("group_round_body"));
        };
        // The public surface's wire shape is the frozen plain message
        // itself: it rides as the round's single ordered part.
        let data = match payload {
            Payload::Inline(value) => value.clone(),
            Payload::Ref(_) => return Err(invalid("group_round_payload_inline")),
        };
        // A fresh session starts at generation one; the store's generation
        // fencing (`stale_generation`) holds every other claim.
        let request_id = RequestId(command.input.input_id.0.clone());
        let request = GroupStartRequest {
            input: RoundInput {
                schema_version: RoundSchemaVersion::V1,
                source: GroupRoundSource::GroupA2a,
                key: RoundKey {
                    scope: scope.clone(),
                    group: definition.group.clone(),
                    sid: command.input.sid.clone(),
                    tid: command.input.tid.clone(),
                    generation: std::num::NonZeroU64::new(1)
                        .ok_or_else(|| invalid("group_round_generation"))?,
                },
                ordered_parts: vec![RoundPart::Data { data }],
                workflow_ref: definition.workflow.clone(),
                history_cut: BlobRef {
                    key: HISTORY_CUT_KEY.into(),
                    digest: bcs_dist_encoding::digest_json(&serde_json::json!({
                        "entries": [],
                    })),
                },
                execution_deadline: TimestampMillis((now.0).saturating_add(
                    i64::try_from(definition.limits.task_timeout_millis)
                        .unwrap_or(i64::MAX / 2),
                )),
            },
            request_id: request_id.clone(),
            request_digest: command.input.business_digest.clone(),
            normalization_ref: BlobRef {
                key: format!("group-round-norm:v1:{}", command.input.input_id.0),
                digest: bcs_dist_encoding::digest_json(&serde_json::json!({
                    "schema_version": 1,
                    "defaults": {"output_mode": "text"},
                })),
            },
            return_configuration: serde_json::json!({"mode": "text"}),
        };
        let input_id =
            bcs_dist_store::group_admission::strict_round_input_id(&request)?;
        Ok((request, input_id))
    }

    /// The strict round a message key's first construction froze in its
    /// durable record. Plan T2: a retry replays the original normalization —
    /// never a newly generated timestamp — so the drifting fields of a fresh
    /// construction (the execution window) can never read as changed content.
    async fn original_group_round(
        &self,
        scope: &TaskScope,
        definition: &bcs_service_api::port::dist_agent::FrozenGroupDefinition,
        command: &TaskAdmissionCommand,
    ) -> Result<Option<(GroupStartRequest, InputId)>, StoreError> {
        let input_id = bcs_dist_store::group_admission::strict_round_input_id_parts(
            scope,
            &definition.group,
            &RequestId(command.input.input_id.0.clone()),
            &command.input.business_digest,
        )?;
        let Some(original) = self.inbox.lookup(scope, &input_id).await? else {
            return Ok(None);
        };
        let InputBody::User { payload } = &original.input.body else {
            return Ok(None);
        };
        let Payload::Inline(value) = payload else {
            return Ok(None);
        };
        let request: GroupStartRequest = serde_json::from_value(value.clone())
            .map_err(|_| invalid("group_round_encoding"))?;
        if request.input.key.scope != *scope
            || request.input.key.sid != command.input.sid
            || request.input.key.tid != command.input.tid
        {
            return Err(invalid("group_round_identity"));
        }
        Ok(Some((request, input_id)))
    }

    /// Bridge the strict round start of a fresh GroupA2a session: stage the
    /// round's durable inbox record (its session pre-write included), then
    /// install the licensed round through the canonical strict seam. The
    /// staging is a idempotent durable repair — the licensed install drives
    /// or re-drives the same record.
    async fn admit_strict_start(
        &self,
        scope: &TaskScope,
        command: &TaskAdmissionCommand,
    ) -> Result<AdmitOutcome, StoreError> {
        let now = (self.now)();
        let (request, strict_input_id) = match self
            .original_group_round(scope, command.create_session.as_ref().expect("checked by the caller"), command)
            .await?
        {
            Some(original) => original,
            None => self.group_round_of(now, scope, command)?,
        };
        let initial = self.initialize(scope, command)?;
        let inbox_input = InputEnvelope {
            schema_version: command.input.schema_version,
            input_id: strict_input_id.clone(),
            business_digest: request.request_digest.clone(),
            sid: command.input.sid.clone(),
            tid: command.input.tid.clone(),
            body: InputBody::User {
                payload: Payload::Inline(
                    serde_json::to_value(&request)
                        .map_err(|_| invalid("group_round_encoding"))?,
                ),
            },
        };
        let staged = self
            .inbox
            .stage_strict_round(
                AdmitRequest {
                    scope: scope.clone(),
                    input: inbox_input,
                    create_session: initial.create_session,
                    create_task: initial.create_task,
                },
                StrictRoundIdentity {
                    request_id: request.request_id.0.clone(),
                    generation: request.input.key.generation.get(),
                },
            )
            .await?;
        if matches!(
            staged,
            StrictRoundStage::Delivered(AdmitOutcome::ContentConflict)
        ) {
            // The staged record's first identity wins; a changed
            // normalization never delivers.
            return Ok(AdmitOutcome::ContentConflict);
        }
        match self.admit_group_round(scope, &request).await? {
            Admission::New { .. } | Admission::Replay { .. } => {
                Ok(AdmitOutcome::Accepted(AdmissionReceipt {
                    sid: command.input.sid.clone(),
                    tid: command.input.tid.clone(),
                    input_id: command.input.input_id.clone(),
                    business_digest: command.input.business_digest.clone(),
                }))
            }
            // A competition answered by the durable authority: no accepted
            // task, no queue entry, no reserved message key — the caller
            // retries the same message key.
            Admission::Busy => Err(StoreError::NotApplied {
                code: "group_round_busy".into(),
            }),
            Admission::Conflict => Ok(AdmitOutcome::ContentConflict),
            Admission::Expired => Err(StoreError::NotApplied {
                code: "group_round_expired".into(),
            }),
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
        // The GroupA2a entrance's first message names a fresh strict
        // session (the `g` namespace marker of the durable session
        // identity is the routing fact, never caller metadata): its
        // round start goes through the licensed strict seam, so the
        // generic inbox entry never installs a task on the session it
        // would have to later fence anyway. Every other command keeps
        // the generic path — the old-RPC-compatible contract stays.
        if self.rounds.is_some()
            && command.start_task
            && command.create_session.is_some()
            && matches!(command.input.body, InputBody::User { .. })
            && CreationSource::of_sid(&command.input.sid) == Some(CreationSource::GroupA2a)
        {
            return self.admit_strict_start(scope, &command).await;
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
