//! Bounds of the "read through a controlled participating Bot"
//! view leg: each basis Bot is one COMPLETE authorization basis
//! — its own join boundary plus its own participant scope, with
//! the Bot itself as the view actor — and permissions of
//! different basis Bots are never spliced together to admit a
//! message no single basis fully admits.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_domain::{DeliveryType, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_message_flow::{managed_delivery::ManagedMessageDelivery, BcsMessageFlow};
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};
use bcs_service_api::types::MessageViewScope;
use bcs_service_api::{
    CallerContext, HumanActor, ManagedMessageDeliveryService, MessageFlowService, Participant,
    ParticipantRole,
};
use bcs_test_support::{authority_hook_for, grant_owner_edge};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "support/session.rs"]
mod session_support;

const GROUP: &str = "group-1";
const SESSION: &str = "group-1:bounds";

struct Rig {
    delivery: Arc<ManagedMessageDelivery>,
    flow: BcsMessageFlow,
}

/// A static-session rig where the Owner Human controls both
/// participating Bots (`shared-bot` keeps the RESTRICTED
/// Participant scope in the fixture data so audience misses are
/// observable; `shared-bot-2` keeps the default Full Bot scope).
/// `join` optionally pins the session's join-sequence map.
async fn rig(scopes_and_join: Option<serde_json::Value>) -> Rig {
    let fixture = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    let bots = Arc::new(bcs_bot_store::MemoryBotRepo::new());
    grant_owner_edge(&bots, "shared-bot", "owner").await;
    grant_owner_edge(&bots, "shared-bot-2", "owner").await;
    let authority = authority_hook_for(bots);

    let mut first = Participant::bot("shared-bot", ParticipantRole::Observer);
    first.message_view_scope = MessageViewScope::Participant;
    let second = Participant::bot("shared-bot-2", ParticipantRole::Observer);
    let mut session =
        session_support::test_session(SESSION, GROUP, vec![first, second]);
    if let Some(join) = scopes_and_join {
        session.participant_join_seq = Some(join);
    }

    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let delivery = Arc::new(ManagedMessageDelivery::new(repo.clone()));
    let flow = BcsMessageFlow::new(
        fixture.group,
        fixture.routing,
        fixture.registry,
        fixture.bot_delivery,
        fixture.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(delivery.clone())
    .with_session_management(Arc::new(
        session_support::StaticSessionManagement::new(session),
    ))
    .with_bot_authority_hook(Some(authority));
    Rig { delivery, flow }
}

/// Admits one message; with `directed` the message becomes a
/// ManagerWorker-domain artifact directed at exactly those actor
/// ids (session_seq is assigned in admission order: 1, 2, …).
async fn admit(rig: &Rig, message_id: &str, directed: Option<Vec<&str>>) {
    rig.delivery
        .admit(AdmitMessageDeliveries {
            display_message: None,
            message_id: message_id.into(),
            flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
            now_ms: 100,
            expire_at_ms: None,
            event: None,
            message: NewMessage {
                group_id: GROUP.into(),
                session_id: SESSION.into(),
                sender_id: "shared-bot".into(),
                sender_type: SenderType::Bot,
                message_type: "chat".into(),
                content: serde_json::json!({"text": "bounded read"}),
                client_msg_id: None,
                owner_bot_id: None,
                created_at: 100,
                run_id: "run-1".into(),
                visibility_domain: directed
                    .as_ref()
                    .map_or(MessageVisibilityDomain::Chat, |_| {
                        MessageVisibilityDomain::ManagerWorker
                    }),
                audience: directed.map(|actors| {
                    bcs_domain::MessageAudience::directed(actors).expect("directed audience")
                }),
            },
            targets: vec![DeliveryAdmissionTarget {
                rejection: None,
                target_bot_id: "shared-bot-2".into(),
                kind: DeliveryType::Send,
                max_queued: 10,
                semantic_projection_json: serde_json::json!({"version": 1}), run_id: None,}],
        })
        .await
        .unwrap();
}

async fn owner_query(rig: &Rig, message_id: &str) -> Result<usize, bcs_service_api::ServiceError> {
    rig.flow
        .query_message_deliveries(bcs_service_api::DeliveryStatusQuery {
            caller: CallerContext::Human(HumanActor {
                actor_id: "human_owner".into(),
                staff_no: "owner".into(),
            }),
            session_id: SESSION.into(),
            message_ids: vec![message_id.into()],
            client_msg_id: None,
        })
        .await
        .map(|rows| rows.len())
}

fn expect_forbidden(result: &Result<usize, bcs_service_api::ServiceError>, reason: &str) {
    let Err(error) = result else {
        panic!("expected Forbidden({reason}), got {result:?}");
    };
    assert!(
        matches!(&error, bcs_service_api::ServiceError::Forbidden(r) if r == reason),
        "expected Forbidden({reason}), got {error:?}"
    );
}

/// A message that predates the basis Bot's join point is outside
/// the Owner's history window; the same message inside the
/// window reads fine.
#[tokio::test]
async fn read_stops_at_the_basis_bot_join_boundary() {
    let predating = rig(Some(serde_json::json!({"shared-bot": 5, "shared-bot-2": 5}))).await;
    admit(&predating, "m-old", None).await; // session_seq 1 < join 5
    expect_forbidden(&owner_query(&predating, "m-old").await, "message predates session access");

    let in_window = rig(Some(serde_json::json!({"shared-bot": 1, "shared-bot-2": 1}))).await;
    admit(&in_window, "m-in", None).await; // session_seq 1 >= join 1
    assert_eq!(owner_query(&in_window, "m-in").await.expect("in-window read"), 1);
}

/// A directed artifact reaches the Owner through the basis Bot
/// the audience NAMES: the view actor is each basis Bot itself,
/// so a message directed at `shared-bot-2` is admitted by that
/// basis alone.
#[tokio::test]
async fn directed_audience_resolves_against_each_basis_bot() {
    let rig = rig(None).await;
    admit(&rig, "m-directed", Some(vec!["shared-bot-2"])).await;
    assert_eq!(owner_query(&rig, "m-directed").await.expect("directed read"), 1);
}

/// THE no-splice pin: `shared-bot` passes the join window but is
/// not in the directed audience (restricted Participant scope),
/// `shared-bot-2` IS named in the audience but joined after the
/// message — only splicing B2's audience with B1's window would
/// admit it, and the implementation must not.
#[tokio::test]
async fn partial_permissions_never_splice_across_bots() {
    let rig = rig(Some(serde_json::json!({"shared-bot-2": 5}))).await;
    admit(&rig, "m-directed", Some(vec!["shared-bot-2"])).await; // seq 1
    expect_forbidden(
        &owner_query(&rig, "m-directed").await,
        "message audience is not visible",
    );
}
