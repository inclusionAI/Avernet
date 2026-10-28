use std::sync::Arc;

use async_trait::async_trait;
use axum::{
    body::{to_bytes, Body},
    http::{Request, StatusCode},
};
use bcs_auth_api::AuthError;
use bcs_bot::BotCore;
use bcs_domain::{actor::ActorKind, edge_permission::{FriendListEntry, PermissionRequest, RequestKind, RequestStatus}};
use bcs_http::{
    router::build_router,
    state::{HttpAppState, UserIdentityPort},
};
use bcs_service_api::{
    application::{
        connect::{ConnectResult, ConnectService, ConnectStatus, RequestDirection, RequestsPage},
        BotListEntry, BotQueryService, BotVisibilityQueryCommand, BotVisibilityQueryResult,
    },
    BotRegistryCoreService, ServiceError, ServiceResult,
};
use bcs_services_container::Services;
use serde_json::Value;
use tempfile::TempDir;
use tokio::sync::Mutex;
use tower::ServiceExt;

struct RecordingConnectService {
    create_commands: Mutex<Vec<(String, String, Option<String>)>>,
    create_request_auths: Mutex<Vec<Option<bcs_service_api::RequestAuthHeaders>>>,
    approve_commands: Mutex<Vec<(String, String)>>,
    reject_commands: Mutex<Vec<(String, String, Option<String>)>>,
    cancel_commands: Mutex<Vec<String>>,
    request_lookup: Mutex<std::collections::HashMap<String, PermissionRequest>>,
    revoke_commands: Mutex<Vec<(String, String)>>,
    list_friends_commands: Mutex<Vec<String>>,
    list_requests_commands: Mutex<Vec<(String, RequestDirection, Option<RequestStatus>, u32, u32)>>,
    create_result: ConnectResult,
    list_friends_result: Vec<FriendListEntry>,
    list_requests_result: RequestsPage,
}

impl Default for RecordingConnectService {
    fn default() -> Self {
        Self {
            create_commands: Mutex::new(Vec::new()),
            create_request_auths: Mutex::new(Vec::new()),
            approve_commands: Mutex::new(Vec::new()),
            reject_commands: Mutex::new(Vec::new()),
            cancel_commands: Mutex::new(Vec::new()),
            request_lookup: Mutex::new(std::collections::HashMap::new()),
            revoke_commands: Mutex::new(Vec::new()),
            list_friends_commands: Mutex::new(Vec::new()),
            list_requests_commands: Mutex::new(Vec::new()),
            create_result: ConnectResult {
                request_ids: vec!["1".to_string()],
                edge_ids: vec![11],
                status: ConnectStatus::Pending,
                auto_accepted: false,
            },
            list_friends_result: vec![FriendListEntry {
                actor_id: "friend-bot".to_string(),
                name: Some("Friend Bot".to_string()),
                summary: Some("friend summary".to_string()),
                is_online: true,
                kind: ActorKind::Bot,
            }],
            list_requests_result: RequestsPage {
                items: vec![],
                total: 0,
                page: 1,
                page_size: 20,
            },
        }
    }
}

#[async_trait]
impl ConnectService for RecordingConnectService {
    async fn create_connect(
        &self,
        caller: &str,
        to_bot: &str,
        message: Option<String>,
        request_auth: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<ConnectResult> {
        self.create_request_auths.lock().await.push(request_auth);
        self.create_commands
            .lock()
            .await
            .push((caller.to_string(), to_bot.to_string(), message));
        Ok(self.create_result.clone())
    }

    async fn approve(&self, request_id: &str, decider: &str, _request_auth: Option<bcs_service_api::RequestAuthHeaders>) -> ServiceResult<Vec<u64>> {
        self.approve_commands
            .lock()
            .await
            .push((request_id.to_string(), decider.to_string()));
        Ok(vec![211])
    }

    async fn reject(
        &self,
        request_id: &str,
        decider: &str,
        reason: Option<String>,
    ) -> ServiceResult<()> {
        self.reject_commands
            .lock()
            .await
            .push((request_id.to_string(), decider.to_string(), reason));
        Ok(())
    }

    async fn cancel(&self, request_id: &str) -> ServiceResult<()> {
        self.cancel_commands.lock().await.push(request_id.to_string());
        Ok(())
    }

    async fn get_request(&self, request_id: &str) -> ServiceResult<PermissionRequest> {
        self.request_lookup
            .lock()
            .await
            .get(request_id)
            .cloned()
            .ok_or_else(|| {
                ServiceError::FriendRequestNotFound(request_id.to_string())
            })
    }

    async fn revoke_friend(
        &self,
        caller: &str,
        target: &str,
        _request_auth: Option<bcs_service_api::RequestAuthHeaders>,
    ) -> ServiceResult<Vec<u64>> {
        self.revoke_commands
            .lock()
            .await
            .push((caller.to_string(), target.to_string()));
        Ok(vec![311])
    }

    async fn list_friends_paginated(
        &self,
        _: &str,
        _: bcs_service_api::application::connect::FriendListQuery,
    ) -> ServiceResult<bcs_service_api::application::connect::FriendEntriesPage> {
        unreachable!("legacy caller must retain the unpaginated query")
    }

    async fn list_friends(&self, actor: &str) -> ServiceResult<Vec<FriendListEntry>> {
        self.list_friends_commands.lock().await.push(actor.to_string());
        Ok(self.list_friends_result.clone())
    }

    async fn list_requests(
        &self,
        actor: &str,
        direction: RequestDirection,
        status: Option<RequestStatus>,
        page: u32,
        page_size: u32,
    ) -> ServiceResult<RequestsPage> {
        self.list_requests_commands
            .lock()
            .await
            .push((actor.to_string(), direction, status, page, page_size));
        Ok(self.list_requests_result.clone())
    }

}

struct RecordingBotQueryService {
    list_bots_by_creator_calls: Mutex<Vec<String>>,
    list_bots_by_creator_result: Option<Vec<BotListEntry>>,
}

impl Default for RecordingBotQueryService {
    fn default() -> Self {
        Self {
            list_bots_by_creator_calls: Mutex::new(Vec::new()),
            list_bots_by_creator_result: Some(vec![]),
        }
    }
}

#[async_trait]
impl BotQueryService for RecordingBotQueryService {
    async fn list_bots(
        &self,
        _command: bcs_service_api::application::BotListCommand,
    ) -> Result<bcs_service_api::application::BotListResult, bcs_service_api::application::BotUseCaseError>
    {
        Err(not_configured("list_bots"))
    }

