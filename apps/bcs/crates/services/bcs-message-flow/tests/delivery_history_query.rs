//! Read-only delivery status must honor the same pre-join window as history.
use std::sync::Arc;

use bcs_domain::{DeliveryType, GroupStrategy, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_message_flow::{BcsMessageFlow, managed_delivery::ManagedMessageDelivery};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::{
    CallerContext, DeliveryStatusQuery, GroupCoreService, HumanActor,
    ManagedMessageDeliveryService, MessageFlowService,
    port::repo::{MessageRepoPort, message_delivery::{AdmitMessageDeliveries, DeliveryAdmissionTarget}},
};
use serde_json::json;

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

#[path = "support/delivery_history_repo.rs"]
mod repository_support;

const SESSION: &str = "group-1:history-window";

fn human() -> CallerContext {
    CallerContext::Human(HumanActor { actor_id: "human_1".into(), staff_no: "1".into() })
}

fn message(seq: i64) -> NewMessage {
    NewMessage {
        group_id: "group-1".into(), session_id: SESSION.into(),
        sender_id: "human_1".into(), sender_type: SenderType::Human,
        message_type: "chat".into(), content: json!({"text": format!("message {seq}")}),
        client_msg_id: Some(format!("client-{seq}")), owner_bot_id: None,
        created_at: seq as u64, run_id: String::new(),
        visibility_domain: MessageVisibilityDomain::Chat, audience: None,
    }
}

async fn fixture() -> (BcsMessageFlow, Arc<MemoryMessageRepo>) {
    fixture_with_messages(14).await
}

async fn fixture_with_messages(count: i64) -> (BcsMessageFlow, Arc<MemoryMessageRepo>) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::Chat;
    support.group.upsert(group.clone()).await.unwrap();
    let mut session = session_support::test_session(SESSION, "group-1", group.participants);
    session.participant_join_seq = Some(json!({"human_1": 6, "bot-driver": 6}));
    let repo = Arc::new(MemoryMessageRepo::new());
    let deliveries = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    for seq in 1..=count {
        deliveries.admit(AdmitMessageDeliveries {
            display_message: None, message_id: format!("message-{seq}"),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: seq, expire_at_ms: None, event: None, message: message(seq),
            targets: vec![DeliveryAdmissionTarget {
                rejection: None, target_bot_id: "bot-driver".into(), kind: DeliveryType::Send,
                max_queued: 100, semantic_projection_json: json!({"version": 1}), run_id: None,
            }],
        }).await.unwrap();
    }
    let flow = BcsMessageFlow::new(support.group, support.routing, support.registry,
        support.bot_delivery, support.frontend_delivery)
        .with_message_repo(repo.clone())
        .with_managed_deliveries(deliveries)
        .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session)))
        .with_bot_authority_hook(Some(bcs_test_support::lazy_authority_chain().hook));
    (flow, repo)
}

fn query(ids: &[i64]) -> DeliveryStatusQuery {
    DeliveryStatusQuery {
        caller: human(), session_id: SESSION.into(),
        message_ids: ids.iter().map(|seq| format!("message-{seq}")).collect(),
        client_msg_id: None,
    }
}

#[tokio::test]
async fn member_can_query_batch_containing_visible_message_before_join() {
    let (flow, repo) = fixture().await;
    let from = repo.resolve_history_window_start(SESSION, 6, 100).await.unwrap();
    assert!(from <= 4, "the historical message is inside the configured window");
    let result = flow.query_message_deliveries(query(&[4, 9, 13, 14])).await;
    assert!(result.is_ok(), "history-visible batch must not fail: {result:?}");
    let ids: Vec<_> = result.unwrap().into_iter().map(|row| row.message_id).collect();
    assert_eq!(ids, ["message-4", "message-9", "message-13", "message-14"]);
}

async fn admit(flow: &BcsMessageFlow, id: &str, message: NewMessage) {
    flow.managed_deliveries.as_ref().unwrap().admit(AdmitMessageDeliveries {
        display_message: None, message_id: id.into(),
        flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
        now_ms: 100, expire_at_ms: None, event: None, message,
        targets: vec![DeliveryAdmissionTarget {
            rejection: None, target_bot_id: "bot-driver".into(), kind: DeliveryType::Send,
            max_queued: 100, semantic_projection_json: json!({"version": 1}), run_id: None,
        }],
    }).await.unwrap();
}

