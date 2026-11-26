#![allow(
    clippy::unwrap_used,
    reason = "real-provider temporary output assertions"
)]
mod processor_support;
mod support;
use bcs_dist_model::*;
use bcs_service_api::{
    core::dist_runner::*,
    port::repo::distributed::{dispatch::*, inbox::*, task_feed::*},
};
use futures::StreamExt;
use processor_support::*;
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
struct WaitingRunner(AtomicUsize);
impl DistRunnerCoreService for WaitingRunner {
    fn init(&self, _: &EvaluationContext<'_>) -> Result<RunnerState, RunnerError> {
        Ok(RunnerState {
            format_version: 1,
            value: serde_json::json!({"private": "unchanged"}),
        })
    }
    fn reduce(
        &self,
        c: &EvaluationContext<'_>,
        state: &RunnerState,
        inputs: &[RunnerInput],
    ) -> Result<Decision, RunnerError> {
        self.0.fetch_add(1, Ordering::SeqCst);
        let RunnerInput::Start { tid, .. } = &inputs[0] else {
            panic!("Thinking reached pure Runner")
        };
        let binding = &c.spec.participants[0];
        Ok(Decision {
            next_state: state.clone(),
            next_deadlines: vec![],
            actions: vec![ActionIntent::StartDispatch(DispatchSpec {
                schema_version: 1,
                sid: c.spec.sid.clone(),
                tid: tid.clone(),
                did: Did(format!("{}.dispatch", tid.0)),
                node: binding.node.clone(),
                attempt: 1,
                target: binding.target.clone(),
                pool: binding.pool.clone(),
                capabilities: binding.capabilities.clone(),
                request: Payload::Inline(serde_json::Value::Null),
                deadline: TimestampMillis(60_000),
            })],
        })
    }
}
#[tokio::test]
async fn thinking_is_delivered_once_without_runner_snapshot_or_usage_changes() {
    let runner = Arc::new(WaitingRunner(AtomicUsize::new(0)));
    let f = Fixture::new(runner.clone()).await;
    let mut definition = spec();
    definition.participants.push(ParticipantBinding {
        node: NodeId("node".into()),
        target: AgentBinding {
            agent: AgentId("agent".into()),
            binding_version: BindingVersion("1".into()),
            credential_ref: CredentialRef("opaque".into()),
        },
        pool: PoolId("pool".into()),
        capabilities: DownstreamCapabilities {
            idempotent_start: true,
            idempotent_input: true,
            query_by_request: true,
            query_by_task: true,
            subscribe: true,
            resume_events: true,
            cancel: true,
            isolated_memory: true,
            persistent_dedupe: true,
        },
    });
    let tid = f.admit_for(definition, "thinking").await;
    f.warm().await;
    let did = Did(format!("{}.dispatch", tid.0));
    for _ in 0..80 {
        let _ = f.step().await;
        if f.dispatch.get(&did).await.unwrap().is_some()
            && f.snapshot(&tid).await.content.status == TaskStatus::Working
        {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    let before = f.checkpoint().await;
    let snapshot = f.snapshot(&tid).await;
    let RestoredTask::Live { cursor, .. } =
        f.feed.restore(&spec().scope, &tid).await.unwrap().unwrap()
    else {
        panic!()
    };
    let input = AppendAgentEvent {
        sid: spec().sid,
        tid: tid.clone(),
        event: AgentEvent {
            did: did.clone(),
            holder_epoch: HolderEpoch(1),
            seq: InputSeq(1),
            kind: AgentEventKind::Thinking,
            remote_event_id: None,
            payload: Payload::Inline(serde_json::json!("working through it")),
        },
    };
    f.inbox.append_stream(input.clone()).await.unwrap();
    f.inbox.append_stream(input).await.unwrap();
    for _ in 0..120 {
        let _ = f.step().await;
        if f.checkpoint()
            .await
            .dispatch_seq
            .get(&did)
            .is_some_and(|s| s.sequence == InputSeq(1))
        {
            // Commit precedes effect replay; let the exact append finish.
            for _ in 0..12 {
                let _ = f.step().await;
            }
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    assert_eq!(runner.0.load(Ordering::SeqCst), 1);
    let after = f.checkpoint().await;
    assert_eq!(after.runner_state, before.runner_state);
    assert_eq!(after.tasks[0].steps, before.tasks[0].steps);
    assert_eq!(after.tasks[0].output_usage, before.tasks[0].output_usage);
    assert_eq!(after.tasks[0].publication, before.tasks[0].publication);
    assert_eq!(f.snapshot(&tid).await, snapshot);
    let mut follow = f.feed.follow(spec().scope, cursor);
    let item = tokio::time::timeout(Duration::from_millis(100), follow.next())
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    let FollowItem::Events { items, .. } = item else {
        panic!()
    };
    assert_eq!(items.len(), 1);
    assert!(matches!(
        items[0].body,
        OutputBody::Artifact {
            class: OutputClass::Thinking,
            ..
        }
    ));
    let RestoredTask::Live { view, .. } =
        f.feed.restore(&spec().scope, &tid).await.unwrap().unwrap()
    else {
        panic!()
    };
    assert_eq!(view.content.snapshot, snapshot.content.snapshot);
}

#[tokio::test]
async fn thinking_above_the_inline_threshold_does_not_block_the_session() {
    let runner = Arc::new(WaitingRunner(AtomicUsize::new(0)));
    let f = Fixture::new(runner.clone()).await;
    let mut definition = spec();
    definition.participants.push(ParticipantBinding {
        node: NodeId("node".into()),
        target: AgentBinding {
            agent: AgentId("agent".into()),
            binding_version: BindingVersion("1".into()),
            credential_ref: CredentialRef("opaque".into()),
        },
        pool: PoolId("pool".into()),
        capabilities: DownstreamCapabilities {
            idempotent_start: true,
            idempotent_input: true,
            query_by_request: true,
            query_by_task: true,
            subscribe: true,
            resume_events: true,
            cancel: true,
            isolated_memory: true,
            persistent_dedupe: true,
        },
    });
    let mut initial = metadata();
    initial.output_limits.inline_threshold_bytes = 128;
    let tid = initial.tid.clone();
    f.inbox
        .admit(AdmitRequest {
            scope: definition.scope.clone(),
            input: InputEnvelope {
                schema_version: 1,
                sid: definition.sid.clone(),
                tid: tid.clone(),
                input_id: InputId("thinking-quota".into()),
                business_digest: Digest(String::new()),
                body: InputBody::User {
                    payload: Payload::Inline(serde_json::Value::Null),
                },
            },
            create_session: Some(definition),
            create_task: Some(initial),
        })
        .await
        .unwrap();
    f.warm().await;
    let did = Did(format!("{}.dispatch", tid.0));
    for _ in 0..80 {
        let _ = f.step().await;
        if f.dispatch.get(&did).await.unwrap().is_some()
            && f.snapshot(&tid).await.content.status == TaskStatus::Working
        {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    let input = AppendAgentEvent {
        sid: spec().sid,
        tid: tid.clone(),
        event: AgentEvent {
            did: did.clone(),
            holder_epoch: HolderEpoch(1),
            seq: InputSeq(1),
            kind: AgentEventKind::Thinking,
            remote_event_id: None,
            payload: Payload::Inline(serde_json::json!("x".repeat(256))),
        },
    };
    f.inbox.append_stream(input.clone()).await.unwrap();
    f.inbox.append_stream(input).await.unwrap();
    for _ in 0..120 {
        let _ = f.step().await;
        if f.checkpoint()
            .await
            .dispatch_seq
            .get(&did)
            .is_some_and(|s| s.sequence == InputSeq(1))
        {
            // Commit precedes effect replay; let the exact append finish.
            for _ in 0..12 {
                let _ = f.step().await;
            }
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    use bcs_service_api::port::repo::distributed::control::*;
    let control = f
        .coordination
        .load_control(&spec().sid)
        .await
        .unwrap()
        .unwrap();
    assert!(
        matches!(control.control.mode, ControlMode::Normal),
        "{:?}",
        control.control.mode
    );
    assert_eq!(
        f.checkpoint()
            .await
            .dispatch_seq
            .get(&did)
            .unwrap()
            .sequence,
        InputSeq(1)
    );
    assert_eq!(runner.0.load(Ordering::SeqCst), 1);
}
