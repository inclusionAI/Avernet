use crate::lifecycle_support::{Mode, Runner, control, dispatch_id, drive, session};
use crate::processor_support::{Fixture, metadata};
use bcs_dist_model::*;
use bcs_service_api::port::repo::distributed::{control::*, dispatch::*, inbox::*, task_feed::*};
use futures::StreamExt;
use std::time::Duration;

#[tokio::test(start_paused = true)]
async fn thinking_advances_sequence_without_changing_durable_output_or_blocking_the_session() {
    let runner = Runner::new(Mode::Wait);
    let f = Fixture::new(runner.clone()).await;
    let mut task = metadata();
    task.output_limits.inline_threshold_bytes = 128;
    let tid = task.tid.clone();
    let did = dispatch_id(&tid);
    let scope = session().scope;
    let start = AdmitRequest {
        create_task: Some(task),
        create_session: Some(session()),
        ..control(&tid, "thinking", InputBody::User { payload: Payload::Inline(serde_json::Value::Null) })
    };
    assert!(matches!(f.inbox.admit(start).await.unwrap(), AdmitOutcome::Accepted(_)));
    f.warm().await;
    for _ in 0..80 {
        drive(&f, &f.processor, 1).await;
        if f.dispatch.get(&did).await.unwrap().is_some()
            && f.snapshot(&tid).await.content.status == TaskStatus::Working
        {
            break;
        }
    }
    let before = f.checkpoint().await;
    let snapshot = f.snapshot(&tid).await;
    assert_eq!(snapshot.content.status, TaskStatus::Working);
    let Some(RestoredTask::Live { cursor, .. }) = f.feed.restore(&scope, &tid).await.unwrap() else {
        panic!("working task must have a live cursor");
    };
    let mut follow = f.feed.follow(scope.clone(), cursor);

    // The same dispatch receives a small update and one above the inline threshold.
    // Duplicate each input; wait for its consumption and effect replay, not a fixed tick count.
    for (seq, text) in [(1, "working through it".to_owned()), (2, "x".repeat(256))] {
        let input = AppendAgentEvent {
            sid: session().sid,
            tid: tid.clone(),
            event: AgentEvent {
                did: did.clone(),
                holder_epoch: HolderEpoch(1),
                seq: InputSeq(seq),
                kind: AgentEventKind::Thinking,
                remote_event_id: None,
                payload: Payload::Inline(serde_json::json!(text)),
            },
        };
        f.inbox.append_stream(input.clone()).await.unwrap();
        let last = f.inbox.append_stream(input).await.unwrap();
        for _ in 0..120 {
            drive(&f, &f.processor, 1).await;
            if f.checkpoint().await.consumed.as_ref() == Some(&last)
                && f.processor.observations().await.unwrap().pending_outboxes == 0
            {
                break;
            }
        }
        assert_eq!(f.processor.observations().await.unwrap().pending_outboxes, 0);
        let after = f.checkpoint().await;
        assert_eq!(after.consumed, Some(last));
        assert_eq!(after.dispatch_seq[&did].sequence, InputSeq(seq));
        assert_eq!(runner.calls(), 1);
        assert_eq!(after.runner_state, before.runner_state);
        assert_eq!(after.tasks[0].steps, before.tasks[0].steps);
        assert_eq!(after.tasks[0].publication, before.tasks[0].publication);
        assert_eq!(after.tasks[0].output_usage, before.tasks[0].output_usage);
        assert_eq!(f.snapshot(&tid).await, snapshot);
        let item = tokio::time::timeout(Duration::from_millis(100), follow.next()).await.unwrap();
        let Some(Ok(FollowItem::Events { items, .. })) = item else {
            panic!("missing Thinking event for sequence {seq}: {item:?}");
        };
        assert_eq!(items.len(), 1);
        assert!(matches!(&items[0].body, OutputBody::Artifact {
            class: OutputClass::Thinking, update: ArtifactUpdate::Upsert { content: Payload::Inline(value) }, ..
        } if value.as_str() == Some(text.as_str())));
    }
    let Some(RestoredTask::Live { view, .. }) = f.feed.restore(&scope, &tid).await.unwrap() else {
        panic!("Thinking must leave the task live");
    };
    assert_eq!(view.content.snapshot, snapshot.content.snapshot);
    let state = f.coordination.load_control(&session().sid).await.unwrap().unwrap();
    assert!(matches!(state.control.mode, ControlMode::Normal));
}