fn assert_forbidden<T: std::fmt::Debug>(result: Result<T, bcs_service_api::ServiceError>, reason: &str) {
    assert!(matches!(&result, Err(bcs_service_api::ServiceError::Forbidden(message)) if message == reason),
        "expected Forbidden({reason}), got {result:?}");
}

#[tokio::test]
async fn configured_window_includes_boundary_but_rejects_older_messages() {
    let (mut flow, _) = fixture().await;
    flow.new_participant_visible_limit = 3; // join=6: visible from 4
    assert_eq!(flow.query_message_deliveries(query(&[4, 6, 14])).await.unwrap().len(), 3);
    assert_forbidden(flow.query_message_deliveries(query(&[3, 9])).await,
        "message predates session access");
    flow.new_participant_visible_limit = 0;
    assert_forbidden(flow.query_message_deliveries(query(&[6])).await,
        "message predates session access");
    assert_eq!(flow.query_message_deliveries(query(&[7])).await.unwrap().len(), 1);
}

#[tokio::test]
async fn bot_and_client_id_queries_use_the_same_history_window() {
    let (mut flow, _) = fixture().await;
    flow.new_participant_visible_limit = 3;
    let mut bot_query = query(&[4]);
    bot_query.caller = CallerContext::Bot(bcs_service_api::BotActor { bot_uuid: "bot-driver".into() });
    assert_eq!(flow.query_message_deliveries(bot_query).await.unwrap().len(), 1);
    let mut client_query = query(&[]);
    client_query.client_msg_id = Some("client-4".into());
    assert_eq!(flow.query_message_deliveries(client_query).await.unwrap().len(), 1);
}

#[tokio::test]
async fn read_window_does_not_relax_cancellation_or_resolution() {
    let (flow, _) = fixture().await;
    assert_forbidden(flow.cancel_message_deliveries(bcs_service_api::CancelMessageDeliveryCommand {
        caller: human(), session_id: SESSION.into(), message_id: "message-4".into(), delivery_id: None,
    }).await, "message predates session access");
    assert_forbidden(flow.resolve_message_delivery(bcs_service_api::ResolveMessageDeliveryCommand {
        caller: human(), session_id: SESSION.into(), message_id: "message-4".into(),
        delivery_id: "unused".into(), expected_state_version: 1,
        resolution: bcs_service_api::DeliveryResolution::ConfirmedNotSent, reason: "test".into(),
    }).await, "message predates session access");
    let cancelled = flow.cancel_message_deliveries(bcs_service_api::CancelMessageDeliveryCommand {
        caller: human(), session_id: SESSION.into(), message_id: "message-9".into(), delivery_id: None,
    }).await.unwrap();
    assert_eq!(cancelled.len(), 1);
}

#[tokio::test]
async fn nonmembers_and_private_messages_remain_forbidden() {
    let (flow, _) = fixture().await;
    let mut outsider = query(&[4]);
    outsider.caller = CallerContext::Human(HumanActor { actor_id: "human_other".into(), staff_no: "other".into() });
    assert_forbidden(flow.query_message_deliveries(outsider).await, "session membership required");
    let mut private = message(15);
    private.owner_bot_id = Some("bot-observer".into());
    admit(&flow, "private", private).await;
    let mut request = query(&[4]);
    request.message_ids.push("private".into());
    assert_forbidden(flow.query_message_deliveries(request).await, "private message is not visible");
}

#[tokio::test]
async fn non_chat_history_uses_audience_not_join_sequence() {
    use bcs_domain::{MessageAudience, MessageViewScope};
    for strategy in [GroupStrategy::ManagerWorker, GroupStrategy::StateMachine] {
        let (mut flow, _) = fixture().await;
        let mut group = flow.group.get("group-1").await.unwrap();
        group.group_strategy = strategy;
        flow.group.upsert(group).await.unwrap();
        // Both strategies allow history before the recorded join.
        assert_eq!(flow.query_message_deliveries(query(&[4])).await.unwrap().len(), 1);
        let mut session = flow.session_management.as_ref().unwrap().get(SESSION).await.unwrap().unwrap();
        session.participant_join_seq = Some(json!({"human_1": 20}));
        session.participants.iter_mut().find(|p| p.bot_uuid == "human_1").unwrap()
            .message_view_scope = MessageViewScope::Participant;
        flow.session_management = Some(Arc::new(session_support::StaticSessionManagement::new(session)));
        let mut hidden = message(15);
        hidden.visibility_domain = MessageVisibilityDomain::StateMachine;
        hidden.audience = Some(MessageAudience::FullOnly);
        admit(&flow, "hidden", hidden).await;
        let mut request = query(&[]);
        request.message_ids = vec!["hidden".into()];
        assert_forbidden(flow.query_message_deliveries(request).await, "message audience is not visible");
    }
}

