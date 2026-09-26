use std::sync::Arc;

use bcs_message_flow::BcsMessageFlow;
use bcs_service_api::{
    ActorKind, BotActor, BotEventCommand, CallerContext, ChatEventState, GroupCoreService,
    GroupKind, GroupMessageType, GroupStrategy, HumanActor, HumanMentionNotifyMode,
    MessageFlowService, MessageRole, Participant, ParticipantMode, ParticipantRole,
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

// --- human-mention notify mode regression (spec §8.5) -----------------------

type ModeSetup = (
    support::FlowTestSupport,
    Arc<support::RecordingHumanMentionNotify>,
    BcsMessageFlow,
);

/// Fixture with an extra present Human (`human_2`) and an extra Bot
/// (`bot-worker`) in the shared repo AND in the Session, so mentions of both
/// survive `apply_session_participant_scope`.
async fn setup_mode_matrix() -> ModeSetup {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("human_2", "Human Two").await;
    support.registry.insert_named_actor("bot-worker", "Worker").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.label = Some("研发协作群".to_string());
    for participant in &mut group.participants {
        if participant.bot_uuid == "human_1" {
            participant.mode = Some(ParticipantMode::Present);
        }
    }
    group.participants.push(Participant {
        bot_uuid: "human_2".to_string(),
        bot_name: Some("Human Two".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Human,
        mode: Some(ParticipantMode::Present),
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    });
    group.participants.push(Participant {
        bot_uuid: "bot-worker".to_string(),
        bot_name: Some("Worker".to_string()),
        kind: None,
        role: ParticipantRole::Observer,
        actor_kind: ActorKind::Bot,
        mode: None,
        tags: Vec::new(),
        message_view_scope: bcs_domain::MessageViewScope::Full,
    });
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

async fn set_notify_mode(support: &support::FlowTestSupport, mode: HumanMentionNotifyMode) {
    let mut group = support.group.get("group-1").await.unwrap();
    group.human_mention_notify_mode = mode;
    support.group.upsert(group).await.unwrap();
}

fn web_send_from(sender: &str, mentions: Vec<String>) -> WebSendCommand {
    WebSendCommand {
        caller: if sender.starts_with("human_") {
            CallerContext::Human(HumanActor { actor_id: sender.to_string(), staff_no: "1".to_string() })
        } else {
            CallerContext::Bot(BotActor { bot_uuid: sender.to_string() })
        },
        group_id: "group-1".to_string(),
        session_id: Some("group-1:s1".to_string()),
        from_actor_id: sender.to_string(),
        from_name: None,
        message: "请确认".to_string(),
        mentions,
        attachments: None,
        thinking: None,
        idempotency_key: None,
        source_im_message_id: None,
        channel_sender_identity: None,
        sender_conn_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

/// Scenario 1 (spec §8.5): `none` suppresses the WebSend notification while
/// the message is still routed with a normal delivery outcome, exactly one
/// scoped policy read happens, and no Session-title read is issued.
#[tokio::test]
async fn none_mode_suppresses_web_send_but_message_flow_continues() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    set_notify_mode(&support, HumanMentionNotifyMode::None).await;
    let outcome = flow
        .handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert!(
        outcome.delivery_results.iter().any(|result| result.success),
        "Bot/Workbench delivery must be unchanged under `none`: {:?}",
        outcome.delivery_results,
    );
    assert_eq!(outcome.status, "started");
    assert_eq!(support.group.policy_read_count().await, 1);
    assert!(
        recorder.notifications().await.is_empty(),
        "`none` must suppress the external notification"
    );
}

/// Scenario 2: `driver_bot_only` allows the driver bot as WebSend sender and
/// suppresses a non-driver bot sender.
#[tokio::test]
async fn driver_bot_only_web_send_allows_driver_and_suppresses_other_bot() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    set_notify_mode(&support, HumanMentionNotifyMode::DriverBotOnly).await;

    flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1, "driver may notify");

    flow.handle_web_send(web_send_from("bot-observer", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1);
    assert_eq!(support.group.policy_read_count().await, 2);
}

/// Scenario 3: a Human sender is allowed under `all` and suppressed under
/// `driver_bot_only`.
#[tokio::test]
async fn human_sender_allowed_under_all_and_suppressed_under_driver_bot_only() {
    let (support, recorder, flow) = setup_mode_matrix().await;

    flow.handle_web_send(web_send_from("human_1", vec!["human_2".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1, "human sender under `all`");

    set_notify_mode(&support, HumanMentionNotifyMode::DriverBotOnly).await;
    flow.handle_web_send(web_send_from("human_1", vec!["human_2".to_string()]))
        .await
        .unwrap();
    assert_eq!(
        recorder.wait_for(1).await.len(),
        1,
        "human sender must be suppressed under driver_bot_only"
    );
}

/// Scenario 4: the bot-event relay goes through the same policy gate.
#[tokio::test]
async fn bot_event_relay_uses_the_same_policy_gate() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    set_notify_mode(&support, HumanMentionNotifyMode::None).await;

    flow.handle_bot_event(bot_event()).await.unwrap();
    assert!(recorder.notifications().await.is_empty());

    set_notify_mode(&support, HumanMentionNotifyMode::All).await;
    flow.handle_bot_event(bot_event()).await.unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1);
}

/// Scenario 5: the persistent group send goes through the same policy gate,
/// and the message is still stored and delivered under `none`.
#[tokio::test]
async fn persistent_group_send_uses_the_same_policy_gate() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    set_notify_mode(&support, HumanMentionNotifyMode::None).await;
    let messages_before = support.group.get("group-1").await.unwrap().messages.len();

    flow.handle_persistent_group_send(PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".to_string(), staff_no: "1".to_string() }),
        group_id: "group-1".to_string(),
        sender: "human_1".to_string(),
        content: "@Human Two 请确认".to_string(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::Assistant,
        max_group_messages: 10,
        store_messages: true,
    })
    .await
    .unwrap();

    assert!(recorder.notifications().await.is_empty());
    let group = support.group.get("group-1").await.unwrap();
    assert_eq!(
        group.messages.len(),
        messages_before + 1,
        "message must still be persisted under `none`"
    );
    assert_eq!(support.group.policy_read_count().await, 1);

    set_notify_mode(&support, HumanMentionNotifyMode::All).await;
    flow.handle_persistent_group_send(PersistentGroupSendCommand {
        caller: CallerContext::Human(HumanActor { actor_id: "human_1".to_string(), staff_no: "1".to_string() }),
        group_id: "group-1".to_string(),
        sender: "human_1".to_string(),
        content: "@Human Two 请再确认".to_string(),
        message_type: GroupMessageType::Bot,
        role: MessageRole::Assistant,
        max_group_messages: 10,
        store_messages: true,
    })
    .await
    .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1);
}

/// Scenario 6: a `manager_worker` Group policy compares against the manager
/// (`driver_bot = manager`): the manager notifies, a worker does not.
#[tokio::test]
async fn manager_worker_group_allows_manager_and_suppresses_worker() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    support.registry.insert_named_actor("bot-manager", "Manager").await;
    let mut group = support.group.get("group-1").await.unwrap();
    group.group_strategy = GroupStrategy::ManagerWorker;
    group.driver_bot = "bot-manager".to_string();
    support.group.upsert(group).await.unwrap();

    flow.handle_web_send(web_send_from("bot-manager", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1, "manager may notify");

    flow.handle_web_send(web_send_from("bot-worker", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1, "worker must not notify");
}

/// Scenario 7: a Dm Group never notifies externally, for every mode — the
/// helper is not even consulted (zero policy reads).
#[tokio::test]
async fn dm_group_never_notifies_for_any_mode() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support.registry.insert_named_actor("human_2", "Human Two").await;
    let mut dm = support.group.get("group-1").await.unwrap();
    dm.group_kind = GroupKind::Dm;
    dm.driver_bot = "bot-driver".to_string();
    dm.human_mention_notify_mode = HumanMentionNotifyMode::All;
    dm.participants = vec![
        Participant {
            bot_uuid: "human_1".to_string(),
            bot_name: Some("Human One".to_string()),
            kind: None,
            role: ParticipantRole::Observer,
            actor_kind: ActorKind::Human,
            mode: Some(ParticipantMode::Present),
            tags: Vec::new(),
            message_view_scope: bcs_domain::MessageViewScope::Full,
        },
        Participant {
            bot_uuid: "bot-driver".to_string(),
            bot_name: Some("Driver".to_string()),
            kind: None,
            role: ParticipantRole::Driver,
            actor_kind: ActorKind::Bot,
            mode: Some(ParticipantMode::Auto),
            tags: Vec::new(),
            message_view_scope: bcs_domain::MessageViewScope::Full,
        },
    ];
    support.group.upsert(dm).await.unwrap();

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder.clone());

    for mode in [
        HumanMentionNotifyMode::All,
        HumanMentionNotifyMode::DriverBotOnly,
        HumanMentionNotifyMode::None,
    ] {
        set_notify_mode(&support, mode).await;
        flow.handle_web_send(WebSendCommand {
            caller: CallerContext::Human(HumanActor { actor_id: "human_1".to_string(), staff_no: "1".to_string() }),
            group_id: "group-1".to_string(),
            session_id: None,
            from_actor_id: "human_1".to_string(),
            from_name: None,
            message: "dm only".to_string(),
            mentions: vec!["human_2".to_string()],
            attachments: None,
            thinking: None,
            idempotency_key: None,
            source_im_message_id: None,
            channel_sender_identity: None,
            sender_conn_id: None,
            provider_bypass_headers: Vec::new(),
        })
        .await
        .unwrap();
    }
    assert!(
        recorder.notifications().await.is_empty(),
        "Dm groups must never notify"
    );
    assert_eq!(support.group.policy_read_count().await, 0);
}

