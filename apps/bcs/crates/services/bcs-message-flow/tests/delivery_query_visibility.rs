//! Per-message visibility matrix for `query_message_deliveries`
//! over REAL managed-delivery rows: the entry authorization and
//! the per-message checks must agree end to end (spec §8
//! authority cutover; the `898870bd` round only opened the
//! session entry, leaving a non-member Owner/Manager to be
//! rejected by the Human-membership rule once a real delivery
//! row made the request reach `visible()`).
//!
//! Authority reads run against the REAL SQLite-backed
//! `DbBotAuthorityStore` + `BotAuthorityCoreServiceImpl` so the
//! legacy fallback window and live edge state behave exactly as
//! in production. Session access is static fixture data.
#![allow(clippy::expect_used, clippy::unwrap_used)]

use std::sync::Arc;

use bcs_domain::{DeliveryType, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_message_flow::BcsMessageFlow;
use bcs_service_api::port::repo::message_delivery::{
    AdmitMessageDeliveries, DeliveryAdmissionTarget,
};
use bcs_service_api::{
    BotRegistryCoreService, CallerContext, DeliveryStatusQuery, GroupCoreService, HumanActor,
    ManagedMessageDeliveryService, MessageFlowService,
};

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;
#[path = "support/session.rs"]
mod session_support;

/// One matrix row. `member` keeps `human_1` in the Session; a
/// `private` message sets `owner_bot_id = bot-driver`; `fallback`
/// toggles the §10.2a legacy creator window; `owner` grants
/// `human_<owner>` the CURRENT owner edge on `bot-driver`;
/// `has_row` admits one real delivery row for `m-1`.
async fn scenario(
    member: bool,
    private: bool,
    fallback: bool,
    owner: Option<&str>,
    has_row: bool,
) -> Result<usize, String> {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer().await;
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let group = support.group.get("group-1").await.unwrap();
    let mut participants = group.participants;
    if !member {
        participants.retain(|p| p.bot_uuid != "human_1");
    }
    let repo = Arc::new(bcs_message_store::MemoryMessageRepo::new());
    let service = Arc::new(bcs_message_flow::managed_delivery::ManagedMessageDelivery::new(
        repo.clone(),
    ));
    let flow = BcsMessageFlow::new(
        support.group,
        support.routing,
        support.registry,
        support.bot_delivery,
        support.frontend_delivery,
    )
    .with_message_repo(repo)
    .with_managed_deliveries(service.clone())
    .with_session_management(Arc::new(session_support::StaticSessionManagement::new(
        session_support::test_session("group-1:matrix", "group-1", participants),
    )));

    use bcs_test_support::bot_authority_sqlite::{execute, new_fixture};
    use bcs_test_support::contract::bot_authority_contract::BotAuthorityContractFixture;
    let fixture = new_fixture("matrix").await;
    fixture
        .seed_bot("matrix", "bot-driver", if owner.is_some() { 1 } else { 0 })
        .await;
    execute(
        fixture.db.as_ref(),
        "UPDATE bcs_bots SET created_by = '1' WHERE bot_uuid = 'bot-driver'",
        vec![],
    )
    .await;
    if let Some(owner) = owner {
        fixture
            .grant_edge(
                "matrix",
                "bot-driver",
                &format!("human_{owner}"),
                bcs_service_api::types::bot_authority::BotAccessRelation::Owner,
            )
            .await;
    }
    let store = bcs_edge_permission_store::DbBotAuthorityStore::new(
        fixture.db.clone(),
        bcs_db_api::DbSqlFlavor::Sqlite,
        "matrix".into(),
    )
    .with_legacy_created_by_fallback(fallback);
    let hook = bcs_edge_permission::BotAuthorityCoreServiceImpl::new(Arc::new(store), "matrix");
    let flow = flow.with_bot_authority_hook(Some(Arc::new(hook)));

    if has_row {
        service
            .admit(AdmitMessageDeliveries {
                display_message: None,
                message_id: "m-1".into(),
                flow_kind: bcs_domain::message_delivery::DeliveryFlowKind::Group,
                now_ms: 100,
                expire_at_ms: None,
                event: None,
                message: NewMessage {
                    group_id: "group-1".into(),
                    session_id: "group-1:matrix".into(),
                    sender_id: "bot-driver".into(),
                    sender_type: SenderType::Bot,
                    message_type: "chat".into(),
                    content: serde_json::json!({"text": "matrix"}),
                    client_msg_id: None,
                    owner_bot_id: private.then(|| "bot-driver".into()),
                    created_at: 100,
                    run_id: "run-1".into(),
                    visibility_domain: MessageVisibilityDomain::Chat,
                    audience: None,
                },
                targets: vec![DeliveryAdmissionTarget {
                    rejection: None,
                    target_bot_id: "bot-observer".into(),
                    kind: DeliveryType::Send,
                    max_queued: 10,
                    semantic_projection_json: serde_json::json!({"version": 1}), run_id: None,}],
            })
            .await
            .unwrap();
    }
    flow.query_message_deliveries(DeliveryStatusQuery {
        caller: CallerContext::Human(HumanActor {
            actor_id: "human_1".into(),
            staff_no: "1".into(),
        }),
        session_id: "group-1:matrix".into(),
        message_ids: vec!["m-1".into()],
        client_msg_id: None,
    })
    .await
    .map(|rows| rows.len())
    .map_err(|error| format!("{error:?}"))
}

fn expect_ok(result: &Result<usize, String>, rows: usize) {
    assert_eq!(result.as_ref().copied(), Ok(rows), "unexpected outcome: {result:?}");
}

fn expect_forbidden(result: &Result<usize, String>, reason: &str) {
    let Err(error) = result else {
        panic!("expected Forbidden({reason}), got {result:?}");
    };
    assert!(
        error.contains(reason),
        "expected Forbidden({reason}), got {error}"
    );
}

#[tokio::test]
async fn member_public_message_needs_no_bot_role() {
    expect_ok(&scenario(true, false, false, None, true).await, 1);
}

#[tokio::test]
async fn member_private_message_inside_legacy_fallback_window() {
    expect_ok(&scenario(true, true, true, None, true).await, 1);
}

#[tokio::test]
async fn member_private_message_outside_fallback_window_stays_denied() {
    expect_forbidden(
        &scenario(true, true, false, None, true).await,
        "private message is not visible",
    );
}

#[tokio::test]
async fn member_private_message_visible_to_current_owner() {
    expect_ok(&scenario(true, true, true, Some("1"), true).await, 1);
}

#[tokio::test]
async fn member_private_message_hidden_from_former_creator() {
    expect_forbidden(
        &scenario(true, true, true, Some("2"), true).await,
        "private message is not visible",
    );
}

#[tokio::test]
async fn nonmember_owner_reads_empty_result() {
    expect_ok(&scenario(false, false, true, Some("1"), false).await, 0);
}

/// THE regression of the 0957bfed5 round: a non-member Human who
/// CURRENTLY controls a participating Bot passed the session entry
/// but was rejected by the Human-membership rule once a real
/// delivery row reached `visible()`. The basis-Bot read context
/// must carry through the per-message checks.
#[tokio::test]
async fn nonmember_owner_reads_real_delivery_through_controlled_bot() {
    expect_ok(&scenario(false, false, true, Some("1"), true).await, 1);
}

/// The mate case: a non-member Human with NO live edge on any
/// participating Bot stays denied at the entry, with or without
/// rows. The fallback window is OFF here so `created_by` alone
/// grants nothing — the deny is a genuine no-control verdict.
#[tokio::test]
async fn nonmember_without_control_stays_denied_with_real_delivery() {
    expect_forbidden(
        &scenario(false, false, false, None, true).await,
        "session membership required",
    );
}
