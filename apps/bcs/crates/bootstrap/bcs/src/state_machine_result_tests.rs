use super::*;
use bcs_domain::{MessageAudience, MessageVisibilityDomain};
use bcs_message_store::MemoryMessageRepo;
use bcs_test_support::NoopMessageFlowService;
use bcs_service_api::{ManagedMessageDeliveryService, ManagedDeliveryPreparationService, BotDeliveryPort};
use bcs_domain::{GroupStrategy, ParticipantRole, SenderType, NewMessage};
use bcs_domain::message_delivery::MessageDeliveryStatus as Status;
use std::collections::BTreeMap;

#[tokio::test]
async fn unconfigured_message_flow_does_not_prewrite_chat_result_history() {
    let publisher = MessageFlowStateMachineResultPublisher::new(Arc::new(NoopMessageFlowService));
    let error = publisher.publish_state_machine_result(publication_command()).await.unwrap_err();
    assert!(error.to_string().contains("state-machine result publication"));
}

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod flow_support;
#[path = "../../../services/bcs-message-flow/tests/support/session.rs"]
mod session_support;

async fn publication_fixture(queued: bool) -> (
    MessageFlowStateMachineResultPublisher,
    Arc<MemoryMessageRepo>,
    Arc<bcs_message_flow::managed_delivery::ManagedMessageDelivery>,
    Arc<flow_support::RecordingBotDelivery>,
    Arc<bcs_message_flow::BcsMessageFlow>,
) {
    publication_fixture_with_options(GroupStrategy::Chat, queued.then(|| BTreeMap::from([
        ("bot-driver".into(), 10), ("bot-observer".into(), 10),
    ]))).await
}

async fn publication_fixture_with_options(strategy: GroupStrategy, limits: Option<BTreeMap<String, u32>>) -> (
    MessageFlowStateMachineResultPublisher,
    Arc<MemoryMessageRepo>,
    Arc<bcs_message_flow::managed_delivery::ManagedMessageDelivery>,
    Arc<flow_support::RecordingBotDelivery>,
    Arc<bcs_message_flow::BcsMessageFlow>,
) {
    let support = flow_support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = strategy;
    if strategy == GroupStrategy::ManagerWorker {
        for participant in &mut group.participants {
            if participant.bot_uuid == "bot-driver" { participant.role = ParticipantRole::Manager; }
            if participant.bot_uuid == "bot-observer" { participant.role = ParticipantRole::Worker; }
        }
    }
    support.group.upsert(group.clone()).await.unwrap();
    let messages = Arc::new(MemoryMessageRepo::new());
    let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(messages.clone()));
    let flow = bcs_message_flow::BcsMessageFlow::new(
        support.group, Arc::new(bcs_routing::MessageRouter::new()), support.registry,
        support.bot_delivery.clone(), support.frontend_delivery,
    ).with_message_repo(messages.clone())
        .with_bot_run_context(Arc::new(bcs_message_flow::MemoryBotRunContextStore::new()))
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(
            session_support::test_session("group-1:result", "group-1", group.participants),
        )));
    let flow = if let Some(limits) = limits {
        flow.with_managed_deliveries(managed.clone()).with_group_delivery_limits(limits)
    } else { flow };
    let flow = Arc::new(flow);
    (MessageFlowStateMachineResultPublisher::new(flow.clone()), messages,
        managed, support.bot_delivery, flow)
}

fn publication_command() -> StateMachineResultPublishCommand {
    StateMachineResultPublishCommand {
        run_id: "one-shot-result".into(), group_id: "group-1".into(),
        session_id: "group-1:result".into(), sender_bot_id: "bot-observer".into(),
        content: "状态机最终结果".into(), created_at_ms: 100,
    }
}