/// Scenario 8: changing the Group from `all` to `none` between two messages
/// on the same Session id suppresses the second notification without touching
/// Session participants.
#[tokio::test]
async fn group_patch_between_two_messages_uses_new_policy_without_touching_session() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1);

    let group = support.group.get("group-1").await.unwrap();
    let participants: Vec<String> =
        group.participants.iter().map(|p| p.bot_uuid.clone()).collect();
    set_notify_mode(&support, HumanMentionNotifyMode::None).await;

    flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(
        recorder.wait_for(2).await.len(),
        1,
        "the second message on the same Session must not notify under `none`"
    );
    let reloaded = support.group.get("group-1").await.unwrap();
    assert_eq!(
        reloaded.participants.iter().map(|p| p.bot_uuid.clone()).collect::<Vec<_>>(),
        participants,
        "the policy patch must not change participants"
    );
    assert_eq!(support.group.policy_read_count().await, 2);
}

/// Scenario 9: the message is held after the early Group snapshot but before
/// the notification helper starts; an `all -> none` commit inside that window
/// is what the helper's dedicated current-policy read observes.
#[tokio::test]
async fn barrier_policy_read_observes_value_committed_during_hold() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    let release = support.group.hold_next_policy_read().await;

    let task = tokio::spawn(async move {
        flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
            .await
            .unwrap();
    });

    // The read is in flight before the patch is committed; no sleeps.
    support.group.wait_policy_reads(1).await;
    set_notify_mode(&support, HumanMentionNotifyMode::None).await;
    let _ = release.send(()).await;
    task.await.unwrap();

    let observed = support.group.observed_policy().await.expect("policy observed");
    assert_eq!(observed.mode, HumanMentionNotifyMode::None);
    assert_eq!(observed.driver_bot_id, "bot-driver");
    assert_eq!(support.group.policy_read_count().await, 1);
    assert!(
        recorder.notifications().await.is_empty(),
        "the committed `none` value must decide the notification"
    );
}

