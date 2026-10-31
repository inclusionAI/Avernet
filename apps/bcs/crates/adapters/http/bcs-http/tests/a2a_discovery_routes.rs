mod support;
use std::sync::Arc;
use async_trait::async_trait;
use axum::{body::{Body,to_bytes},http::{Request,StatusCode}};
use bcs_auth_api::{AuthPluginChain,AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_http::{router::build_router,state::{HttpAppState,ChainUserIdentityPort}};
use bcs_service_api::{ActorKind,ActorStatus,BotCapabilities,BotDetailResult,BotDiscoveryCommand,
    BotDiscoveryService,BotDiscoveryResult,BotDiscoveryEntry,BotUseCaseError,DynamicStatusResponse,
    BotSearchResult,BotQueryEntry};
use bcs_services_container::Services;
use serde_json::{json,Value};
use support::bot_use_cases::RecordingBotQueryService;
use tower::ServiceExt;

fn card() -> Value {json!({"name":"remote name","description":"remote description",
    "skills":[{"id":"lookup","name":"Lookup","description":"complete skill description","future":{"keep":true}}]})}
fn detail(raw:Option<Value>) -> BotDetailResult {
    BotDetailResult {agent_card:raw,bot_uuid:"bot".into(),capabilities:BotCapabilities::default(),status:ActorStatus::Online,
        visibility:"public".into(),owner_actor_id:None,created_by:None,actor_kind:ActorKind::Bot,env:None,
        dynamic_status:DynamicStatusResponse {status:"active".into()}}
}
fn authenticated(services:Services) -> axum::Router {
    let chain=Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(AuthPrincipal {
        user_id:Some("alice".into()),..Default::default()
    }))]));
    build_router(HttpAppState::new(services).with_user_identity(Arc::new(ChainUserIdentityPort::new(chain))))
}
async fn get(app:axum::Router,path:&str) -> (StatusCode,Value) {
    let response=app.oneshot(Request::builder().uri(path).body(Body::empty()).unwrap()).await.unwrap();
    let status=response.status();let body=to_bytes(response.into_body(),16384).await.unwrap();
    (status,serde_json::from_slice(&body).unwrap())
}

#[tokio::test]
async fn details_and_card_endpoint_return_identical_unprojected_card() {
    let query=Arc::new(RecordingBotQueryService {detail_result:Ok(detail(Some(card()))),..Default::default()});
    let app=authenticated(Services::builder().bot_query(query.clone()).build_for_test());
    let (status,raw)=get(app.clone(),"/bots/bot/agent-card").await;
    assert_eq!(status,StatusCode::OK);assert_eq!(raw,card());
    let (status,raw)=get(app,"/bots/bot").await;
    assert_eq!(status,StatusCode::OK);assert_eq!(raw["agent_card"],card());
    let commands=query.detail_commands.lock().await;
    assert_eq!(commands.len(),2);
    assert!(commands.iter().all(|command|command.bot_id=="bot" && command.caller_actor_id.as_deref()==Some("human_alice")));
}

#[tokio::test]
async fn card_endpoint_enforces_auth_and_application_denial_and_handles_legacy_bot() {
    let query=Arc::new(RecordingBotQueryService {detail_result:Ok(detail(Some(card()))),..Default::default()});
    let app=build_router(HttpAppState::new(Services::builder().bot_query(query.clone()).build_for_test()));
    assert_eq!(get(app,"/bots/bot/agent-card").await.0,StatusCode::UNAUTHORIZED);
    assert!(query.detail_commands.lock().await.is_empty());
    let denied=Arc::new(RecordingBotQueryService {detail_result:Err(BotUseCaseError::Forbidden("denied".into())),..Default::default()});
    assert_eq!(get(authenticated(Services::builder().bot_query(denied).build_for_test()),"/bots/bot/agent-card").await.0,StatusCode::FORBIDDEN);
    let legacy=Arc::new(RecordingBotQueryService {detail_result:Ok(detail(None)),..Default::default()});
    let app=authenticated(Services::builder().bot_query(legacy).build_for_test());
    assert_eq!(get(app.clone(),"/bots/bot/agent-card").await.0,StatusCode::NOT_FOUND);
    assert!(get(app,"/bots/bot").await.1.get("agent_card").is_none());
}

struct Discovery;
#[async_trait]
impl BotDiscoveryService for Discovery {
    async fn discover_bots(&self,_command:BotDiscoveryCommand) -> Result<BotDiscoveryResult,BotUseCaseError> {
        Ok(BotDiscoveryResult {count:1,bots:vec![BotDiscoveryEntry {agent_card:Some(card()),bot_uuid:"bot".into(),
            capabilities:Default::default(),visibility:"public".into(),is_friend:None,agent_code:None,provider_info:None,organization_member:None}]})
    }
}

#[tokio::test]
async fn discovery_and_search_preserve_skill_descriptions_and_unknown_card_fields() {
    let row=BotQueryEntry {agent_card:Some(card()),bot_uuid:"bot".into(),capabilities:Default::default(),visibility:"public".into(),
        status:ActorStatus::Online,actor_kind:ActorKind::Bot,env:None,dynamic_status:DynamicStatusResponse {status:"active".into()},
        created_by:None,user_visibility:"protected".into(),friend_ext:Default::default(),friend_check_in_strategy:"APPROVAL".into(),
        is_friend:None,access_relation:None};
    let query=Arc::new(RecordingBotQueryService {search_result:Ok(BotSearchResult {items:vec![row],total:1}),..Default::default()});
    let app=authenticated(Services::builder().bot_query(query).bot_discovery(Arc::new(Discovery)).build_for_test());
    let (status,discovery)=get(app.clone(),"/bots/discover").await;
    assert_eq!(status,StatusCode::OK);assert_eq!(discovery["bots"][0]["agent_card"],card());
    let (status,search)=get(app,"/bots/search?q=complete").await;
    assert_eq!(status,StatusCode::OK);assert_eq!(search["items"][0]["agent_card"],card());
}
