#![allow(dead_code)]
use async_trait::async_trait;
use axum::{
    Router,
    body::{Body, to_bytes},
    http::{HeaderMap, Request, StatusCode},
};
use bcs_api_http::{
    CredentialKind, PrincipalVerificationError, PrincipalVerifier, VerifiedRequestIdentity,
};
use bcs_edge_permission::agent_invoke::{
    AgentInvokeApplicationService, AgentInvokeInterceptor, AgentInvokePolicy,
};
use bcs_message_flow::{
    a2a_chat::{A2aChat, ChatRunStore},
    agent_invoke_ingress::AgentInvokeDirectApplicationService,
};
use bcs_service_api::port::agent_invoke::AgentInvokeIdentityResolutionPort;
use bcs_service_api::{ChatRunCleanupPort, ChatRunEventPort, application::agent_invoke::*};
use serde_json::{Value, json};
use std::{
    num::NonZeroU64,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering::SeqCst},
    },
};
use tower::ServiceExt;
#[path = "../../../../test-support/message_flow_contract_support.rs"]
pub mod flow;
#[path = "../../../../adapters/http/bcs-api-http/tests/agent_invoke_support/mod.rs"]
pub mod manage;
const KEY: &[u8] = b"TEST-ONLY-AgentInvoke-credential-contract-key";
pub const PATH: &str = "/api/v1/collaboration/agent-invoke/invocations";
pub const MIRROR: &str = "/openapi/v1/collaboration/agent-invoke/invocations";
struct SignedSource(bcs_api_http::v1::gateway_principal::GatewayPrincipalTokenVerifier);
#[async_trait]
impl PrincipalVerifier for SignedSource {
    async fn verify(
        &self,
        h: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let all = h.get_all("x-avernet-principal");
        if all.iter().count() != 1 {
            return Err(PrincipalVerificationError::Missing);
        }
        let token = all
            .iter()
            .next()
            .and_then(|v| v.to_str().ok())
            .ok_or_else(|| PrincipalVerificationError::Invalid("header".into()))?;
        let caller = self
            .0
            .verify(token)
            .map_err(|_| PrincipalVerificationError::Invalid("signature or claims".into()))?;
        Ok(VerifiedRequestIdentity::pin(
            caller,
            "gateway-fixture",
            CredentialKind::GatewayPrincipalHeader,
        ))
    }
}
pub fn token(scenario: &str) -> String {
    let mut bot = json!({"type":"bot","tenant":"http-tenant","bot":{"bot_uuid":"http-a","owner_id":"source","app_id":7,"agent_code":"code","tenant":"http-tenant"}});
    match scenario {
        "wrong-code" => bot["bot"]["agent_code"] = json!("Code"),
        "missing" => bot["bot"]["bot_uuid"] = json!("absent"),
        "other-bot" => bot["bot"]["bot_uuid"] = json!("http-c"),
        "foreign" => {
            bot["tenant"] = json!("foreign");
            bot["bot"]["tenant"] = json!("foreign");
        }
        _ => {}
    }
    let mut principals = vec![bot];
    if scenario == "user-and-bot" {
        principals.push(json!({"type":"user","tenant":"http-tenant","subject":{"id":"source","username":"source"}}));
    }
    if scenario == "user" {
        principals = vec![json!({"type":"user","subject":{"id":"target","username":"target"}})];
    }
    if scenario == "app" {
        principals = vec![
            json!({"type":"app","tenant":"http-tenant","app":{"app_id":7,"app_name":"spring-agent","owners":"target","tenant":"http-tenant","app_type":"agent"}}),
        ];
    }
    let now = chrono::Utc::now().timestamp() as u64;
    let mut header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256);
    header.kid = Some("test".into());
    jsonwebtoken::encode(
        &header,
        &json!({"iss":"gateway","aud":"bcs","iat":now,"exp":now+60,"principals":principals}),
        &jsonwebtoken::EncodingKey::from_secret(KEY),
    )
    .unwrap()
}
#[derive(Default)]
pub struct Events {
    pub count: AtomicUsize,
    senders: Mutex<Vec<tokio::sync::mpsc::Sender<String>>>,
}
#[async_trait]
impl ChatRunEventPort for Events {
    async fn register(
        &self,
        _: String,
        _: String,
        sender: tokio::sync::mpsc::Sender<String>,
        _: Option<String>,
        _: Option<String>,
    ) {
        self.count.fetch_add(1, SeqCst);
        self.senders.lock().unwrap().push(sender);
    }
    async fn unregister(&self, _: &str) {}
}
#[async_trait]
impl ChatRunCleanupPort for Events {
    async fn unregister(&self, _: &str) {}
}
pub struct Identity {
    pub store: Arc<bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore>,
    pub calls: AtomicUsize,
    pub pause: std::sync::atomic::AtomicBool,
    pub entered: tokio::sync::Notify,
    pub release: tokio::sync::Notify,
}
#[async_trait]
impl AgentInvokeIdentityResolutionPort for Identity {
    async fn resolve_invocation_source(
        &self,
        scope: &AgentInvokeScope,
        claim: &AgentInvokeVerifiedBot,
    ) -> AgentInvokeResult<AgentInvokeAuthenticatedSource> {
        self.calls.fetch_add(1, SeqCst);
        let result = self.store.resolve_invocation_source(scope, claim).await?;
        if self.pause.load(SeqCst) {
            self.entered.notify_one();
            tokio::time::timeout(std::time::Duration::from_secs(5), self.release.notified())
                .await
                .unwrap();
        }
        Ok(result)
    }
}
pub struct Fixture {
    pub managed: manage::Fixture,
    pub app: Router,
    pub events: Arc<Events>,
    pub support: flow::FlowTestSupport,
    pub identity: Arc<Identity>,
    pub service: Arc<AgentInvokeDirectApplicationService>,
}
impl Fixture {
    pub async fn new() -> Self {
        let managed = manage::Fixture::new().await;
        managed
            .d
            .sql(
                "UPDATE bcs_bots SET agent_code='code' WHERE bot_uuid IN ('http-a','http-c')",
                vec![],
            )
            .await;
        let support = flow::FlowTestSupport::new_group_with_driver_and_observer().await;
        for bot in ["http-a", "http-b", "http-c"] {
            support.registry.insert_named_actor(bot, bot).await;
            support.registry.without_agent_credentials(bot).await;
        }
        let application = Arc::new(
            AgentInvokeApplicationService::new(
                managed.d.scope.clone(),
                Arc::new(AgentInvokePolicy),
                managed.d.store.clone(),
                managed.d.commands.clone(),
            )
            .unwrap(),
        );
        let mut chain = bcs_service_api::interceptor::InterceptorChain::new();
        chain.push(AgentInvokeInterceptor::new(application));
        let events = Arc::new(Events::default());
        let runs = Arc::new(ChatRunStore::with_repo(Arc::new(
            bcs_chat_run_store::SqlChatRunRepo::new(
                managed.d.db.clone(),
                bcs_db_api::DbSqlFlavor::Sqlite,
                Arc::new(bcs_cache_local::InMemoryCachePlugin::new()),
                "invoke-http:".into(),
                60_000,
                managed.d.scope.env.clone(),
            ),
        )));
        let direct = Arc::new(
            A2aChat::new_with_run_ports(
                support.bot_delivery.clone(),
                runs,
                60_000,
                support.registry.clone(),
                Arc::new(bcs_test_support::NoopFriendCoreService),
                events.clone(),
                events.clone(),
            )
            .with_interceptors(Arc::new(chain)),
        );
        let identity = Arc::new(Identity {
            store: managed.d.store.clone(),
            calls: AtomicUsize::new(0),
            pause: Default::default(),
            entered: Default::default(),
            release: Default::default(),
        });
        let service = Arc::new(
            AgentInvokeDirectApplicationService::new(
                managed.d.scope.clone(),
                identity.clone(),
                direct,
                AgentInvokeDirectLimits {
                    default_timeout_ms: NonZeroU64::new(60_000).unwrap(),
                    max_timeout_ms: NonZeroU64::new(120_000).unwrap(),
                },
            )
            .unwrap(),
        );
        let mut state = manage::state().with_agent_invoke_direct(
            bcs_api_http::v1::agent_invoke::AgentInvokeDirectHttpServices {
                scope: managed.d.scope.clone(),
                tenant: Some("http-tenant".into()),
                service: service.clone(),
            },
        );
        let trust = bcs_api_http::v1::gateway_principal::GatewayPrincipalTrust::new(
            vec!["gateway".into()],
            "bcs",
            "test",
        )
        .unwrap();
        state.principal_verifier = Arc::new(SignedSource(
            bcs_api_http::v1::gateway_principal::GatewayPrincipalTokenVerifier::new(KEY, trust)
                .unwrap(),
        ));
        let app = bcs_api_http::router(state);
        Self {
            managed,
            app,
            events,
            support,
            identity,
            service,
        }
    }
    pub async fn grant(&self) {
        self.managed.grant("http-b", "invoke-grant").await;
    }
    pub async fn call(&self, path: &str, kind: Option<&str>, body: Value) -> (StatusCode, Value) {
        let mut request = Request::builder()
            .method("POST")
            .uri(path)
            .header("content-type", "application/json")
            .header("x-request-id", "invoke-http-test");
        if let Some(kind) = kind {
            request = request.header(
                "x-avernet-principal",
                if kind == "unsigned" {
                    "not-a-signed-principal".into()
                } else {
                    token(kind)
                },
            );
        }
        let response = self
            .app
            .clone()
            .oneshot(request.body(Body::from(body.to_string())).unwrap())
            .await
            .unwrap();
        assert_eq!(response.headers()["cache-control"], "no-store");
        assert_eq!(response.headers()["x-request-id"], "invoke-http-test");
        let status = response.status();
        let body = to_bytes(response.into_body(), 1024 * 1024).await.unwrap();
        (status, serde_json::from_slice(&body).unwrap())
    }
    pub async fn assert_no_effects(&self) {
        assert_eq!(self.events.count.load(SeqCst), 0);
        assert!(self.support.bot_delivery.frames().await.is_empty());
        let rows = self
            .managed
            .d
            .db
            .query(bcs_db_api::DbStatement::new(
                "SELECT COUNT(*) AS n FROM bcs_chat_runs",
            ))
            .await
            .unwrap();
        assert_eq!(bcs_db_api::db_get_column::<i64>(&rows[0], "n").unwrap(), 0);
    }
}
pub fn body() -> Value {
    json!({"to_bot_id":"http-b","message":" original direct text 世界 "})
}