/// Scenario 10: changing the Group `driver_bot` between two `driver_bot_only`
/// messages unpins the Session: the old driver loses eligibility and the new
/// driver becomes eligible.
#[tokio::test]
async fn driver_bot_change_between_messages_is_not_pinned_by_session() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    set_notify_mode(&support, HumanMentionNotifyMode::DriverBotOnly).await;

    flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1, "old driver eligible");

    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "bot-worker".to_string();
    support.group.upsert(group).await.unwrap();

    flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(1).await.len(), 1, "old driver now suppressed");

    flow.handle_web_send(web_send_from("bot-worker", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(recorder.wait_for(2).await.len(), 2, "new driver eligible");
    assert_eq!(support.group.policy_read_count().await, 3);
}

/// Scenario 11 (with the differential Session-read attribution of scenario
/// 12): a failed policy read keeps the message persisted and routed, spawns
/// no notification, and performs no Session-title read.
#[tokio::test]
async fn failed_policy_read_fail_closed_but_message_still_flows() {
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
    let session_service = Arc::new(session_support::StaticSessionManagement::new(session));
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
    .with_session_management(session_service.clone())
    .with_human_mention_notify(recorder.clone());

    support.group.fail_policy_reads(true);
    let suppressed = flow
        .handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert!(
        suppressed.delivery_results.iter().any(|result| result.success),
        "message must remain routed when the policy read fails"
    );
    assert!(recorder.notifications().await.is_empty());
    assert_eq!(support.group.policy_read_count().await, 1);
    // The main message path makes its own Session reads; the suppressed
    // message's total is the no-title-read baseline for the differential
    // assertion below.
    let suppressed_session_reads = session_service.get_count();

    // Differential attribution: the only new Session `get` of an otherwise
    // identical eligible message is the notification title read.
    support.group.fail_policy_reads(false);
    let eligible = flow
        .handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert!(eligible.delivery_results.iter().any(|result| result.success));
    let after_eligible_send = session_service.get_count();
    assert_eq!(
        after_eligible_send,
        suppressed_session_reads * 2,
        "the main path performs the same Session reads whether the gate allows or suppresses"
    );
    assert_eq!(recorder.wait_for(1).await.len(), 1);
    assert_eq!(
        session_service.get_count(),
        after_eligible_send + 1,
        "the only extra Session get is the notification title read of the spawned task"
    );
    assert_eq!(support.group.policy_read_count().await, 2);
}

/// Scenario 12: helper-only policy-read boundary — zero reads while the Port
/// is unavailable or no valid Human resolves, exactly one scoped read for an
/// eligible candidate.
#[tokio::test]
async fn policy_read_counts_zero_until_an_eligible_candidate() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let mut group = support.group.get("group-1").await.unwrap();
    for participant in &mut group.participants {
        if participant.bot_uuid == "human_1" {
            participant.mode = Some(ParticipantMode::Present);
        }
    }
    support.group.upsert(group).await.unwrap();
    // Port unavailable: no notification and zero policy reads.
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(Arc::new(support::RecordingHumanMentionNotify::available(false)));
    flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
        .await
        .unwrap();
    assert_eq!(support.group.policy_read_count().await, 0);

    let recorder = Arc::new(support::RecordingHumanMentionNotify::available(true));
    let flow = BcsMessageFlow::new(
        support.group.clone(), support.routing.clone(), support.registry.clone(),
        support.bot_delivery.clone(), support.frontend_delivery.clone(),
    )
    .with_human_mention_notify(recorder);

    // Bot-only mention: no valid Human, zero reads.
    flow.handle_web_send(web_send_from("bot-driver", vec!["bot-observer".to_string()]))
        .await
        .unwrap();
    assert_eq!(support.group.policy_read_count().await, 0);

    // Two Humans mentioned at once (the maximum the Group supports without a
    // new participant): still exactly one scoped read for the candidate.
    flow.handle_web_send(web_send_from(
        "bot-driver",
        vec!["human_1".to_string(), "human_2".to_string()],
    ))
    .await
    .unwrap();
    assert_eq!(support.group.policy_read_count().await, 1);
}

