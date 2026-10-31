use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use async_trait::async_trait;
use axum::{body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::application::a2a_bot::{A2aBotManagementService, ConfigureA2aBotCommand};
use bcs_service_api::types::a2a_bot::{A2aBotRecord};
use bcs_services_container::Services;
use serde_json::{json, Value};
use tower::ServiceExt;

#[derive(Default)]
struct Management { calls: AtomicUsize }

fn record() -> A2aBotRecord {
    A2aBotRecord { bot_uuid:"bot".into(), provider_id:"provider".into(), provider_bot_ref:"remote".into(),
        agent_card_url:Some("https://example.org/card".into()), agent_card:json!({"skills":[{"description":"unchanged", "unknown":42}]}), revision:1 ,}
}

#[async_trait]
impl A2aBotManagementService for Management {
    async fn register_provider_bot(&self, command: bcs_service_api::RegisterProviderBotCommand,
        source:bcs_service_api::types::a2a_bot::A2aCardSource, now: i64) -> ServiceResult<bcs_service_api::RegisterProviderBotOutcome> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if command.provider_admin_token != "test-admin" {return Err(ServiceError::Forbidden("test denied".into()));}
        assert_eq!(command.provider_id,"provider");assert_eq!(command.provider_bot_ref,"remote");
        match source {
            bcs_service_api::types::a2a_bot::A2aCardSource::Url(url)=>assert_eq!(url,"https://example.org/card"),
            bcs_service_api::types::a2a_bot::A2aCardSource::Inline(card)=>{assert_eq!(card["name"],"Inline");assert!(command.name.is_empty());}
        }assert!(now>0);
        assert!(command.webhook_url.is_none());assert_eq!(command.owners,vec!["owner"]);
        Ok(bcs_service_api::RegisterProviderBotOutcome {auth_mode:bcs_service_api::ProviderAuthMode::StaticBearer,
            identity:bcs_service_api::types::provider_identity::normalize(bcs_service_api::ProviderAuthMode::StaticBearer,"remote").unwrap(),
            webhook_url:None,bot_uuid:"bot".into(),provider_id:"provider".into(),provider_bot_ref:"remote".into(),
            bot_runtime_token:Some("test-runtime".into()),message:None,created:true,capabilities:None,
            actor_kind:bcs_service_api::ActorKind::Bot})
    }
    async fn get(&self, provider: &str, token: &str, bot: &str) -> ServiceResult<Option<A2aBotRecord>> {
        assert_eq!((provider,bot),("provider","bot"));
        if token != "test-admin" { return Err(ServiceError::Forbidden("test denied".into())); }
        Ok(Some(record()))
    }
    async fn refresh_due(&self, _now: i64) -> ServiceResult<usize> { panic!("HTTP must not call background maintenance"); }
    async fn configure(&self, command: ConfigureA2aBotCommand) -> ServiceResult<A2aBotRecord> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        assert_eq!(command.provider_id, "provider");
        assert_eq!(command.bot_uuid, "bot");
        assert!(matches!(command.source,bcs_service_api::types::a2a_bot::A2aCardSource::Url(url) if url=="https://example.org/card"));
        assert_eq!(command.expected_revision, 0);
        assert!(command.now_ms > 0);
        if command.provider_admin_token != "test-admin" { return Err(ServiceError::Forbidden("test denied".into())); }
        Ok(record())
    }
    async fn refresh(&self, provider: &str, token: &str, bot: &str, now: i64) -> ServiceResult<A2aBotRecord> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        assert_eq!((provider, token, bot), ("provider", "test-admin", "bot"));
        assert!(now > 0);
        Ok(record())
    }
}

#[tokio::test]
async fn provider_registration_accepts_card_url_on_existing_route_and_requires_admin_bearer() {
    let management=Arc::new(Management::default());
    let app=build_router(HttpAppState::new(Services::builder().a2a_bot_management(management.clone()).build_for_test()));
    let body=json!({"name":"Remote agent","owners":["owner"],"provider_bot_ref":"remote", "agent_card_url":"https://example.org/card"});
    let req=|authorization: Option<&str>| {
        let mut r=Request::builder().method("POST").uri("/providers/provider/bots").header("content-type","application/json");
        if let Some(token)=authorization {r=r.header("authorization",format!("Bearer {token}"));}
        r.body(Body::from(body.to_string())).unwrap()
    };
    assert_eq!(app.clone().oneshot(req(None)).await.unwrap().status(),StatusCode::UNAUTHORIZED);
    assert_eq!(management.calls.load(Ordering::SeqCst),0);
    assert_eq!(app.clone().oneshot(req(Some("wrong-admin"))).await.unwrap().status(),StatusCode::FORBIDDEN);
    let response=app.oneshot(req(Some("test-admin"))).await.unwrap();assert_eq!(response.status(),StatusCode::OK);
    let raw:Value=serde_json::from_slice(&to_bytes(response.into_body(),8192).await.unwrap()).unwrap();
    assert_eq!(raw["bot_uuid"],"bot");assert_eq!(raw["provider_bot_ref"],"remote");
    assert!(raw["webhook_url"].is_null());assert_eq!(raw["bot_runtime_token"],"test-runtime");
}

#[tokio::test]
async fn card_read_requires_provider_admin_and_preserves_full_snapshot() {
    let management = Arc::new(Management::default());
    let app=build_router(HttpAppState::new(Services::builder().a2a_bot_management(management).build_for_test()));
    assert_eq!(app.clone().oneshot(request("GET","",None,json!(null))).await.unwrap().status(),StatusCode::UNAUTHORIZED);
    assert_eq!(app.clone().oneshot(request("GET","",Some("wrong-admin"),json!(null))).await.unwrap().status(),StatusCode::FORBIDDEN);
    let response=app.oneshot(request("GET","",Some("test-admin"),json!(null))).await.unwrap();
    assert_eq!(response.status(),StatusCode::OK);
    let raw: Value=serde_json::from_slice(&to_bytes(response.into_body(),8192).await.unwrap()).unwrap();
    assert_eq!(raw["agent_card"]["skills"][0]["unknown"],42);
}

