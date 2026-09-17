use super::*;
use bcs_service_api::{FinishStateMachineJudge, StateMachineFailureAction, StateMachineJudgeResult};

pub(super) enum JudgingProgress {
    Unclaimed,
    Completed,
    Failed(String),
}

impl CollaborationRuntime {
    /// Both foreground terminal responses and recovery enter through this lease.
    pub(super) async fn resume_judging(
        &self, compiled: &CompiledStateMachine, group: &Group, run: &StateMachineRun,
        node_id: &str, attempt: i32,
    ) -> Result<JudgingProgress, CollaborationRuntimeError> {
        let CollaborationRuntimeDefinition::StateMachine(machine) = &compiled.definition.runtime else {
            return Err(CollaborationRuntimeError::InvalidRequest("Judge requires a state-machine snapshot".into()));
        };
        let definition = machine.nodes.get(node_id).filter(|node| node.judge.is_some())
            .ok_or_else(|| CollaborationRuntimeError::InvalidRequest("Judge missing from immutable snapshot".into()))?;
        let timeout = definition.node_timeout_ms.or(machine.defaults.node_timeout_ms)
            .unwrap_or(DEFAULT_JUDGE_TIMEOUT_MS).max(1);
        let now = bcs_protocol::now_ms();
        // A bounded Judge call plus local commit grace; expiry only takes over
        // abandoned work, never creates a new attempt or a Bot dispatch.
        let Some(claim) = self.runs.claim_node_judging(&run.run_id, node_id, attempt,
            Uuid::new_v4().to_string(), now, now.saturating_add(timeout).saturating_add(5_000)).await?
        else { return Ok(JudgingProgress::Unclaimed); };
        let result = async {
            let node = self.runs.get_node_run(&run.run_id, node_id).await?
                .ok_or_else(|| CollaborationRuntimeError::RunNotFound(run.run_id.clone()))?;
            if node.attempt != attempt || node.status != StateMachineNodeStatus::Running {
                self.runs.release_node_judging(&claim).await?;
                return Ok(JudgingProgress::Unclaimed);
            }
            let artifact = node.artifact_text.as_deref().ok_or_else(||
                CollaborationRuntimeError::InvalidRequest("Judging has no persisted artifact".into()))?;
            if definition.kind == StateMachineNodeKind::HumanInput && node.responded_by.is_none() {
                return Err(CollaborationRuntimeError::InvalidRequest("Human Judge has no persisted responder".into()));
            }
            if !self.progression_run_is_active(&run.run_id).await? {
                self.runs.release_node_judging(&claim).await?;
                return Ok(JudgingProgress::Unclaimed);
            }
            let evaluated = self.evaluate_node_outcome(compiled, run, node_id, attempt, artifact).await?;
            let completed_at = bcs_protocol::now_ms();
            let (result, event, progress) = match evaluated {
                JudgeEvaluationResult::Outcome(outcome, Some(decision)) => {
                    let (actor, actor_type) = if definition.kind == StateMachineNodeKind::HumanInput {
                        (node.responded_by.as_deref(), EventActorType::Human)
                    } else { (node.assignee_bot_id.as_deref(), EventActorType::Bot) };
                    let event = self.prepare_node_event(compiled, "state_machine.node.completed", run,
                        "state_machine.node", node_id, &format!("{node_id}:{attempt}:completed"),
                        actor, Some(actor_type), completed_at, BTreeMap::from([
                            ("run_id".into(), serde_json::json!(run.run_id)),
                            ("node_id".into(), serde_json::json!(node_id)),
                            ("attempt".into(), serde_json::json!(attempt)),
                            ("outcome".into(), serde_json::json!(outcome)),
                            ("output".into(), content_value(Value::String(artifact.into()))?),
                            ("completed_at".into(), serde_json::json!(event_timestamp(completed_at)?)),
                            ("duration_ms".into(), serde_json::json!(completed_at.saturating_sub(node.started_at.unwrap_or(completed_at)))),
                        ]))?;
                    (StateMachineJudgeResult::Completed(decision), event, JudgingProgress::Completed)
                }
                JudgeEvaluationResult::Failed(error, details) => {
                    let action = if attempt.checked_add(1).is_some_and(|next| next < node.max_attempts.max(1)) {
                        StateMachineFailureAction::Retry
                    } else { StateMachineFailureAction::FailRun };
                    (StateMachineJudgeResult::Failed { error: error.clone(), action, details }, None, JudgingProgress::Failed(error))
                }
                _ => return Err(CollaborationRuntimeError::InvalidRequest("Judge returned no decision".into())),
            };
            let outcome = match &result { StateMachineJudgeResult::Completed(decision) => Some(decision.outcome.clone()), _ => None };
            let committed = self.runs.commit_eventful_transition(StateMachineEventfulTransition::FinishJudge(
                FinishStateMachineJudge { claim: claim.clone(), result, completed_at_ms: completed_at, event },
            )).await?;
            if !committed {
                self.runs.release_node_judging(&claim).await?;
                return Ok(JudgingProgress::Unclaimed);
            }
            self.observe_loop_node(compiled, run, node_id, attempt,
                if outcome.is_some() { "completed" } else { "failed" }, outcome.as_deref());
            if let (Some(bot), Some(delivery)) = (&node.assignee_bot_id, &node.delivery_request_id) {
                let correlation = StateMachineDeliveryCorrelation {
                    state_machine_run_id: run.run_id.clone(), node_id: node_id.into(), attempt,
                    assignee_bot_id: bot.clone(), delivery_request_id: delivery.clone(),
                    bot_delivery_run_id: node.bot_delivery_run_id.clone(),
                };
                match &progress {
                    JudgingProgress::Completed => log_state_machine_node_result(run, &correlation,
                        MessageLogStatus::Completed, outcome.as_deref(), None, Some(artifact.len())),
                    JudgingProgress::Failed(error) => log_state_machine_node_result(run, &correlation,
                        MessageLogStatus::Failed, None, Some(error), None),
                    JudgingProgress::Unclaimed => unreachable!("committed Judge has a terminal result"),
                }
            }
            if let Some(outcome) = outcome {
                self.apply_completed_node_progression(compiled, group, run, node_id, &outcome, completed_at).await?;
            } else {
                self.resume_failed_node(compiled, group, run, node_id, attempt).await?;
            }
            Ok(progress)
        }.await;
        if result.is_err() {
            // This cannot release a newer owner. Preserve write failures instead
            // of treating the attempted transition as successful.
            self.runs.release_node_judging(&claim).await?;
        }
        result
    }
}