    async fn get_bot(
        &self,
        _command: bcs_service_api::application::BotDetailCommand,
    ) -> Result<bcs_service_api::application::BotDetailResult, bcs_service_api::application::BotUseCaseError>
    {
        Err(not_configured("get_bot"))
    }

    async fn get_visibility(
        &self,
        _command: BotVisibilityQueryCommand,
    ) -> Result<BotVisibilityQueryResult, bcs_service_api::application::BotUseCaseError> {
        Err(not_configured("get_visibility"))
    }

    async fn list_bots_by_creator(
        &self,
        staff_no: &str,
    ) -> Result<Vec<BotListEntry>, bcs_service_api::application::BotUseCaseError> {
        self.list_bots_by_creator_calls
            .lock()
            .await
            .push(staff_no.to_string());
        match &self.list_bots_by_creator_result {
            Some(value) => Ok(value.clone()),
            None => Err(not_configured("list_bots_by_creator")),
        }
    }
}

#[derive(Clone)]
struct StaticUserIdentityPort {
    staff_no: String,
}

impl StaticUserIdentityPort {
    fn human(staff_no: &str) -> Self {
        Self {
            staff_no: staff_no.to_string(),
        }
    }
}

#[async_trait]
impl UserIdentityPort for StaticUserIdentityPort {
    async fn extract(
        &self,
        _headers: &axum::http::HeaderMap,
        _uri: &axum::http::Uri,
    ) -> Option<bcs_http::state::HttpUserIdentity> {
        Some(bcs_http::state::HttpUserIdentity {
            staff_no: Some(self.staff_no.clone()),
            nick_name: Some("Human".to_string()),
        })
    }

    async fn ensure_identity(
        &self,
        _auth_source: &str,
        _external_user_id: &str,
        _external_user_name: Option<&str>,
        _avatar: Option<&str>,
        _env: &str,
    ) -> Result<String, AuthError> {
        Err(AuthError::LookupFailed("not configured".to_string()))
    }

    async fn get_identity_by_token(
        &self,
        _token: &str,
    ) -> Result<Option<bcs_auth_api::UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn get_identity_by_user_id(
        &self,
        _user_id: &str,
    ) -> Result<Option<bcs_auth_api::UserIdentityInfo>, AuthError> {
        Ok(None)
    }

    async fn update_token(
        &self,
        _user_id: &str,
        _token: &str,
        _expire_at: u64,
    ) -> Result<(), AuthError> {
        Ok(())
    }
}

fn not_configured(name: &str) -> bcs_service_api::application::BotUseCaseError {
    bcs_service_api::application::BotUseCaseError::Service(ServiceError::InvalidOperation {
        message: format!("{name} is not configured"),
        request_id: None,
    })
}

async fn build_app(
    temp_dir: &TempDir,
    connect: Arc<dyn ConnectService>,
    bot_query: Arc<dyn BotQueryService>,
    user_identity: Option<Arc<dyn UserIdentityPort>>,
    token_map: Option<(&str, &str)>,
) -> axum::Router {
    build_app_with_authority(
        temp_dir,
        connect,
        bot_query,
        user_identity,
        token_map,
        None,
    )
    .await
}

/// Human act-as-Bot entries consult the REAL
/// authority hook (bot-authority plan Task 6.5);
/// pass a seeded chain via `authority`.
async fn build_app_with_authority(
    temp_dir: &TempDir,
    connect: Arc<dyn ConnectService>,
    bot_query: Arc<dyn BotQueryService>,
    user_identity: Option<Arc<dyn UserIdentityPort>>,
    token_map: Option<(&str, &str)>,
    authority: Option<Arc<bcs_test_support::LazySeedAuthorityHook>>,
) -> axum::Router {
    let registry = Arc::new(BotCore::with_base_dir(temp_dir.path().to_path_buf()));
    if let Some((token, bot_id)) = token_map {
        registry
            .store_token_mapping(token.to_string(), bot_id.to_string())
            .await;
    }
    let mut services = Services::builder()
        .registry(registry)
        .bot_query(bot_query);
    if let Some(authority) = authority {
        services = services.bot_authority_hook(authority);
    }
    let services = services.build_for_test();
    let state = if let Some(user_identity) = user_identity {
        HttpAppState::new(services)
            .with_connect(connect)
            .with_user_identity(user_identity)
    } else {
        HttpAppState::new(services).with_connect(connect)
    };
    build_router(state)
}

#[path = "friend_connections_contract/request_authorization.rs"]
mod request_authorization;

#[path = "friend_connections_contract/lifecycle_and_listing.rs"]
mod lifecycle_and_listing;
