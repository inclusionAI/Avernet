#![allow(
    clippy::expect_used,
    reason = "test assertions intentionally fail fast"
)]

use std::sync::Arc;

use async_trait::async_trait;
use bcs_app_bot::{BotServiceConfig, InternalBotAttributesServiceImpl};
use bcs_bot::BotControlPlaneCore;
use bcs_bot_store::MemoryBotRepo;
use bcs_service_api::{
    BotCapabilities, BotControlPlaneCoreService, BotInternalAttributes, BotRepoPort,
    FriendCheckInStrategy, InternalBotAttributesService, PatchBotInternalAttributes, ServiceError,
    ServiceResult, UserVisibility,
};

#[test]
fn internal_attributes_deserialize_legacy_data_with_default_visibility() {
    let attributes: BotInternalAttributes = serde_json::from_value(serde_json::json!({
        "user_visibility": "protected",
        "friend_ext": {},
        "friend_check_in_strategy": "APPROVAL"
    }))
    .expect("legacy attributes deserialize");

    assert_eq!(attributes.visibility, "protected");
}

#[tokio::test]
async fn internal_attributes_default_and_partial_patch_round_trip_through_control_plane() {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    repo.register_with_owner_and_token(
        "bot-1".to_string(),
        BotCapabilities {
            name: Some("Bot One".to_string()),
            ..Default::default()
        },
        "staff-1",
        "token-1",
    )
    .await
    .expect("register bot");
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        repo,
        Arc::new(bcs_bot_store::MemoryProviderStore::new()),
        Arc::new(bcs_bot_store::MemoryProviderStore::new()),
    ));
    let service = InternalBotAttributesServiceImpl::new(
        control_plane,
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );

    let attributes = service
        .get("bot-1".to_string())
        .await
        .expect("legacy record defaults");
    assert_eq!(
        serde_json::to_value(&attributes).expect("serialize attributes"),
        serde_json::json!({
            "visibility": "protected",
            "user_visibility": "protected",
            "friend_ext": {},
            "friend_check_in_strategy": "APPROVAL"
        })
    );

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-1".to_string(),
            visibility: Some("private".to_string()),
            user_visibility: Some(UserVisibility::Public),
            friend_ext: Some(serde_json::Map::from_iter([(
                "department".to_string(),
                serde_json::json!("engineering"),
            )])),
            friend_check_in_strategy: Some(FriendCheckInStrategy::DeptFree),
        })
        .await
        .expect("set internal attributes");
    assert_eq!(attributes.visibility, "private");
    assert_eq!(attributes.user_visibility, UserVisibility::Public);
    assert_eq!(
        attributes.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );
    assert_eq!(attributes.friend_ext["department"], "engineering");

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-1".to_string(),
            friend_ext: Some(serde_json::Map::new()),
            ..Default::default()
        })
        .await
        .expect("clear friend extension");
    assert_eq!(attributes.friend_ext, serde_json::Map::new());
    assert_eq!(attributes.visibility, "private");
    assert_eq!(attributes.user_visibility, UserVisibility::Public);
    assert_eq!(
        attributes.friend_check_in_strategy,
        FriendCheckInStrategy::DeptFree
    );
}

#[tokio::test]
async fn internal_attributes_map_invalid_empty_and_missing_requests_to_application_errors() {
    let control_plane: Arc<dyn BotControlPlaneCoreService> = Arc::new(BotControlPlaneCore::new(
        Arc::new(MemoryBotRepo::new()),
        Arc::new(bcs_bot_store::MemoryProviderStore::new()),
        Arc::new(bcs_bot_store::MemoryProviderStore::new()),
    ));
    let service = InternalBotAttributesServiceImpl::new(
        control_plane,
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );

    assert_eq!(
        service
            .get(" ".to_string())
            .await
            .expect_err("blank bot ID is invalid")
            .code(),
        "invalid_request"
    );
    assert_eq!(
        service
            .patch(PatchBotInternalAttributes {
                bot_id: "bot-1".to_string(),
                ..Default::default()
            })
            .await
            .expect_err("empty patch is invalid")
            .code(),
        "invalid_request"
    );
    assert_eq!(
        service
            .patch(PatchBotInternalAttributes {
                bot_id: "bot-1".to_string(),
                visibility: Some("friends".to_string()),
                ..Default::default()
            })
            .await
            .expect_err("invalid visibility is rejected")
            .code(),
        "invalid_request"
    );
    assert_eq!(
        service
            .get("missing".to_string())
            .await
            .expect_err("unknown bot is not found")
            .code(),
        "bot_not_found"
    );
}

struct FailingControlPlane;

