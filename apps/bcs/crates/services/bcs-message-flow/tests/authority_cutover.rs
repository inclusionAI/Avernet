//! Message-flow authority cutover (bot-authority plan
//! Task 6.5, entry-map rows 20–22 + the a2a-chat
//! discovery): Human→Bot control judgments on message
//! sending and history viewing follow the CURRENT live
//! management edge through the real hook chain. The
//! historical `created_by` (and the owner-suffix fallback)
//! authorize nothing, and a failing authority read is an
//! internal error — never a deny-as-verdict, never a
//! silent fallback.
//!
//! Matrix at REAL entries (plan 6.2): A(owner) / M(manager)
//! / X(unrelated), the A→B handover (old owner
//! fallback-free denied DESPITE the `created_by` mirror),
//! manager withdrawal, authority-read failure
//! propagation, and the Bot-self identity triad.

#![allow(clippy::expect_used, clippy::unwrap_used)]

use bcs_message_flow::{BcsGroupMessageHistory, BcsMessageFlow};
use bcs_service_api::{
    BotDeliveryTarget, BotRegistryCoreService, CallerContext,
    GroupChatCommand, GroupHistoryBotRequestPort,
    GroupHistoryCommand, GroupMessageHistoryService, GroupUseCaseError,
    HumanActor, MessageFlowService, ServiceError,
};
use serde_json::json;
use std::sync::Arc;

#[path = "../../../test-support/message_flow_contract_support.rs"]
mod support;

use bcs_test_support::LazySeedAuthorityHook;

/// The REAL authority chain: `pairs` seed live owner edges
/// over the same store the hook reads.
fn staff_authority(pairs: &[(&str, &str)]) -> Arc<LazySeedAuthorityHook> {
    let chain = bcs_test_support::lazy_authority_chain();
    for (bot_id, owner) in pairs {
        chain.hook.seed_owner(bot_id, owner);
    }
    chain.hook
}

fn human(staff: &str) -> CallerContext {
    CallerContext::Human(HumanActor {
        actor_id: format!("human_{staff}"),
        staff_no: staff.to_string(),
    })
}

fn flow(support: &support::FlowTestSupport) -> BcsMessageFlow {
    BcsMessageFlow::new(
        support.group.clone(),
        support.routing.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        support.frontend_delivery.clone(),
    )
}

fn history(
    support: &support::FlowTestSupport,
    authority: Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>,
) -> BcsGroupMessageHistory {
    struct NoHistoryRequests;
    #[async_trait::async_trait]
    impl GroupHistoryBotRequestPort for NoHistoryRequests {
        async fn send_history_request(
            &self,
            _target: BotDeliveryTarget,
            _path: &str,
            _params: serde_json::Value,
            _timeout_ms: u64,
        ) -> Result<serde_json::Value, String> {
            Ok(json!({"messages": []}))
        }
    }
    BcsGroupMessageHistory::new(
        support.group.clone(),
        support.registry.clone(),
        support.bot_delivery.clone(),
        Arc::new(NoHistoryRequests),
    )
    .with_bot_authority_hook(Some(authority))
}

fn speak_as(entry: GroupChatCommand, sender: &str) -> GroupChatCommand {
    GroupChatCommand {
        requested_sender_id: Some(sender.to_string()),
        ..entry
    }
}

fn entry(caller: CallerContext) -> GroupChatCommand {
    GroupChatCommand {
        caller_ingress: None,
        caller,
        group_id: "group-1".to_string(),
        requested_sender_id: None,
        message: "as my bot".to_string(),
        session_id: None,
        provider_bypass_headers: Vec::new(),
    }
}

#[tokio::test]
async fn web_send_steering_follows_the_current_control_matrix() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer()
        .await;
    let authority = staff_authority(&[("bot-driver", "1")]);
    authority.seed_manager("bot-driver", "9");
    // Display mirror only — never the authorization source.
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();

    let flow = flow(&support).with_bot_authority_hook(Some(authority.clone()));

    // A(owner): the CURRENT owner steers the driver Bot.
    flow.handle_group_chat(speak_as(entry(human("1")), "bot-driver"))
        .await
        .expect("the CURRENT owner speaks as the controlled Bot");

    // M(manager): same control judgment via the manager edge.
    flow.handle_group_chat(speak_as(entry(human("9")), "bot-driver"))
        .await
        .expect("the CURRENT manager speaks as the controlled Bot");

    // X(unrelated): no edge → denied despite the mirror.
    let denied = flow
        .handle_group_chat(speak_as(entry(human("7")), "bot-driver"))
        .await
        .expect_err("an unrelated Human is denied");
    assert!(matches!(denied, ServiceError::Unauthorized(_)));

    // Manager withdrawal closes the whole steering judgment.
    authority.revoke_manager("bot-driver", "9");
    let denied = flow
        .handle_group_chat(speak_as(entry(human("9")), "bot-driver"))
        .await
        .expect_err("the withdrawn manager is fallback-free denied");
    assert!(matches!(denied, ServiceError::Unauthorized(_)));
}

