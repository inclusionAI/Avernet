use std::sync::{Arc, OnceLock};

use async_trait::async_trait;
use bcs_fuse_client::FuseClient;
pub use bcs_http::state::BotRuntimeTokenResolverPort;
use bcs_http::state::{
    BcsHttpAuthBotRuntimeTokenResolver, BotRequestPort, ChainUserIdentityPort, HealthPort,
    HttpAppState, VisibilitySyncPort, VisibilitySyncRequest,
};
use bcs_secret::DefaultSecretService;
use bcs_secret_local::{EnvSecretAccess, NoopSecretAccess};
use bcs_service_api::port::secret::SecretAccessPort;
use bcs_service_api::{ChatRunCleanupPort, ChatRunEventPort, SecretService};
use bcs_services_container::Services;
use bcs_ws::bot::BotConnectionRegistry;
use bcs_ws::shared::RunChannelManager;
use opentelemetry::trace::TraceContextExt;
use tokio::sync::mpsc;
use tracing_opentelemetry::OpenTelemetrySpanExt;

use crate::plugins::build_registered_secret_plugin;
use crate::server::BcsServerState;

pub struct BotRuntimeTokenResolverBuildContext {
    pub base: Arc<dyn BotRuntimeTokenResolverPort>,
    pub state: Arc<BcsServerState>,
}

pub type RegisteredBotRuntimeTokenResolverBuild =
    fn(BotRuntimeTokenResolverBuildContext) -> Option<Arc<dyn BotRuntimeTokenResolverPort>>;

pub struct BotRuntimeTokenResolverFactoryRegistration {
    pub name: &'static str,
    pub build: RegisteredBotRuntimeTokenResolverBuild,
}

inventory::collect!(BotRuntimeTokenResolverFactoryRegistration);

pub fn build_bot_runtime_token_resolver(
    state: Arc<BcsServerState>,
    base: Arc<dyn BotRuntimeTokenResolverPort>,
) -> Arc<dyn BotRuntimeTokenResolverPort> {
    let mut resolver = base;
    for registration in inventory::iter::<BotRuntimeTokenResolverFactoryRegistration> {
        if let Some(next) = (registration.build)(BotRuntimeTokenResolverBuildContext {
            base: Arc::clone(&resolver),
            state: Arc::clone(&state),
        }) {
            tracing::info!(
                resolver = registration.name,
                "registered bot runtime token resolver"
            );
            resolver = next;
        }
    }
    resolver
}