#[tokio::test]
async fn queued_chat_result_creates_delivery_instead_of_an_orphan_history_row() {
    let (publisher, messages, managed, _, _) = publication_fixture(true).await;
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    let rows = managed.snapshot(None).await.unwrap();
    assert!(rows.iter().any(|row| row.target_bot_id == "bot-driver"
        && row.state.kind == bcs_service_api::DeliveryType::Send), "manager/driver must receive a queued Send");
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 1);
}

#[tokio::test]
async fn legacy_chat_result_preserves_original_history_and_delivers_to_driver() {
    let (publisher, messages, _, delivery, _) = publication_fixture(false).await;
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    assert!(delivery.targets().await.iter().any(|target| target.bot_id() == "bot-driver"));
    let saved = messages.get_message_by_id("group-1:result", "state-machine-result:one-shot-result")
        .await.unwrap().unwrap();
    assert_eq!(saved.content, serde_json::json!("状态机最终结果"));
    assert_eq!(saved.run_id, "one-shot-result");
    assert_eq!(saved.created_at, 100);
    assert_eq!(saved.visibility_domain, Some(MessageVisibilityDomain::StateMachine));
    assert_eq!(saved.audience, Some(MessageAudience::Public));
}

#[tokio::test]
async fn queued_result_replays_without_new_rows_and_preserves_history_shape() {
    let (publisher, messages, managed, delivery, flow) = publication_fixture(true).await;
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    let rows = managed.snapshot(None).await.unwrap();
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    assert_eq!(managed.snapshot(None).await.unwrap().len(), rows.len());
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 1);
    assert!(delivery.frames().await.is_empty(), "admission must not invoke legacy transport");
    let saved = messages.run_chat_segments("group-1:result", "bot-observer", "one-shot-result")
        .await.unwrap();
    assert_eq!(saved.len(), 1);
    assert_eq!(saved[0].content, serde_json::json!("状态机最终结果"));
    assert_eq!(saved[0].created_at, 100);
    assert_eq!(saved[0].visibility_domain, Some(MessageVisibilityDomain::StateMachine));
    assert_eq!(saved[0].audience, Some(MessageAudience::Public));
    assert!(messages.list_state_machine_history("group-1", "group-1:result", None, None, 100)
        .await.unwrap().messages.is_empty(), "published chat is ordinary history, not a panel/output projection");
    let preparer = bcs_message_flow::queued_group::QueuedGroupPreparation {
        flow: Arc::downgrade(&flow), deliveries: managed,
    };
    let send = rows.iter().find(|row| row.target_bot_id == "bot-driver").unwrap();
    let prepared = preparer.prepare(send).await.unwrap();
    let wire = serde_json::to_value(&prepared.command.frame).unwrap();
    assert_eq!(wire["method"], "chat.send");
    assert!(wire.to_string().contains("状态机最终结果"));
    assert!(delivery.deliver(prepared.command).await.unwrap().delivered);
    assert_eq!(delivery.targets().await[0].bot_id(), "bot-driver");
}

#[tokio::test]
async fn manager_worker_result_reaches_manager_with_queue_on_or_off() {
    for queued in [false, true] {
        let limits = queued.then(|| BTreeMap::from([("bot-driver".into(), 10)]));
        let (publisher, messages, managed, delivery, flow) =
            publication_fixture_with_options(GroupStrategy::ManagerWorker, limits).await;
        publisher.publish_state_machine_result(publication_command()).await.unwrap();
        if queued {
            let rows = managed.snapshot(None).await.unwrap();
            assert_eq!(rows.len(), 1);
            assert_eq!(rows[0].target_bot_id, "bot-driver");
            assert_eq!(rows[0].state.kind, bcs_service_api::DeliveryType::Send);
            let preparer = bcs_message_flow::queued_group::QueuedGroupPreparation {
                flow: Arc::downgrade(&flow), deliveries: managed,
            };
            let prepared = preparer.prepare(&rows[0]).await.unwrap();
            assert!(delivery.deliver(prepared.command).await.unwrap().delivered);
        }
        assert_eq!(delivery.targets().await[0].bot_id(), "bot-driver");
        let saved = messages.run_chat_segments("group-1:result", "bot-observer", "one-shot-result")
            .await.unwrap();
        assert_eq!(saved.len(), 1);
        assert_eq!(saved[0].visibility_domain, Some(MessageVisibilityDomain::StateMachine));
    }
}