#[async_trait]
impl BotControlPlaneCoreService for FailingControlPlane {
    async fn get_record(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneRecord>> {
        Err(ServiceError::InternalError("store unavailable".to_string()))
    }

    async fn get(
        &self,
        _bot_id: &str,
        _env: &str,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not called by internal attributes service")
    }

    async fn get_by_ids(
        &self,
        _bot_ids: &[String],
        _env: &str,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not called by internal attributes service")
    }

    async fn list_candidates(
        &self,
        _query: bcs_service_api::BotCandidateReadQuery,
    ) -> ServiceResult<(Vec<bcs_service_api::BotControlPlaneCandidate>, u64)> {
        unreachable!("not called by internal attributes service")
    }

    async fn list_by_creator(
        &self,
        _query: bcs_service_api::BotControlPlaneOwnedQuery,
    ) -> ServiceResult<Vec<bcs_service_api::BotControlPlaneView>> {
        unreachable!("not called by internal attributes service")
    }

    async fn patch(
        &self,
        _bot_id: &str,
        _env: &str,
        _patch: bcs_service_api::BotControlPlanePatch,
    ) -> ServiceResult<Option<bcs_service_api::BotControlPlaneView>> {
        Err(ServiceError::InternalError("store unavailable".to_string()))
    }
}

#[tokio::test]
async fn internal_attributes_map_core_errors_to_internal_application_errors() {
    let service = InternalBotAttributesServiceImpl::new(
        Arc::new(FailingControlPlane),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );

    assert_eq!(
        service
            .get("bot-1".to_string())
            .await
            .expect_err("core failure maps to application error")
            .code(),
        "internal_error"
    );
}

const AGENT_VIEW_SCOPE_DEPS_KEY: &str = "view_scope_agent_friend_deps";

fn scoped_agent_friend_ext() -> serde_json::Map<String, serde_json::Value> {
    serde_json::Map::from_iter([(
        AGENT_VIEW_SCOPE_DEPS_KEY.to_string(),
        serde_json::json!([{ "deptNo": "D1", "deptName": "工程" }]),
    )])
}

async fn control_plane_service_with_bot(
    bot_id: &str,
) -> (InternalBotAttributesServiceImpl, tempfile::TempDir) {
    let temp = tempfile::tempdir().expect("temp dir");
    let repo = Arc::new(MemoryBotRepo::with_base_dir(temp.path().to_path_buf()));
    repo.register_with_owner_and_token(
        bot_id.to_string(),
        BotCapabilities {
            name: Some("Bot One".to_string()),
            ..Default::default()
        },
        "staff-1",
        "token-1",
    )
    .await
    .expect("register bot");
    let service = InternalBotAttributesServiceImpl::new(
        Arc::new(BotControlPlaneCore::new(
            repo,
            Arc::new(bcs_bot_store::MemoryProviderStore::new()),
            Arc::new(bcs_bot_store::MemoryProviderStore::new()),
        )),
        BotServiceConfig {
            env: bcs_config::resolve_env_str(),
        },
    );
    (service, temp)
}

#[tokio::test]
async fn open_strategy_promotes_protected_without_agent_view_scope_to_public() {
    let (service, _temp) = control_plane_service_with_bot("bot-open-1").await;

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-1".to_string(),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        })
        .await
        .expect("open friend check-in");

    assert_eq!(attributes.visibility, "public");
    assert_eq!(
        attributes.friend_check_in_strategy,
        FriendCheckInStrategy::Open
    );
}

#[tokio::test]
async fn open_strategy_keeps_protected_with_agent_view_scope() {
    let (service, _temp) = control_plane_service_with_bot("bot-open-2").await;
    service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-2".to_string(),
            friend_ext: Some(scoped_agent_friend_ext()),
            ..Default::default()
        })
        .await
        .expect("pin department scope");

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-2".to_string(),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        })
        .await
        .expect("open friend check-in");

    assert_eq!(attributes.visibility, "protected");
    assert_eq!(attributes.friend_ext, scoped_agent_friend_ext());
}

#[tokio::test]
async fn open_strategy_reads_the_agent_view_scope_from_the_patch_friend_ext() {
    let (service, _temp) = control_plane_service_with_bot("bot-open-3").await;

    // 同一次 patch 自带组织范围 → 目标态仍是限定可见 → 不升
    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-3".to_string(),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            friend_ext: Some(scoped_agent_friend_ext()),
            ..Default::default()
        })
        .await
        .expect("open with scoped friend_ext");
    assert_eq!(attributes.visibility, "protected");

    // 先回到 APPROVAL 再同 patch 清范围 + OPEN → 目标态无范围 → 升
    service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-3".to_string(),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Approval),
            ..Default::default()
        })
        .await
        .expect("back to approval");
    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-3".to_string(),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            friend_ext: Some(serde_json::Map::new()),
            ..Default::default()
        })
        .await
        .expect("open with cleared friend_ext");
    assert_eq!(attributes.visibility, "public");
}

#[tokio::test]
async fn open_strategy_leaves_private_and_public_visibility_untouched() {
    let (service, _temp) = control_plane_service_with_bot("bot-open-4").await;

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-4".to_string(),
            visibility: Some("private".to_string()),
            ..Default::default()
        })
        .await
        .expect("pin private");
    assert_eq!(attributes.visibility, "private");
    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-4".to_string(),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        })
        .await
        .expect("open friend check-in on private bot");
    assert_eq!(attributes.visibility, "private");

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-4".to_string(),
            visibility: Some("public".to_string()),
            ..Default::default()
        })
        .await
        .expect("pin public");
    assert_eq!(attributes.visibility, "public");
    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-4".to_string(),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        })
        .await
        .expect("re-open strategy on public bot");
    assert_eq!(attributes.visibility, "public");
}

#[tokio::test]
async fn explicit_visibility_wins_over_open_strategy_promotion() {
    let (service, _temp) = control_plane_service_with_bot("bot-open-5").await;

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-5".to_string(),
            visibility: Some("protected".to_string()),
            friend_check_in_strategy: Some(FriendCheckInStrategy::Open),
            ..Default::default()
        })
        .await
        .expect("explicit protected with open strategy");

    assert_eq!(attributes.visibility, "protected");
}

#[tokio::test]
async fn patches_without_strategy_change_never_touch_visibility() {
    let (service, _temp) = control_plane_service_with_bot("bot-open-6").await;

    let attributes = service
        .patch(PatchBotInternalAttributes {
            bot_id: "bot-open-6".to_string(),
            user_visibility: Some(UserVisibility::Public),
            ..Default::default()
        })
        .await
        .expect("non-strategy patch");

    assert_eq!(attributes.visibility, "protected");
}