/// Scenario 13 (barrier): a Patch overlapping the policy read contributes
/// exactly one consistent committed (mode, driver) snapshot, and the
/// notification decision follows that snapshot.
#[tokio::test]
async fn barrier_patch_overlapping_the_read_uses_one_consistent_snapshot() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    set_notify_mode(&support, HumanMentionNotifyMode::DriverBotOnly).await;
    let release = support.group.hold_next_policy_read().await;

    let task = tokio::spawn(async move {
        flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
            .await
            .unwrap();
    });

    support.group.wait_policy_reads(1).await;
    // Patch commits while the read is held: mode stays, driver moves.
    let mut group = support.group.get("group-1").await.unwrap();
    group.driver_bot = "bot-worker".to_string();
    support.group.upsert(group).await.unwrap();
    let _ = release.send(()).await;
    task.await.unwrap();

    let observed = support.group.observed_policy().await.expect("policy observed");
    assert_eq!(observed.mode, HumanMentionNotifyMode::DriverBotOnly);
    assert_eq!(observed.driver_bot_id, "bot-worker");
    assert!(
        recorder.notifications().await.is_empty(),
        "sender must be judged against the single committed snapshot"
    );
    assert_eq!(support.group.policy_read_count().await, 1);
}

/// Scenario 13 companion: a notification allowed by the snapshot the read
/// consumed is created and completes — it is not retracted by the overlapping
/// patch.
#[tokio::test]
async fn barrier_created_notification_is_not_retracted_by_overlapping_patch() {
    let (support, recorder, flow) = setup_mode_matrix().await;
    set_notify_mode(&support, HumanMentionNotifyMode::None).await;
    let release = support.group.hold_next_policy_read().await;

    let task = tokio::spawn(async move {
        flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
            .await
            .unwrap();
    });

    support.group.wait_policy_reads(1).await;
    set_notify_mode(&support, HumanMentionNotifyMode::All).await;
    let _ = release.send(()).await;
    task.await.unwrap();

    // The read consumed the committed `all` snapshot; there is no retraction
    // of the already-created notification task.
    assert_eq!(recorder.wait_for(1).await.len(), 1);
    let observed = support.group.observed_policy().await.expect("policy observed");
    assert_eq!(observed.mode, HumanMentionNotifyMode::All);
}

