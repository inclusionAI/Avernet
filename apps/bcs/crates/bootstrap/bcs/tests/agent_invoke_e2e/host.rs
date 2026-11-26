//! Synthetic trusted inventory publisher linked ONLY into the required-features
//! companion, never the product binary. Business commands must use public HTTP.
use std::{collections::BTreeMap, num::{NonZeroU32, NonZeroU64}, sync::Arc};
use anyhow::{bail, Context};
use axum::{extract::State, http::{HeaderMap, StatusCode}, routing::post, Json, Router};
use bcs_edge_permission_store::agent_invoke::DbAgentInvokeStore;
use bcs_service_api::{port::repo::agent_invoke::{AgentInvokeFactRepoPort, AgentInvokeGovernanceRepoPort},
    types::agent_invoke::*, application::agent_invoke::AgentInvokeDirectLimits};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
enum Publication {
    Fact { bot_id: String, binding_generation: NonZeroU64, eligibility_generation: NonZeroU64,
        eligible: bool, active: bool, hidden: bool, expected_revision: u64, ttl_ms: NonZeroU64 },
    Delegation { principal_id: String, from: String, to: String, expected_revision: u64, actions: Vec<String> },
    Gates { expected_revision: NonZeroU64, writer: bool, runtime: bool },
    Inspect,
}
struct Feed { store: Arc<DbAgentInvokeStore>, scope: AgentInvokeScope, key: String }
async fn publish(State(feed): State<Arc<Feed>>, headers: HeaderMap, Json(command): Json<Publication>)
    -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    // A per-process capability, separate from every ingress/Gateway credential.
    if headers.get("x-fixture-key").and_then(|v| v.to_str().ok()) != Some(feed.key.as_str()) {
        return Err((StatusCode::UNAUTHORIZED, Json(json!({"error":"fixture_auth"}))));
    }
    let result: AgentInvokeResult<Value> = async {
        match command {
            Publication::Inspect => {
                let s = feed.store.scope_state().await?;
                Ok(json!({"revision":s.revision.get(),"writer":s.writer_enabled,"runtime":s.runtime_enabled}))
            }
            Publication::Gates { expected_revision, writer, runtime } => {
                let s = feed.store.set_enabled(expected_revision, writer, runtime).await?;
                Ok(json!({"revision":s.revision.get(),"writer":s.writer_enabled,"runtime":s.runtime_enabled}))
            }
            Publication::Fact { bot_id, binding_generation, eligibility_generation,
                eligible, active, hidden, expected_revision, ttl_ms } => {
                let now = feed.store.database_time_ms().await?;
                let valid_until_ms = now.checked_add(ttl_ms.get()).ok_or(AgentInvokeError::InvalidInput)?;
                let f = feed.store.publish_fact(&feed.scope, AgentInvokeFactPublication {
                    state: AgentInvokeBotState {bot_id, binding_generation, eligibility_generation, eligible, active, hidden},
                    expected_revision, valid_until_ms }).await?;
                Ok(json!({"revision":f.revision.get(),"valid_until_ms":f.valid_until_ms}))
            }
            Publication::Delegation { principal_id, from, to, expected_revision, actions } => {
                let actions = actions.iter().map(|a| match a.as_str() {
                    "request"=>Ok(AgentInvokeAction::Request), "grant"=>Ok(AgentInvokeAction::Grant),
                    "approve"=>Ok(AgentInvokeAction::Approve), "reject"=>Ok(AgentInvokeAction::Reject),
                    "cancel"=>Ok(AgentInvokeAction::Cancel), "revoke"=>Ok(AgentInvokeAction::Revoke),
                    "query"=>Ok(AgentInvokeAction::Query), _=>Err(AgentInvokeError::InvalidInput),
                }).collect::<AgentInvokeResult<Vec<_>>>()?;
                let revision = feed.store.publish_delegation(&feed.scope, AgentInvokeDelegationPublication {
                    principal: AgentInvokePrincipal::Governance(principal_id),
                    pair: AgentInvokePair {from_bot_id:from,to_bot_id:to}, expected_revision, actions,
                    reason:"explicit isolated synthetic inventory".into() }).await?;
                Ok(json!({"revision":revision.get()}))
            }
        }
    }.await;
    result.map(Json).map_err(|e| (StatusCode::CONFLICT, Json(json!({"error":e.to_string()}))))
}

pub async fn run(config_dir: Option<&std::path::PathBuf>, feed_port: u16) -> anyhow::Result<()> {
    let mut config = bcs::BcsConfig::try_load_with_env(config_dir).map_err(anyhow::Error::msg)?;
    if bcs_config::resolve_env_str() != "local" || config.bind != "127.0.0.1"
        || config.database.database_type != bcs::DatabaseType::Sqlite || feed_port == 0 {
        bail!("AgentInvoke synthetic companion requires local/loopback/SQLite and an explicit feed port");
    }
    let key = std::env::var("BCS_AGENT_INVOKE_FIXTURE_KEY").context("missing isolated fixture capability")?;
    if key.len() < 32 { bail!("fixture capability too short"); }
    let access = bcs::http_adapter::build_secret_access(&config).await?;
    bcs::resolve_config_secrets(&mut config, access.as_ref()).await?;
    let infrastructure = bcs::InfrastructurePlugins::from_config(&config).await?;
    let db = infrastructure.db().context("SQLite plugin required")?;
    let scope = AgentInvokeScope {namespace:"isolated-agent-invoke-e2e".into(),env:"local".into()};
    let n = NonZeroU32::new(3).unwrap();
    let limits = AgentInvokeLimits {max_out_degree:n,max_in_degree:n,max_pending_out:n,max_pending_in:n};
    let ttl = NonZeroU64::new(300_000).unwrap();
    let extensions = bcs::BcsServerExtensions { agent_invoke:Some(bcs::agent_invoke_wiring::AgentInvokeRegistration {
        scope:scope.clone(), tenant:Some("agent-invoke-e2e".into()),
        governance_apps:BTreeMap::from([(7,"fixture-governor".into())]), limits, max_fact_ttl_ms:ttl,
        direct_limits:AgentInvokeDirectLimits { default_timeout_ms:NonZeroU64::new(10_000).unwrap(),
            max_timeout_ms:NonZeroU64::new(30_000).unwrap() }, }), ..Default::default() };
    // The actual production SQL root applies migrations and composes all services.
    let server = bcs::BcsServer::new_with_infrastructure(config, infrastructure, extensions).await?;
    let store = Arc::new(DbAgentInvokeStore::new(db, bcs_db_api::DbSqlFlavor::Sqlite, scope.clone(), limits, ttl)?);
    let feed = Arc::new(Feed {store,scope,key});
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST,feed_port)).await?;
    let task = tokio::spawn(async move {
        axum::serve(listener, Router::new().route("/publish",post(publish)).with_state(feed)).await
    });
    let result = server.run().await;
    task.abort();
    result.map_err(Into::into)
}