fn request(method: &str, suffix: &str, authorization: Option<&str>, body: Value) -> Request<Body> {
    let mut request = Request::builder().method(method).uri(format!("/providers/provider/bots/bot/a2a{suffix}"))
        .header("content-type", "application/json");
    if let Some(token) = authorization { request = request.header("authorization", format!("Bearer {token}")); }
    request.body(Body::from(body.to_string())).unwrap()
}

#[tokio::test]
async fn configuration_requires_bearer_and_preserves_card_fields() {
    let management = Arc::new(Management::default());
    let services = Services::builder().a2a_bot_management(management.clone()).build_for_test();
    let app = build_router(HttpAppState::new(services));
    let body = json!({"agent_card_url":"https://example.org/card", "expected_revision":0});
    let response = app.clone().oneshot(request("PUT", "", None, body.clone())).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    assert_eq!(management.calls.load(Ordering::SeqCst), 0);
    let denied = app.clone().oneshot(request("PUT", "", Some("wrong-admin"), body.clone())).await.unwrap();
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
    let response = app.oneshot(request("PUT", "", Some("test-admin"), body)).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let json: Value = serde_json::from_slice(&to_bytes(response.into_body(), 8192).await.unwrap()).unwrap();
    assert_eq!(json["agent_card"]["skills"][0]["unknown"], 42);
    assert_eq!(management.calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn explicit_refresh_uses_service_binding_and_missing_runtime_is_unavailable() {
    let management = Arc::new(Management::default());
    let services = Services::builder().a2a_bot_management(management.clone()).build_for_test();
    let response = build_router(HttpAppState::new(services)).oneshot(request("POST", "/card:refresh", Some("test-admin"), json!({}))).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(management.calls.load(Ordering::SeqCst), 1);
    let app = build_router(HttpAppState::new(Services::builder().build_for_test()));
    let response = app.oneshot(request("POST", "/card:refresh", Some("test-admin"), json!({}))).await.unwrap();
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
}

#[tokio::test]
async fn provider_registration_accepts_inline_card_without_name_and_rejects_mixed_sources() {
    let management=Arc::new(Management::default());
    let app=build_router(HttpAppState::new(Services::builder().a2a_bot_management(management.clone()).build_for_test()));
    let card=json!({"name":"Inline","unknown":{"keep":true}});
    let body=json!({"owners":["owner"],"provider_bot_ref":"remote","agent_card":card});
    let response=app.clone().oneshot(Request::builder().method("POST").uri("/providers/provider/bots").header("authorization","Bearer test-admin").header("content-type","application/json").body(Body::from(body.to_string())).unwrap()).await.unwrap();
    assert_eq!(response.status(),StatusCode::OK);
    let mut mixed=body;mixed["agent_card_url"]=json!("https://example.org/card");
    let response=app.oneshot(Request::builder().method("POST").uri("/providers/provider/bots").header("authorization","Bearer test-admin").header("content-type","application/json").body(Body::from(mixed.to_string())).unwrap()).await.unwrap();
    assert_eq!(response.status(),StatusCode::BAD_REQUEST);assert_eq!(management.calls.load(Ordering::SeqCst),1);
}

struct ScopedRegistration;
#[async_trait]
impl bcs_service_api::application::v1::RegisterService for ScopedRegistration {
    async fn issue_register_token(&self,_:bcs_service_api::application::v1::IssueRegisterToken)->Result<bcs_service_api::application::v1::RegisterTokenView,bcs_service_api::application::v1::ApplicationError> {panic!("not token issuance")}
    async fn register_bot(&self,_:bcs_service_api::application::v1::RegisterBot)->Result<bcs_service_api::application::v1::BotRegistration,bcs_service_api::application::v1::ApplicationError> {panic!("must select A2A registration")}
    async fn register_a2a_card(&self,command:bcs_service_api::application::v1::RegisterBot,source:bcs_service_api::types::a2a_bot::A2aCardSource)->Result<bcs_service_api::application::v1::BotRegistration,bcs_service_api::application::v1::ApplicationError> {
        assert!(command.bot_name.is_empty());assert_eq!(command.provider_bot_ref.as_deref(),Some("remote"));
        assert!(matches!(source,bcs_service_api::types::a2a_bot::A2aCardSource::Inline(card) if card["name"]=="Inline"));
        Ok(bcs_service_api::application::v1::BotRegistration {bot_name:"Inline".into(),bot_uuid:"bot".into(),bot_token:"test-runtime".into(),registration:None})
    }
}

#[tokio::test]
async fn scoped_register_accepts_inline_body_without_name() {
    use bcs_test_support::contract::bot_registration_chain::{mint_v2_token,OWNER,REGISTER_SECRET};
    let token=mint_v2_token(OWNER,"provider",REGISTER_SECRET);
    let app=build_router(HttpAppState::new(Services::builder().build_for_test())
        .with_register_application(Arc::new(ScopedRegistration))
        .with_invite_config(REGISTER_SECRET.to_vec(),21600,None,None,None));
    let response=app.oneshot(Request::builder().method("POST")
        .uri(format!(concat!("/register?token={token}", "&mode=gateway&provider_bot_ref=remote")))
        .header("content-type","application/json")
        .body(Body::from(json!({"agent_card":{"name":"Inline"}}).to_string())).unwrap()).await.unwrap();
    assert_eq!(response.status(),StatusCode::OK);
}
