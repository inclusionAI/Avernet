mod support;

use axum::{
    body::{Body, to_bytes},
    http::{Request, StatusCode},
};
use bcs_auth_api::{AuthConfig, AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_http::{
    router::build_router,
    state::{ChainUserIdentityPort, HttpAppState},
};
use bcs_service_api::{
    ActorKind, ActorStatus, BotDetailCommand, BotDetailResult, BotDiscoveryCommand,
    BotDiscoveryEntry, BotDiscoveryProviderInfo, BotDiscoveryResult, BotDiscoveryService, BotListCommand, BotListEntry,
    OrganizationMemberSummary,
    BotListResult, BotQueryService, DynamicStatusResponse, Skill,
};
use bcs_services_container::Services;
use serde_json::Value;
use std::sync::Arc;
use support::bot_use_cases::RecordingBotQueryService;
use tokio::sync::Mutex;
use tower::ServiceExt;

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

fn static_bot_auth_chain(bot_uuid: &str) -> Arc<AuthPluginChain> {
    let principal = AuthPrincipal {
        bot_uuid: Some(bot_uuid.to_string()),
        ..Default::default()
    };
    Arc::new(AuthPluginChain::new(vec![Box::new(
        StaticAuthPlugin::with_principal(principal),
    )]))
}

#[derive(Default)]
struct RecordingBotDiscoveryService {
    commands: Mutex<Vec<BotDiscoveryCommand>>,
}

#[async_trait::async_trait]
impl BotDiscoveryService for RecordingBotDiscoveryService {
    async fn discover_bots(
        &self,
        command: BotDiscoveryCommand,
    ) -> Result<BotDiscoveryResult, bcs_service_api::BotUseCaseError> {
        let organization_member = command.organization_code.as_ref().map(|code| OrganizationMemberSummary {
            organization_code: code.clone(),
            role: command.role.clone(),
        });
        self.commands.lock().await.push(command);
        Ok(BotDiscoveryResult {
            bots: vec![BotDiscoveryEntry {
                bot_uuid: "planner-friend".to_string(),
                capabilities: bcs_service_api::BotCapabilities {
                    name: Some("Planner Friend".to_string()),
                    summary: Some("Planning help".to_string()),
                    visibility: "protected".to_string(),
                    skills: vec![Skill::new("planner")],
                    ..Default::default()
                },
                visibility: "protected".to_string(),
                is_friend: Some(true),
                agent_code: Some("agent-code-1".to_string()),
                provider_info: Some(BotDiscoveryProviderInfo {
                    provider_id: "provider-1".to_string(),
                    provider_name: "Provider One".to_string(),
                }),
                organization_member,
            }],
            count: 1,
        })
    }
}

#[tokio::test]
async fn discover_route_delegates_filters_to_bot_discovery_service() {
    let discovery = Arc::new(RecordingBotDiscoveryService::default());
    let services = Services::builder()
        .bot_discovery(discovery.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/discover?collaborate_bot=driver&q=planner")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    let bots = json["bots"].as_array().unwrap();
    assert_eq!(bots.len(), 1);
    assert_eq!(bots[0]["bot_uuid"], "planner-friend");
    assert_eq!(bots[0]["is_friend"], true);
    assert_eq!(bots[0]["agent_code"], "agent-code-1");
    assert_eq!(bots[0]["provider_info"]["provider_id"], "provider-1");
    assert_eq!(bots[0]["provider_info"]["provider_name"], "Provider One");
    assert_eq!(json["count"], 1);

    let commands = discovery.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].collaborate_bot.as_deref(), Some("driver"));
    assert_eq!(commands[0].q.as_deref(), Some("planner"));
    assert_eq!(commands[0].name, None);
    assert_eq!(commands[0].skills, None);
}


