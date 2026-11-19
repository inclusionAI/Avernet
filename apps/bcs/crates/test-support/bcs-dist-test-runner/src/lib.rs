//! Deterministic workloads supplied to the real processor by the test host.
use async_trait::async_trait;
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::session::Checkpoint;
use bcs_service_api::{core::dist_runner::*, port::dist_agent::*};
use std::sync::atomic::{AtomicUsize, Ordering};
pub struct Directory;
#[async_trait]
impl DistGroupDirectoryPort for Directory {
    async fn resolve(
        &self,
        _: &TaskScope,
        _: &GroupId,
    ) -> Result<FrozenGroupDefinition, GroupDirectoryError> {
        Err(GroupDirectoryError::NotFound)
    }
    async fn load_workflow(
        &self,
        _: &TaskScope,
        _: &WorkflowRef,
    ) -> Result<bcs_domain::collaboration::StateMachineDefinition, GroupDirectoryError> {
        serde_json::from_value(serde_json::json!({}))
            .map_err(|_| GroupDirectoryError::InvalidRevision)
    }
}
#[derive(Default)]
pub struct Runner {
    pub calls: AtomicUsize,
}
impl DistRunnerCoreService for Runner {
    fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::Value::Null,
        })
    }
    fn reduce(
        &self,
        context: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let mut actions = vec![];
        let mut next_state = state.clone();
        for input in inputs {
            if let RunnerInput::Start {
                tid,
                payload: Payload::Inline(value),
            } = input
            {
                // The Gateway story supplies its deterministic workload as an A2A DataPart.
                let value = value.get("parts").and_then(|parts| parts.as_array()).and_then(|parts| parts.first()).and_then(|part| part.get("data")).unwrap_or(value);
                let mode = value["mode"].as_str().unwrap_or("finish");
                next_state.value = serde_json::json!({"mode": mode});
                match mode {
                    "interaction" | "stream" | "dispatch" => {
                        actions.push(ActionIntent::StartDispatch(dispatch_spec(context, tid)));
                        if mode == "interaction" {
                            actions.push(ActionIntent::SetStatus {
                                tid: tid.clone(),
                                status: TaskStatus::InputRequired,
                                interactions: vec![Interaction {
                                    id: InteractionId("confirm".into()),
                                    did: dispatch_id(tid),
                                    kind: InteractionKind::HumanInput,
                                }],
                            });
                        } else if mode == "stream" {
                            actions.push(show(tid, Payload::Inline(serde_json::json!("seed"))));
                        }
                    }
                    "quarantine" => actions.push(ActionIntent::FinishTask {
                        tid: Tid("not-the-running-task".into()),
                        status: TerminalStatus::Completed,
                        result: Payload::Inline(serde_json::Value::Null),
                    }),
                    "blocked" => return Err(RunnerError::UnsupportedFormat { format: 99 }),
                    "hold" => actions.push(ActionIntent::SetStatus {
                        tid: tid.clone(),
                        status: TaskStatus::Working,
                        interactions: vec![],
                    }),
                    _ => {
                        let size = value["bytes"].as_u64().unwrap_or(12).min(1_048_576) as usize;
                        let payload = Payload::Inline(serde_json::json!("x".repeat(size)));
                        actions.push(ActionIntent::Show {
                            tid: tid.clone(),
                            node: NodeId("answer".into()),
                            artifact_id: ArtifactId("answer".into()),
                            class: OutputClass::Output,
                            update: if mode == "append" {
                                ArtifactUpdate::AppendText { delta: payload }
                            } else {
                                ArtifactUpdate::Upsert { content: payload }
                            },
                        });
                        actions.push(ActionIntent::FinishTask {
                            tid: tid.clone(),
                            status: TerminalStatus::Completed,
                            result: Payload::Inline(serde_json::json!({"finished":true})),
                        });
                    }
                }
            } else if let RunnerInput::HumanReply { tid, payload, .. } = input {
                actions.push(ActionIntent::ContinueDispatch {
                    did: dispatch_id(tid),
                    input: PendingInput {
                        seq: InputSeq(1),
                        input_id: InputId(format!("{}.reply", tid.0)),
                        business_digest: Digest("fixture-reply".into()),
                        payload: payload.clone(),
                    },
                });
                actions.push(show(tid, payload.clone()));
                actions.push(ActionIntent::SetStatus {
                    tid: tid.clone(),
                    status: TaskStatus::Working,
                    interactions: vec![],
                });
            } else if let RunnerInput::Agent { tid, event } = input
                && next_state.value["mode"] == "stream"
            {
                actions.push(show(tid, event.payload.clone()));
            }
        }
        Ok(Decision {
            next_state,
            actions,
            next_deadlines: vec![],
        })
    }
}
pub fn dispatch_id(tid: &Tid) -> Did {
    Did(format!("{}.dispatch", tid.0))
}
pub fn participant() -> ParticipantBinding {
    ParticipantBinding {
        node: NodeId("answer".into()),
        target: AgentBinding {
            agent: AgentId("fixture-agent".into()),
            binding_version: BindingVersion("1".into()),
            credential_ref: CredentialRef("fixture-reference".into()),
        },
        pool: PoolId("fixture-pool".into()),
        capabilities: DownstreamCapabilities {
            idempotent_start: true,
            idempotent_input: true,
            query_by_request: true,
            query_by_task: true,
            subscribe: true,
            resume_events: true,
            cancel: true,
        },
    }
}
fn dispatch_spec(c: &EvaluationContext<'_>, tid: &Tid) -> DispatchSpec {
    let p = &c.spec.participants[0];
    DispatchSpec {
        schema_version: 1,
        sid: c.spec.sid.clone(),
        tid: tid.clone(),
        did: dispatch_id(tid),
        node: p.node.clone(),
        attempt: 1,
        target: p.target.clone(),
        pool: p.pool.clone(),
        capabilities: p.capabilities.clone(),
        request: Payload::Inline(serde_json::json!("fixture request")),
        deadline: TimestampMillis(c.now.0 + 60_000),
    }
}
fn show(tid: &Tid, delta: Payload) -> ActionIntent {
    ActionIntent::Show {
        tid: tid.clone(),
        node: NodeId("answer".into()),
        artifact_id: ArtifactId("answer".into()),
        class: OutputClass::Output,
        update: ArtifactUpdate::AppendText { delta },
    }
}
pub fn initial() -> Checkpoint {
    Checkpoint {
        format_version: 1,
        commit_seq: CommitSeq(0),
        consumed: None,
        tasks: Default::default(),
        runner_state: None,
        dispatch_seq: Default::default(),
        deadlines: vec![],
        deadline_history: Default::default(),
        view_drafts: Default::default(),
        last_event_id: None,
        outbox: None,
    }
}
pub fn scope() -> TaskScope {
    TaskScope {
        tenant: TenantId("singlebox".into()),
        principal: PrincipalId("worker-story".into()),
    }
}
pub fn specification(sid: Sid) -> SessionSpec {
    SessionSpec {
        schema_version: 1,
        sid,
        shard: ShardId(1),
        scope: scope(),
        kind: SessionKind::FsmGroup,
        group: GroupId("fixture".into()),
        workflow: WorkflowRef {
            definition_id: "fixture".into(),
            revision: "1".into(),
            digest: Digest("fixture".into()),
        },
        participants: vec![participant()],
        limits: SessionLimits {
            max_queued_tasks: 32,
            max_task_steps: 100,
            task_timeout_millis: 60_000,
            max_inline_bytes: 65_536,
        },
        digest: Digest("fixture".into()),
        created_at: TimestampMillis(0),
    }
}
pub fn metadata(spec: &SessionSpec, tid: Tid) -> TaskSnapshot {
    TaskSnapshot {
        schema_version: 1,
        scope: spec.scope.clone(),
        sid: spec.sid.clone(),
        tid,
        content: TaskContent {
            status: TaskStatus::Submitted,
            interactions: vec![],
            snapshot: Payload::Inline(serde_json::json!({"format_version":1,"units":[]})),
            result_ref: None,
        },
        output_limits: PHASE_ONE_OUTPUT_LIMITS,
        output_usage: OutputUsage::default(),
        snapshot_event_id: None,
        source_commit: CommitSeq(0),
        created_at: TimestampMillis(0),
        updated_at: TimestampMillis(0),
    }
}