/// Scenario 14: maximum mentions, repeated policy-read failures and
/// concurrent notification candidates — reads scale at most one per eligible
/// message (never per Human), no retry or write is introduced on the notify
/// path.
#[tokio::test]
async fn reads_scale_one_per_eligible_message_under_load_and_failure() {
    let (support, recorder, flow) = setup_mode_matrix().await;

    // Maximum supported mention count of the fixture Group (every Human
    // participant at once): a single message, a single scoped read.
    flow.handle_web_send(web_send_from(
        "bot-driver",
        vec!["human_1".to_string(), "human_2".to_string()],
    ))
    .await
    .unwrap();
    assert_eq!(support.group.policy_read_count().await, 1);
    assert_eq!(recorder.wait_for(1).await.len(), 1);

    // Repeated DB failures: one read attempt per message, no retry, no
    // notification, delivery unchanged.
    support.group.fail_policy_reads(true);
    for _ in 0..3 {
        flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()]))
            .await
            .unwrap();
    }
    assert_eq!(support.group.policy_read_count().await, 4);
    assert_eq!(recorder.wait_for(1).await.len(), 1);

    // Concurrent candidates: exactly one read and one notification each.
    support.group.fail_policy_reads(false);
    let mut pending = Vec::new();
    for _ in 0..5 {
        pending.push(flow.handle_web_send(web_send_from("bot-driver", vec!["human_1".to_string()])));
    }
    for outcome in futures::future::join_all(pending).await {
        outcome.unwrap();
    }
    assert_eq!(
        support.group.policy_read_count().await,
        9,
        "one read per eligible message, no retries"
    );
    assert_eq!(recorder.wait_for(6).await.len(), 6);
}
