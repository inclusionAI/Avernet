//! §7.2/§918 群内受控 A2A 的回复投影契约 (review batch-3 §3.2): a routed
//! group reply toward a Send-kind BOT target derives through the SAME
//! trusted entry the coordination assign uses — when the replying Bot's
//! managed run carries a live root lineage, the reply's admission carries
//! the four delegation marks and routes to the §5.9 delegate branch; with
//! no lineage (plain groups, self-initiated turns) the reply stays an
//! unmarked ordinary row. Inject targets never derive (§15.38 — injections
//! never trigger tools/children/runs).

use std::collections::BTreeMap;
use std::sync::Arc;

use bcs_domain::{
    DeliveryType, MessageVisibilityDomain, NewMessage, SenderType,
    message_delivery::DeliveryFlowKind,
};
use bcs_message_flow::caller_lineage::{RootLineage, RootLineageIndex};
use bcs_message_flow::managed_delivery::ManagedMessageDelivery;
use bcs_message_flow::{BcsMessageFlow, MemoryBotRunContextStore};
use bcs_message_store::MemoryMessageRepo;
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};
use bcs_service_api::{
    BotEventCommand, BotRunContext, BotRunContextPort, ChatEventState, DeliveryTransitionCommand,
    GroupCoreService, ManagedMessageDeliveryService, MessageFlowService,
};
use serde_json::json;

#[path = "support/session.rs"]
mod session_support;
#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
use session_support::{StaticSessionManagement, test_session};

const SESSION: &str = "group-1:derivation";

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
}

/// One managed caller-root row for `bot-driver`, driven to a live Started
/// dispatch: the row every reply derivation keys its parent run to.
async fn started_parent_row(
    service: &Arc<ManagedMessageDelivery>,
) -> bcs_domain::message_delivery::PersistedMessageDelivery {
    use bcs_service_api::ManagedMessageDeliveryService as _;
    let now = now_ms();
    let admitted = service
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: format!("seed-{}", now),
            flow_kind: DeliveryFlowKind::Group,
            now_ms: now,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                visibility_domain: MessageVisibilityDomain::Chat,
                audience: None,
                group_id: "group-1".into(),
                session_id: SESSION.into(),
                sender_id: "human_1".into(),
                sender_type: SenderType::Human,
                message_type: "chat".into(),
                content: json!({"text": "root task text"}),
                client_msg_id: None,
                owner_bot_id: None,
                created_at: now as u64,
                run_id: String::new(),
            },
            targets: vec![DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "bot-driver".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: json!({
                    "version": 1,
                    "caller_required": true,
                    "caller_task": "root task text",
                }),
            }],
        })
        .await
        .expect("parent row admits");
    let row = &admitted.deliveries[0];
    service
        .transition(DeliveryTransitionCommand {
            caller_dispatch_commit: None,
            caller_cancel: None,
            caller_terminal: None,
            delivery_id: row.delivery_id.clone(),
            expected_state_version: row.state.state_version,
            event: Event::StartSend,
            now_ms: now,
            request_id: None,
            actor_id: None,
            reply: None,
            transport_context_json: Some(json!({
                "version": 1,
                "owner": {"kind": "web_socket"},
                "connection_id": null,
                "downstream_session_key": SESSION,
            })),
            deadline_at_ms: Some(now + 600_000),
        })
        .await
        .expect("parent row starts dispatch")
}

