use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};
use async_trait::async_trait;
use axum::{body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_http::{router::build_router, state::HttpAppState};
use bcs_service_api::{ServiceError, ServiceResult};
use bcs_service_api::application::a2a_bot::{A2aBotManagementService, ConfigureA2aBotCommand, NamedA2aCard, NamedA2aRegistration, RegisterNamedA2aBotCommand};
use bcs_service_api::types::a2a_bot::{A2aBotRecord};
use bcs_services_container::Services;
use serde_json::{json, Value};
use tower::ServiceExt;

#[derive(Default)]
struct Management { calls: AtomicUsize, visibility: std::sync::Mutex<Vec<String>> }

fn record() -> A2aBotRecord {
    A2aBotRecord { bot_uuid:"bot".into(), provider_id:"provider".into(), provider_bot_ref:"remote".into(),
        agent_card_url:Some("https://example.org/card".into()), agent_card:json!({"skills":[{"description":"unchanged", "unknown":42}]}), revision:1 ,}
}

#[async_trait]
impl A2aBotManagementService for Management {
    async fn find_agents_by_provider_ref(&self,token:&str,provider_bot_ref:&str)->ServiceResult<Vec<bcs_service_api::application::a2a_bot::ManagedA2aCard>> {
        assert_eq!(provider_bot_ref,"001Ab");
        Ok(vec![self.get_agent_by_id(token,"bot").await?])
    }
    async fn get_agent_by_id(&self,token:&str,bot_id:&str)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.calls.fetch_add(1,Ordering::SeqCst);
        if token!="test-admin" {return Err(ServiceError::Forbidden("private-provider-id private-bot-uuid".into()));}
        assert_eq!(bot_id,"bot");
        Ok(bcs_service_api::application::a2a_bot::ManagedA2aCard {provider_bot_ref:"remote".into(),provider_name:"orders".into(),agent_name:"订单助手".into(),
            bot_id:bot_id.into(),agent_card_url:None,agent_card:json!({"name":"订单助手","skills":[{"description":"完整描述","unknown":42}]})})
    }
    async fn register_agent(&self,command:bcs_service_api::application::a2a_bot::RegisterAgentCommand)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.visibility.lock().unwrap().push(command.visibility.as_str().into());
        assert_eq!(command.owners,Some(vec!["owner".into()]));
        assert_eq!(command.provider_bot_ref,"remote");
        self.get_agent_by_id(&command.provider_admin_token,"bot").await
    }
    async fn update_agent_by_id(&self,token:&str,bot_id:&str,_source:bcs_service_api::types::a2a_bot::A2aCardSource)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {self.get_agent_by_id(token,bot_id).await}
    async fn update_agent_by_id_with_visibility(&self,token:&str,bot_id:&str,_source:Option<bcs_service_api::types::a2a_bot::A2aCardSource>,visibility:Option<bcs_service_api::types::a2a_bot::A2aRegistrationVisibility>)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {
        self.visibility.lock().unwrap().push(visibility.map(|v|v.as_str()).unwrap_or("omitted").into());
        self.get_agent_by_id(token,bot_id).await
    }
    async fn refresh_agent_by_id(&self,token:&str,bot_id:&str)->ServiceResult<bcs_service_api::application::a2a_bot::ManagedA2aCard> {self.get_agent_by_id(token,bot_id).await}
    async fn register_named(&self,command:RegisterNamedA2aBotCommand)->ServiceResult<NamedA2aRegistration> {
        assert_eq!(command.owners,vec!["owner"]);
        assert_eq!(command.provider_name,"orders");
        assert!(matches!(command.source,bcs_service_api::types::a2a_bot::A2aCardSource::Inline(_)));
        let card=self.get_agent_by_id(&command.provider_admin_token,"bot").await?;
        Ok(NamedA2aRegistration {card:NamedA2aCard {provider_bot_ref:card.provider_bot_ref,bot_id:card.bot_id,provider_name:card.provider_name,
            bot_name:card.agent_name,agent_card_url:card.agent_card_url,agent_card:card.agent_card},
            bot_runtime_token:Some("test-runtime".into())})
    }

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
        assert_eq!(command.expected_revision, Some(0));
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
    let body=json!({"provider_bot_ref":"remote","owners":["owner"],"provider_bot_ref":"remote","agent_card":card});
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
        .uri(format!(concat!("/register?token={token}", "&mode=gateway&provider_bot_ref=remote"), token = token))
        .header("content-type","application/json")
        .body(Body::from(json!({"agent_card":{"name":"Inline"}}).to_string())).unwrap()).await.unwrap();
    assert_eq!(response.status(),StatusCode::OK);
}

