use std::sync::Arc;

use bcs_message_flow::BcsMessageFlow;
use bcs_service_api::{
    BotActor, BotEventCommand, CallerContext, ChatEventState, GroupCoreService,
    GroupMessageType, HumanActor, MessageFlowService, MessageRole, ParticipantMode,
    PersistentGroupSendCommand, WebSendCommand,
};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "support/session.rs"]
mod session_support;

async fn setup() -> (support::FlowTestSupport, Arc<support::RecordingHumanMentionNotify>, BcsMessageFlow) {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.label = Some("研发协作群".to_string());
    for participant in &mut group.participants {
        if participant.bot_uuid == "human_1" {
            participant.mode = Some(ParticipantMode::Present);
        }
    }
    let mut session = session_support::test_session("group-1:s1", "group-1", group.participants.clone());
    session.session_title = Some("发布问题排查".to_string());
    support.group.upsert(group).await.unwrap();
    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
    .with_session_management(Arc::new(session_support::StaticSessionManagement::new(session)))
    .with_human_mention_notify(recorder.clone());
    (support, recorder, flow)
}

fn bot_caller() -> CallerContext {
    CallerContext::Bot(BotActor { bot_uuid: "bot-driver".to_string() })
}

#[tokio::test]
async fn web_send_notification_carries_group_label_and_session_title() {
    let (_support, recorder, flow) = setup().await;
    flow.handle_web_send(WebSendCommand {
        caller: bot_caller(),
        group_id: "group-1".to_string(),
        session_id: Some("group-1:s1".to_string()),
        from_actor_id: "bot-driver".to_string(),
        from_name: None,
        message: "请确认".to_string(),
        mentions: vec!["human_1".to_string()],
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    }).await.unwrap();
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:s1");
    assert_eq!(notifications[0].group_name.as_deref(), Some("研发协作群"));
    assert_eq!(notifications[0].session_name.as_deref(), Some("发布问题排查"));
}

fn bot_event() -> BotEventCommand {
    BotEventCommand {
        bot_id: "bot-driver".to_string(),
        run_id: "run-1".to_string(),
        group_id: "group-1".to_string(),
        event_type: "chat.event".to_string(),
        event_payload: serde_json::json!({
            "state": "final",
            "message": { "role": "assistant", "content": [{"type": "text", "text": "@Human One 请确认"}] },
        }),
        state: ChatEventState::Final,
        bcs_session_id: Some("group-1:s1".to_string()),
    }
}

#[tokio::test]
async fn bot_reply_notification_carries_group_label_and_session_title() {
    let (_support, recorder, flow) = setup().await;
    flow.handle_bot_event(bot_event()).await.unwrap();
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_id, "group-1");
    assert_eq!(notifications[0].session_id, "group-1:s1");
    assert_eq!(notifications[0].group_name.as_deref(), Some("研发协作群"));
    assert_eq!(notifications[0].session_name.as_deref(), Some("发布问题排查"));
}

#[tokio::test]
async fn group_only_notification_has_group_name_but_no_session_name() {
    let (support, recorder, flow) = setup().await;
    let mut group = support.group.get("group-1").await.unwrap();
    let mut human = group.participants.iter().find(|p| p.bot_uuid == "human_1").unwrap().clone();
    human.bot_uuid = "human_2".to_string();
    human.bot_name = Some("Human Two".to_string());
    group.participants.push(human);
    support.group.upsert(group).await.unwrap();
    flow.handle_persistent_group_send(PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".to_string(), staff_no: "1".to_string() }),
        group_id: "group-1".to_string(),
        sender: "human_1".to_string(),
        content: "@Human Two 请确认".to_string(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::Assistant,
        max_group_messages: 10,
        store_messages: true,
    }).await.unwrap();
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_name.as_deref(), Some("研发协作群"));
    assert_eq!(notifications[0].session_id, "");
    assert_eq!(notifications[0].session_name, None);
}

#[tokio::test]
async fn bot_reply_keeps_notification_when_session_name_is_unavailable() {
    let (_support, recorder, flow) = setup().await;
    let mut event = bot_event();
    event.bcs_session_id = Some("group-1:missing".to_string());
    flow.handle_bot_event(event).await.unwrap();
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_name.as_deref(), Some("研发协作群"));
    assert_eq!(notifications[0].session_id, "group-1:missing");
    assert_eq!(notifications[0].session_name, None);
}

#[tokio::test]
async fn bot_reply_keeps_notification_on_session_read_failure() {
    let (support, recorder, mut flow) = setup().await;
    let group = support.group.get("group-1").await.unwrap();
    let session = session_support::test_session("group-1:s1", "group-1", group.participants);
    flow.session_management = Some(Arc::new(
        session_support::StaticSessionManagement::new(session).with_get_failure()
    ));
    flow.handle_bot_event(bot_event()).await.unwrap();
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_name.as_deref(), Some("研发协作群"));
    assert_eq!(notifications[0].session_id, "group-1:s1");
    assert_eq!(notifications[0].session_name, None);
}

#[tokio::test]
async fn bot_reply_does_not_expose_a_different_groups_session_title() {
    let (support, recorder, mut flow) = setup().await;
    let group = support.group.get("group-1").await.unwrap();
    let mut session = session_support::test_session("group-1:s1", "another-group", group.participants);
    session.session_title = Some("Other group's private title".to_string());
    flow.session_management = Some(Arc::new(session_support::StaticSessionManagement::new(session)));
    flow.handle_bot_event(bot_event()).await.unwrap();
    let notifications = recorder.wait_for(1).await;
    assert_eq!(notifications.len(), 1);
    assert_eq!(notifications[0].group_name.as_deref(), Some("研发协作群"));
    assert_eq!(notifications[0].session_id, "group-1:s1");
    assert_eq!(notifications[0].session_name, None);
}