#[tokio::test]
async fn supplemental_history_preserves_window_slots_and_audience_checks() {
    use bcs_domain::{MessageAudience, MessageViewScope};
    let (mut flow, _) = fixture().await;
    for seq in 15..=16 {
        let mut supplemental = message(seq);
        supplemental.message_type = bcs_domain::STATE_MACHINE_OUTPUT_MESSAGE_TYPE.into();
        supplemental.visibility_domain = MessageVisibilityDomain::StateMachine;
        supplemental.audience = Some(MessageAudience::FullOnly);
        supplemental.content = json!({"text":"output", "metadata":{"state_machine":{"history_schema_version":1}}});
        admit(&flow, &format!("message-{seq}"), supplemental).await;
    }
    let mut session = flow.session_management.as_ref().unwrap().get(SESSION).await.unwrap().unwrap();
    session.participant_join_seq = Some(json!({"human_1": 16}));
    flow.session_management = Some(Arc::new(session_support::StaticSessionManagement::new(session.clone())));
    flow.new_participant_visible_limit = 2; // supplemental 15/16 don't consume slots: from=13
    assert_eq!(flow.query_message_deliveries(query(&[13, 14, 15, 16])).await.unwrap().len(), 4);
    assert_forbidden(flow.query_message_deliveries(query(&[12])).await, "message predates session access");
    flow.new_participant_visible_limit = 0; // from=17, supplemental still visible to Full
    assert_eq!(flow.query_message_deliveries(query(&[15])).await.unwrap().len(), 1);
    session.participants.iter_mut().find(|p| p.bot_uuid == "human_1").unwrap()
        .message_view_scope = MessageViewScope::Participant;
    flow.session_management = Some(Arc::new(session_support::StaticSessionManagement::new(session)));
    assert_forbidden(flow.query_message_deliveries(query(&[15])).await, "message audience is not visible");
}

#[tokio::test]
async fn max_batch_resolves_history_window_once_and_storage_failure_is_an_error() {
    let (mut flow, repo) = fixture_with_messages(100).await;
    let probe = Arc::new(repository_support::WindowProbe::new(repo));
    flow.message_repo = Some(probe.clone());
    let ids: Vec<_> = (1..=100).collect();
    assert_eq!(flow.query_message_deliveries(query(&ids)).await.unwrap().len(), 100);
    assert_eq!(*probe.calls.lock().unwrap(), vec![(6, 100)]);
    probe.fail.store(true, std::sync::atomic::Ordering::SeqCst);
    let result = flow.query_message_deliveries(query(&[4])).await;
    assert!(matches!(result, Err(bcs_service_api::ServiceError::InternalError(_))), "{result:?}");
    // IDs without deliveries still return an empty result, without a window read.
    assert!(flow.query_message_deliveries(query(&[101])).await.unwrap().is_empty());
    assert_eq!(probe.calls.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn missing_legacy_join_anchor_keeps_existing_behavior_without_window_read() {
    let (mut flow, repo) = fixture().await;
    let probe = Arc::new(repository_support::WindowProbe::new(repo));
    flow.message_repo = Some(probe.clone());
    let mut session = flow.session_management.as_ref().unwrap().get(SESSION).await.unwrap().unwrap();
    for anchors in [None, Some(json!({"human_other": 6}))] {
        session.participant_join_seq = anchors;
        flow.session_management = Some(Arc::new(session_support::StaticSessionManagement::new(session.clone())));
        flow.new_participant_visible_limit = 0;
        assert_eq!(flow.query_message_deliveries(query(&[4])).await.unwrap().len(), 1);
    }
    assert!(probe.calls.lock().unwrap().is_empty());
}
