use async_trait::async_trait;

use crate::types::{
    CollaborationDefinition, CollaborationDefinitionRef, GroupRuntimeBinding,
    ResolvedParticipantBinding, RuntimeParticipantBinding, ServiceResult,
    StateMachineDeliveryCorrelation, StateMachineNodeRun, StateMachineRun, StateMachineRunStatus,
};
use std::collections::BTreeMap;

use super::AppendEventRecord;
use super::collaboration_history::*;
use super::collaboration_dispatch::*;
use super::collaboration_publication::*;
use super::collaboration_terminal_im::*;

/// Immutable rendered opening. Recovery never re-renders from current Group
/// configuration. The stable client id identifies the message-history barrier.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateMachineOpeningPayload {
    pub run_id: String,
    pub group_id: String,
    pub session_id: String,
    pub client_msg_id: String,
    pub content: String,
    pub component: Option<String>,
    pub created_at_ms: u64,
}

#[derive(Debug, Clone)]
pub struct StateMachineOpeningCheckpoint {
    pub payload: StateMachineOpeningPayload,
    /// Set only after the exact opening exists in Message history. This is not
    /// an acknowledgement of realtime frontend publication.
    pub delivered_at_ms: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct MarkHumanNodeRunningCommand {
    pub run_id: String,
    pub node_id: String,
    pub attempt: i32,
    pub started_at_ms: u64,
    pub timeout_deadline_ms: u64,
}

/// Internal continuation chosen by runtime when a node attempt fails.
/// Dispatch rejection remains fatal even when the node has retries available.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StateMachineFailureAction {
    Retry,
    FailRun,
}

#[derive(Debug, Clone)]
pub struct FailStateMachineNodeAttempt {
    pub run_id: String,
    pub node_id: String,
    pub attempt: i32,
    pub error: String,
    pub completed_at_ms: u64,
    pub action: StateMachineFailureAction,
}

#[derive(Debug, Clone)]
pub struct StateMachineNodeAttemptFailure {
    pub node: StateMachineNodeRun,
    /// None identifies a legacy failure without a durable continuation decision.
    /// Recovery must not infer its action from the error text or current policy.
    pub action: Option<StateMachineFailureAction>,
}

/// Internal Node lease, independent of delivery correlation and attempt identity.
#[derive(Debug, Clone)]
pub struct StateMachineJudgeClaim {
    pub run_id: String,
    pub node_id: String,
    pub attempt: i32,
    pub owner: String,
    pub token: i64,
    pub lease_until_ms: u64,
}

#[derive(Debug, Clone)]
pub enum StateMachineJudgeResult {
    Completed(crate::JudgeDecision),
    Failed {
        error: String,
        action: StateMachineFailureAction,
        /// Existing state_machine.judge.failed audit payload.
        details: serde_json::Value,
    },
}

#[derive(Debug, Clone)]
pub struct FinishStateMachineJudge {
    pub claim: StateMachineJudgeClaim,
    pub result: StateMachineJudgeResult,
    pub completed_at_ms: u64,
    pub event: Option<AppendEventRecord>,
}

#[derive(Debug, Clone)]
pub struct CreateStateMachineRerun {
    pub source_run_id: String,
    pub run: StateMachineRun,
    pub nodes: Vec<StateMachineNodeRun>,
    pub reactivate_service_session: bool,
}

#[derive(Debug, Clone)]
pub enum CreateStateMachineRerunOutcome {
    Created,
    Existing(StateMachineRun),
    Conflict,
}