#[tokio::test]
async fn discover_route_forwards_organization_scope_for_bot_callers() {
    let discovery = Arc::new(RecordingBotDiscoveryService::default());
    let services = Services::builder()
        .bot_discovery(discovery.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services).with_auth_chain(static_bot_auth_chain("bot-a"), AuthConfig::default()));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/discover?organization_code=promo-2026&role=traffic_analyst&q=planner")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["bots"][0]["organization_member"]["organization_code"], "promo-2026");
    assert_eq!(json["bots"][0]["organization_member"]["role"], "traffic_analyst");

    let commands = discovery.commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].requester_bot_id.as_deref(), Some("bot-a"));
    assert_eq!(commands[0].organization_code.as_deref(), Some("promo-2026"));
    assert_eq!(commands[0].role.as_deref(), Some("traffic_analyst"));
}

#[tokio::test]
async fn discover_route_rejects_organization_scope_for_human_callers() {
    let discovery = Arc::new(RecordingBotDiscoveryService::default());
    let services = Services::builder()
        .bot_discovery(discovery.clone())
        .build_for_test();
    let chain = static_auth_chain("alice", "Alice");
    let app = build_router(HttpAppState::new(services).with_user_identity(Arc::new(
        ChainUserIdentityPort::new(chain),
    )));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/discover?organization_code=promo-2026&q=planner")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert!(discovery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn discover_route_rejects_missing_caller() {
    let discovery = Arc::new(RecordingBotDiscoveryService::default());
    let services = Services::builder()
        .bot_discovery(discovery.clone())
        .build_for_test();
    let app = build_router(HttpAppState::new(services));

    let response = app
        .oneshot(
            Request::builder()
                .uri("/bots/discover?collaborate_bot=driver&q=planner")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert!(discovery.commands.lock().await.is_empty());
}

#[tokio::test]
async fn recording_bot_query_service_supports_discover_list_expectations() {
    let service = RecordingBotQueryService {
        list_result: Ok(BotListResult {
            bots: vec![BotListEntry {
                bot_uuid: "bot-public".to_string(),
                name: Some("Public Bot".to_string()),
                summary: Some("Visible in discover".to_string()),
                capabilities: Default::default(),
                status: ActorStatus::Online,
                visibility: "public".to_string(),
                owner_actor_id: Some("human_owner".to_string()),
                created_by: Some("owner".to_string()),
            }],
            offset: 20,
            limit: 5,
            total: 21,
        }),
        ..Default::default()
    };

    let result = service
        .list_bots(BotListCommand {
            caller_actor_id: Some("human_alice".to_string()),
            offset: 20,
            limit: 5,
            onboarded: Some(true),
        })
        .await
        .expect("discover list result");

    assert_eq!(result.total, 21);
    assert_eq!(result.bots[0].bot_uuid, "bot-public");

    let commands = service.list_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].offset, 20);
    assert_eq!(commands[0].limit, 5);
    assert_eq!(commands[0].onboarded, Some(true));
}

#[tokio::test]
async fn recording_bot_query_service_supports_discover_detail_expectations() {
    let service = RecordingBotQueryService {
        detail_result: Ok(BotDetailResult {
            bot_uuid: "bot-public".to_string(),
            capabilities: Default::default(),
            status: ActorStatus::Hidden,
            visibility: "public".to_string(),
            owner_actor_id: Some("human_owner".to_string()),
            created_by: Some("owner".to_string()),
            actor_kind: ActorKind::Bot,
            env: Some("dev".to_string()),
            dynamic_status: DynamicStatusResponse {
                status: "offline".to_string(),
            },
        }),
        ..Default::default()
    };

    let result = service
        .get_bot(BotDetailCommand {
            caller_actor_id: Some("human_alice".to_string()),
            bot_id: "bot-public".to_string(),
        })
        .await
        .expect("discover detail result");

    assert_eq!(result.bot_uuid, "bot-public");
    assert_eq!(result.status, ActorStatus::Hidden);
    assert_eq!(result.dynamic_status.status, "offline");

    let commands = service.detail_commands.lock().await;
    assert_eq!(commands.len(), 1);
    assert_eq!(commands[0].caller_actor_id.as_deref(), Some("human_alice"));
    assert_eq!(commands[0].bot_id, "bot-public");
}
