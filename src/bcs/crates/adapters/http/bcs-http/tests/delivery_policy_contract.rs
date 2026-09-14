use async_trait::async_trait;
use axum::{body::{Body, to_bytes}, http::{Request, StatusCode}};
use bcs_config_api::message_delivery::{DeliveryPolicy, DeliveryPolicyRecord};
use bcs_http::{router::build_router, service_key::{ApiKeyEntry, ApiKeyRegistry, sha256_hex}, state::HttpAppState};
use bcs_service_api::{CallerContext, MessageFlowService, ServiceResult, WebSendCommand, WebSendOutcome};
use bcs_services_container::Services;
use std::sync::Arc;
use tower::ServiceExt;

struct PolicyService;
#[async_trait]
impl MessageFlowService for PolicyService {
    async fn handle_bot_event(&self, _: bcs_service_api::BotEventCommand) -> ServiceResult<bcs_service_api::BotEventOutcome> { unreachable!() }
    async fn handle_group_callback(&self, _: bcs_service_api::GroupCallbackCommand) -> ServiceResult<bcs_service_api::GroupCallbackOutcome> { unreachable!() }
    async fn handle_chat_abort(&self, _: bcs_service_api::ChatAbortCommand) -> ServiceResult<bcs_service_api::ChatAbortOutcome> { unreachable!() }
    async fn register_task_run_alias(&self, _: &str, _: &str, _: &str) -> ServiceResult<bcs_service_api::TaskRunAliasRegistration> { unreachable!() }
    async fn handle_task_dispatch(&self, _: bcs_service_api::TaskDispatchCommand) -> ServiceResult<bcs_service_api::TaskDispatchOutcome> { unreachable!() }
    async fn handle_task_complete(&self, _: bcs_service_api::TaskCompleteCommand) -> ServiceResult<bcs_service_api::TaskCompleteOutcome> { unreachable!() }
    async fn handle_web_send(&self, _: WebSendCommand) -> ServiceResult<WebSendOutcome> { unreachable!() }
    async fn get_delivery_policy(&self, caller: CallerContext) -> ServiceResult<DeliveryPolicyRecord> {
        let CallerContext::Human(human) = caller else { panic!("untrusted caller reached application") };
        assert_eq!(human.actor_id, "human_operator");
        Ok(DeliveryPolicyRecord::default())
    }
    async fn replace_delivery_policy(&self, caller: CallerContext, expected: u64, policy: DeliveryPolicy) -> ServiceResult<DeliveryPolicyRecord> {
        self.get_delivery_policy(caller).await?;
        assert_eq!(expected, 0);
        Ok(DeliveryPolicyRecord { version: 1, policy, updated_by: "human_operator".into(), updated_at_ms: 1 })
    }
}