#[derive(Debug, Clone)]
pub enum StateMachineEventfulTransition {
    AcceptHistory(AcceptStateMachineHistory),
    /// Commit the fenced Judge result, terminal Node, audit and optional public
    /// completion Event in one local transaction. Input is read from the Node.
    FinishJudge(FinishStateMachineJudge),
    StartRun {
        run_id: String,
        started_at_ms: u64,
        events: Vec<AppendEventRecord>,
    },
    StartBotNode {
        run_id: String,
        node_id: String,
        attempt: i32,
        delivery_request_id: String,
        started_at_ms: u64,
        event: AppendEventRecord,
    },
    StartHumanNode {
        command: MarkHumanNodeRunningCommand,
        event: AppendEventRecord,
    },
    CompleteNode {
        run_id: String,
        node_id: String,
        attempt: i32,
        outcome: String,
        artifact_text: String,
        responded_by: Option<String>,
        completed_at_ms: u64,
        event: AppendEventRecord,
    },
    ScheduleNodeRetry {
        run_id: String,
        node_id: String,
        failed_attempt: i32,
        next_attempt: i32,
        event: AppendEventRecord,
    },
    CompleteRun {
        run_id: String,
        output: Option<String>,
        completed_at_ms: u64,
        event: AppendEventRecord,
    },
}

#[derive(Debug, Clone)]
pub struct CollaborationDefinitionRecord {
    pub definition: CollaborationDefinition,
    pub source_format: Option<String>,
    pub yaml_text: Option<String>,
    pub content_hash: Option<String>,
}

/// The exact compiled plan and its integrity/version envelope, stored with a Run.
/// All three values are present together; legacy v1 snapshots omit this envelope.
#[derive(Debug, Clone)]
pub struct StateMachineExecutionPlanSnapshot {
    pub plan: bcs_domain::StateMachineExecutionPlan,
    pub content_hash: String,
    pub compiler_version: String,
}

#[derive(Debug, Clone)]
pub struct StateMachineRunSnapshot {
    pub definition: CollaborationDefinition,
    pub execution_plan: Option<StateMachineExecutionPlanSnapshot>,
    pub resolved_participant_bindings: Option<BTreeMap<String, ResolvedParticipantBinding>>,
}

#[async_trait]
pub trait StateMachineDefinitionRepoPort: Send + Sync {
    async fn upsert(&self, definition: CollaborationDefinition) -> ServiceResult<()>;
    async fn upsert_with_source_yaml(
        &self,
        definition: CollaborationDefinition,
        source_yaml: String,
    ) -> ServiceResult<()> {
        let _ = source_yaml;
        self.upsert(definition).await
    }
    async fn get(&self, id: &str, version: i32) -> ServiceResult<Option<CollaborationDefinition>>;
    async fn get_record(
        &self,
        id: &str,
        version: i32,
    ) -> ServiceResult<Option<CollaborationDefinitionRecord>> {
        Ok(self
            .get(id, version)
            .await?
            .map(|definition| CollaborationDefinitionRecord {
                definition,
                source_format: None,
                yaml_text: None,
                content_hash: None,
            }))
    }
    async fn save_run_snapshot(
        &self,
        run: &StateMachineRun,
        group_version: i32,
        definition: &CollaborationDefinition,
        resolved_participant_bindings: Option<&BTreeMap<String, ResolvedParticipantBinding>>,
        execution_plan: Option<&StateMachineExecutionPlanSnapshot>,
    ) -> ServiceResult<()>;
    async fn get_run_snapshot(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineRunSnapshot>>;
}

#[async_trait]
pub trait GroupRuntimeBindingRepoPort: Send + Sync {
    async fn upsert(&self, binding: GroupRuntimeBinding) -> ServiceResult<()>;
    async fn get(&self, group_id: &str) -> ServiceResult<Option<GroupRuntimeBinding>>;
    /// Delete all runtime binding state for a Group. Idempotent.
    async fn delete(&self, group_id: &str) -> ServiceResult<bool>;
    async fn bind_default_definition(
        &self,
        group_id: &str,
        group_version: i32,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<()>;
    async fn bind_default_definition_if_current(
        &self,
        group_id: &str,
        group_version: i32,
        expected_definition: Option<CollaborationDefinitionRef>,
        definition: Option<CollaborationDefinitionRef>,
        participant_bindings: Option<BTreeMap<String, RuntimeParticipantBinding>>,
        auto_start_on_service_invocation: bool,
    ) -> ServiceResult<bool> {
        let current = self.get(group_id).await?;
        let current_definition = current.and_then(|binding| binding.default_definition);
        if current_definition != expected_definition {
            return Ok(false);
        }
        self.bind_default_definition(
            group_id,
            group_version,
            definition,
            participant_bindings,
            auto_start_on_service_invocation,
        )
        .await?;
        Ok(true)
    }
}

#[async_trait]
pub trait StateMachineRunRepoPort: Send + Sync {
    /// Return the committed immutable fact without re-reading the normal write.
    async fn accept_history_message(&self, _command: AcceptStateMachineHistory) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> {
        Err(crate::ServiceError::InternalError("atomic history acceptance unavailable".into()))
    }
    async fn get_history_message(&self, _identity: &StateMachineHistoryIdentity) -> ServiceResult<Option<StateMachineHistoryCheckpoint>> {
        Ok(None)
    }
    async fn list_history_messages_pending(&self, _after: Option<&StateMachineHistoryCursor>, _limit: usize) -> ServiceResult<StateMachineHistoryPage> {
        Err(crate::ServiceError::InternalError("history checkpoint scan unavailable".into()))
    }
    async fn confirm_history_message(&self, _checkpoint: &StateMachineHistoryCheckpoint, _at: u64) -> ServiceResult<()> {
        Err(crate::ServiceError::InternalError("history confirmation unavailable".into()))
    }