#[tokio::test]
async fn result_conflicts_are_rejected_before_legacy_delivery_or_duplicate_ack() {
    for queued in [false, true] {
        let (publisher, messages, managed, delivery, _) = publication_fixture(queued).await;
        publisher.publish_state_machine_result(publication_command()).await.unwrap();
        let deliveries = delivery.frames().await.len();
        let admitted = managed.snapshot(None).await.unwrap().len();
        for field in ["content", "timestamp", "sender"] {
            let mut changed = publication_command();
            match field {
                "content" => changed.content = "changed".into(),
                "timestamp" => changed.created_at_ms += 1,
                _ => changed.sender_bot_id = "bot-driver".into(),
            }
            assert!(publisher.publish_state_machine_result(changed).await.is_err());
        }
        assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 1);
        assert_eq!(delivery.frames().await.len(), deliveries);
        assert_eq!(managed.snapshot(None).await.unwrap().len(), admitted);
    }
}

#[tokio::test]
async fn orphan_history_is_an_error_not_a_successful_queue_publication() {
    let (publisher, messages, managed, delivery, _) = publication_fixture(true).await;
    let cmd = publication_command();
    messages.append_message_with_id("state-machine-result:one-shot-result".into(), NewMessage {
        group_id: cmd.group_id, session_id: cmd.session_id, run_id: cmd.run_id,
        sender_id: cmd.sender_bot_id, sender_type: SenderType::Bot, message_type: "chat".into(),
        content: serde_json::json!(cmd.content), client_msg_id: Some("state-machine-result:one-shot-result".into()),
        owner_bot_id: None, created_at: cmd.created_at_ms,
        visibility_domain: MessageVisibilityDomain::StateMachine, audience: Some(MessageAudience::Public),
    }).await.unwrap();
    let error = publisher.publish_state_machine_result(publication_command()).await.unwrap_err();
    assert!(error.to_string().contains("no deliveries"));
    assert!(managed.snapshot(None).await.unwrap().is_empty());
    assert!(delivery.frames().await.is_empty());
}

#[tokio::test]
async fn rejected_queue_and_failed_admission_do_not_acknowledge_publication() {
    let (publisher, messages, managed, delivery, _) = publication_fixture_with_options(
        GroupStrategy::Chat, Some(BTreeMap::from([("bot-driver".into(), 0)])),
    ).await;
    assert!(publisher.publish_state_machine_result(publication_command()).await.is_err());
    assert!(managed.snapshot(None).await.unwrap().iter().any(|row| row.state.status == Status::RejectedCapacity));
    assert!(delivery.frames().await.is_empty());
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 1);

    let (publisher, messages, managed, delivery, _) = publication_fixture(true).await;
    managed.set_admission_available(false);
    assert!(publisher.publish_state_machine_result(publication_command()).await.is_err());
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 0);
    assert!(delivery.frames().await.is_empty());
}

#[tokio::test]
async fn legacy_transport_errors_and_explicit_rejections_are_publication_errors() {
    for rejection in [false, true] {
        let (publisher, _, _, delivery, _) = publication_fixture(false).await;
        if rejection { delivery.not_delivered_for("bot-driver").await; }
        else { delivery.fail_for("bot-driver").await; }
        assert!(publisher.publish_state_machine_result(publication_command()).await.is_err());
    }
}