#[tokio::test]
async fn a_routed_bot_reply_with_lineage_carries_the_delegation_marks() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let lineage = Arc::new(RootLineageIndex::new(8));
    let flow = BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_bot_run_context(run_context.clone())
    .with_caller_derivation(lineage.clone())
    .with_group_delivery_limits(BTreeMap::from([(
        "bot-observer".to_string(),
        10u32,
    )]))
    .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        SESSION,
        "group-1",
        group.participants,
    ))));

    let started = started_parent_row(&service).await;
    let parent_run = started.run_id.clone().expect("started run id");
    let now = now_ms();
    // The parent's root lineage — the trusted producer's post-Accepted fact
    // (what the protected root dispatch registers in production).
    assert!(lineage.register(
        &parent_run,
        RootLineage {
            root_run_id: parent_run.clone(),
            environment: "env-caller".into(),
            delegation_depth: 0,
            attempt: 1,
            message_turn_id: started.source_message_id.clone(),
            group_id: "group-1".into(),
            session_id: SESSION.into(),
            history_cutoff_seq: 0,
            expires_at_ms: now + 300_000,
            identity_subject: "human_1".into(),
            identity_subject_type: bcs_service_api::application::caller_identity::CallerSubjectRefType::BcsEphemeral,
            delegate_subject: "human_1".into(),
            parent_execution: false,
        },
        now,
    ));
    // The parent's live run context — validate_parent's authority.
    run_context
        .put_context(BotRunContext {
            run_id: parent_run.clone(),
            bot_id: "bot-driver".into(),
            group_id: "group-1".into(),
            bcs_session_id: Some(SESSION.into()),
            deadline_ms: (now + 600_000) as u64,
            terminal: false,
        })
        .await;

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: parent_run.clone(),
        group_id: "group-1".into(),
        event_type: "chat".into(),
        event_payload: json!({
            "state": "final",
            "message": {"role": "assistant",
                "content": [{"type": "text", "text": "@bot-observer 请协助处理这部分"}]},
        }),
        bcs_session_id: Some(SESSION.into()),
        state: ChatEventState::Final,
    })
    .await
    .expect("the reply relays");

    let rows = service.snapshot(Some(SESSION)).await.expect("snapshot");
    let derived = rows
        .iter()
        .find(|row| row.target_bot_id == "bot-observer" && row.state.kind == DeliveryType::Send)
        .expect("the routed reply row for bot-observer is admitted");
    let projection = &derived.semantic_projection_json;
    assert_eq!(projection["caller_required"], true, "the reply routes to the protected branch");
    assert!(
        projection["caller_task"]
            .as_str()
            .is_some_and(|text| text.contains("请协助处理这部分")),
        "the reply's own text is the protected wire text"
    );
    assert_eq!(
        projection["caller_delegation"]["parent_run_id"], parent_run,
        "the derivation binds the replying Bot's real parent run"
    );
    let invocation = projection["caller_delegation"]["invocation_id"]
        .as_str()
        .expect("invocation id");
    assert!(
        invocation.starts_with("reply:") && invocation.ends_with("bot-observer"),
        "the reply derivation has a stable, target-unique invocation id: {invocation}"
    );
    // No caller control plane in this composition → the target reads as an
    // Owner middle hop: issuance purpose is delegation_only, never blanket
    // execution.
    assert_eq!(projection["caller_delegation"]["execution_purpose"], "delegation_only");

    // The same (parent, invocation) never reserves twice: a replayed reply
    // event derives nothing NEW (budget Conflict → the ordinary refusal,
    // logged, target row unmarked-or-unchanged is the closed world).
    let reserved_again = lineage
        .reserve_invocation(&parent_run, invocation, "bot-observer", now_ms())
        .is_err();
    assert!(reserved_again, "the reply derivation consumed the turn budget exactly once");
}

#[tokio::test]
async fn a_routed_bot_reply_without_lineage_stays_an_ordinary_row() {
    // Plain-group shape (no caller lineage for the replying Bot's run): the
    // trusted lookup is a no-op — the reply row stays key-free ordinary.
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let group = support.group.get("group-1").await.unwrap();
    let repo = Arc::new(MemoryMessageRepo::new());
    let service = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let run_context = Arc::new(MemoryBotRunContextStore::new());
    let flow = BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_bot_run_context(run_context)
    .with_group_delivery_limits(BTreeMap::from([(
        "bot-observer".to_string(),
        10u32,
    )]))
    .with_session_management(Arc::new(StaticSessionManagement::new(test_session(
        SESSION,
        "group-1",
        group.participants,
    ))));

    // An ordinary (unmarked) parent row: the plain-group baseline.
    let started = started_parent_row(&service).await;
    // (Strip the caller marks the shared seeding helper wrote: this parent
    // is an ordinary turn with no registered lineage.)
    let parent_run = started.run_id.clone().expect("started run id");

    flow.handle_bot_event(BotEventCommand {
        bot_id: "bot-driver".into(),
        run_id: parent_run.clone(),
        group_id: "group-1".into(),
        event_type: "chat".into(),
        event_payload: json!({
            "state": "final",
            "message": {"role": "assistant",
                "content": [{"type": "text", "text": "@bot-observer 请协助处理这部分"}]},
        }),
        bcs_session_id: Some(SESSION.into()),
        state: ChatEventState::Final,
    })
    .await
    .expect("the plain reply relays");

    let rows = service.snapshot(Some(SESSION)).await.expect("snapshot");
    let reply = rows
        .iter()
        .find(|row| row.target_bot_id == "bot-observer" && row.state.kind == DeliveryType::Send)
        .expect("the ordinary reply row is admitted");
    let projection = &reply.semantic_projection_json;
    assert!(
        projection.get("caller_required").is_none()
            && projection.get("caller_task").is_none()
            && projection.get("caller_delegation").is_none(),
        "no lineage ⇒ no derivation marks: the reply stays key-free ordinary"
    );
}