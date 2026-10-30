//! Manager RunResult for failed or aborted Runs. `fail_run` and `abort_run`
//! commit the terminal CAS, the public Event and the RunResult intent in one
//! `TerminateRun` transaction; this module builds them and delivers the intent
//! after the commit and from the recovery scanner.
use super::*;
use bcs_service_api::{StateMachineRunFailedNode, StateMachineRunResultOutcome, StateMachineRunResultPayload,
    StateMachineRunResultStatus, StateMachineRunResultTerminal, StateMachineRunTerminalReason};

/// How long a pending RunResult may still wake the manager.
const RUN_RESULT_TIMEOUT_MS: u64 = 10 * 60_000;
/// Lease for one publication attempt; also bounds the publisher call.
const RUN_RESULT_LEASE_MS: u64 = 30_000;

/// Why `fail_run` was called. Node failures name the failed attempt; whether a
/// Node failure is a human timeout is derived from the Node kind and deadline.
pub(super) struct RunFailure {
    reason: StateMachineRunTerminalReason,
    node: Option<(String, i32)>,
    pub(super) error: String,
}

impl RunFailure {
    pub(super) fn node(node_id: &str, attempt: i32, error: String) -> Self {
        Self { reason: StateMachineRunTerminalReason::NodeFailed, node: Some((node_id.to_string(), attempt)), error }
    }

    pub(super) fn startup(error: String) -> Self {
        Self { reason: StateMachineRunTerminalReason::StartupFailed, node: None, error }
    }

    pub(super) fn publication(error: String) -> Self {
        Self { reason: StateMachineRunTerminalReason::PublicationFailed, node: None, error }
    }
}

/// Public Event and RunResult intent committed with one terminal transition.
pub(super) struct TerminalArtifacts {
    pub(super) event: Option<bcs_service_api::port::repo::AppendEventRecord>,
    pub(super) result: Option<StateMachineRunResultPayload>,
    pub(super) is_chat_session: bool,
}

impl CollaborationRuntime {
    pub(super) async fn failure_artifacts(
        &self,
        run: &StateMachineRun,
        failure: &RunFailure,
        now: u64,
    ) -> Result<TerminalArtifacts, CollaborationRuntimeError> {
        let failed = failure.node.as_ref().map(|(node_id, attempt)| (node_id.as_str(), *attempt));
        self.terminal_artifacts(run, StateMachineRunResultTerminal::Failed, failure.reason, failed, now).await
    }

    pub(super) async fn terminal_artifacts(
        &self,
        run: &StateMachineRun,
        terminal: StateMachineRunResultTerminal,
        reason: StateMachineRunTerminalReason,
        failed: Option<(&str, i32)>,
        now: u64,
    ) -> Result<TerminalArtifacts, CollaborationRuntimeError> {
        let nodes = self.runs.list_node_runs(&run.run_id).await?;
        let completed_nodes: Vec<String> = nodes
            .iter()
            .filter(|node| node.status == StateMachineNodeStatus::Completed)
            .map(|node| node.node_id.clone())
            .collect();
        let definition = self.definitions.get_run_snapshot(&run.run_id).await?.map(|snapshot| snapshot.definition);
        let failed_node = failed.map(|(node_id, attempt)| StateMachineRunFailedNode {
            node_id: node_id.to_string(),
            attempt: attempt.saturating_add(1),
            max_attempts: nodes.iter().find(|node| node.node_id == node_id).map_or(1, |node| node.max_attempts.max(1)),
        });
        let reason = match failed {
            Some((node_id, _)) if reason == StateMachineRunTerminalReason::NodeFailed
                && human_node_timed_out(definition.as_ref(), &nodes, node_id) =>
            {
                StateMachineRunTerminalReason::HumanTimeout
            }
            _ => reason,
        };
        let is_chat_session = self.is_chat_session(&run.session_id).await?;
        let mut data = BTreeMap::from([
            (format!("{}_at", terminal.as_str()), serde_json::json!(event_timestamp(now)?)),
            ("reason".to_string(), serde_json::json!(reason.as_str())),
            ("completed_nodes".to_string(), serde_json::json!(completed_nodes)),
            ("duration_ms".to_string(), serde_json::json!(now.saturating_sub(run.created_at))),
        ]);
        if terminal == StateMachineRunResultTerminal::Failed {
            data.insert("failed_node".into(), serde_json::json!(failed_node.as_ref().map(|node| node.node_id.clone())));
            data.insert("attempt".into(), serde_json::json!(failed_node.as_ref().map(|node| node.attempt)));
            data.insert("max_attempts".into(), serde_json::json!(failed_node.as_ref().map(|node| node.max_attempts)));
        }
        let event_type = format!("state_machine.run.{}", terminal.as_str());
        let event = self.prepare_public_event(
            &event_type,
            run,
            "state_machine.run",
            &run.run_id,
            terminal.as_str(),
            None,
            None,
            now,
            data,
        )?;
        let result = match &run.created_by {
            Some(manager) if is_chat_session && reason.notifies_manager() => Some(StateMachineRunResultPayload {
                run_id: run.run_id.clone(),
                group_id: run.group_id.clone(),
                session_id: run.session_id.clone(),
                target_bot_id: manager.clone(),
                workflow: definition.as_ref().map_or_else(String::new, |definition| definition.name.clone()),
                terminal,
                reason,
                failed_node,
                completed_nodes,
                terminated_at_ms: now,
                deadline_ms: now.saturating_add(RUN_RESULT_TIMEOUT_MS),
            }),
            _ => None,
        };
        Ok(TerminalArtifacts { event, result, is_chat_session })
    }