struct Credentials;
#[async_trait]
impl bcs_auth_api::AuthPlugin for Credentials {
    fn can_authenticate(&self, _: &axum::http::HeaderMap) -> bool { true }
    fn priority(&self) -> u8 { 10 }
    fn name(&self) -> &'static str { "contract_credentials" }
    async fn authenticate(&self, headers: &axum::http::HeaderMap) -> Result<Option<bcs_auth_api::AuthPrincipal>, bcs_auth_api::AuthError> {
        if headers.get("Authorization").and_then(|v| v.to_str().ok()) == Some("Bearer bot-token") {
            let mut principal = bcs_auth_api::AuthPrincipal::new(bcs_auth_api::AuthSource::SessionToken);
            principal.bot_uuid = Some("bot".into());
            principal.user_id = Some("owner-must-not-be-human".into());
            return Ok(Some(principal));
        }
        if headers.get("Cookie").and_then(|v| v.to_str().ok()) == Some("contract_session=valid") {
            let mut principal = bcs_auth_api::AuthPrincipal::new(bcs_auth_api::AuthSource::Cookie);
            principal.user_id = Some("operator".into());
            return Ok(Some(principal));
        }
        Ok(None)
    }
}
struct ProviderCredentials;
#[async_trait]
impl bcs_http::state::BotRuntimeTokenResolverPort for ProviderCredentials {
    async fn resolve_agentpass_agent_code(&self, _: &str) -> Option<String> { None }
    async fn try_provider_admin(&self, token: &str) -> Option<String> {
        (token == "provider-token").then(|| "provider".into())
    }
}
fn app(local: bool) -> axum::Router {
    let mut services = Services::noop();
    services.message_flow = Arc::new(PolicyService);
    let plugins: Vec<Box<dyn bcs_auth_api::AuthPlugin>> = if local {
        vec![Box::new(bcs_auth_local::LocalAuthPlugin::from_config(&bcs_auth_api::LocalAuthConfig {
            mock_user_id: Some("operator".into()), mock_user_name: None, allow_mock_headers: false,
        }))]
    } else { vec![Box::new(Credentials)] };
    build_router(HttpAppState::new(services)
        .with_auth_chain(Arc::new(bcs_auth_api::AuthPluginChain::new(plugins)), Default::default())
        .with_bot_runtime_token_resolver(Arc::new(ProviderCredentials))
        .with_service_api_keys(Arc::new(ApiKeyRegistry::new(vec![
            ApiKeyEntry { name: "service-admin".into(), sha256: sha256_hex("service-key"), bound_groups: vec![] },
        ]))))
}
async fn call(app: axum::Router, method: &str, path: &str, headers: &[(&str, &str)]) -> axum::response::Response {
    let mut request = Request::builder().uri(path).method(method).header("content-type", "application/json");
    for (key, value) in headers { request = request.header(*key, *value); }
    let body = serde_json::to_vec(&serde_json::json!({"expected_version":0,"policy":DeliveryPolicy::default()})).unwrap();
    app.oneshot(request.body(Body::from(body)).unwrap()).await.unwrap()
}
#[tokio::test]
async fn human_only_routes_and_old_path_is_not_an_alias() {
    for method in ["GET", "PUT"] {
        for (headers, expected) in [
            (vec![], StatusCode::UNAUTHORIZED),
            (vec![("X-Mock-User-Id", "operator"), ("X-Human-Id", "operator")], StatusCode::UNAUTHORIZED),
            (vec![("Cookie", "contract_session=invalid")], StatusCode::UNAUTHORIZED),
            (vec![("Authorization", "Bearer bot-token")], StatusCode::FORBIDDEN),
            (vec![("Authorization", "Bearer provider-token")], StatusCode::FORBIDDEN),
            (vec![("X-BCS-Service-Key", "service-key")], StatusCode::FORBIDDEN),
            (vec![("X-BCS-Service-Key", "invalid")], StatusCode::UNAUTHORIZED),
            (vec![("Cookie", "contract_session=valid")], StatusCode::OK),
            (vec![("Cookie", "contract_session=valid"), ("Authorization", "Bearer bot-token")], StatusCode::FORBIDDEN),
        ] {
            let response = call(app(false), method, "/admin/message-delivery/policy", &headers).await;
            assert_eq!(response.status(), expected, "{method} {headers:?}");
            if expected == StatusCode::OK {
                let value: DeliveryPolicyRecord = serde_json::from_slice(&to_bytes(response.into_body(), 10240).await.unwrap()).unwrap();
                assert_eq!(value.version, if method == "PUT" { 1 } else { 0 });
                if method == "PUT" { assert_eq!(value.updated_by, "human_operator"); }
            }
        }
        let old = call(app(false), method, "/openapi/v1/admin/message-delivery/policy", &[("Cookie", "contract_session=valid")]).await;
        assert_eq!(old.status(), StatusCode::NOT_FOUND);
    }
}
#[tokio::test]
async fn configured_local_mock_does_not_upgrade_machine_credentials() {
    for method in ["GET", "PUT"] {
        assert_eq!(call(app(true), method, "/admin/message-delivery/policy", &[]).await.status(), StatusCode::OK);
        for (header, value, expected) in [
            ("X-BCS-Service-Key", "service-key", StatusCode::FORBIDDEN),
            ("Authorization", "Bearer provider-token", StatusCode::FORBIDDEN),
            ("Authorization", "Bearer invalid", StatusCode::UNAUTHORIZED),
            ("X-BCS-Bot-Token", "invalid", StatusCode::UNAUTHORIZED),
        ] {
            assert_eq!(call(app(true), method, "/admin/message-delivery/policy", &[(header, value)]).await.status(), expected);
        }
    }
}