#[tokio::test]
async fn public_routes_use_ids_return_bot_id_and_exclude_credentials() {
    let management=Arc::new(Management::default());
    let app=build_router(HttpAppState::new(Services::builder().a2a_bot_management(management.clone()).build_for_test()));
    let cases=[
        ("POST","/a2a/agents",json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{"name":"订单助手"}}),StatusCode::CREATED),
        ("GET","/a2a/agents/bot/agent-card",Value::Null,StatusCode::OK),
        ("PUT","/a2a/agents/bot/agent-card",json!({"agent_card":{"name":"订单助手"}}),StatusCode::OK),
        ("POST","/a2a/agents/bot/agent-card:refresh",json!({}),StatusCode::OK),
    ];
    for (method,path,body,expected) in cases {
        for token in [None,Some("wrong"),Some("test-admin")] {
            let mut request=Request::builder().method(method).uri(path).header("content-type","application/json");
            if let Some(token)=token {request=request.header("authorization",format!("Bearer {token}"));}
            let response=app.clone().oneshot(request.body(if method=="GET" {Body::empty()} else {Body::from(body.to_string())}).unwrap()).await.unwrap();
            assert_eq!(response.status(),match token {None=>StatusCode::UNAUTHORIZED,Some("wrong")=>StatusCode::FORBIDDEN,_=>expected});
            let bytes=to_bytes(response.into_body(),8192).await.unwrap();
            let value:Value=serde_json::from_slice(&bytes).unwrap();
            assert!(!String::from_utf8_lossy(&bytes).contains("private-"));
            for key in ["provider_id","bot_uuid","revision"] {assert!(value.get(key).is_none());}
            if token==Some("test-admin") {
                assert_eq!(value["agent_name"],"订单助手");assert_eq!(value["bot_id"],"bot");assert!(value.get("bot_runtime_token").is_none());
                assert_eq!(value["agent_card"]["skills"][0]["unknown"],42);
            }
        }
    }
}

#[tokio::test]
async fn resource_paths_source_recovery_and_retired_routes_follow_contract() {
    let app=build_router(HttpAppState::new(Services::builder().a2a_bot_management(Arc::new(Management::default())).build_for_test()));
    let req=|method:&str,path:&str,body:Value| Request::builder().method(method).uri(path)
        .header("authorization","Bearer test-admin").header("content-type","application/json")
        .body(if body.is_null() {Body::empty()} else {Body::from(body.to_string())}).unwrap();
    let missing_ref=app.clone().oneshot(req("POST","/a2a/agents",json!({"owners":["owner"],"agent_card":{}}))).await.unwrap();
    assert_eq!(missing_ref.status(),StatusCode::BAD_REQUEST);
    let response=app.clone().oneshot(req("POST","/a2a/agents",json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{}}))).await.unwrap();
    assert_eq!(response.status(),StatusCode::CREATED);
    let location=response.headers()["location"].to_str().unwrap().to_owned();
    assert_eq!(location,"/a2a/agents/bot/agent-card");
    assert_eq!(app.clone().oneshot(req("GET",&location,Value::Null)).await.unwrap().status(),StatusCode::OK);
    let response=app.clone().oneshot(req("GET","/a2a/agents?provider_bot_ref=001Ab",Value::Null)).await.unwrap();
    assert_eq!(response.status(),StatusCode::OK);
    let value:Value=serde_json::from_slice(&to_bytes(response.into_body(),8192).await.unwrap()).unwrap();
    assert_eq!(value["items"][0]["bot_id"],"bot");
    for path in ["/a2a/agents?source_agent_id=001Ab", "/a2a/agents", "/a2a/agents?agent_name=x", "/a2a/agents?provider_bot_ref=001Ab&provider_name=orders", "/a2a/agents?provider_bot_ref=a&provider_bot_ref=b"] {
        assert_eq!(app.clone().oneshot(req("GET",path,Value::Null)).await.unwrap().status(),StatusCode::BAD_REQUEST);
    }
    for method in ["PUT","POST"] {
        let path=if method=="PUT" {"/a2a/agents/bot/agent-card"} else {"/a2a/agents/bot/agent-card:refresh"};
        for body in [json!({"agent_name":"x","agent_card":{}}),json!({"bot_id":"bot","agent_card":{}})] {
            assert_eq!(app.clone().oneshot(req(method,path,body)).await.unwrap().status(),StatusCode::BAD_REQUEST);
        }
    }
    assert_eq!(app.clone().oneshot(req("POST","/a2a/agents/bot/agent-card:refresh",Value::Null)).await.unwrap().status(),StatusCode::OK);
    for path in ["/a2a/agent-card?agent_name=x", "/a2a/agents/bot/versions"] {
        assert_eq!(app.clone().oneshot(req("GET",path,Value::Null)).await.unwrap().status(),StatusCode::NOT_FOUND);
    }
}