#[tokio::test]
async fn mixed_result_delivers_legacy_targets_once_and_rejects_ambiguous_replay() {
    let (publisher, messages, managed, delivery, _) = publication_fixture_with_options(
        GroupStrategy::Chat, Some(BTreeMap::from([("bot-driver".into(), 10)])),
    ).await;
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    assert_eq!(managed.snapshot(None).await.unwrap().len(), 1);
    assert_eq!(delivery.targets().await.len(), 1);
    assert_eq!(delivery.targets().await[0].bot_id(), "bot-observer");
    assert!(publisher.publish_state_machine_result(publication_command()).await.is_err());
    assert_eq!(delivery.targets().await.len(), 1, "ambiguous legacy recipients must not be replayed");
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 1);
}

#[tokio::test]
async fn mixed_result_does_not_hide_legacy_failure_after_queue_commit() {
    let (publisher, _, managed, delivery, _) = publication_fixture_with_options(
        GroupStrategy::Chat, Some(BTreeMap::from([("bot-driver".into(), 10)])),
    ).await;
    delivery.not_delivered_for("bot-observer").await;
    assert!(publisher.publish_state_machine_result(publication_command()).await.is_err());
    assert_eq!(managed.snapshot(None).await.unwrap().len(), 1);
}

#[tokio::test]
async fn long_run_identity_uses_the_same_physical_id_as_history_repair() {
    for queued in [false, true] {
        let (publisher, messages, _, _, _) = publication_fixture(queued).await;
        let mut cmd = publication_command();
        cmd.run_id = "r".repeat(90);
        let key = format!("state-machine-result:{}", cmd.run_id);
        let id = bcs_domain::state_machine_history::physical_message_id(&key);
        publisher.publish_state_machine_result(cmd).await.unwrap();
        let saved = messages.get_message_by_id("group-1:result", &id).await.unwrap().unwrap();
        assert_eq!(saved.client_msg_id.as_deref(), Some(key.as_str()));
    }
}

#[tokio::test]
async fn queued_result_string_can_be_bound_as_context_for_the_next_send() {
    let (publisher, _, managed, _, flow) = publication_fixture(true).await;
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    let mut next = publication_command();
    next.run_id = "next-result".into();
    next.content = "@Observer 检查下一条结果".into();
    publisher.publish_state_machine_result(next).await.unwrap();
    let rows = managed.snapshot(None).await.unwrap();
    let observer_send = rows.iter().find(|row| row.target_bot_id == "bot-observer"
        && row.state.kind == bcs_service_api::DeliveryType::Send).unwrap();
    assert!(rows.iter().any(|row| row.target_bot_id == "bot-observer"
        && row.state.status == Status::Bound));
    let preparer = bcs_message_flow::queued_group::QueuedGroupPreparation {
        flow: Arc::downgrade(&flow), deliveries: managed,
    };
    let prepared = preparer.prepare(observer_send).await.unwrap();
    let wire = serde_json::to_value(&prepared.command.frame).unwrap().to_string();
    assert!(wire.contains("状态机最终结果"));
    assert!(wire.contains("检查下一条结果"));
}

struct ResultEventFactory;

impl bcs_service_api::port::EventRecordFactoryPort for ResultEventFactory {
    fn prepare(&self, mut event: bcs_service_api::port::NewEvent) -> std::result::Result<
        Option<bcs_service_api::port::repo::AppendEventRecord>, bcs_service_api::port::EventRecordError,
    > {
        event.event_id = "evt_result_test".into();
        Ok(Some(bcs_service_api::port::repo::AppendEventRecord {
            event, env: "dev".into(), recorded_at: "2026-10-16T00:00:00.000Z".into(),
            retention_until_ms: u64::MAX,
        }))
    }
}