pub(crate) async fn build_http_app_state(state: Arc<BcsServerState>) -> HttpAppState {
    let config = state.config.clone();
    let max_group_messages = if config.max_group_messages > 0 {
        config.max_group_messages as u64
    } else {
        0
    };
    let secret_service = build_secret_service(&config).await;
    let services_with_secret = Services {
        secret: secret_service,
        ..state.services.clone()
    };

    let runtime_token_resolver = build_bot_runtime_token_resolver(
        Arc::clone(&state),
        Arc::new(
            BcsHttpAuthBotRuntimeTokenResolver::default()
                .with_credentials(state.provider_credentials.clone()),
        ),
    );

    HttpAppState::new(services_with_secret)
        .with_bot_runtime_token_resolver(runtime_token_resolver)
        .with_health(Arc::new(BootstrapHealthPort {
            state: Arc::clone(&state),
        }))
        .with_async_chat_poll_wait_max_ms(config.async_chat_poll_wait_max_ms)
        .with_onboard_url_config(config.botchat_url.clone(), config.register_path.clone())
        .with_chat_run_cleanup(Arc::new(BootstrapRunChannelPort {
            run_channels: Arc::clone(&state.run_channels),
        }))
        .with_chat_run_events(Arc::new(BootstrapRunChannelPort {
            run_channels: Arc::clone(&state.run_channels),
        }))
        .with_bot_request(Arc::new(BootstrapBotRequestPort {
            bot_connections: Arc::clone(&state.bot_connections),
        }))
        .with_visibility_sync(Arc::new(BootstrapVisibilitySyncPort {
            fuse_client: state.fuse_client.clone(),
            bcsfuse_config: config.bcsfuse.clone(),
            bots_base_dir: config.bots_base_dir.clone(),
        }))
        .with_group_request_config(
            config.bcs_endpoint.clone(),
            config.bind.clone(),
            config.port,
            config.max_groups_as_driver,
            config.max_group_members,
            config.max_groups_as_member,
        )
        .with_strict_container_validation(config.strict_container_validation)
        .with_onboard_policy(
            config.onboard_binding_enabled,
            config.default_visibility.clone(),
        )
        .with_service_api_keys(Arc::new(bcs_http::service_key::ApiKeyRegistry::new(
            config.api_keys.clone(),
        )))
        .with_manifest_config(
            crate::config_loader::Environment::resolve().as_str().to_string(),
            config.manifest.clone(),
        )
        .with_message_config(
            config.store_messages,
            max_group_messages,
            config.group_chat_delay_min_ms,
            config.group_chat_delay_max_ms,
            config.async_chat_run_timeout_ms,
        )
        .with_invite_config(
            config.invite.token_secret
                .as_deref()
                .map(|s| s.as_bytes().to_vec())
                .unwrap_or_else(|| {
                    tracing::warn!("invite.token_secret not configured — generating random secret (tokens will not survive restart)");
                    let key: Vec<u8> = (0..32).map(|_| fastrand::u8(..)).collect();
                    key
                }),
            config.invite.default_ttl_seconds,
            config.invite.base_url.clone(),
            config.invite.group_link_url.clone(),
            config.invite.session_link_url.clone(),
        )
        .with_allowed_switch_provider_ids(config.allowed_switch_provider_ids.clone())
        .with_provider_stream_gray_list(state.provider_stream_gray_list.clone())
        .with_judge_enabled(config.llm.is_enabled())
        .with_channel_http_ingress(state.channel_http_ingress.clone())
        .with_auth_chain(state.auth_chain.clone(), state.auth_config.clone())
        .with_outbound_url_guard(state.outbound_url_guard.clone())
        .with_admin_invocation_runs(state.admin_invocation_runs.clone())
        .with_user_identity(Arc::new(
            ChainUserIdentityPort::new(state.auth_chain.clone()),
        ))
}

async fn build_secret_service(config: &crate::config::BcsConfig) -> Arc<dyn SecretService> {
    let access = build_secret_access(config).await;
    Arc::new(DefaultSecretService::new(access))
}

async fn build_secret_access(config: &crate::config::BcsConfig) -> Arc<dyn SecretAccessPort> {
    let provider = resolve_secret_provider(config);
    let provider_config = config
        .secret
        .providers
        .get(provider)
        .cloned()
        .unwrap_or_default();

    match provider {
        "noop" => {
            if config.mist.enabled {
                tracing::warn!(
                    "legacy mist config is enabled but secret.provider is noop; using NoopSecretAccess"
                );
            } else {
                tracing::info!("secret.provider=noop; using NoopSecretAccess");
            }
            Arc::new(NoopSecretAccess)
        }
        "env" => {
            let prefix = provider_config
                .get("prefix")
                .and_then(|value| value.as_str())
                .unwrap_or("BCS_SECRET_");
            tracing::info!(provider = "env", "secret backend enabled");
            Arc::new(EnvSecretAccess::new(prefix))
        }
        other => match build_registered_secret_plugin(config, other, provider_config).await {
            Ok(Some(registration)) => {
                tracing::info!(provider = %registration.provider, "registered secret backend enabled");
                registration.access
            }
            Ok(None) => {
                tracing::warn!(
                    provider = other,
                    "secret provider is not available in this binary; using NoopSecretAccess"
                );
                Arc::new(NoopSecretAccess)
            }
            Err(err) => {
                tracing::error!(
                    provider = other,
                    error = %err,
                    "secret provider initialization failed; using NoopSecretAccess"
                );
                Arc::new(NoopSecretAccess)
            }
        },
    }
}

fn resolve_secret_provider(config: &crate::config::BcsConfig) -> &str {
    let configured = config.secret.provider.trim();
    if configured == "noop" && config.mist.enabled {
        "mist"
    } else if configured.is_empty() {
        "noop"
    } else {
        configured
    }
}

struct BootstrapHealthPort {
    state: Arc<BcsServerState>,
}

const BCS_VERSION: &str = concat!(
    env!("CARGO_PKG_VERSION"),
    " (",
    env!("GIT_COMMIT_HASH"),
    " ",
    env!("BUILD_DATE"),
    ")",
);

