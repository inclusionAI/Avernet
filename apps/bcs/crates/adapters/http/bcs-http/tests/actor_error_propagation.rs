use std::sync::Arc;
use async_trait::async_trait;
use axum::{body::Body, http::{HeaderMap, Request, StatusCode}};
use bcs_auth_api::{AuthConfig, AuthError, AuthPlugin, AuthPluginChain, AuthPrincipal};
use bcs_auth_local::StaticAuthPlugin;
use bcs_http::{router::build_router, state::{ChainUserIdentityPort, HttpAppState}};
use bcs_service_api::{BotDiscoveryCommand, BotDiscoveryResult, BotDiscoveryService};
use bcs_services_container::Services;
use tower::ServiceExt;

struct Refused;
#[async_trait]
impl AuthPlugin for Refused {
    fn can_authenticate(&self, _: &HeaderMap) -> bool { true }
    async fn authenticate(&self, _: &HeaderMap) -> Result<Option<AuthPrincipal>, AuthError> {
        Err(AuthError::InvalidToken("ActorRequired".into()))
    }
    fn priority(&self) -> u8 { 0 }
    fn name(&self) -> &'static str { "refused" }
}
struct Discovery;
#[async_trait]
impl BotDiscoveryService for Discovery {
    async fn discover_bots(&self, _: BotDiscoveryCommand) -> Result<BotDiscoveryResult, bcs_service_api::BotUseCaseError> {
        Ok(BotDiscoveryResult { bots: vec![], count: 0 })
    }
}

#[tokio::test]
async fn actor_required_error_cannot_fall_back_to_human_identity() {
    let services = Services::builder().bot_discovery(Arc::new(Discovery)).build_for_test();
    let user = AuthPrincipal { user_id: Some("alice".into()), ..Default::default() };
    let user_chain = Arc::new(AuthPluginChain::new(vec![Box::new(StaticAuthPlugin::with_principal(user))]));
    let bot_chain = Arc::new(AuthPluginChain::new(vec![Box::new(Refused)]));
    let state = HttpAppState::new(services).with_auth_chain(bot_chain, AuthConfig::default())
        .with_user_identity(Arc::new(ChainUserIdentityPort::new(user_chain)));
    let response = build_router(state).oneshot(Request::builder().uri("/bots/discover?skill=planner")
        .header("authorization", "Bearer parent.jwt.signature").body(Body::empty()).unwrap()).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
}
