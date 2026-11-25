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
use bcs_service_api::{application::v1::*, types::agent_invoke::*};
use bcs_test_support::contract::agent_invoke::AgentInvokeContractDriver;
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    num::NonZeroU32,
    sync::Arc,
    sync::atomic::{AtomicU8, AtomicUsize, Ordering},
};
use tower::ServiceExt;
#[path = "../../../../../services/bcs-edge-permission-store/tests/support/agent_invoke_sql_driver.rs"]
pub mod driver;
#[path = "../register_support/mod.rs"]
mod register_support;
pub const PREFIX: &str = "/api/v1/collaboration/agent-invoke";
pub const MIRROR: &str = "/openapi/v1/collaboration/agent-invoke";
struct Unused;
#[async_trait]
impl RegisterService for Unused {
    async fn issue_register_token(
        &self,
        _: IssueRegisterToken,
    ) -> Result<RegisterTokenView, ApplicationError> {
        panic!("legacy registration must not be called")
    }
    async fn register_bot(&self, _: RegisterBot) -> Result<BotRegistration, ApplicationError> {
        panic!("legacy registration must not be called")
    }
}
pub struct Verify;
#[async_trait]
impl PrincipalVerifier for Verify {
    async fn verify(
        &self,
        h: &HeaderMap,
    ) -> Result<VerifiedRequestIdentity, PrincipalVerificationError> {
        let token = h
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .ok_or(PrincipalVerificationError::Missing)?;
        if token == "outage" {
            return Err(PrincipalVerificationError::DependencyUnavailable);
        }
        let mut c = AuthenticatedCaller {
            tenant: Some("http-tenant".into()),
            user: None,
            bot: None,
            app: None,
            access_key: None,
        };
        match token {
            "source" | "target" | "manager" | "stranger" | "cookie" | "foreign" => {
                c.user = Some(AuthenticatedUserIdentity {
                    id: if token == "cookie" || token == "foreign" {
                        "target"
                    } else {
                        token
                    }
                    .into(),
                    username: "fixture".into(),
                    display_name: None,
                    full_name: None,
                })
            }
            "bot" => {
                c.bot = Some(AuthenticatedBotIdentity {
                    bot_uuid: "http-a".into(),
                    owner_id: "source".into(),
                    app_id: 7,
                    agent_code: "code".into(),
                });
                c.user = Some(AuthenticatedUserIdentity {
                    id: "target".into(),
                    username: "owner-metadata".into(),
                    display_name: None,
                    full_name: None,
                });
            }
            "governor" | "unmapped-app" => {
                c.app = Some(AuthenticatedAppIdentity {
                    app_id: if token == "governor" { 7 } else { 8 },
                    app_name: "fixture".into(),
                    owners: "target".into(),
                    app_type: "agent".into(),
                })
            }
            _ => return Err(PrincipalVerificationError::Invalid("rejected".into())),
        }
        if token == "foreign" {
            c.tenant = Some("other-tenant".into());
        }
        Ok(VerifiedRequestIdentity::pin(
            c,
            "http-test-fixture",
            if token == "cookie" {
                CredentialKind::OAuthSessionCookie
            } else {
                CredentialKind::ExplicitToken
            },
        ))
    }
}
pub fn state() -> bcs_api_http::ApiState {
    let mut s = register_support::test_state(Arc::new(Unused));
    s.principal_verifier = Arc::new(Verify);
    s
}
#[derive(Default)]
pub struct Faults {
    pub code: AtomicU8,
    pub calls: AtomicUsize,
}
fn error(code: u8) -> Option<AgentInvokeError> {
    match code {
        0 => None,
        1 => Some(AgentInvokeError::InvalidInput),
        2 => Some(AgentInvokeError::Unauthenticated),
        3 => Some(AgentInvokeError::Forbidden),
        4 => Some(AgentInvokeError::NotFound),
        5 => Some(AgentInvokeError::Conflict),
        6 => Some(AgentInvokeError::LimitExceeded),
        7 => Some(AgentInvokeError::Unavailable),
        8 => Some(AgentInvokeError::CommitUnknown),
        _ => Some(AgentInvokeError::InvalidState),
    }
}
struct ObservedManagement {
    inner: Arc<dyn bcs_service_api::application::agent_invoke::AgentInvokeGrantManagementService>,
    faults: Arc<Faults>,
}
#[async_trait]
impl bcs_service_api::application::agent_invoke::AgentInvokeGrantManagementService
    for ObservedManagement
{
    async fn change(&self, c: AgentInvokeCommand) -> AgentInvokeResult<AgentInvokeReceipt> {
        self.faults.calls.fetch_add(1, Ordering::SeqCst);
        if let Some(e) = error(self.faults.code.load(Ordering::SeqCst)) {
            return Err(e);
        }
        self.inner.change(c).await
    }
}
struct ObservedQueries {
    inner: Arc<dyn bcs_service_api::application::agent_invoke::AgentInvokeGovernanceQueryService>,
    faults: Arc<Faults>,
}
#[async_trait]
impl bcs_service_api::application::agent_invoke::AgentInvokeGovernanceQueryService
    for ObservedQueries
{
    async fn read(
        &self,
        q: bcs_service_api::types::agent_invoke_query::AgentInvokeQuery,
    ) -> AgentInvokeResult<bcs_service_api::types::agent_invoke_query::AgentInvokeQueryResult> {
        self.faults.calls.fetch_add(1, Ordering::SeqCst);
        if let Some(e) = error(self.faults.code.load(Ordering::SeqCst)) {
            return Err(e);
        }
        self.inner.read(q).await
    }
}
pub struct Fixture {
    pub write_faults: Arc<Faults>,
    pub read_faults: Arc<Faults>,
    pub d: driver::Driver,
    pub app: Router,
}
impl Fixture {
    pub async fn new() -> Self {
        use bcs_edge_permission::agent_invoke::{
            AgentInvokeApplicationService, AgentInvokePolicy, AgentInvokeQueryApplicationService,
            AgentInvokeQueryPolicy,
        };
        let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
        bcs_db_migrations::run_sqlite_migrations(db.as_ref())
            .await
            .unwrap();
        let n = NonZeroU32::new(2).unwrap();
        let d = driver::Driver::new_on_with_limits(
            db,
            bcs_db_api::DbSqlFlavor::Sqlite,
            "http-invoke",
            AgentInvokeLimits {
                max_out_degree: n,
                max_in_degree: n,
                max_pending_out: n,
                max_pending_in: n,
            },
        )
        .await;
        for bot in ["http-a", "http-b", "http-c", "http-d"] {
            d.register(bot).await;
        }
        d.role("human_source", "http-a", Some(AgentInvokeRole::Owner))
            .await;
        d.role("human_manager", "http-a", Some(AgentInvokeRole::Manager))
            .await;
        for bot in ["http-b", "http-c", "http-d"] {
            d.role("human_target", bot, Some(AgentInvokeRole::Owner))
                .await;
        }
        let management = Arc::new(
            AgentInvokeApplicationService::new(
                d.scope.clone(),
                Arc::new(AgentInvokePolicy),
                d.store.clone(),
                d.commands.clone(),
            )
            .unwrap(),
        );
        let queries = Arc::new(
            AgentInvokeQueryApplicationService::new(
                d.scope.clone(),
                Arc::new(AgentInvokeQueryPolicy),
                d.query_repo.clone(),
            )
            .unwrap(),
        );
        let write_faults = Arc::new(Faults::default());
        let read_faults = Arc::new(Faults::default());
        let management = Arc::new(ObservedManagement {
            inner: management,
            faults: write_faults.clone(),
        });
        let queries = Arc::new(ObservedQueries {
            inner: queries,
            faults: read_faults.clone(),
        });
        let app = bcs_api_http::router(state().with_agent_invoke(
            bcs_api_http::v1::agent_invoke::AgentInvokeHttpServices {
                scope: d.scope.clone(),
                tenant: Some("http-tenant".into()),
                governance_apps: BTreeMap::from([(7, "http-governor".into())]),
                management,
                queries,
            },
        ));
        Self {
            d,
            app,
            write_faults,
            read_faults,
        }
    }
    pub async fn request(
        &self,
        method: &str,
        path: &str,
        token: Option<&str>,
        key: Option<&str>,
        body: Value,
    ) -> (StatusCode, Value) {
        call(
            &self.app,
            method,
            &format!("{PREFIX}{path}"),
            token,
            key,
            body,
        )
        .await
    }
    pub async fn grant(&self, to: &str, key: &str) -> Value {
        let (status,data)=self.request("POST","/grants",Some("target"),Some(key),json!({"from_bot_id":"http-a","to_bot_id":to,"expected_version":0,"reason":"authorized grant"})).await;
        assert_eq!(status, StatusCode::CREATED, "{data}");
        data["data"].clone()
    }
    pub async fn pending(&self, key: &str) -> Value {
        let (status, data) = self
            .request(
                "POST",
                "/requests",
                Some("source"),
                Some(key),
                json!({"from_bot_id":"http-a","to_bot_id":"http-b","reason":"source request"}),
            )
            .await;
        assert_eq!(status, StatusCode::CREATED, "{data}");
        data["data"].clone()
    }
    pub async fn gates_off(&self) {
        let r = self.d.store.scope_state().await.unwrap().revision;
        self.d.store.set_enabled(r, false, false).await.unwrap();
    }
    pub async fn delegate_query(&self) {
        use bcs_service_api::port::repo::agent_invoke::AgentInvokeGovernanceRepoPort;
        self.d
            .store
            .publish_delegation(
                &self.d.scope,
                AgentInvokeDelegationPublication {
                    principal: AgentInvokePrincipal::Governance("http-governor".into()),
                    pair: AgentInvokePair {
                        from_bot_id: "http-a".into(),
                        to_bot_id: "http-b".into(),
                    },
                    expected_revision: 0,
                    actions: vec![AgentInvokeAction::Query],
                    reason: "trusted fixture".into(),
                },
            )
            .await
            .unwrap();
    }
}
pub async fn call(
    app: &Router,
    method: &str,
    path: &str,
    token: Option<&str>,
    key: Option<&str>,
    body: Value,
) -> (StatusCode, Value) {
    let mut builder = Request::builder()
        .method(method)
        .uri(path)
        .header("content-type", "application/json")
        .header("x-request-id", "agent-http-request");
    if let Some(token) = token {
        builder = builder.header("authorization", format!("Bearer {token}"));
    }
    if let Some(key) = key {
        builder = builder.header("idempotency-key", key);
    }
    let response = app
        .clone()
        .oneshot(builder.body(Body::from(body.to_string())).unwrap())
        .await
        .unwrap();
    let status = response.status();
    assert_eq!(response.headers().get("cache-control").unwrap(), "no-store");
    assert_eq!(
        response.headers().get("x-request-id").unwrap(),
        "agent-http-request"
    );
    let value: Value =
        serde_json::from_slice(&to_bytes(response.into_body(), 1024 * 1024).await.unwrap())
            .unwrap();
    assert_eq!(value["request_id"], "agent-http-request");
    (status, value)
}