#[tokio::test]
async fn result_history_and_message_event_share_the_canonical_identity() {
    for queued in [false, true] {
        let (publisher, _, _, _, flow) = publication_fixture(false).await;
        drop(publisher);
        let mut flow = Arc::try_unwrap(flow).ok().unwrap();
        let events = Arc::new(bcs_event_store::MemoryEventStore::new());
        let messages = Arc::new(MemoryMessageRepo::new().with_environment("dev".into())
            .with_event_store(events.clone()));
        let managed = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(messages.clone()));
        flow.message_repo = Some(messages.clone());
        flow.event_record_factory = Some(Arc::new(ResultEventFactory));
        if queued {
            flow.managed_deliveries = Some(managed);
            flow.group_delivery_limits = BTreeMap::from([
                ("bot-driver".into(), 10), ("bot-observer".into(), 10),
            ]);
        }
        let publisher = MessageFlowStateMachineResultPublisher::new(Arc::new(flow));
        publisher.publish_state_machine_result(publication_command()).await.unwrap();
        let saved = messages.get_message_by_id("group-1:result", "state-machine-result:one-shot-result")
            .await.unwrap().unwrap();
        assert_eq!(saved.content, serde_json::json!("状态机最终结果"));
        assert_eq!(saved.run_id, "one-shot-result");
        assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 1);
        use bcs_service_api::port::repo::EventRepoPort;
        let event = events.get_event("evt_result_test", "dev").await.unwrap().unwrap();
        assert_eq!(event.envelope.subject.id, saved.message_id);
        assert_eq!(event.envelope.event_type, "message.created");
        assert_eq!(event.envelope.data["run_id"], "one-shot-result");
        assert_eq!(event.envelope.data["content"]["json"], saved.content);
    }
}

#[tokio::test]
async fn configured_queue_with_unmanaged_targets_preserves_legacy_result_history() {
    let (publisher, messages, managed, delivery, _) = publication_fixture_with_options(
        GroupStrategy::Chat, Some(BTreeMap::new()),
    ).await;
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    assert!(managed.snapshot(None).await.unwrap().is_empty());
    assert!(delivery.targets().await.iter().any(|target| target.bot_id() == "bot-driver"));
    let saved = messages.run_chat_segments("group-1:result", "bot-observer", "one-shot-result")
        .await.unwrap();
    assert_eq!(saved.len(), 1);
    assert_eq!(saved[0].content, serde_json::json!("状态机最终结果"));
    assert_eq!(saved[0].visibility_domain, Some(MessageVisibilityDomain::StateMachine));
}

#[tokio::test]
async fn disabling_queue_does_not_bypass_an_existing_result_lane() {
    let (publisher, messages, _, delivery, flow) = publication_fixture(true).await;
    publisher.publish_state_machine_result(publication_command()).await.unwrap();
    drop(publisher);
    let mut flow = Arc::try_unwrap(flow).ok().unwrap();
    flow.group_delivery_limits.clear();
    let publisher = MessageFlowStateMachineResultPublisher::new(Arc::new(flow));
    let mut next = publication_command();
    next.run_id = "must-wait-for-lane".into();
    let error = publisher.publish_state_machine_result(next).await.unwrap_err();
    assert!(error.to_string().contains("queue_draining"));
    assert!(delivery.frames().await.is_empty());
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 1);
}

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
async fn instrumented_result_publication_preserves_queue_and_legacy_behavior() {
    for queued in [false, true] {
        let (_, messages, managed, delivery, flow) = publication_fixture(queued).await;
        let instrumented = Arc::new(crate::metrics::InstrumentedMessageFlowService::new(
            flow, Arc::from("test"),
        ));
        let publisher = MessageFlowStateMachineResultPublisher::new(instrumented);
        publisher.publish_state_machine_result(publication_command()).await.unwrap();
        let history = messages.run_chat_segments("group-1:result", "bot-observer", "one-shot-result")
            .await.unwrap();
        assert_eq!(history.len(), 1);
        assert_eq!(history[0].content, serde_json::json!("状态机最终结果"));
        if queued {
            assert!(managed.snapshot(None).await.unwrap().iter().any(|row|
                row.target_bot_id == "bot-driver" && row.state.kind == bcs_service_api::DeliveryType::Send));
            assert!(delivery.frames().await.is_empty());
        } else {
            assert!(delivery.targets().await.iter().any(|target| target.bot_id() == "bot-driver"));
        }
    }
}

