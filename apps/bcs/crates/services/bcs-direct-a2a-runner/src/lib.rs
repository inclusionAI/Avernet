//! One distributed task drives one downstream A2A call. The Worker owns
//! durability, dispatch supervision, cancellation fencing and ephemeral thinking.
mod output;

use async_trait::async_trait;
use bcs_dist_encoding::{connector::input_request_id, digest_json};
use bcs_dist_model::*;
use bcs_service_api::core::dist_runner::*;
use output::Artifacts;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

#[derive(Debug, Default)]
pub struct DirectA2aRunner;

#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct DirectA2aState {
    deadline: Option<TimestampMillis>,
    replies: u64,
    awaiting: Option<InteractionId>,
    artifacts: Artifacts,
}

fn invalid(code: &str) -> RunnerError {
    RunnerError::InvalidWorkflow { code: code.to_owned() }
}

fn corrupt(code: &str) -> RunnerError {
    RunnerError::CorruptState { code: code.to_owned() }
}

fn content(payload: &Payload) -> Result<&Value, RunnerError> {
    match payload {
        Payload::Inline(value) => Ok(value),
        Payload::Ref(_) => Err(corrupt("unresolved_direct_input")),
    }
}

#[async_trait]
impl CollaborationModeRunnerService for DirectA2aRunner {
    type Config = AuthorizedAgent;
    type State = DirectA2aState;

    fn mode(&self) -> CollaborationMode {
        CollaborationMode::DirectA2a
    }

    fn shares_session_downstream_context(&self) -> bool {
        true
    }

    fn parse_and_validate_config(
        &self,
        raw: &Value,
        agents: &[AuthorizedAgent],
        _: &SessionLimits,
    ) -> Result<Self::Config, RunnerError> {
        let [binding] = agents else {
            return Err(invalid("expected_one_direct_agent"));
        };
        if binding.agent.0.is_empty()
            || binding.pool.0.is_empty()
            || raw.get("agent").and_then(Value::as_str) != Some(binding.agent.0.as_str())
        {
            return Err(invalid("direct_agent_config_mismatch"));
        }
        Ok(binding.clone())
    }

    async fn init_task(
        &self,
        spec: &SessionSpec,
        config: &Self::Config,
        _: &Tid,
        _: u64,
        _: TimestampMillis,
    ) -> Result<Self::State, RunnerError> {
        if !matches!(&spec.target, SessionTarget::DirectAgent { agent } if agent == &config.agent) {
            return Err(invalid("direct_agent_target_mismatch"));
        }
        Ok(DirectA2aState::default())
    }