    /// Bounded, exclusive Run-ID page of Completed/Failed/Aborted active records.
    /// This walks indexed terminal history, without loading snapshots or payloads.
    async fn list_terminal_runs_for_cleanup(&self, _after: Option<&str>, _limit: usize) -> ServiceResult<Vec<String>> {
        Err(crate::ServiceError::InternalError("terminal cleanup scan is not configured".into()))
    }
    /// Recheck Run terminal on every write. Retire at most `limit` unfinished
    /// dispatch/Chat checkpoints and `limit` Node phases/leases. Preserve saved
    /// outcomes, payloads, tokens, audit and terminal IM/opening/startup facts.
    /// Partial writes are resumable; never perform external IO or delete history.
    async fn cleanup_terminal_run_checkpoints(&self, _run: &str, _limit: usize) -> ServiceResult<usize> {
        Err(crate::ServiceError::InternalError("terminal cleanup is not configured".into()))
    }

    /// Atomically fail an active Run only while its original snapshot/opening
    /// is still absent. Recovery waits the saved creation time + preparation
    /// grace; foreground errors may fail immediately. Save a typed failure fact
    /// with the Run CAS so missing-snapshot Session completion can recover.
    async fn fail_missing_startup(&self, _command: super::FailStateMachineStartup) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("missing startup recovery is not configured".into()))
    }
    async fn get_startup_failure(&self, _run: &str) -> ServiceResult<Option<super::StateMachineStartupFailure>> {
        Err(crate::ServiceError::InternalError("startup failure read is not configured".into()))
    }
    /// Running Bot attempt only, no artifact, Provider run ID or dispatch checkpoint. Wait
    /// its saved timeout deadline, or saved start + preparation grace when no
    /// timeout exists (Run creation is the legacy missing-start fallback).
    /// Persist Failed and the original retry policy for timed attempts; absent
    /// timeout means FailRun. Never rebuild a request or resend this attempt.
    async fn fail_missing_dispatch(&self, _run: &str, _node: &str, _attempt: i32, _now: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("missing dispatch recovery is not configured".into()))
    }

    /// Save immutable terminal IM intent before completing the same Service
    /// Session activation. Existing identical intent is preserved on replay.
    async fn save_terminal_im(&self, _payload: StateMachineTerminalImPayload) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("terminal IM persistence is not configured".into()))
    }
    async fn get_terminal_im(&self, _run: &str) -> ServiceResult<Option<StateMachineTerminalImCheckpoint>> {
        Err(crate::ServiceError::InternalError("terminal IM read is not configured".into()))
    }
    /// Environment-scoped Pending intents by exclusive Run ID, including intents
    /// whose Session has completed. Zero limit returns empty; no historical scan.
    async fn list_terminal_im_pending(&self, _after: Option<&str>, _limit: usize) -> ServiceResult<Vec<String>> {
        Err(crate::ServiceError::InternalError("terminal IM scan is not configured".into()))
    }
    /// Requires the original terminal Run and Completed Service Session activation.
    async fn claim_terminal_im(&self, _run: &str, _owner: String, _now: u64, _until: u64) -> ServiceResult<Option<StateMachineTerminalImClaim>> {
        Err(crate::ServiceError::InternalError("terminal IM claim is not configured".into()))
    }
    /// Whole-progress CAS within this one notification checkpoint, fenced by
    /// owner/token/lease and original Session activation. Terminal recipients
    /// cannot regress and Sending cannot become Pending. No external IO inside.
    async fn update_terminal_im_progress(&self, _claim: &StateMachineTerminalImClaim, _expected: StateMachineTerminalImProgress,
        _next: StateMachineTerminalImProgress, _now: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("terminal IM acknowledgement is not configured".into()))
    }
    async fn release_terminal_im(&self, _claim: &StateMachineTerminalImClaim) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("terminal IM release is not configured".into()))
    }
    /// Retire only a Pending intent whose original Run/Session activation no
    /// longer exists or matches. A same-activation Running Session is not stale.
    async fn supersede_terminal_im(&self, _run: &str) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("terminal IM retirement is not configured".into()))
    }

    /// Save immutable output, original Chat target and deadline before IO.
    /// Requires a Running Run with matching identity and only Completed/Skipped
    /// nodes. Identical replay succeeds; conflicting payload is an error.
    async fn save_chat_result(&self, _payload: StateMachineChatResultPayload) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Chat result persistence is not configured".into()))
    }
    async fn get_chat_result(&self, _run: &str) -> ServiceResult<Option<StateMachineChatResultCheckpoint>> {
        Err(crate::ServiceError::InternalError("Chat result read is not configured".into()))
    }
    /// Pending only, active Run, original deadline and empty/expired lease.
    async fn claim_chat_result(&self, _run: &str, _owner: String, _now: u64, _until: u64) -> ServiceResult<Option<StateMachineChatResultClaim>> {
        Err(crate::ServiceError::InternalError("Chat result claim is not configured".into()))
    }
    /// Persist Pending -> Delivering before calling the publisher. Never reset
    /// Delivering to Pending, even when the lease expires or a call is cancelled.
    async fn begin_chat_result_send(&self, _claim: &StateMachineChatResultClaim, _now: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Chat result send barrier is not configured".into()))
    }
    /// Fence acknowledgement/failure by active Run and unexpired owner/token.
    /// A local rejection can fail Pending without starting IO.
    async fn finish_chat_result(&self, _claim: &StateMachineChatResultClaim, _outcome: StateMachineChatResultOutcome, _now: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Chat result completion is not configured".into()))
    }
    /// Fail only Pending/Delivering at the immutable deadline on an active Run.
    /// An already committed acknowledgement wins; no blind resend is allowed.
    async fn expire_chat_result(&self, _run: &str, _now: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Chat result expiry is not configured".into()))
    }
    /// Clear only this owner's lease, preserving the send marker.
    async fn release_chat_result(&self, _claim: &StateMachineChatResultClaim) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Chat result release is not configured".into()))
    }

    /// Save the immutable request after the Node Running CAS and before any send.
    /// Identical replay is read-only; conflicting payload is rejected.
    async fn save_node_dispatch(&self, _payload: StateMachineDispatchPayload) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Dispatch checkpoint persistence is not configured".into()))
    }
    async fn get_node_dispatch(&self, _run: &str, _node: &str, _attempt: i32) -> ServiceResult<Option<StateMachineDispatchCheckpoint>> {
        Err(crate::ServiceError::InternalError("Dispatch checkpoint read is not configured".into()))
    }
    /// Only Pending (provably unsent) work is claimable. Active Run/Node/attempt,
    /// original deadline and an empty/expired lease must match. Claim does not send.
    async fn claim_node_dispatch(&self, _run: &str, _node: &str, _attempt: i32, _owner: String, _now: u64, _until: u64) -> ServiceResult<Option<StateMachineDispatchClaim>> {
        Err(crate::ServiceError::InternalError("Dispatch claim is not configured".into()))
    }
    /// Fenced Pending -> Delivering CAS immediately before IO. Delivering is
    /// never claimable again: loss of its result waits for the original deadline.
    async fn begin_node_dispatch_send(&self, _claim: &StateMachineDispatchClaim, _now: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Dispatch send barrier is not configured".into()))
    }
    /// Fence result by active Run/Node/attempt, owner/token and lease expiry.
    /// Accepted requires Delivering; a local rejection may finish Pending too.
    async fn finish_node_dispatch(&self, _claim: &StateMachineDispatchClaim, _result: StateMachineDispatchResult, _now: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Dispatch completion is not configured".into()))
    }
    /// Expire only Pending/Delivering at the saved deadline with the same active
    /// attempt and no received artifact. Atomically save the existing retry/fail
    /// decision and retire the checkpoint; an accepted ACK/Judge wins over expiry.
    async fn expire_node_dispatch(&self, _command: FailStateMachineNodeAttempt) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Dispatch expiry is not configured".into()))
    }
    /// Clear only this owner's lease without changing Pending/Delivering status.
    async fn release_node_dispatch(&self, _claim: &StateMachineDispatchClaim) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Dispatch release is not configured".into()))
    }
    /// Retire unsent/uncertain operations whose Run or Node attempt is no longer
    /// active. Accepted/failed history is retained; no Provider cancel is sent.
    async fn supersede_inactive_node_dispatches(&self, _run: &str) -> ServiceResult<()> {
        Err(crate::ServiceError::InternalError("Dispatch supersede is not configured".into()))
    }

    /// Insert a run_opening checkpoint for a Pending/Running Run with matching
    /// identity. Identical replay succeeds; conflicting content is an error.
    /// No external send or Run/Message cross-store transaction is implied.
    async fn save_run_opening(&self, _payload: StateMachineOpeningPayload) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Opening checkpoint persistence is not configured".into()))
    }
    async fn get_run_opening(&self, _run_id: &str) -> ServiceResult<Option<StateMachineOpeningCheckpoint>> {
        Err(crate::ServiceError::InternalError("Opening checkpoint read is not configured".into()))
    }
    /// Idempotently record the history barrier on an active Run. Replays retain
    /// the original delivery time. Local idempotent message insertion needs no
    /// external-delivery lease; this method does not schedule realtime publish.
    async fn mark_run_opening_delivered(&self, _run_id: &str, _at_ms: u64) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Opening checkpoint completion is not configured".into()))
    }

    /// Same bounded exclusive cursor contract as list_running_runs, but Pending
    /// only. The application merges both status pages under one total limit.
    async fn list_pending_runs(&self, _after_run_id: Option<&str>, _limit: usize) -> ServiceResult<Vec<StateMachineRun>> {
        Err(crate::ServiceError::InternalError("Startup recovery scanning is not configured".into()))
    }

    /// Atomically save the first Judge input and explicit judging phase on an
    /// active Running attempt. Identical replays succeed without changing it;
    /// different input or legacy artifact-only rows are rejected. No lease yet.
    async fn begin_node_judging(
        &self, _run_id: &str, _node_id: &str, _attempt: i32,
        _artifact_text: String, _responded_by: Option<String>,
    ) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Judge input persistence is not configured".into()))
    }

    /// Claim only an explicitly persisted judging phase on an active Running
    /// attempt. Every claim increments the token; an unexpired lease excludes
    /// all other owners, including the normal request path. Legacy artifact
    /// presence alone is never sufficient recovery evidence.
    async fn claim_node_judging(
        &self, _run_id: &str, _node_id: &str, _attempt: i32,
        _owner: String, _now_ms: u64, _lease_until_ms: u64,
    ) -> ServiceResult<Option<StateMachineJudgeClaim>> {
        Err(crate::ServiceError::InternalError("Judge claim is not configured".into()))
    }

    /// Release only this owner/token/attempt. Successful finish clears its lease
    /// in the same transaction; explicit release is for failed local work.
    async fn release_node_judging(&self, _claim: &StateMachineJudgeClaim) -> ServiceResult<bool> {
        Err(crate::ServiceError::InternalError("Judge release is not configured".into()))
    }

    /// Atomically commit one state-machine transition and its public Event(s).
    async fn commit_eventful_transition(
        &self,
        _transition: StateMachineEventfulTransition,
    ) -> ServiceResult<bool> {
        Err(crate::types::ServiceError::InvalidOperation {
            message: "Eventful state-machine transitions are not configured".to_string(),
            request_id: None,
        })
    }

    async fn create_run(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<()>;

    /// Atomically create a run only when the target session has no active run.
    ///
    /// Stores used by one-shot session launches must override this method with
    /// backend-level serialization. The default preserves compatibility for
    /// external implementations, but only provides best-effort protection.
    async fn create_run_if_session_idle(
        &self,
        run: StateMachineRun,
        nodes: Vec<StateMachineNodeRun>,
    ) -> ServiceResult<bool> {
        if self
            .get_run_by_session_id(&run.session_id)
            .await?
            .is_some_and(|existing| {
                matches!(
                    existing.status,
                    StateMachineRunStatus::Pending | StateMachineRunStatus::Running
                )
            })
        {
            return Ok(false);
        }
        self.create_run(run, nodes).await?;
        Ok(true)
    }

    /// Atomically create the sole direct child of a terminal source Run.
    ///
    /// Implementations must serialize on the Session, reject another active
    /// Run, copy the immutable source snapshot, and reactivate a Service
    /// Session in the same transaction/critical section when requested.
    async fn create_rerun_if_session_idle(
        &self,
        _command: CreateStateMachineRerun,
    ) -> ServiceResult<CreateStateMachineRerunOutcome> {
        Err(crate::types::ServiceError::InvalidOperation {
            message: "Atomic state-machine rerun is not configured".to_string(),
            request_id: None,
        })
    }

    async fn get_direct_rerun(
        &self,
        source_run_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>> {
        let Some(source) = self.get_run(source_run_id).await? else {
            return Ok(None);
        };
        Ok(self
            .list_runs_by_session_id(&source.session_id)
            .await?
            .into_iter()
            .find(|run| run.rerun_of.as_deref() == Some(source_run_id)))
    }

    async fn get_run(&self, run_id: &str) -> ServiceResult<Option<StateMachineRun>>;

    /// A bounded, ascending run_id page of active Running Runs. The cursor is
    /// exclusive; callers restart from None after reaching the end of a sweep.
    /// Implementations must filter status and environment before applying LIMIT.
    async fn list_running_runs(
        &self,
        after_run_id: Option<&str>,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineRun>> {
        let _ = (after_run_id, limit);
        Err(crate::types::ServiceError::InvalidOperation {
            message: "State-machine progression scanning is not configured".into(),
            request_id: None,
        })
    }
    async fn get_run_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Option<StateMachineRun>>;
    /// List every run associated with a session.
    ///
    /// The compatibility default preserves existing external implementations;
    /// production stores override it so cleanup can cancel all active runs.
    async fn list_runs_by_session_id(
        &self,
        session_id: &str,
    ) -> ServiceResult<Vec<StateMachineRun>> {
        Ok(self
            .get_run_by_session_id(session_id)
            .await?
            .into_iter()
            .collect())
    }
    async fn list_node_runs(&self, run_id: &str) -> ServiceResult<Vec<StateMachineNodeRun>>;
    async fn get_node_run(
        &self,
        run_id: &str,
        node_id: &str,
    ) -> ServiceResult<Option<StateMachineNodeRun>>;

    async fn mark_node_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<()>;

    async fn mark_node_running_if_run_active(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        delivery_request_id: String,
        started_at: u64,
    ) -> ServiceResult<bool> {
        self.mark_node_running(run_id, node_id, attempt, delivery_request_id, started_at)
            .await?;
        Ok(true)
    }

    async fn complete_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        outcome: String,
        artifact_text: String,
        responded_by: Option<String>,
        completed_at: u64,
    ) -> ServiceResult<bool>;

    /// Persist the bot artifact while the node remains running so callers can
    /// distinguish bot execution from the subsequent Judge evaluation.
    async fn record_node_artifact_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
    ) -> ServiceResult<bool>;

    /// Atomically accept the first Human response while the node remains
    /// running. The accepted response stays available during Judge evaluation
    /// and after a Judge failure.
    async fn record_human_response_if_running(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        artifact_text: String,
        responded_by: String,
    ) -> ServiceResult<bool>;

    async fn mark_human_node_running_if_run_active(
        &self,
        command: MarkHumanNodeRunningCommand,
    ) -> ServiceResult<bool>;

    async fn fail_node_attempt(
        &self,
        run_id: &str,
        node_id: &str,
        attempt: i32,
        error: String,
        completed_at: u64,
    ) -> ServiceResult<bool>;

    /// In the same Running-attempt CAS, persist Failed, its error/time and the
    /// selected action. Requires an active Run; false leaves every fact intact.
    /// A Retry action must have a remaining attempt. Never fall back to the
    /// legacy action-less write when this capability is missing.
    async fn fail_node_attempt_with_action(
        &self,
        _command: FailStateMachineNodeAttempt,
    ) -> ServiceResult<bool> {
        Err(crate::ServiceError::InvalidOperation {
            message: "Node failure action persistence is not configured".into(), request_id: None,
        })
    }

    /// Read only this exact current Failed attempt. None means missing/stale;
    /// a returned record with action=None is an unrecoverable legacy failure.
    async fn get_node_attempt_failure(
        &self,
        _run_id: &str,
        _node_id: &str,
        _attempt: i32,
    ) -> ServiceResult<Option<StateMachineNodeAttemptFailure>> {
        Err(crate::ServiceError::InvalidOperation {
            message: "Node failure action lookup is not configured".into(), request_id: None,
        })
    }

    /// Schedule the caller-selected next attempt while Failed and the Run is Running.
    /// A saved FailRun action cannot be retried. Legacy action-less failures
    /// retain the foreground API's retry behavior, but are not recovered.
    async fn schedule_node_retry(
        &self,
        run_id: &str,
        node_id: &str,
        failed_attempt: i32,
        next_attempt: i32,
    ) -> ServiceResult<bool>;

    /// CAS Pending/Ready/RetryScheduled to Skipped only while the Run is Running.
    /// A false result must not stop traversal of an already Skipped branch.
    async fn skip_node(&self, run_id: &str, node_id: &str, skipped_at: u64) -> ServiceResult<bool>;

    async fn update_run_status(
        &self,
        run_id: &str,
        status: StateMachineRunStatus,
        output: Option<String>,
        error: Option<String>,
        updated_at: u64,
        completed_at: Option<u64>,
    ) -> ServiceResult<bool>;

    async fn upsert_delivery_correlation(
        &self,
        correlation: StateMachineDeliveryCorrelation,
    ) -> ServiceResult<()>;

    async fn register_delivery_alias(
        &self,
        delivery_request_id: &str,
        bot_delivery_run_id: String,
    ) -> ServiceResult<()>;

    async fn lookup_delivery_correlation(
        &self,
        run_id: &str,
    ) -> ServiceResult<Option<StateMachineDeliveryCorrelation>>;

    async fn list_expired_running_node_runs(
        &self,
        now_ms: u64,
        timeout_grace_ms: u64,
        limit: usize,
    ) -> ServiceResult<Vec<StateMachineNodeRun>> {
        let _ = (now_ms, timeout_grace_ms, limit);
        Ok(Vec::new())
    }
}

#[async_trait]
pub trait CollaborationEventRepoPort: Send + Sync {
    async fn append_event(
        &self,
        state_machine_run_id: &str,
        node_id: Option<&str>,
        attempt: Option<i32>,
        event_type: &str,
        payload: serde_json::Value,
        created_at: u64,
    ) -> ServiceResult<()>;

    async fn list_events_by_run_and_type(
        &self,
        state_machine_run_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>>;

    async fn list_events_by_run_node_and_type(
        &self,
        state_machine_run_id: &str,
        node_id: &str,
        event_type: &str,
    ) -> ServiceResult<Vec<CollaborationEventRecord>>;
}

#[derive(Debug, Clone)]
pub struct CollaborationEventRecord {
    pub state_machine_run_id: String,
    pub node_id: Option<String>,
    pub attempt: Option<i32>,
    pub event_type: String,
    pub payload: serde_json::Value,
    pub created_at: u64,
}