#[cfg(feature = "prometheus-metrics")]
#[tokio::test]
async fn instrumented_result_publication_propagates_inner_conflicts_and_delivery_errors() {
    for queued in [false, true] {
        let (_, _, _, delivery, flow) = publication_fixture(queued).await;
        let instrumented = crate::metrics::InstrumentedMessageFlowService::new(flow, Arc::from("test"));
        if queued {
            instrumented.publish_state_machine_result(publication_command()).await.unwrap();
            let mut changed = publication_command();
            changed.content = "conflicting result".into();
            assert!(matches!(instrumented.publish_state_machine_result(changed).await,
                Err(bcs_service_api::ServiceError::Conflict(_))));
        } else {
            delivery.not_delivered_for("bot-driver").await;
            let error = instrumented.publish_state_machine_result(publication_command()).await.unwrap_err();
            assert!(error.to_string().contains("legacy delivery was not accepted"));
        }
    }
}

#[tokio::test]
async fn authorized_group_owner_receives_its_one_shot_result_with_queue_on_or_off() {
    for strategy in [GroupStrategy::Chat, GroupStrategy::ManagerWorker] {
        for queued in [false, true] {
            for decorated in [false, true] {
                let limits = queued.then(|| BTreeMap::from([
                    ("bot-driver".into(), 10), ("bot-observer".into(), 10),
                ]));
                let (_, messages, managed, delivery, flow) =
                    publication_fixture_with_options(strategy, limits).await;
                let group = flow.group.get("group-1").await.unwrap();
                assert_eq!(group.group_kind, bcs_service_api::GroupKind::Normal);
                let mut command = publication_command();
                // The public one-shot permission check requires the group owner;
                // the runtime preserves that caller as created_by/sender_bot_id.
                command.sender_bot_id = group.driver_bot.clone();
                let service: Arc<dyn MessageFlowService> = if decorated {
                    #[cfg(feature = "prometheus-metrics")]
                    { Arc::new(crate::metrics::InstrumentedMessageFlowService::new(flow.clone(), Arc::from("test"))) }
                    #[cfg(not(feature = "prometheus-metrics"))]
                    { flow.clone() }
                } else { flow.clone() };
                let publisher = MessageFlowStateMachineResultPublisher::new(service);
                publisher.publish_state_machine_result(command).await.unwrap();
                let history = messages.run_chat_segments("group-1:result", &group.driver_bot, "one-shot-result")
                    .await.unwrap();
                assert_eq!(history.len(), 1);
                assert_eq!(history[0].sender_id, group.driver_bot);
                assert_eq!(history[0].content, serde_json::json!("状态机最终结果"));
                if queued {
                    let rows = managed.snapshot(None).await.unwrap();
                    let send = rows.iter().find(|row| row.target_bot_id == group.driver_bot
                        && row.state.kind == bcs_service_api::DeliveryType::Send).unwrap();
                    let preparer = bcs_message_flow::queued_group::QueuedGroupPreparation {
                        flow: Arc::downgrade(&flow), deliveries: managed,
                    };
                    let prepared = preparer.prepare(send).await.unwrap();
                    assert_eq!(serde_json::to_value(&prepared.command.frame).unwrap()["method"], "chat.send");
                    assert!(delivery.deliver(prepared.command).await.unwrap().delivered);
                }
                assert!(delivery.targets().await.iter().any(|target| target.bot_id() == group.driver_bot));
            }
        }
    }
}

// ---- Manager RunResult for failed and aborted Runs (issue #2610) ----

