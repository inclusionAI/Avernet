//! Legacy `/bots/my` route contract (ruling T6c-1,
//! bot-authority plan Task 6.3, spec §8 first row):
//! the legacy mine page lists the owner/manager
//! UNION from the control-plane controllable read
//! over the SAME stored repo — a revoked owner
//! stops seeing the Bot, a manager gains the
//! entry, and the caller's onboarded Human self
//! entry rides along (ruling T6d-3: self
//! semantics on this page).

#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{Bot, BotControlPlaneCore, BotCore};
use bcs_bot_store::{MemoryBotRepo, MemoryProviderStore};
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::BotControlPlaneCoreService;
use tower::ServiceExt;
use bcs_services_container::{Services, ServicesBuilder};
use bcs_service_api::port::repo::BotRepoPort;
use bcs_service_api::{BotCapabilities, BotRegistryCoreService as _ey, Skill};
use bcs_test_support::LazySeedAuthorityHook;
use serde_json::Value;
use tempfile::TempDir;

// The verbatim bcs-http contract-test auth chain
// (mirrors route_contract.rs).
fn static_auth_chain(
    staff_no: &str,
    nick_name: &str,
) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

fn shared_route_authority() -> Arc<LazySeedAuthorityHook> {
    static AUTHORITY: std::sync::OnceLock<
        Arc<LazySeedAuthorityHook>,
    > = std::sync::OnceLock::new();
    AUTHORITY
        .get_or_init(|| {
            bcs_test_support::lazy_authority_chain().hook
        })
        .clone()
}

fn services_builder_with_bot_use_cases_and_control_plane(
    registry: Arc<BotCore>,
    control_plane: Arc<dyn BotControlPlaneCoreService>,
) -> ServicesBuilder {
    let bot_use_cases = Arc::new(
        Bot::new_with_friend(
            registry,
            Arc::new(bcs_test_support::NoopFriendCoreService),
        )
        // Ruling T6c-1: the legacy /bots/my page
        // lists through the control-plane union
        // read over the SAME stored repo.
        .with_control_plane(control_plane)
        .with_bot_authority(shared_route_authority()),
    );
    Services::builder()
        .bot_query(bot_use_cases.clone())
        .bot_management(bot_use_cases.clone())
        .bot_discovery(bot_use_cases)
}

#[tokio::test]
async fn my_bots_route_uses_mock_user_identity_and_union_filter() {
    let temp_dir = TempDir::new().unwrap();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(
        temp_dir.path().to_path_buf(),
    ));
    let provider_store = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::with_repo(bot_repo.clone()));
    let control_plane: Arc<dyn BotControlPlaneCoreService> =
        Arc::new(BotControlPlaneCore::new(
            bot_repo.clone(),
            provider_store.clone(),
            provider_store,
        ));
    for (bot_id, owner) in [("bot-owned", "alice"), ("bot-other", "bob")] {
        registry
            .register_with_owner_and_token(
                bot_id.to_string(),
                BotCapabilities {
                    name: Some(bot_id.to_string()),
                    summary: Some("Test bot".to_string()),
                    skills: vec![Skill::new("ops")],
                    visibility: "protected".to_string(),
                    ..BotCapabilities::default()
                },
                owner,
                &format!("token-{bot_id}"),
            )
            .await
            .unwrap();
    }
    registry
        .save_created_by("bot-owned", "alice", true)
        .await
        .unwrap();
    shared_route_authority().seed_owner("bot-owned", "alice");
    // The union read needs the edges on the SAME
    // repo the control plane reads.
    bcs_test_support::grant_owner_edge(&bot_repo, "bot-owned", "alice")
        .await;
    registry
        .save_created_by("bot-other", "bob", true)
        .await
        .unwrap();
    shared_route_authority().seed_owner("bot-other", "bob");
    bcs_test_support::grant_owner_edge(&bot_repo, "bot-other", "bob")
        .await;
    registry
        .register_streaming_connection("bot-owned".to_string())
        .await
        .unwrap();
    let services = services_builder_with_bot_use_cases_and_control_plane(
        registry,
        control_plane,
    )
    .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/my?offset=0&limit=10")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    // The edge union (bot-owned) plus the caller's own
    // onboarded Human actor row (T6d-3): the fixtures in
    // this suite ensure their Humans the production way.
    assert_eq!(json["total"], 2);
    assert_eq!(json["items"][0]["bot_uuid"], "bot-owned");
    assert_eq!(json["items"][0]["created_by"], "alice");
    assert_eq!(json["items"][0]["visibility"], "protected");
    assert_eq!(
        json["items"][0]["capabilities"]["skills"],
        serde_json::json!(["ops"])
    );
    assert_eq!(json["items"][0]["dynamic_status"]["status"], "active");
    // The other user's Bot stays filtered out; the second
    // entry is the caller's self, `alice`'s actor row.
    assert_eq!(json["items"][1]["bot_uuid"], "human_alice");
    assert!(!json["items"]
        .as_array()
        .expect("items array")
        .iter()
        .any(|item| item["bot_uuid"] == "bot-other"));
}