static HEALTH_VERSION_OVERRIDE: OnceLock<&'static str> = OnceLock::new();

pub fn set_health_version(version: &'static str) {
    if HEALTH_VERSION_OVERRIDE.set(version).is_err() {
        tracing::warn!(
            version,
            "health version override was already set; ignoring subsequent override"
        );
    }
}

fn health_version() -> String {
    HEALTH_VERSION_OVERRIDE
        .get()
        .copied()
        .unwrap_or(BCS_VERSION)
        .to_string()
}

#[async_trait]
impl HealthPort for BootstrapHealthPort {
    async fn health(&self) -> serde_json::Value {
        let is_leader = self.state.leader_election.is_leader().await.unwrap_or(true);
        let leader_info = self
            .state
            .leader_election
            .current_leader()
            .await
            .ok()
            .flatten();

        serde_json::json!({
            "status": "ok",
            "service": "bcs",
            "version": health_version(),
            "is_leader": is_leader,
            "pod_ip": bcs_leader_election::get_local_ip(),
            "leader_info": leader_info.map(|m| serde_json::json!({
                "pod_ip": m.node_id,
                "elected_at": m.elected_at_ms / 1_000,
            })),
        })
    }
}

pub(crate) struct BootstrapRunChannelPort {
    pub(crate) run_channels: Arc<RunChannelManager>,
}

#[async_trait]
impl ChatRunCleanupPort for BootstrapRunChannelPort {
    async fn unregister(&self, run_id: &str) {
        self.run_channels.unregister(run_id).await;
    }
}

#[async_trait]
impl ChatRunEventPort for BootstrapRunChannelPort {
    async fn register(
        &self,
        run_id: String,
        session_key: String,
        sender: mpsc::Sender<String>,
        source: Option<String>,
        from: Option<String>,
    ) {
        let current_span = tracing::Span::current();
        let is_gateway_dispatch = current_span.metadata().is_some_and(|metadata| {
            metadata.target() == "bcn_otel" && metadata.name() == "bcn.gateway.dispatch"
        });
        let (trace_parent, trace_context_status) = if source.as_deref() != Some("http-chat-async") {
            (None, "source_not_http_chat_async")
        } else if !is_gateway_dispatch {
            (None, "gateway_span_not_current")
        } else {
            let context = current_span.context();
            let span_context = context.span().span_context().clone();
            if span_context.is_valid() {
                (Some(span_context), "attached")
            } else {
                (None, "current_span_context_invalid")
            }
        };
        let trace_id = trace_parent
            .as_ref()
            .map(|context| context.trace_id().to_string())
            .unwrap_or_default();
        let parent_span_id = trace_parent
            .as_ref()
            .map(|context| context.span_id().to_string())
            .unwrap_or_default();
        tracing::info!(
            run_id = %run_id,
            session_key = %session_key,
            source = ?source,
            is_gateway_dispatch,
            trace_context_status,
            trace_id = %trace_id,
            parent_span_id = %parent_span_id,
            "Chat run trace context registration evaluated"
        );
        self.run_channels
            .register_with_trace_parent(
                run_id,
                session_key,
                sender,
                source,
                from,
                trace_parent,
            )
            .await;
    }

    async fn unregister(&self, run_id: &str) {
        self.run_channels.unregister(run_id).await;
    }
}

struct BootstrapBotRequestPort {
    bot_connections: Arc<BotConnectionRegistry>,
}