fn run_result_payload(target: &str) -> bcs_service_api::StateMachineRunResultPayload {
    bcs_service_api::StateMachineRunResultPayload {
        run_id: "failed-run".into(),
        group_id: "group-1".into(),
        session_id: "group-1:result".into(),
        target_bot_id: target.into(),
        workflow: "谁是卧底 第1轮 发言".into(),
        terminal: bcs_service_api::StateMachineRunResultTerminal::Failed,
        reason: bcs_service_api::StateMachineRunTerminalReason::NodeFailed,
        failed_node: Some(bcs_service_api::StateMachineRunFailedNode {
            node_id: "speak_3".into(),
            attempt: 1,
            max_attempts: 1,
        }),
        completed_nodes: vec!["speak_1".into(), "speak_2".into()],
        terminated_at_ms: 100,
        deadline_ms: 600_100,
    }
}

fn run_result_history_query() -> bcs_domain::MessageQuery {
    bcs_domain::MessageQuery {
        group_id: "group-1".into(),
        session_id: "group-1:result".into(),
        cursor: None,
        limit: 50,
        keyword: None,
        sender_id: None,
        message_type: None,
        owner_filter: bcs_domain::MessageOwnerFilter::Any,
        time_range: None,
        visible_from_seq: None,
        human_view: None,
    }
}

fn queued_limits() -> Option<BTreeMap<String, u32>> {
    Some(BTreeMap::from([("bot-driver".into(), 10), ("bot-observer".into(), 10)]))
}

#[tokio::test]
async fn queued_run_result_targets_only_the_manager_and_stays_out_of_history() {
    let (publisher, messages, managed, delivery, flow) =
        publication_fixture_with_options(GroupStrategy::ManagerWorker, queued_limits()).await;
    let outcome = publisher.publish_state_machine_run_result(run_result_payload("bot-driver")).await.unwrap();
    assert_eq!(outcome, bcs_service_api::StateMachineRunResultOutcome::Delivered);
    let rows = managed.snapshot(None).await.unwrap();
    assert_eq!(rows.len(), 1, "only the manager receives the RunResult");
    assert_eq!(rows[0].target_bot_id, "bot-driver");
    assert_eq!(rows[0].state.kind, bcs_service_api::DeliveryType::Send);
    assert!(delivery.frames().await.is_empty(), "admission must not invoke legacy transport");

    let replay = publisher.publish_state_machine_run_result(run_result_payload("bot-driver")).await.unwrap();
    assert_eq!(replay, bcs_service_api::StateMachineRunResultOutcome::Delivered);
    assert_eq!(managed.snapshot(None).await.unwrap().len(), 1, "a replay admits no second delivery");
    let history = messages.query_messages(run_result_history_query()).await.unwrap();
    assert!(history.messages.iter().all(|message| message.message_type != bcs_domain::RUN_RESULT_MESSAGE_TYPE));

    // The scheduler prepares the admitted row like any one-shot result and sends the RunResult text.
    let preparer = bcs_message_flow::queued_group::QueuedGroupPreparation { flow: Arc::downgrade(&flow), deliveries: managed };
    let prepared = preparer.prepare(&rows[0]).await.unwrap();
    assert!(serde_json::to_string(&prepared.command.frame).unwrap().contains("[State Machine Run Result]"));
    assert!(delivery.deliver(prepared.command).await.unwrap().delivered);
    assert_eq!(delivery.targets().await[0].bot_id(), "bot-driver");
}

#[tokio::test]
async fn legacy_run_result_reaches_only_the_manager_and_stays_out_of_history() {
    let (publisher, messages, _, delivery, _) =
        publication_fixture_with_options(GroupStrategy::ManagerWorker, None).await;
    let outcome = publisher.publish_state_machine_run_result(run_result_payload("bot-driver")).await.unwrap();
    assert_eq!(outcome, bcs_service_api::StateMachineRunResultOutcome::Delivered);
    let targets: Vec<String> = delivery.targets().await.iter().map(|target| target.bot_id().to_string()).collect();
    assert_eq!(targets, vec!["bot-driver".to_string()]);
    let history = messages.query_messages(run_result_history_query()).await.unwrap();
    assert!(history.messages.iter().all(|message| message.message_type != bcs_domain::RUN_RESULT_MESSAGE_TYPE));
}