#[tokio::test]
async fn my_bots_route_lists_the_current_union_not_the_created_list() {
    let temp_dir = TempDir::new().unwrap();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(
        temp_dir.path().to_path_buf(),
    ));
    let provider_store = Arc::new(MemoryProviderStore::new());
    let registry = Arc::new(BotCore::with_repo(bot_repo.clone()));
    let control_plane: Arc<dyn BotControlPlaneCoreService> =
        Arc::new(BotControlPlaneCore::new(
            bot_repo.clone(),
            provider_store.clone(),
            provider_store,
        ));
    registry
        .register_with_owner_and_token(
            "bot-owned".to_string(),
            BotCapabilities {
                name: Some("bot-owned".to_string()),
                ..BotCapabilities::default()
            },
            "alice",
            "token-bot-owned",
        )
        .await
        .unwrap();
    registry
        .save_created_by("bot-owned", "alice", true)
        .await
        .unwrap();
    bcs_test_support::grant_owner_edge(&bot_repo, "bot-owned", "alice")
        .await;
    bcs_test_support::grant_manager_edge(
        &bot_repo,
        "bot-owned",
        "mallory",
    )
    .await;
    let services = services_builder_with_bot_use_cases_and_control_plane(
        registry,
        control_plane,
    )
    .build_for_test();

    async fn page(services: Services, user: &str) -> (StatusCode, Value) {
        let chain = static_auth_chain(user, user);
        let app = build_router(
            HttpAppState::new(services).with_user_identity(Arc::new(
                ChainUserIdentityPort::new(chain),
            )),
        );
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/bots/my?offset=0&limit=10")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        let status = response.status();
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        let json: Value = serde_json::from_slice(&body).unwrap();
        (status, json)
    }

    // A (owner) and M (manager) both see the Bot; each
    // page also carries the caller's own Human self entry
    // (the grant fixtures ensure the Human actor rows).
    for user in ["alice", "mallory"] {
        let (status, json) = page(services.clone(), user).await;
        assert_eq!(status, StatusCode::OK, "{user}");
        assert_eq!(
            json["total"], 2,
            "{user} sees the union entry and the self entry"
        );
        assert_eq!(json["items"][0]["bot_uuid"], "bot-owned", "{user}");
        assert_eq!(
            json["items"][1]["bot_uuid"],
            format!("human_{user}"),
            "{user}"
        );
    }

    // The A→B handover: A's ONLY leg falls away.
    bcs_test_support::revoke_authority_edge(
        &bot_repo,
        "bot-owned",
        "alice",
        bcs_bot_store::memory_authority::MemoryAuthorityEdgeKind::Owner,
    )
    .await;
    bcs_test_support::grant_owner_edge(&bot_repo, "bot-owned", "bob")
        .await;
    let (status, json) = page(services.clone(), "alice").await;
    assert_eq!(status, StatusCode::OK);
    // The Bot is gone from the revoked owner's page; only
    // her own Human self entry remains (spec §8 first row).
    assert_eq!(
        json["total"], 1,
        "the revoked owner no longer sees the Bot: {json}"
    );
    assert_eq!(json["items"][0]["bot_uuid"], "human_alice");
    let (_, json) = page(services.clone(), "bob").await;
    assert_eq!(json["total"], 2, "B sees the handed-over Bot and the self entry");
    let (_, json) = page(services.clone(), "mallory").await;
    assert_eq!(
        json["total"], 2,
        "the manager still sees the Bot and the self entry"
    );
}
