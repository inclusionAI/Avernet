use std::sync::Arc;

use async_trait::async_trait;
use bcs_fuse_client::FuseClient;
use bcs_http::state::{
    BcsHttpAuthBotRuntimeTokenResolver, BotRequestPort, ChainUserIdentityPort, HealthPort,
    HttpAppState, VisibilitySyncPort, VisibilitySyncRequest,
};
use bcs_secret::DefaultSecretService;
use bcs_secret_local::NoopSecretAccess;
use bcs_service_api::port::secret::SecretAccessPort;
use bcs_service_api::{ChatRunCleanupPort, ChatRunEventPort, SecretService};
use bcs_services_container::Services;
use bcs_ws::bot::BotConnectionRegistry;
use bcs_ws::shared::RunChannelManager;
use tokio::sync::mpsc;

use crate::server::BcsServerState;

pub(crate) async fn build_http_app_state(state: Arc<BcsServerState>) -> HttpAppState {
    let config = state.config.clone();
    let max_group_messages = if config.max_group_messages > 0 {
        config.max_group_messages as u64
    } else {
        0
    };
    let secret_service = build_secret_service(&config.mist).await;
    let services_with_secret = Services {
        secret: secret_service,
        ..state.services.clone()
    };

    HttpAppState::new(services_with_secret)
        .with_bot_runtime_token_resolver(Arc::new(
            BcsHttpAuthBotRuntimeTokenResolver::default()
                .with_credentials(state.provider_credentials.clone()),
        ))
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
        .with_user_identity(Arc::new(
            ChainUserIdentityPort::new(state.auth_chain.clone()),
        ))
}

async fn build_secret_service(cfg: &bcs_config_api::MistConfig) -> Arc<dyn SecretService> {
    if cfg.enabled {
        tracing::warn!("mist config is ignored in the public build; using NoopSecretAccess");
    } else {
        tracing::info!("mist disabled in config; using NoopSecretAccess");
    }
    let access: Arc<dyn SecretAccessPort> = Arc::new(NoopSecretAccess);
    Arc::new(DefaultSecretService::new(access))
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
            "version": BCS_VERSION,
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
        self.run_channels
            .register(run_id, session_key, sender, source, from)
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