#[tokio::test]
async fn handover_leaves_the_old_owner_fallback_free_denied() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer()
        .await;
    // Historical display mirror deliberately kept: the legacy
    // `created_by` still reads "1" — it must NOT authorize.
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = flow(&support).with_bot_authority_hook(Some(staff_authority(&[])));

    let denied = flow
        .handle_group_chat(speak_as(entry(human("1")), "bot-driver"))
        .await
        .expect_err("the revoked owner is fallback-free denied");
    assert!(matches!(denied, ServiceError::Unauthorized(_)));
}

#[tokio::test]
async fn owner_suffix_and_created_by_fallbacks_are_dead() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer()
        .await;
    // No live edge at all: the legacy `created_by` mirror and
    // the owner-suffix heuristic (no `:1` suffix on
    // `bot-driver`, but the check itself is gone) authorize
    // nothing.
    support
        .registry
        .save_created_by("bot-driver", "1", true)
        .await
        .unwrap();
    let flow = flow(&support).with_bot_authority_hook(Some(staff_authority(&[])));

    let denied = flow
        .handle_group_chat(speak_as(entry(human("1")), "bot-driver"))
        .await
        .expect_err("a `created_by` mirror without a live edge is denied");
    assert!(matches!(denied, ServiceError::Unauthorized(_)));
}

#[tokio::test]
async fn authority_read_failure_is_internal_never_a_deny() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer()
        .await;
    let flow =
        flow(&support).with_bot_authority_hook(Some(bcs_test_support::failing_authority_hook()));

    let error = flow
        .handle_group_chat(speak_as(entry(human("1")), "bot-driver"))
        .await
        .expect_err("a failing authority read is an internal error");
    assert!(
        matches!(error, ServiceError::InternalError(_)),
        "got {error:?}"
    );
}

#[tokio::test]
async fn group_history_access_and_view_follow_the_current_edges() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer()
        .await;
    let authority = staff_authority(&[("bot-observer", "1")]);

    // A(owner of the VIEW Bot): group access via the live edge
    // + view-actor ownership via the same edge.
    let result = history(&support, authority.clone())
        .get_history(GroupHistoryCommand {
            caller: human("1"),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 10,
            before: None,
        })
        .await;
    assert!(result.is_ok(), "got {result:?}");

    // M(manager): the same two judgments through the manager
    // edge.
    authority.seed_manager("bot-observer", "9");
    let result = history(&support, authority.clone())
        .get_history(GroupHistoryCommand {
            caller: human("9"),
            group_id: "group-1".to_string(),
            view_bot_id: Some("bot-observer".to_string()),
            limit: 10,
            before: None,
        })
        .await;
    assert!(result.is_ok(), "manager edge reads history: {result:?}");

    // X(unrelated): denied.
    let denied = history(&support, authority.clone())
        .get_history(GroupHistoryCommand {
            caller: human("7"),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 10,
            before: None,
        })
        .await
        .expect_err("an unrelated Human gets no group history");
    assert!(matches!(denied, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn group_history_authorizes_against_the_snapshot_not_the_display_cache() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer()
        .await;
    // alice is NOT a group participant herself; her only way in
    // is controlling the participant Bot through a live edge.
    let authority = staff_authority(&[("bot-observer", "alice")]);

    // Positive control: a Human controlling a participant Bot
    // reads history through the live edge.
    let allowed = history(&support, authority.clone())
        .get_history(GroupHistoryCommand {
            caller: human("alice"),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 10,
            before: None,
        })
        .await;
    assert!(
        allowed.is_ok(),
        "member Bot control reads history: {allowed:?}"
    );

    // The Bot is removed from the MASTER data (a second store
    // instance committed it); the display cache still lists it.
    // The access decision must follow the strict snapshot
    // (spec §8.3) — a master-removed Bot admits nobody.
    support
        .group
        .remove_participant_from_master("group-1", "bot-observer")
        .await;
    let denied = history(&support, authority)
        .get_history(GroupHistoryCommand {
            caller: human("alice"),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 10,
            before: None,
        })
        .await
        .expect_err("a master-removed participant Bot must not keep admitting");
    assert!(matches!(denied, GroupUseCaseError::Forbidden(_)));
}

#[tokio::test]
async fn history_handover_revocation_and_failing_read() {
    let support = support::FlowTestSupport::new_group_with_driver_and_observer()
        .await;
    // The old controller's edges are revoked; the display
    // mirror stays — the judgment is fallback-free.
    let authority = staff_authority(&[]);
    // A NON-participant controller whose edges are revoked
    // (the Human self-participant leg must not be confused
    // with it) is denied.
    let denied = history(&support, authority.clone())
        .get_history(GroupHistoryCommand {
            caller: human("7"),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 10,
            before: None,
        })
        .await
        .expect_err("the revoked controller is denied");
    assert!(matches!(denied, GroupUseCaseError::Forbidden(_)));

    let error = history(&support, bcs_test_support::failing_authority_hook())
        .get_history(GroupHistoryCommand {
            caller: human("7"),
            group_id: "group-1".to_string(),
            view_bot_id: None,
            limit: 10,
            before: None,
        })
        .await
        .expect_err("a failing authority read is an internal error");
    assert!(
        matches!(error, GroupUseCaseError::Service(ServiceError::InternalError(_))),
        "got {error:?}"
    );
}
