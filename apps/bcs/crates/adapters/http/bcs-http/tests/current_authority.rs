//! Legacy-route authority cutover tests (plan Task 12, spec §12.2/§12.4 +
//! brief Step 1/2).
//!
//! The routes under test are the LEGACY `bcs-http` surfaces that used to
//! derive business authorization from `created_by` (or the creator
//! relation):
//!
//! - `PUT /actors/{id}/status` — a former creator whose `created_by` still
//!   matches but who holds NO current owner/manager role must be DENIED
//!   (`former_creator_without_role_legacy_patch.is_err()`); the CURRENT
//!   owner and managers remain allowed. The Bot-ID suffix never derives
//!   authority.
//! - `GET /bots/my` — now serves the SAME mine union labels as the v1 mine
//!   projection (`access_relation` ∈ {"owner","manager"}, never a
//!   creator-only list) while strictly preserving its legacy filter
//!   (`active_only`), sort (active first, id ascending) and pagination.
//!
//! All fixtures are the REAL application services over the REAL memory
//! authority store (`bcs_bot_store::MemoryBotRepo` with its Task 3 role-row
//! shape) — no recording shortcuts on the authorization answers.

use std::sync::Arc;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_bot::{
    ActorDirectory, Bot, BotCandidateSearchCore, BotCore, BotControlPlaneCore,
    EmptyWorkerProfileCoreService,
};
use bcs_bot_store::MemoryBotRepo;
use bcs_bot_store::provider::{
    MemoryBotProviderStore, MemoryProviderStore, ProviderBindingProjection,
};
use bcs_service_api::BotRepoPort as _;
use bcs_service_api::{
    ActorDirectoryService, ActorStatus, BotCapabilities, BotControlPlaneCoreService,
    BotRegistryCoreService, FriendCoreService, RelationCoreService, ServiceResult,
};
use bcs_services_container::{Services, ServicesBuilder};
use bcs_service_api::application::v1::BotAuthorityHook;
use bcs_test_support::{NoopFriendCoreService, NoopRelationCoreService};
use bcs_user_directory_api::UserDirectoryPlugin;
use serde_json::Value;
use tempfile::TempDir;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Fixture: a REAL owner/manager authority over the memory bot store
// ---------------------------------------------------------------------------

const FORMER_CREATOR: &str = "2002";
const CURRENT_OWNER: &str = "1001";
const CURRENT_MANAGER: &str = "3003";

struct AuthorityFixture {
    bot_repo: Arc<MemoryBotRepo>,
    registry: Arc<BotCore>,
    _temp_dir: TempDir,
}

async fn authority_fixture() -> AuthorityFixture {
    let temp_dir = TempDir::new().unwrap();
    let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
    let registry = Arc::new(BotCore::with_repo(bot_repo.clone()));
    AuthorityFixture {
        bot_repo,
        registry,
        _temp_dir: temp_dir,
    }
}

/// A real `BotAuthorityHook` equivalent grounding (Core over the same
/// bot authority repo) — matches bootstrap wiring; application services ask
/// this hook for the live role facts.
async fn seed_named_bot(fixture: &AuthorityFixture, bot_id: &str) {
    // The memory control-plane record only projects ONBOARDED bots (a
    // non-empty name); register through the real registry so the mine
    // projection behaves like a production onboarded bot.
    fixture
        .registry
        .register(
            bot_id.to_string(),
            BotCapabilities {
                name: Some(bot_id.to_string()),
                ..BotCapabilities::default()
            },
        )
        .await
        .unwrap();
}

async fn seed_supported_authority(fixture: &AuthorityFixture) {
    let bot_repo = fixture.bot_repo.clone();
    for bot in ["bot-legacy", "bot-managed"] {
        seed_named_bot(fixture, bot).await;
    }
    // Physical bots and their live role rows. `created_by` keeps the
    // historical creation fact for the former-creator scenario.
    bot_repo
        .seed_authority_owned("bot-legacy", CURRENT_OWNER)
        .await
        .unwrap();
    bot_repo
        .seed_authority_owned("bot-managed", CURRENT_OWNER)
        .await
        .unwrap();
    bot_repo
        .seed_authority_manager_source("bot-managed", CURRENT_MANAGER, "direct", "manual")
        .await
        .unwrap();
    // The former creator's historical creation fact: `created_by` keeps
    // 2002, but NO current role row for 2002 anywhere.
    fixture
        .registry
        .save_created_by("bot-legacy", FORMER_CREATOR, true)
        .await
        .unwrap();
}