#[tokio::test]
async fn public_visibility_accepts_private_preserves_default_and_rejects_unknown_values() {
    let management=Arc::new(Management::default());
    let app=build_router(HttpAppState::new(Services::builder().a2a_bot_management(management.clone()).build_for_test()));
    for (method,path,body,expected) in [
        ("POST","/a2a/agents",json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{}}),201),
        ("POST","/a2a/agents",json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{},"visibility":"private"}),201),
        ("PUT","/a2a/agents/bot/agent-card",json!({"visibility":"private"}),200),
        ("PUT","/a2a/agents/bot/agent-card",json!({"visibility":"public"}),200),
        ("PUT","/a2a/agents/bot/agent-card",json!({"visibility":"protected"}),200),
        ("PUT","/a2a/agents/bot/agent-card",json!({"agent_card":{}}),200),
        ("POST","/a2a/agents",json!({"provider_bot_ref":"remote","owners":["owner"],"agent_card":{},"visibility":"secret"}),400),
        ("PUT","/a2a/agents/bot/agent-card",json!({"visibility":"secret"}),400),
    ] {
        let response=app.clone().oneshot(Request::builder().method(method).uri(path)
            .header("authorization","Bearer test-admin").header("content-type","application/json")
            .body(Body::from(body.to_string())).unwrap()).await.unwrap();
        assert_eq!(response.status().as_u16(),expected,"{method} {body}");
    }
    assert_eq!(*management.visibility.lock().unwrap(),vec!["protected","private","private","public","protected","omitted"]);
}

#[tokio::test]
async fn public_errors_are_structured_including_parser_and_method_failures() {
    let app=build_router(HttpAppState::new(Services::builder().a2a_bot_management(Arc::new(Management::default())).build_for_test()));
    for (method,path,content_type,accept,body,status,code) in [
        ("DELETE","/a2a/agents","application/json","*/*","{}",405,"METHOD_NOT_ALLOWED"),
        ("POST","/a2a/agents","application/json","text/plain","{}",406,"NOT_ACCEPTABLE"),
        ("POST","/a2a/agents","text/plain","*/*","{}",415,"UNSUPPORTED_MEDIA_TYPE"),
        ("POST","/a2a/agents","application/json","*/*","{",400,"INVALID_REQUEST"),
        ("POST","/a2a/agents","application/json","*/*",r#"{"owners":["owner"],"agent_card":{"name":"one","name":"two"}}"#,400,"INVALID_REQUEST"),
        ("POST","/a2a/agents","application/json","*/*",r#"{"owners":["owner"],"provider_name":"injected","agent_card":{}}"#,400,"INVALID_REQUEST"),
        ("POST","/a2a/agents","application/json","*/*",r#"{"provider_bot_ref":"remote","owners":["owner"]}"#,422,"CARD_SOURCE_INVALID"),
        ("GET","/a2a/agents/bot/agent-card?provider_name=orders&agent_name=x","application/json","*/*","",400,"INVALID_REQUEST"),
        ("GET","/a2a/agents/bot/agent-card?agent_name=a&agent_name=b","application/json","*/*","",400,"INVALID_REQUEST"),
    ] {
        let response=app.clone().oneshot(Request::builder().method(method).uri(path)
            .header("authorization","Bearer test-admin").header("content-type",content_type).header("accept",accept)
            .body(Body::from(body)).unwrap()).await.unwrap();
        assert_eq!(response.status().as_u16(),status,"{path} {body}");
        let id=response.headers()["x-request-id"].to_str().unwrap().to_owned();
        assert_eq!(response.headers()["cache-control"],"no-store");
        if status==405 {assert_eq!(response.headers()["allow"],"GET, POST");}
        let value:Value=serde_json::from_slice(&to_bytes(response.into_body(),8192).await.unwrap()).unwrap();
        assert_eq!(value["error"]["code"],code);assert_eq!(value["error"]["request_id"],id);
        assert!(value["error"]["details"].is_array());
    }
    let response=app.oneshot(Request::builder().method("POST").uri("/a2a/agents")
        .body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(),StatusCode::UNAUTHORIZED);
    assert_eq!(response.headers()["www-authenticate"],"Bearer");
}