    /// Commit Run abortion with its public Event and, for an explicit user
    /// abort in a Chat Session, the RunResult intent for the manager.
    pub(super) async fn abort_run(
        &self,
        cmd: CancelStateMachineRunCommand,
        reason: StateMachineRunTerminalReason,
    ) -> Result<StateMachineRunView, CollaborationRuntimeError> {
        let run = self
            .runs
            .get_run(&cmd.run_id)
            .await?
            .ok_or_else(|| CollaborationRuntimeError::RunNotFound(cmd.run_id.clone()))?;
        let now = bcs_protocol::now_ms();
        let artifacts = self
            .terminal_artifacts(&run, StateMachineRunResultTerminal::Aborted, reason, None, now)
            .await?;
        let has_result = artifacts.result.is_some();
        let aborted = self
            .runs
            .commit_eventful_transition(StateMachineEventfulTransition::TerminateRun {
                run_id: cmd.run_id.clone(),
                status: StateMachineRunStatus::Aborted,
                error: cmd.reason.clone(),
                terminated_at_ms: now,
                event: artifacts.event,
                result: artifacts.result,
            })
            .await?;
        self.runs.supersede_inactive_node_dispatches(&run.run_id).await?;
        if !aborted {
            return self.run_view(&cmd.run_id).await?
                .ok_or(CollaborationRuntimeError::RunNotFound(cmd.run_id));
        }
        let (completed_session, session_missing) = if artifacts.is_chat_session {
            (None, false)
        } else {
            match self
                .complete_service_session_for_run(&run, None, Some("aborted".to_string()))
                .await
            {
                Ok(completed_session) => (completed_session, false),
                Err(SessionUseCaseError::NotFound(_)) => (None, true),
                Err(error) => {
                    return Err(CollaborationRuntimeError::InvalidRequest(error.to_string()));
                }
            }
        };
        let session_complete_result = if artifacts.is_chat_session {
            "chat_preserved"
        } else if session_missing {
            "missing"
        } else if completed_session.is_some() {
            "completed"
        } else {
            "not_running"
        };
        info!(
            run_id = %cmd.run_id,
            group_id = %run.group_id,
            session_id = %run.session_id,
            definition_id = %run.definition_id,
            definition_version = run.definition_version,
            reason = ?cmd.reason,
            terminal_reason = reason.as_str(),
            session_complete_result = %session_complete_result,
            "state_machine: run aborted"
        );
        if has_result {
            self.deliver_run_result(&cmd.run_id).await;
        }
        self.run_view(&cmd.run_id)
            .await?
            .ok_or(CollaborationRuntimeError::RunNotFound(cmd.run_id))
    }