#[async_trait]
impl BotRequestPort for BootstrapBotRequestPort {
    async fn send_request(
        &self,
        bot_uuid: &str,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> Result<serde_json::Value, String> {
        self.bot_connections
            .send_request(bot_uuid, method, params, timeout_ms)
            .await
    }
}

struct BootstrapVisibilitySyncPort {
    fuse_client: Option<Arc<FuseClient>>,
    bcsfuse_config: bcs_fuse_client::BcsFuseConfig,
    bots_base_dir: std::path::PathBuf,
}

#[async_trait]
impl VisibilitySyncPort for BootstrapVisibilitySyncPort {
    async fn sync_visibility(&self, request: VisibilitySyncRequest) {
        if request.actor_kind == bcs_service_api::ActorKind::Human {
            return;
        }

        let Some(fuse_client) = self.fuse_client.clone() else {
            return;
        };

        let bot_context = match bcs_fusion::load_bot_context(&self.bots_base_dir, &request.bot_uuid)
        {
            Ok(ctx) => ctx,
            Err(e) => {
                tracing::info!(
                    bot_id = %request.bot_uuid,
                    error = %e,
                    "No local bot context found, syncing with empty context"
                );
                bcs_service_api::ContextBotSummary {
                    bot_uuid: request.bot_uuid.clone(),
                    name: None,
                    emoji: None,
                    identity: None,
                    soul: None,
                    rules: None,
                    memory: None,
                }
            }
        };
        let bot_name = request.capabilities.name.clone().unwrap_or_default();
        let sync_req = bcs_fusion::build_sync_request(
            &self.bcsfuse_config,
            &request.bot_uuid,
            &bot_name,
            request.capabilities.summary.as_deref(),
            &request.capabilities.domains,
            &request.capabilities.skills,
            &bot_context,
            &request.visibility,
        );

        bcs_fusion::sync_worker_with_retry(&fuse_client, &request.bot_uuid, &sync_req).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bcs_bot_store::MemoryProviderStore;
    use bcs_secret_local::InMemorySecretAccess;
    use crate::plugins::{SecretPluginFactory, SecretPluginRegistration};
    use futures::future::BoxFuture;
    use bcs_leader_election::StandaloneLeaderElection;
    use bcs_route_security::OutboundUrlGuard;
    use bcs_service_api::ProviderStreamGrayList;
    use bcs_service_api::{
        BotMetricCount, BotMetricsSnapshotPort, ChatRunMetricCount,
        DirectChatRunSnapshotPort, GroupMetricCount, GroupMetricsSnapshotPort,
        GroupSessionMetricCount, GroupSessionMetricsSnapshotPort, ProviderCredential,
        ProviderCredentialRepoPort, ServiceResult,
    };
    use bcs_services_container::Services;
    use bcs_ws::web::WorkbenchConnectionRegistry;
    use std::collections::HashMap;
    use tokio::sync::Mutex;
    use tracing::{Instrument, info_span, instrument::WithSubscriber};
    use tracing_subscriber::prelude::*;

    fn test_secret_factory(
        _config: crate::config::BcsConfig,
        provider_config: bcs_config_api::SecretProviderConfig,
    ) -> BoxFuture<'static, crate::Result<SecretPluginRegistration>> {
        Box::pin(async move {
            let user = provider_config
                .get("user")
                .and_then(|value| value.as_str())
                .unwrap_or("svc")
                .to_string();
            Ok(SecretPluginRegistration {
                provider: "test-secret".to_string(),
                access: Arc::new(InMemorySecretAccess::with_entries([(
                    "unit_secret",
                    user,
                    "unit-value".to_string(),
                )])),
            })
        })
    }

    inventory::submit! {
        SecretPluginFactory {
            name: "test-secret",
            build: test_secret_factory,
        }
    }

    fn test_mist_secret_factory(
        _config: crate::config::BcsConfig,
        _provider_config: bcs_config_api::SecretProviderConfig,
    ) -> BoxFuture<'static, crate::Result<SecretPluginRegistration>> {
        Box::pin(async move {
            Ok(SecretPluginRegistration {
                provider: "mist".to_string(),
                access: Arc::new(InMemorySecretAccess::with_entries([(
                    "legacy_mist_secret",
                    "mist-user".to_string(),
                    "mist-value".to_string(),
                )])),
            })
        })
    }

    inventory::submit! {
        SecretPluginFactory {
            name: "mist",
            build: test_mist_secret_factory,
        }
    }

    #[tokio::test]
    async fn default_secret_service_uses_noop_backend() {
        let service = build_secret_service(&crate::config::BcsConfig::default()).await;

        let err = service
            .get_secret("unit_secret")
            .await
            .expect_err("noop backend should not resolve secrets");

        assert!(matches!(err, bcs_service_api::application::SecretServiceError::Unavailable(_)));
    }

