mod common;
use bcn_runtime_api::{
    application::*,
    port::{Clock, repo::*},
};
use bcn_runtime_model::*;
use common::group::*;
use common::*;
use serde_json::json;
use std::{sync::Arc, sync::atomic::Ordering};
#[tokio::test]
async fn parallel_node_takeover_keeps_only_own_partial_and_resets_before_failure() {
    let f = Fixture::new();
    let g = gateway(&f, json!({"a":bot(),"b":bot()}));
    let w = f.worker("w");
    let task = start_parallel(&f, &g, &w, group_request("parallel")).await;
    let commands = f
        .repo
        .receive(
            &scope(),
            &node("old"),
            CommandConsumerKind::Connector,
            None,
            10,
        )
        .await
        .unwrap()
        .items;
    let mut records = vec![];
    for command in commands {
        let record = f
            .repo
            .claim(&command.command_id, node("old"))
            .await
            .unwrap();
        let record = f
            .repo
            .mark_may_have_sent(&record.fence, record.revision)
            .await
            .unwrap();
        f.repo.ack(&command.command_id, &node("old")).await.unwrap();
        let snapshot = f.repo.read_snapshot(&task.task_id).await.unwrap().unwrap();
        let label = record.context.node_id.clone().unwrap();
        f.repo
            .publish_reset(
                &record.fence,
                ConnectorProjection {
                    task_id: task.task_id.clone(),
                    output: text(&label),
                    expected_cursor: snapshot.cursor,
                },
                EventId::new(scope(), label),
            )
            .await
            .unwrap();
        records.push(record);
    }
    let before = f.repo.read_snapshot(&task.task_id).await.unwrap().unwrap();
    assert_eq!(before.task.output, [text("a"), text("b")].concat());
    let t = Arc::new(Transport::new(vec![]));
    let c = f.connector("new", t.clone());
    for now in [1000, 2000, 3000, 4000] {
        f.clock.set(now);
        c.tick().await.unwrap();
    }
    for record in &records {
        let current = f
            .repo
            .read(&record.fence.dispatch_id)
            .await
            .unwrap()
            .unwrap();
        let DispatchReceipt::Agent {
            event: AgentEvent::FailedWithOutput { output, error },
        } = current.receipt.unwrap()
        else {
            panic!("missing terminal unknown receipt")
        };
        assert_eq!(output, text(record.context.node_id.as_ref().unwrap()));
        assert_eq!(error, RuntimeError::ExecutionUnknown);
    }
    let after = f.repo.read_snapshot(&task.task_id).await.unwrap().unwrap();
    assert!(after.cursor.epoch > before.cursor.epoch);
    assert_eq!(after.task.output, [text("a"), text("b")].concat());
    assert_eq!(t.sends.load(Ordering::SeqCst), 0);
    f.pump(&w, &c, 12).await;
    assert_eq!(
        g.get(caller(), task.task_id).await.unwrap().status,
        TaskStatus::Failed
    );
}
#[tokio::test]
async fn cancellation_unknown_never_becomes_confirmed_cancelled() {
    let f = Fixture::new();
    let g = f.gateway();
    let task = g.submit(caller(), request("cancel")).await.unwrap();
    let w = f.worker("w");
    let t = Arc::new(Transport::new(vec![Ok(AgentEvent::Accepted {
        handle: handle(),
    })]));
    let c = f.connector("c", t);
    f.pump(&w, &c, 6).await;
    let returned = g
        .cancel(
            caller(),
            CancelCommand {
                task_id: task.task_id.clone(),
                operation_id: OperationId::new(scope(), "cancel"),
            },
        )
        .await
        .unwrap();
    assert_eq!(returned.status, TaskStatus::Working);
    f.pump(&w, &c, 15).await;
    let done = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(done.status, TaskStatus::Failed);
    assert_eq!(done.error, Some(RuntimeError::CancellationUnknown));
}
#[tokio::test]
async fn explicit_interactions_support_two_parallel_human_nodes_and_retry_after_completion() {
    let f = Fixture::new();
    let g = gateway(&f, json!({"a":human(),"b":human()}));
    let w = f.worker("w");
    let task = start_parallel(&f, &g, &w, group_request("humans")).await;
    let cp = f.repo.load(&task.session_id).await.unwrap().unwrap();
    let interactions: Vec<_> = cp
        .state
        .current_task
        .unwrap()
        .nodes
        .values()
        .filter_map(|n| n.interaction_id.clone())
        .collect();
    assert_eq!(interactions.len(), 2);
    let mut reply = group_request("reply-a");
    reply.message.task_id = Some(task.task_id.clone());
    assert!(matches!(
        g.submit(caller(), reply.clone()).await,
        Err(RuntimeError::InvalidInput(_))
    ));
    reply.interaction_id = Some(interactions[0].clone());
    g.submit(caller(), reply.clone()).await.unwrap();
    w.step(&task.session_id).await.unwrap();
    let mut second = group_request("reply-b");
    second.message.task_id = Some(task.task_id.clone());
    second.interaction_id = Some(interactions[1].clone());
    g.submit(caller(), second).await.unwrap();
    let t = Arc::new(Transport::new(vec![]));
    let c = f.connector("c", t);
    f.pump(&w, &c, 6).await;
    let mut join = group_request("join-reply");
    join.message.task_id = Some(task.task_id.clone());
    g.submit(caller(), join).await.unwrap();
    f.pump(&w, &c, 6).await;
    assert_eq!(
        g.get(caller(), task.task_id).await.unwrap().status,
        TaskStatus::Completed
    );
    f.clock.set(Clock::now_ms(f.clock.as_ref()) + 1000);
    assert_eq!(
        g.submit(caller(), reply).await.unwrap().status,
        TaskStatus::Completed
    );
}
#[tokio::test]
async fn accepted_fanout_that_exceeds_checkpoint_budget_saves_explicit_failure() {
    let f = Fixture::new();
    let mut nodes = serde_json::Map::new();
    for n in 0..30 {
        nodes.insert(format!("node{n}"), bot());
    }
    let g = gateway(&f, serde_json::Value::Object(nodes));
    let mut request = group_request("budget");
    request.message.parts = text(&"x".repeat(60000));
    let w = f.worker("w");
    let task = start_parallel(&f, &g, &w, request).await;
    let t = Arc::new(Transport::new(vec![]));
    let c = f.connector("c", t.clone());
    f.pump(&w, &c, 12).await;
    let done = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(done.status, TaskStatus::Failed);
    assert_eq!(done.error, Some(RuntimeError::CapacityExceeded));
    assert_eq!(t.sends.load(Ordering::SeqCst), 0);
    assert!(
        f.repo
            .read_batch(&task.session_id, 0, 100)
            .await
            .unwrap()
            .is_empty()
    );
}
#[tokio::test]
async fn known_handle_recovers_by_subscription_when_query_is_unsupported() {
    use async_trait::async_trait;
    use bcn_runtime_api::port::{AgentCatalog, AgentConnector, AgentEventStream};
    use bcn_runtime_app::{DefaultConnector, DefaultGateway};
    struct SubscribeOnly;
    #[async_trait]
    impl AgentConnector for SubscribeOnly {
        fn capabilities(&self) -> ConnectorCapabilities {
            let mut cap = binding().capabilities;
            cap.query = false;
            cap
        }
        async fn subscribe_to_task(&self, h: ExecutionHandle) -> ConnectorResult<AgentEventStream> {
            assert_eq!(h, handle());
            Ok(Box::pin(futures::stream::iter(vec![Ok(
                AgentEvent::Completed {
                    output: text("subscribed"),
                },
            )])))
        }
    }
    struct SubscribeCatalog;
    #[async_trait]
    impl AgentCatalog for SubscribeCatalog {
        async fn resolve(
            &self,
            c: &CallerContext,
            t: &ExecutionTarget,
            s: &SessionId,
        ) -> RuntimeResult<SessionSpec> {
            let mut spec = Catalog.resolve(c, t, s).await?;
            spec.bindings
                .values_mut()
                .next()
                .unwrap()
                .capabilities
                .query = false;
            Ok(spec)
        }
    }
    let f = Fixture::new();
    let g = DefaultGateway::new(
        f.ports.clone(),
        f.runners(),
        Arc::new(SubscribeCatalog),
        vec![Arc::new(Allow)],
        f.clock.clone(),
        true,
    )
    .unwrap();
    let task = g.submit(caller(), request("subscribe")).await.unwrap();
    let w = f.worker("w");
    w.step(&task.session_id).await.unwrap();
    let command = f
        .repo
        .receive(
            &scope(),
            &node("old"),
            CommandConsumerKind::Connector,
            None,
            1,
        )
        .await
        .unwrap()
        .items
        .remove(0);
    let r = f
        .repo
        .claim(&command.command_id, node("old"))
        .await
        .unwrap();
    let r = f
        .repo
        .mark_may_have_sent(&r.fence, r.revision)
        .await
        .unwrap();
    f.repo
        .save_handle(&r.fence, r.revision, handle())
        .await
        .unwrap();
    f.repo.ack(&command.command_id, &node("old")).await.unwrap();
    let mut binding = binding();
    binding.capabilities.query = false;
    let c = DefaultConnector::new(
        node("new"),
        f.ports.clone(),
        vec![(binding, Arc::new(SubscribeOnly))],
        f.clock.clone(),
    )
    .unwrap();
    f.pump(&w, &c, 24).await;
    let done = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(done.status, TaskStatus::Completed);
    assert_eq!(done.output, text("subscribed"));
}
#[tokio::test]
async fn parallel_timer_inputs_freeze_own_partial_before_restart_and_ignore_later_projection() {
    use common::faults::SessionFault;
    use std::sync::atomic::AtomicBool;
    let mut f = Fixture::new();
    let faulty = Arc::new(SessionFault {
        inner: f.repo.clone(),
        commit: AtomicBool::new(false),
    });
    f.ports.sessions = faulty.clone();
    let g = gateway(&f, json!({"a":bot(),"b":bot()}));
    let w = f.worker("w");
    let task = start_parallel(&f, &g, &w, group_request("parallel-timeout")).await;
    let commands = f
        .repo
        .receive(
            &scope(),
            &node("c"),
            CommandConsumerKind::Connector,
            None,
            10,
        )
        .await
        .unwrap()
        .items;
    let mut records = vec![];
    for command in commands {
        let r = f.repo.claim(&command.command_id, node("c")).await.unwrap();
        let r = f
            .repo
            .mark_may_have_sent(&r.fence, r.revision)
            .await
            .unwrap();
        let label = r.context.node_id.clone().unwrap();
        let cursor = f
            .repo
            .read_snapshot(&task.task_id)
            .await
            .unwrap()
            .unwrap()
            .cursor;
        f.repo
            .publish_reset(
                &r.fence,
                ConnectorProjection {
                    task_id: task.task_id.clone(),
                    output: text(&label),
                    expected_cursor: cursor,
                },
                EventId::new(scope(), label),
            )
            .await
            .unwrap();
        records.push(r);
    }
    f.clock.set(70000);
    faulty.commit.store(true, Ordering::SeqCst);
    assert!(w.step(&task.session_id).await.is_err());
    let cp = f.repo.load(&task.session_id).await.unwrap().unwrap();
    let timers = f
        .repo
        .read_batch(&task.session_id, cp.consumed_input, 10)
        .await
        .unwrap();
    assert_eq!(timers.len(), 2);
    for input in &timers {
        assert!(
            matches!(&input.input.kind,SessionInputKind::TimerFired{output,..} if output==&text("a") || output==&text("b"))
        );
    }
    let cursor = f
        .repo
        .read_snapshot(&task.task_id)
        .await
        .unwrap()
        .unwrap()
        .cursor;
    f.repo
        .publish_reset(
            &records[0].fence,
            ConnectorProjection {
                task_id: task.task_id.clone(),
                output: text("too late"),
                expected_cursor: cursor,
            },
            EventId::new(scope(), "late"),
        )
        .await
        .unwrap();
    f.repo
        .transfer_shard(cp.fence, node("new-w"))
        .await
        .unwrap();
    let new = f.worker("new-w");
    for _ in 0..6 {
        new.step(&task.session_id).await.unwrap();
        new.tick().await.unwrap();
    }
    let done = g.get(caller(), task.task_id).await.unwrap();
    assert_eq!(done.status, TaskStatus::Failed);
    assert_eq!(done.output, [text("a"), text("b")].concat());
}