    /// Abort every non-terminal Run of a Session. Session end and Group closure
    /// pass their own reason, so they publish only the public Event.
    pub(super) async fn abort_session_runs(
        &self,
        session_id: &str,
        reason: &str,
        terminal_reason: StateMachineRunTerminalReason,
    ) -> Result<(), CollaborationRuntimeError> {
        let mut first_error = None;
        for run in self.runs.list_runs_by_session_id(session_id).await? {
            if !matches!(run.status, StateMachineRunStatus::Pending | StateMachineRunStatus::Running) {
                continue;
            }
            let cmd = CancelStateMachineRunCommand { run_id: run.run_id, reason: Some(reason.to_string()) };
            if let Err(error) = self.abort_run(cmd, terminal_reason).await
                && first_error.is_none()
            {
                first_error = Some(error);
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    /// First delivery attempt right after the terminal commit. Failures are left
    /// to the recovery scanner, so they never change the terminal outcome.
    pub(super) async fn deliver_run_result(&self, run_id: &str) {
        if let Err(error) = self.ensure_run_result(run_id).await {
            warn!(run_id = %run_id, error = %error, "state_machine: RunResult delivery deferred to recovery");
        }
    }

    /// Deliver a pending RunResult at most once. Persistence errors propagate;
    /// a publisher error after the send barrier is recorded as uncertain.
    pub(super) async fn ensure_run_result(&self, run_id: &str) -> Result<(), CollaborationRuntimeError> {
        let now = bcs_protocol::now_ms();
        let Some(saved) = self.runs.get_run_result(run_id).await? else { return Ok(()); };
        match saved.status {
            StateMachineRunResultStatus::Pending if saved.payload.deadline_ms > now => {}
            StateMachineRunResultStatus::Pending | StateMachineRunResultStatus::Delivering => {
                // Past the deadline, or a send whose lease ended without an outcome.
                self.runs.expire_run_result(run_id, now).await?;
                return Ok(());
            }
            _ => return Ok(()),
        }
        let Some(publisher) = self.result_publisher.as_ref() else { return Ok(()); };
        let until = now.saturating_add(RUN_RESULT_LEASE_MS).min(saved.payload.deadline_ms);
        let Some(claim) = self.runs.claim_run_result(run_id, Uuid::new_v4().to_string(), now, until).await? else {
            return Ok(());
        };
        if !self.runs.begin_run_result_send(&claim, bcs_protocol::now_ms()).await? {
            return Ok(());
        }
        let remaining = claim.lease_until_ms.saturating_sub(bcs_protocol::now_ms());
        let outcome = match tokio::time::timeout(
            Duration::from_millis(remaining),
            publisher.publish_state_machine_run_result(claim.payload.clone()),
        )
        .await
        {
            Ok(Ok(outcome)) => outcome,
            Ok(Err(error)) => StateMachineRunResultOutcome::Uncertain { error: error.to_string() },
            // The lease ends with the call; recovery records the unknown outcome.
            Err(_) => {
                warn!(run_id = %run_id, lease_token = claim.token,
                    "state_machine: RunResult publication timed out; delivery is unknown");
                return Ok(());
            }
        };
        let committed = self.runs.finish_run_result(&claim, outcome.clone(), bcs_protocol::now_ms()).await?;
        info!(
            run_id = %run_id,
            session_id = %claim.payload.session_id,
            manager = %claim.payload.target_bot_id,
            reason = claim.payload.reason.as_str(),
            outcome = ?outcome,
            committed,
            "state_machine: RunResult publication settled"
        );
        Ok(())
    }

    pub(super) async fn recover_run_results(
        &self,
        after_run_id: Option<String>,
        limit: usize,
    ) -> Result<bcs_service_api::StateMachineProgressionRecoveryPage, CollaborationRuntimeError> {
        if limit == 0 {
            return Ok(Default::default());
        }
        let ids = self.runs.list_run_results_pending(after_run_id.as_deref(), limit).await?;
        let mut page = bcs_service_api::StateMachineProgressionRecoveryPage {
            scanned: ids.len(),
            next_run_id: if ids.len() == limit { ids.last().cloned() } else { None },
            ..Default::default()
        };
        for run_id in ids {
            match self.ensure_run_result(&run_id).await {
                Ok(()) => page.reconciled += 1,
                Err(error) => page.failures.push(bcs_service_api::StateMachineProgressionRecoveryFailure {
                    run_id,
                    error: error.to_string(),
                }),
            }
        }
        Ok(page)
    }
}

/// A HumanInput Node that failed at or after its deadline timed out. Human Nodes
/// keep only their timeout and start time, so the deadline is derived from them.
fn human_node_timed_out(definition: Option<&CollaborationDefinition>, nodes: &[StateMachineNodeRun], node_id: &str) -> bool {
    let human = match definition.map(|definition| &definition.runtime) {
        Some(CollaborationRuntimeDefinition::StateMachine(machine)) => machine
            .nodes
            .get(node_id)
            .is_some_and(|node| node.kind == StateMachineNodeKind::HumanInput),
        _ => false,
    };
    human
        && nodes.iter().find(|node| node.node_id == node_id).is_some_and(|node| {
            let deadline = node.timeout_deadline_ms.or_else(|| match (node.started_at, node.node_timeout_ms) {
                (Some(started), Some(timeout)) if timeout > 0 => started.checked_add(timeout),
                _ => None,
            });
            deadline.is_some_and(|deadline| node.completed_at.is_some_and(|completed| completed >= deadline))
        })
}