fn services_with_authority(
    bot_repo: Arc<MemoryBotRepo>,
    bot_core: Arc<BotCore>,
    actor_directory: Arc<dyn ActorDirectoryService>,
) -> Services {
    let registry: Arc<dyn BotRegistryCoreService> = bot_core.clone();
    let friend: Arc<dyn FriendCoreService> = Arc::new(NoopFriendCoreService);
    let relation: Arc<dyn RelationCoreService> = Arc::new(NoopRelationCoreService);
    let provider_store = Arc::new(MemoryProviderStore::new());
    let bot_providers = Arc::new(MemoryBotProviderStore::new(
        bot_repo.clone() as Arc<dyn bcs_service_api::port::repo::BotRepoPort>,
        provider_store.clone(),
    ));
    let provider_bindings = Arc::new(ProviderBindingProjection::new(
        provider_store.clone(),
        bot_providers.clone(),
        bcs_domain::bot_provider::DownlinkDetectionSource::default(),
    ));
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(
        BotControlPlaneCore::new(
            bot_repo.clone(),
            provider_store.clone(),
            provider_bindings.clone(),
        )
        .with_bot_provider_repo(bot_providers.clone()),
    );
    let bot_use_cases = Arc::new(
        Bot::new_with_friend(registry.clone(), friend.clone())
            .with_relation(relation.clone())
            .with_control_plane(control_plane.clone()),
    );
    ServicesBuilder::new()
        .registry(registry)
        .friend(friend)
        .relation(relation)
        .actor_directory(actor_directory)
        .bot_query(bot_use_cases.clone())
        .bot_management(bot_use_cases.clone())
        .bot_discovery(bot_use_cases)
        .build_for_test()
}



fn static_auth_chain(staff_no: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(staff_no.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

fn app_for(services: Services, staff_no: &str) -> axum::Router {
    let chain = static_auth_chain(staff_no);
    build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )))
}

// ---------------------------------------------------------------------------
// PUT /actors/{id}/status — former creator vs live roles
// ---------------------------------------------------------------------------

async fn actor_directory_for(
    bot_repo: Arc<MemoryBotRepo>,
    registry: Arc<dyn BotRegistryCoreService>,
) -> Arc<dyn ActorDirectoryService> {
    let authority_core: Arc<dyn bcs_service_api::core::BotAuthorityCoreService> =
        Arc::new(bcs_edge_permission::authority::BotAuthorityCoreServiceImpl::new(bot_repo));
    let hook: Arc<dyn BotAuthorityHook> = Arc::new(SeededAuthorityHook { core: authority_core });
    let friend: Arc<dyn FriendCoreService> = Arc::new(NoopFriendCoreService);
    Arc::new(
        ActorDirectory::new(
            registry.clone(),
            friend.clone(),
            Arc::new(EmptyWorkerProfileCoreService),
            Arc::new(BotCandidateSearchCore::new(
                registry,
                friend,
                Arc::new(EmptyWorkerProfileCoreService),
                0.0,
            )),
        )
        .with_authority(hook),
    )
}

/// Thin live-authority hook: the strict Core (over the same memory role
/// rows) answers through the REAL core contract.
struct SeededAuthorityHook {
    core: Arc<dyn bcs_service_api::core::BotAuthorityCoreService>,
}

#[async_trait::async_trait]
impl BotAuthorityHook for SeededAuthorityHook {
    async fn can_manage(&self, user_id: &str, bot_id: &str) -> ServiceResult<bool> {
        Ok(self.core.role(user_id, bot_id).await?.is_some())
    }

    async fn require_owner(&self, user_id: &str, bot_id: &str) -> ServiceResult<()> {
        match self.core.role(user_id, bot_id).await? {
            Some(bcs_service_api::types::BotAccessRelation::Owner) => Ok(()),
            _ => Err(bcs_service_api::ServiceError::Authority(
                bcs_service_api::types::error::AuthorityError::Forbidden(format!(
                    "user '{user_id}' is not the owner of bot '{bot_id}'"
                )),
            )),
        }
    }
}

