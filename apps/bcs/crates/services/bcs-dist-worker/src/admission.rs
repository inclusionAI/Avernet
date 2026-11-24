//! Initialize task state once, retaining the canonical request selected by Inbox.
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_admission::*,
    port::repo::distributed::{
        common::StoreError,
        group_round::{GroupRoundRepo, GroupStartRequest},
        inbox::*,
    },
};
use std::sync::Arc;

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