    #[tokio::test]
    async fn legacy_mist_enabled_selects_registered_mist_provider() {
        let mut config = crate::config::BcsConfig::default();
        config.mist.enabled = true;

        let service = build_secret_service(&config).await;

        let secret = service
            .get_secret("legacy_mist_secret")
            .await
            .expect("legacy mist config should select registered mist provider");
        assert_eq!(secret.user, "mist-user");
        assert_eq!(secret.value, "mist-value");
    }

    #[tokio::test]
    async fn configured_registered_secret_provider_backs_secret_service() {
        let mut config = crate::config::BcsConfig::default();
        config.secret.provider = "test-secret".to_string();
        config.secret.providers.insert(
            "test-secret".to_string(),
            [(
                "user".to_string(),
                serde_json::Value::String("configured-user".to_string()),
            )]
            .into_iter()
            .collect(),
        );

        let service = build_secret_service(&config).await;

        let secret = service
            .get_secret("unit_secret")
            .await
            .expect("registered provider should resolve secret");
        assert_eq!(secret.user, "configured-user");
        assert_eq!(secret.value, "unit-value");
    }

    #[test]
    fn health_version_uses_runtime_override_when_set() {
        super::set_health_version("0.1.0 (ocb dev/abc; avernet main/def; 2026-07-10)");

        let version = super::health_version();

        assert!(!version.contains('\n'));
        assert!(version.contains("ocb dev/abc"));
        assert!(version.contains("avernet main/def"));
        assert!(version.contains("2026-07-10"));
        assert!(!version.contains("build "));
    }

    #[tokio::test]
    async fn async_chat_run_registration_captures_current_trace_context() {
        let exporter = opentelemetry_sdk::trace::InMemorySpanExporterBuilder::new().build();
        let provider = opentelemetry_sdk::trace::SdkTracerProvider::builder()
            .with_simple_exporter(exporter)
            .build();
        let tracer = opentelemetry::trace::TracerProvider::tracer(&provider, "run-channel-test");
        let subscriber = tracing_subscriber::registry()
            .with(tracing_subscriber::EnvFilter::new("bcn_otel=info"))
            .with(tracing_opentelemetry::layer().with_tracer(tracer));
        let run_channels = Arc::new(RunChannelManager::new());
        let port = BootstrapRunChannelPort {
            run_channels: run_channels.clone(),
        };
        let (tx, _rx) = mpsc::channel(1);
        let (unrelated_tx, _unrelated_rx) = mpsc::channel(1);

        async move {
            let span = info_span!(target: "bcn_otel", "bcn.gateway.dispatch");
            async {
                port.register(
                    "run-traced".to_string(),
                    "group-1".to_string(),
                    tx,
                    Some("http-chat-async".to_string()),
                    None,
                )
                .await;
            }
            .instrument(span)
            .await;

            let unrelated = info_span!(target: "bcn_otel", "unrelated.span");
            async {
                port.register(
                    "run-unrelated".to_string(),
                    "group-1".to_string(),
                    unrelated_tx,
                    Some("http-chat-async".to_string()),
                    None,
                )
                .await;
            }
            .instrument(unrelated)
            .await;
        }
        .with_subscriber(subscriber)
        .await;

        assert!(run_channels.trace_parent("run-traced").await.is_some());
        assert!(run_channels.trace_parent("run-unrelated").await.is_none());
    }

    struct NoopGroupMetricsSnapshotPort;

    #[async_trait]
    impl GroupMetricsSnapshotPort for NoopGroupMetricsSnapshotPort {
        async fn group_counts(&self) -> ServiceResult<Vec<GroupMetricCount>> {
            Ok(Vec::new())
        }
    }

    struct NoopGroupSessionMetricsSnapshotPort;

    #[async_trait]
    impl GroupSessionMetricsSnapshotPort for NoopGroupSessionMetricsSnapshotPort {
        async fn group_session_counts(&self) -> ServiceResult<Vec<GroupSessionMetricCount>> {
            Ok(Vec::new())
        }
    }

    struct NoopBotMetricsSnapshotPort;

    #[async_trait]
    impl BotMetricsSnapshotPort for NoopBotMetricsSnapshotPort {
        async fn bot_counts(&self) -> ServiceResult<Vec<BotMetricCount>> {
            Ok(Vec::new())
        }
    }

    struct NoopDirectChatRunSnapshotPort;