#[tokio::test]
async fn legacy_actor_status_patch_denies_former_creator_without_role() {
    let fixture = authority_fixture().await;
    seed_supported_authority(&fixture).await;
    let actor_directory = actor_directory_for(fixture.bot_repo.clone(), fixture.registry.clone()).await;
    let services = services_with_authority(
        fixture.bot_repo.clone(),
        fixture.registry.clone(),
        actor_directory,
    );
    let app = app_for(services, FORMER_CREATOR);

    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/actors/bot-legacy/status")
                .header("content-type", "application/json")
                .body(Body::from(
                    serde_json::json!({ "status": "hidden" }).to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    // The brief's RED assertion: `former_creator_without_role_legacy_patch
    // .is_err()` — surfaced as the legacy 403 (the route's error mapping of
    // the application's deny).
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn legacy_actor_status_patch_allows_current_owner_and_manager_only() {
    let fixture = authority_fixture().await;
    seed_supported_authority(&fixture).await;

    for (staff_no, bot_id, expected) in [
        (CURRENT_OWNER, "bot-legacy", StatusCode::OK),
        (CURRENT_MANAGER, "bot-managed", StatusCode::OK),
        (CURRENT_MANAGER, "bot-legacy", StatusCode::FORBIDDEN),
    ] {
        let fresh = authority_fixture().await;
        // NOTE: the fixtures are per-case so each HTTP one-shot runs on its
        // own store (the route test is one request per app instance).
        seed_supported_authority(&fresh).await;
        let actor_directory = actor_directory_for(fresh.bot_repo.clone(), fresh.registry.clone()).await;
        let services = services_with_authority(
            fresh.bot_repo.clone(),
            Arc::new(BotCore::with_repo(fresh.bot_repo.clone())),
            actor_directory,
        );
        let app = app_for(services, staff_no);
        let response = app
            .oneshot(
                Request::builder()
                    .method("PUT")
                    .uri(format!("/actors/{bot_id}/status"))
                    .header("content-type", "application/json")
                    .body(Body::from(
                        serde_json::json!({ "status": "hidden" }).to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), expected, "caller {staff_no} on {bot_id}");
        let _ = fixture;
    }
}

// ---------------------------------------------------------------------------
// GET /bots/my — union labels with legacy filters/sort kept
// ---------------------------------------------------------------------------

#[tokio::test]
async fn legacy_bots_my_serves_union_labels_and_keeps_filters() {
    let fresh = authority_fixture().await;
    seed_supported_authority(&fresh).await;
    // `bot-creator-only` keeps `created_by` = 1001 but its ownership went
    // to ANOTHER user — proves the mine lane does not fall back to the
    // historical creation fact.
    for bot in ["bot-legacy", "bot-managed", "bot-creator-only"] {
        seed_named_bot(&fresh, bot).await;
    }
    fresh
        .bot_repo
        .seed_authority_owned("bot-creator-only", "9999")
        .await
        .unwrap();
    fresh
        .bot_repo
        .save_created_by("bot-creator-only", CURRENT_OWNER, true)
        .await
        .unwrap();

    let actor_directory = actor_directory_for(fresh.bot_repo.clone(), fresh.registry.clone()).await;
    let services = services_with_authority(
        fresh.bot_repo.clone(),
        fresh.registry.clone(),
        actor_directory,
    );
    let app = app_for(services, CURRENT_OWNER);

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/bots/my?offset=0&limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();

    let items = json["items"].as_array().unwrap();
    assert!(items
        .iter()
        .all(|item| matches!(item["access_relation"].as_str(), Some("owner" | "manager"))),
        "every mine item carries the same label vocabulary as the v1 mine projection");
    let uuids: Vec<&str> = items
        .iter()
        .map(|item| item["bot_uuid"].as_str().unwrap())
        .collect();
    assert!(
        uuids.iter().any(|id| *id == "bot-legacy"),
        "the owned bot appears: {uuids:?}"
    );
    assert!(
        uuids.iter().any(|id| *id == "bot-managed"),
        "the managed bot appears: {uuids:?}"
    )
    ;
    assert!(
        !uuids.iter().any(|id| *id == "bot-creator-only"),
        "a creation fact the caller no longer holds a role for must not appear"
    );
    assert_eq!(json["total"], serde_json::json!(2));

    // `active_only` filter still applies (nothing is runtime-active in the
    // fixture, so the filtered page is empty but total reflects it).
    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/my?active_only=true&offset=0&limit=20")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["items"].as_array().unwrap().len(), 0);
    assert_eq!(json["total"], serde_json::json!(0));
}

#[tokio::test]
async fn legacy_bots_my_keeps_active_first_id_ascending_sort() {
    let fresh = authority_fixture().await;
    for bot in ["bot-dd", "bot-aa", "bot-zz", "bot-mm"] {
        seed_named_bot(&fresh, bot).await;
        fresh.bot_repo.seed_authority_owned(bot, CURRENT_OWNER).await.unwrap();
    }
    let actor_directory = actor_directory_for(fresh.bot_repo.clone(), fresh.registry.clone()).await;
    let services = services_with_authority(
        fresh.bot_repo.clone(),
        fresh.registry.clone(),
        actor_directory,
    );
    let app = app_for(services, CURRENT_OWNER);
    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/my?offset=1&limit=2")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["offset"], serde_json::json!(1));
    assert_eq!(json["limit"], serde_json::json!(2));
    // Total counts the WHOLE union before the page window.
    assert_eq!(json["total"], serde_json::json!(4));
    let uuids: Vec<&str> = json["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["bot_uuid"].as_str().unwrap())
        .collect();
    // The legacy [active-first, id ASC] sort: the full order is
    // [bot-aa, bot-dd, bot-mm, bot-zz]; page 2 (offset=1, limit=2) keeps it.
    assert_eq!(uuids, ["bot-dd", "bot-mm"], "the legacy [active-first, id ASC] page-2 window");
}

#[allow(dead_code)]
struct _NoopUserDirectory;

#[async_trait::async_trait]
#[allow(dead_code)]
impl UserDirectoryPlugin for _NoopUserDirectory {
    async fn lookup_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<
        Option<bcs_user_directory_api::UserDirectoryProfile>,
        bcs_user_directory_api::UserDirectoryError,
    > {
        let _ = staff_no;
        Ok(None)
    }

    async fn lookup_department_by_staff_no(
        &self,
        staff_no: &str,
    ) -> Result<Option<String>, bcs_user_directory_api::UserDirectoryError> {
        let _ = staff_no;
        Ok(None)
    }
}