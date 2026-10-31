#[path = "bots_contract_parts/bot_search_checks.rs"]
mod bot_search_checks;
#[path = "bots_contract_parts/bot_query_checks.rs"]
mod bot_query_checks;
#[path = "bots_contract_parts/bot_lifecycle_checks.rs"]
mod bot_lifecycle_checks;
mod support;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use async_trait::async_trait;
use bcs_auth_api::{AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_bot::{Bot, BotCore};
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    application::v1::{
        ApplicationError, BotInternalAttributes, FriendCheckInStrategy,
        InternalBotAttributesService, UserVisibility,
    },
    ActorKind, ActorStatus, BotConnectCommand, BotConnectResult, BotDetailCommand, BotDetailResult,
    BotDynamicStatus, BotLeaveResult, BotListCommand, BotListEntry, BotListResult,
    BotManagementService, BotPagedListResult, BotQueryByIdsResult, BotQueryEntry, BotQueryService,
    BotRegistryCoreService, BotSearchResult, BotStatusUpdateCommand, BotStatusUpdateResult,
    BotUseCaseError, BotVisibilityCommand, BotVisibilityQueryResult, BotVisibilityResult,
    ConnectError, DynamicStatusResponse, ServiceError, Skill,
};
use bcs_services_container::Services;
use serde_json::Value;
use std::{collections::HashMap, sync::Arc};
use support::bot_use_cases::{RecordingBotManagementService, RecordingBotQueryService};
use tempfile::TempDir;
use tower::ServiceExt;

struct RecordingSearchPolicyService {
    attributes: HashMap<String, BotInternalAttributes>,
}

#[async_trait]
impl InternalBotAttributesService for RecordingSearchPolicyService {
    async fn get(&self, bot_id: String) -> Result<BotInternalAttributes, ApplicationError> {
        self.attributes
            .get(&bot_id)
            .cloned()
            .ok_or_else(|| ApplicationError::not_found("bot_not_found", "bot not found"))
    }

    async fn patch(
        &self,
        _command: bcs_service_api::application::v1::PatchBotInternalAttributes,
    ) -> Result<BotInternalAttributes, ApplicationError> {
        Err(ApplicationError::internal("patch is not configured"))
    }
}

fn static_auth_chain(staff_no: &str, nick_name: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        user_id: Some(staff_no.to_string()),
        user_name: Some(nick_name.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

fn assert_invalid_operation<T>(result: Result<T, BotUseCaseError>, expected_message: &str) {
    assert!(
        matches!(
            result,
            Err(BotUseCaseError::Service(ServiceError::InvalidOperation {
                message,
                request_id: None,
            })) if message == expected_message
        ),
        "expected InvalidOperation with message {expected_message}"
    );
}

fn query_entry_with_friend(bot_uuid: &str, is_friend: Option<bool>) -> BotQueryEntry {
    let mut entry = query_entry(bot_uuid);
    entry.is_friend = is_friend;
    entry
}

fn query_entry_with_policy(
    bot_uuid: &str,
    user_visibility: UserVisibility,
    friend_ext: serde_json::Map<String, Value>,
    friend_check_in_strategy: FriendCheckInStrategy,
) -> BotQueryEntry {
    BotQueryEntry {
        agent_card: None,
        bot_uuid: bot_uuid.to_string(),
        capabilities: bcs_service_api::BotCapabilities {
            name: Some(bot_uuid.to_string()),
            summary: Some("Test bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        visibility: "public".to_string(),
        status: ActorStatus::Online,
        actor_kind: ActorKind::Bot,
        env: Some("dev".to_string()),
        dynamic_status: DynamicStatusResponse {
            status: "active".to_string(),
        },
        created_by: Some("alice".to_string()),
        user_visibility: match user_visibility {
            UserVisibility::Public => "public".to_string(),
            UserVisibility::Protected => "protected".to_string(),
            UserVisibility::Private => "private".to_string(),
        },
        friend_ext,
        friend_check_in_strategy: match friend_check_in_strategy {
            FriendCheckInStrategy::Open => "OPEN".to_string(),
            FriendCheckInStrategy::Approval => "APPROVAL".to_string(),
            FriendCheckInStrategy::DeptFree => "DEPT_FREE".to_string(),
        },
        is_friend: None,
        access_relation: None,
    }
}

fn query_entry(bot_uuid: &str) -> BotQueryEntry {
    BotQueryEntry {
        agent_card: None,
        bot_uuid: bot_uuid.to_string(),
        capabilities: bcs_service_api::BotCapabilities {
            name: Some(bot_uuid.to_string()),
            summary: Some("Test bot".to_string()),
            visibility: "public".to_string(),
            ..Default::default()
        },
        visibility: "public".to_string(),
        status: ActorStatus::Online,
        actor_kind: ActorKind::Bot,
        env: Some("dev".to_string()),
        dynamic_status: DynamicStatusResponse {
            status: "active".to_string(),
        },
        created_by: Some("alice".to_string()),
        user_visibility: "protected".to_string(),
        friend_ext: serde_json::Map::new(),
        friend_check_in_strategy: "APPROVAL".to_string(),
        is_friend: None,
        access_relation: None,
    }
}