    #[async_trait]
    impl DirectChatRunSnapshotPort for NoopDirectChatRunSnapshotPort {
        async fn direct_chat_run_counts(&self) -> ServiceResult<Vec<ChatRunMetricCount>> {
            Ok(Vec::new())
        }
    }

    struct RegisteredAgentpassResolver {
        fallback: Arc<dyn BotRuntimeTokenResolverPort>,
        observed_port: u16,
    }

    #[async_trait]
    impl BotRuntimeTokenResolverPort for RegisteredAgentpassResolver {
        async fn resolve_agentpass_agent_code(&self, _token: &str) -> Option<String> {
            Some(format!("registered-agent-code:{}", self.observed_port))
        }

        async fn try_provider_admin(&self, token: &str) -> Option<String> {
            self.fallback.try_provider_admin(token).await
        }
    }

    fn build_registered_agentpass_resolver(
        ctx: BotRuntimeTokenResolverBuildContext,
    ) -> Option<Arc<dyn BotRuntimeTokenResolverPort>> {
        Some(Arc::new(RegisteredAgentpassResolver {
            fallback: ctx.base,
            observed_port: ctx.state.config.port,
        }))
    }

    inventory::submit! {
        BotRuntimeTokenResolverFactoryRegistration {
            name: "test-agentpass",
            build: build_registered_agentpass_resolver,
        }
    }

    fn test_server_state(port: u16) -> Arc<BcsServerState> {
        let mut config = crate::BcsConfig::default();
        config.port = port;
        let credentials: Arc<dyn ProviderCredentialRepoPort> =
            Arc::new(MemoryProviderStore::new());
        Arc::new(BcsServerState {
            config,
            services: Services::noop(),
            run_channels: Arc::new(RunChannelManager::new()),
            bot_connections: Arc::new(BotConnectionRegistry::new()),
            frontend_connections: Arc::new(WorkbenchConnectionRegistry::new()),
            frontend_run_channels: Arc::new(RunChannelManager::new()),
            coordination_processed: Arc::new(Mutex::new(HashMap::new())),
            leader_election: Arc::new(StandaloneLeaderElection::local()),
            lifecycle: Arc::new(Mutex::new(crate::lifecycle::LifecycleOrchestrator::new())),
            fuse_client: None,
            provider_credentials: credentials,
            provider_stream_gray_list: Arc::new(ProviderStreamGrayList::new(Vec::new())),
            channel_http_ingress: None,
            group_metrics_snapshot: Arc::new(NoopGroupMetricsSnapshotPort),
            group_session_metrics_snapshot: Arc::new(NoopGroupSessionMetricsSnapshotPort),
            bot_metrics_snapshot: Arc::new(NoopBotMetricsSnapshotPort),
            direct_chat_run_snapshot: Arc::new(NoopDirectChatRunSnapshotPort),
            metrics: None,
            auth_chain: Arc::new(bcs_auth_api::AuthPluginChain::new(Vec::new())),
            auth_config: bcs_auth_api::AuthConfig::default(),
            user_identity_port: None,
            outbound_url_guard: OutboundUrlGuard::allowing_private_networks_for_tests(),
            admin_invocation_runs: Arc::new(bcs_http::state::AdminInvocationStore::default()),
        })
    }

    #[tokio::test]
    async fn registered_runtime_token_resolver_extends_default_resolver() {
        let credentials = Arc::new(MemoryProviderStore::new());
        credentials
            .insert_credential(ProviderCredential {
                provider_id: "provider-1".to_string(),
                credential_kind: "provider_admin".to_string(),
                secret_value: "provider-admin-token".to_string(),
                disabled: false,
                created_at: 0,
                updated_at: 0,
            })
            .await
            .expect("insert provider admin credential");
        let credentials: Arc<dyn ProviderCredentialRepoPort> = credentials;
        let resolver = build_bot_runtime_token_resolver(test_server_state(21999), Arc::new(
            BcsHttpAuthBotRuntimeTokenResolver::default()
                .with_credentials(credentials),
        ));

        let agent_code = resolver
            .resolve_agentpass_agent_code("agentpass.header.sig")
            .await;

        assert_eq!(agent_code.as_deref(), Some("registered-agent-code:21999"));
        assert_eq!(
            resolver
                .try_provider_admin("provider-admin-token")
                .await
                .as_deref(),
            Some("provider-1")
        );
    }
}