    async fn reduce_task(
        &self,
        spec: &SessionSpec,
        config: &Self::Config,
        tid: &Tid,
        downstream_generation: u64,
        state: &Self::State,
        inputs: &[RunnerInput],
        now: TimestampMillis,
    ) -> Result<TypedDecision<Self::State>, RunnerError> {
        let did = Did(format!("{}.agent.1", tid.0));
        let mut state = state.clone();
        let mut actions = Vec::new();
        for input in inputs {
            match input {
                RunnerInput::Start { tid: task, payload }
                    if task == tid && state.deadline.is_none() =>
                {
                    let duration = i64::try_from(spec.limits.task_timeout_millis)
                        .map_err(|_| invalid("direct_dispatch_deadline"))?;
                    let deadline = TimestampMillis(
                        now.0
                            .checked_add(duration)
                            .ok_or_else(|| invalid("direct_dispatch_deadline"))?,
                    );
                    state.deadline = Some(deadline);
                    actions.push(ActionIntent::StartDispatch(DispatchSpec {
                        schema_version: DISPATCH_SCHEMA_VERSION,
                        sid: spec.sid.clone(),
                        tid: tid.clone(),
                        did: did.clone(),
                        source: CorrelationKey("agent".into()),
                        attempt: 1,
                        agent: config.agent.clone(),
                        pool: config.pool.clone(),
                        downstream_context: Some(format!(
                            "{}:{}",
                            spec.sid.0, downstream_generation
                        )),
                        request: payload.clone(),
                        deadline,
                    }));
                }
                RunnerInput::HumanReply { tid: task, interaction, payload, .. } if task == tid => {
                    if state.awaiting.as_ref() != Some(interaction) {
                        continue;
                    }
                    state.awaiting = None;
                    state.replies = state
                        .replies
                        .checked_add(1)
                        .ok_or_else(|| corrupt("direct_input_sequence_overflow"))?;
                    let seq = InputSeq(state.replies);
                    actions.push(ActionIntent::ContinueDispatch {
                        did: did.clone(),
                        input: PendingInput {
                            seq,
                            input_id: InputId(input_request_id(&did, seq).0),
                            business_digest: digest_json(content(payload)?),
                            payload: payload.clone(),
                        },
                    });
                    status(&mut actions, tid, TaskStatus::Working, vec![]);
                }
                RunnerInput::Agent { tid: task, event } if task == tid && event.did == did => {
                    let value = content(&event.payload)?;
                    match &event.kind {
                        AgentEventKind::Delta | AgentEventKind::Tool | AgentEventKind::Snapshot => {
                            state.artifacts.update(tid, &event.kind, value, &mut actions);
                        }
                        AgentEventKind::Thinking => {} // Worker handles this before reduction.
                        AgentEventKind::InputRequired { interaction }
                        | AgentEventKind::AuthRequired { interaction } => {
                            let (waiting, kind) = match event.kind {
                                AgentEventKind::AuthRequired { .. } => {
                                    (TaskStatus::AuthRequired, InteractionKind::Authorization)
                                }
                                _ => (TaskStatus::InputRequired, InteractionKind::HumanInput),
                            };
                            state.awaiting = Some(interaction.clone());
                            let prompt = value.get("message").and_then(output::message_text);
                            status(
                                &mut actions,
                                tid,
                                waiting,
                                vec![Interaction {
                                    id: interaction.clone(),
                                    origin: InteractionOrigin::Dispatch { did: did.clone() },
                                    kind,
                                    prompt,
                                }],
                            );
                        }
                        AgentEventKind::Final | AgentEventKind::Error => {
                            let success = event.kind == AgentEventKind::Final;
                            if success {
                                state.artifacts.final_output(tid, value, &mut actions);
                            }
                            actions.push(ActionIntent::FinishTask {
                                tid: tid.clone(),
                                status: if success {
                                    TerminalStatus::Completed
                                } else {
                                    TerminalStatus::Failed
                                },
                                result: event.payload.clone(),
                            });
                            state.deadline = None;
                            break;
                        }
                    }
                }
                RunnerInput::Timeout(Deadline {
                    kind: DeadlineKind::Dispatch { tid: task, did: dispatch },
                    ..
                }) if task == tid && dispatch == &did => {
                    actions.push(ActionIntent::CancelDispatch {
                        did: did.clone(),
                        intent: CancelIntent {
                            request_id: RequestId(format!("{}:timeout", did.0)),
                            reason: Some("DispatchDeadlineExceeded".into()),
                        },
                    });
                    actions.push(ActionIntent::FinishTask {
                        tid: tid.clone(),
                        status: TerminalStatus::Failed,
                        result: Payload::Inline(json!({"code": "DispatchDeadlineExceeded"})),
                    });
                    state.deadline = None;
                    break;
                }
                _ => return Err(corrupt("unexpected_direct_input")),
            }
        }
        // Worker replaces the runner's deadline set after each committed reduction.
        let next_deadlines = state
            .deadline
            .map(|at| Deadline {
                id: DeadlineId(format!("{}.deadline", did.0)),
                at,
                kind: DeadlineKind::Dispatch { tid: tid.clone(), did },
            })
            .into_iter()
            .collect();
        Ok(TypedDecision { next_state: state, actions, next_deadlines })
    }
}

fn status(
    actions: &mut Vec<ActionIntent>,
    tid: &Tid,
    status: TaskStatus,
    interactions: Vec<Interaction>,
) {
    actions.retain(|action| !matches!(action, ActionIntent::SetStatus { .. }));
    actions.push(ActionIntent::SetStatus { tid: tid.clone(), status, interactions });
}