#[tokio::test]
async fn run_result_never_reaches_other_bots_in_a_chat_group() {
    let (publisher, _, _, delivery, _) = publication_fixture_with_options(GroupStrategy::Chat, None).await;
    let outcome = publisher.publish_state_machine_run_result(run_result_payload("bot-driver")).await.unwrap();
    assert_eq!(outcome, bcs_service_api::StateMachineRunResultOutcome::Delivered);
    let targets: Vec<String> = delivery.targets().await.iter().map(|target| target.bot_id().to_string()).collect();
    assert_eq!(targets, vec!["bot-driver".to_string()], "the observer must not be injected");
}

#[tokio::test]
async fn run_result_is_skipped_when_the_manager_left_the_session() {
    let (publisher, messages, managed, delivery, _) =
        publication_fixture_with_options(GroupStrategy::ManagerWorker, queued_limits()).await;
    let outcome = publisher.publish_state_machine_run_result(run_result_payload("bot-stranger")).await.unwrap();
    assert!(matches!(outcome, bcs_service_api::StateMachineRunResultOutcome::Skipped { .. }));
    assert!(managed.snapshot(None).await.unwrap().is_empty());
    assert!(delivery.targets().await.is_empty());
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 0);
}

/// Publishes a RunResult for `bot-driver` while the Group and the Session carry the given statuses.
async fn run_result_at_boundary(
    group_status: bcs_domain::GroupStatus,
    session_status: bcs_service_api::SessionStatus,
) -> (bcs_service_api::StateMachineRunResultOutcome, Arc<flow_support::RecordingBotDelivery>, Arc<MemoryMessageRepo>) {
    let support = flow_support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.status = group_status;
    support.group.upsert(group.clone()).await.unwrap();
    let mut session = session_support::test_session("group-1:result", "group-1", group.participants);
    session.status = session_status;
    let messages = Arc::new(MemoryMessageRepo::new());
    let flow = Arc::new(bcs_message_flow::BcsMessageFlow::new(
        support.group, Arc::new(bcs_routing::MessageRouter::new()), support.registry,
        support.bot_delivery.clone(), support.frontend_delivery,
    ).with_message_repo(messages.clone())
        .with_bot_run_context(Arc::new(bcs_message_flow::MemoryBotRunContextStore::new()))
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session))));
    let publisher = MessageFlowStateMachineResultPublisher::new(flow);
    let outcome = publisher.publish_state_machine_run_result(run_result_payload("bot-driver")).await.unwrap();
    (outcome, support.bot_delivery, messages)
}

fn skipped_because(reason: &str) -> bcs_service_api::StateMachineRunResultOutcome {
    bcs_service_api::StateMachineRunResultOutcome::Skipped { reason: reason.into() }
}

#[tokio::test]
async fn run_result_is_skipped_when_the_session_is_no_longer_running() {
    let (outcome, delivery, _) =
        run_result_at_boundary(bcs_domain::GroupStatus::Active, bcs_service_api::SessionStatus::Completed).await;
    assert_eq!(outcome, skipped_because("session is not running"));
    assert!(delivery.targets().await.is_empty());
}

#[tokio::test]
async fn run_result_is_skipped_when_the_group_is_closed() {
    let (outcome, delivery, messages) =
        run_result_at_boundary(bcs_domain::GroupStatus::Closed, bcs_service_api::SessionStatus::Running).await;
    assert_eq!(outcome, skipped_because("group is not active"));
    assert!(delivery.targets().await.is_empty());
    assert_eq!(messages.get_current_seq("group-1:result").await.unwrap(), 0);
}
