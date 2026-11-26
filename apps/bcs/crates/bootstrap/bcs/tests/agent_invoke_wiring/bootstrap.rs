//! The real SQL BcsServer constructor, its final ApiState and signed ingress.
//! In-process integration, not a production inventory or Singlebox claim.
use super::*;
use tower::ServiceExt;
use bcs_db_api::{DbPlugin, DbStatement};

const KEY: &str = "TEST-ONLY-composition-principal-signing-key";
#[test]
fn sql_bootstrap_mounts_only_explicit_registration_and_never_activates_it() {
    const NAME: &str = "server::state::agent_invoke_bootstrap_tests::sql_bootstrap_mounts_only_explicit_registration_and_never_activates_it";
    const CHILD: &str = "BCS_AGENT_INVOKE_COMPOSITION_CHILD";
    if std::env::var_os(CHILD).is_none() {
        let dir = tempfile::tempdir().unwrap();
        let output = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", NAME, "--nocapture"]).current_dir(dir.path()).env(CHILD, "1")
            .env("SERVER_ENV", "local")
            .env("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", KEY)
            .env("BCS_SECRET_BCN_GROUP_SESSION_WS_JWT", "TEST-ONLY-session-jwt-composition-key")
            .output().unwrap();
        assert!(output.status.success(), "isolated actual SQL bootstrap failed:\n{}\n{}",
            String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
        return;
    }
    tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
        for injected in [false, true] {
            let dir = tempfile::tempdir().unwrap();
            let mut config = BcsConfig { bots_base_dir: dir.path().join("bots"), ..BcsConfig::default() };
            config.invite.token_secret = Some("TEST-ONLY-invite-composition-key-32bytes".into());
            config.manifest.bundles.clear();
            config.bcsfuse.enabled = false;
            config.metrics.enabled = false;
            config.gateway_principal.signing_key_secret = None;
            config.gateway_principal.signing_key_env = "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE".into();
            config.session_files.backend.insert("data_dir".into(), toml::Value::String(dir.path().join("files").to_string_lossy().into()));
            config.collaboration.templates.base_dir = dir.path().join("templates");
            let db = Arc::new(bcs_db_local::LocalSqliteDbPlugin::new().unwrap());
            let n = std::num::NonZeroU32::MIN;
            let registration = crate::agent_invoke_wiring::AgentInvokeRegistration {
                scope: bcs_service_api::types::agent_invoke::AgentInvokeScope {
                    namespace: "explicit-bootstrap".into(), env: "local".into() }, tenant: Some("composition-tenant".into()),
                governance_apps: std::collections::BTreeMap::new(),
                limits: bcs_service_api::types::agent_invoke::AgentInvokeLimits {
                    max_out_degree: n, max_in_degree: n, max_pending_out: n, max_pending_in: n },
                max_fact_ttl_ms: std::num::NonZeroU64::new(60_000).unwrap(),
                direct_limits: bcs_service_api::application::agent_invoke::AgentInvokeDirectLimits {
                    default_timeout_ms: std::num::NonZeroU64::new(1_000).unwrap(),
                    max_timeout_ms: std::num::NonZeroU64::new(60_000).unwrap() },
            };
            let infrastructure = crate::InfrastructurePlugins::from_parts(crate::CachePluginKind::LocalMemory,
                crate::DbPluginKind::LocalSqlite, Arc::new(bcs_cache_local::InMemoryCachePlugin::new()), db.clone());
            let extensions = BcsServerExtensions { agent_invoke: injected.then_some(registration), ..Default::default() };
            let server = BcsServer::new_with_infrastructure(config, infrastructure, extensions).await.unwrap();
            let state = server.state.openapi_v1.clone();
            assert_eq!(state.agent_invoke.is_some(), injected);
            assert_eq!(state.agent_invoke_direct.is_some(), injected);
            let scopes = db.query(DbStatement::new("SELECT writer_enabled,runtime_enabled FROM agent_invoke_scopes")).await.unwrap();
            assert_eq!(scopes.len(), usize::from(injected));
            for scope in scopes {
                assert_eq!(bcs_db_api::db_get_column::<u64>(&scope, "writer_enabled").unwrap(), 0);
                assert_eq!(bcs_db_api::db_get_column::<u64>(&scope, "runtime_enabled").unwrap(), 0);
            }
            server.state.services.registry.register("composition-source".into(), bcs_service_api::BotCapabilities {
                name: Some("Composition source".into()), agent_code: Some("source-code".into()),
                visibility: "protected".into(), ..Default::default() }).await.unwrap();
            let now = chrono::Utc::now().timestamp();
            let mut header = jsonwebtoken::Header::new(jsonwebtoken::Algorithm::HS256);
            header.kid = Some(server.config.gateway_principal.key_id.clone());
            let token = jsonwebtoken::encode(&header, &serde_json::json!({
                "iss":server.config.gateway_principal.issuers[0], "aud":server.config.gateway_principal.audience,
                "iat":now, "exp":now+120, "principals":[{"type":"bot","tenant":"composition-tenant","bot":{
                    "bot_uuid":"composition-source","agent_code":"source-code","owner_id":"display-only","app_id":7,"tenant":"composition-tenant"}}]
            }), &jsonwebtoken::EncodingKey::from_secret(KEY.as_bytes())).unwrap();
            let router = bcs_api_http::router(state);
            for prefix in ["/api/v1", "/openapi/v1"] {
                let uri = format!("{prefix}/collaboration/agent-invoke/invocations");
                let response = router.clone().oneshot(Request::builder().method("POST").uri(&uri)
                    .header("content-type", "application/json").header("x-avernet-principal", &token)
                    .body(Body::from(r#"{"to_bot_id":"target","message":"closed capability"}"#)).unwrap()).await.unwrap();
                let status = response.status();
                let body = axum::body::to_bytes(response.into_body(), 100_000).await.unwrap();
                assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "injected={injected}, {uri}: {}", String::from_utf8_lossy(&body));
            }
            for table in ["agent_invoke_facts", "agent_invoke_grants", "agent_invoke_receipts", "agent_invoke_audit"] {
                let rows = db.query(DbStatement::new(format!("SELECT COUNT(*) AS n FROM {table}"))).await.unwrap();
                assert_eq!(bcs_db_api::db_get_column::<u64>(&rows[0], "n").unwrap(), 0, "{table}");
            }
        }
    });
}
