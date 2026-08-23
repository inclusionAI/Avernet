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
