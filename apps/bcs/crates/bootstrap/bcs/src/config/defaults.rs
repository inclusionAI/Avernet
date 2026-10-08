//! Default helpers for BcsConfig and other typed defaults.

use std::collections::BTreeMap;
use std::path::PathBuf;

// bcs_config_api contract types referenced by BcsConfig's Default impl.
#[allow(unused_imports)]
use bcs_config_api::{
    AuthChainConfig, AuthSdkConfig, BcsFuseConfig, CacheConfig, ChannelConfigSection,
    DatabaseConfig, DingTalkAccountConfig, EventingConfig, FusionProviderConfig,
    HumanNotifyConfig, LeaderElectionConfig, LlmConfig, LoggingConfig, ManifestConfig,
    SecretConfig, SecurityConfig, UserDirectoryConfig,
};
// Local types declared in `super::types`.
#[allow(unused_imports)]
use super::types::{
    BcsConfig, CorsConfig, CollaborationConfig, CollaborationTemplateStorageKind,
    CollaborationTemplatesConfig, GatewayPrincipalConfig, GroupSessionWsConfig,
    InviteConfig, MessageHistoryConfig, MetricsConfig, MetricsMode, OpenApiV1Config,
    ProviderHttpConfig, SecurityGatewayConfig, SessionFilesConfig, SessionFilesShareConfig,
    TelemetryConfig,
};

pub(super) fn default_public_claim_max_count() -> u64 {
    1_000
}

pub(super) fn default_invite_ttl_seconds() -> u64 {
    86400
}

pub(super) fn default_session_files_storage_backend() -> String {
    "local".to_string()
}

pub(super) fn default_session_files_multipart_threshold() -> u64 {
    104_857_600
}

pub(super) fn default_session_files_max_file_size() -> u64 {
    5_368_709_120
}

pub(super) fn default_session_files_share_link_ttl() -> u64 {
    3600
}

pub(super) fn default_session_files_share_ttl() -> u64 {
    86400
}

pub(super) fn default_history_attachment_ttl() -> u64 {
    3600
}

pub(super) fn default_telemetry_enabled() -> bool {
    true
}

pub(super) fn default_telemetry_service_name() -> String {
    "bcn".to_string()
}

pub(super) fn default_openapi_v1_public_collaboration_base_url() -> String {
    "http://127.0.0.1:21000/api/v1/collaboration".to_string()
}

pub(super) fn default_collaboration_templates_base_dir() -> PathBuf {
    PathBuf::from("/data/bcs/collaboration-templates")
}

pub(super) fn default_collaboration_templates_default_language() -> String {
    "zh-CN".to_string()
}

pub(super) fn default_gateway_principal_issuers() -> Vec<String> {
    vec!["gateway".to_string(), "backend".to_string()]
}

pub(super) fn default_gateway_principal_audience() -> String {
    "bcs".to_string()
}

pub(super) fn default_gateway_principal_key_id() -> String {
    "bare".to_string()
}

pub(super) fn default_gateway_principal_signing_key_env() -> String {
    "AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE".to_string()
}

pub(super) fn default_group_session_ws_signing_key_secret() -> String {
    "bcn-group-session-ws-jwt".to_string()
}

pub(super) fn default_bind() -> String {
    "127.0.0.1".to_string()
}

pub(super) fn default_port() -> u16 {
    21000
}

pub(super) fn default_provider_stream_gray_enabled() -> bool {
    false
}

pub(super) fn default_max_history() -> usize {
    1000
}

pub(super) fn default_max_groups_as_driver() -> usize {
    3
}

pub(super) fn default_max_group_members() -> usize {
    5
}

pub(super) fn default_max_groups_as_member() -> usize {
    10
}

pub(super) fn default_group_chat_delay_min_ms() -> u64 {
    3000
}

pub(super) fn default_group_chat_delay_max_ms() -> u64 {
    8000
}

pub(super) fn default_max_group_messages() -> i64 {
    100
}

pub(super) fn default_register_path() -> String {
    "/bcn/register".to_string()
}

pub(super) fn default_provider_chat_run_timeout_ms() -> u64 {
    bcs_service_api::DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS
}

pub(super) fn default_async_chat_run_timeout_ms() -> u64 {
    (2 * 60 + 5) * 60 * 1_000
}

pub(super) fn default_async_chat_run_retention_ms() -> u64 {
    120 * 1_000
}

pub(super) fn default_async_chat_poll_wait_max_ms() -> u64 {
    30_000
}

pub(super) fn default_async_chat_run_max_entries() -> usize {
    100_000
}

pub(super) fn default_async_chat_run_store() -> String {
    "memory".to_string()
}

pub(super) fn default_bot_run_context_store() -> String {
    "memory".to_string()
}

pub(super) fn default_metrics_endpoint_path() -> String {
    "/metrics".to_string()
}

pub(super) fn default_metrics_mode() -> MetricsMode {
    MetricsMode::Pull
}

pub(super) fn default_metrics_enabled() -> bool {
    false
}

pub(super) fn default_message_history_cutoff() -> u64 {
    0
}

pub(super) fn default_manager_worker_message_history_cutoff() -> u64 {
    u64::MAX
}

pub(super) fn default_new_participant_visible_limit() -> u64 {
    100
}

pub(super) fn default_message_page_limit() -> u32 {
    50
}

pub(super) fn default_message_max_page_limit() -> u32 {
    100
}

pub(super) fn default_security_provider() -> String {
    "noop".to_string()
}

pub(super) fn default_security_dry_run() -> bool {
    true
}

impl Default for BcsConfig {
    fn default() -> Self {
        Self {
            state_machine_history: Default::default(),
            bind: default_bind(),
            port: default_port(),
            bots_base_dir: PathBuf::from("/bots"),
            fusion_provider: None,
            llm: LlmConfig::default(),
            max_history_per_session: default_max_history(),
            store_messages: false,
            dingtalk_accounts: Vec::new(),
            auth_token: None,
            gateway_principal: GatewayPrincipalConfig::default(),
            group_session_ws: GroupSessionWsConfig::default(),
            leader_election: None,
            cache: CacheConfig::default(),
            database: DatabaseConfig::default(),
            secret: SecretConfig::default(),
            channels: ChannelConfigSection::default(),
            human_notify: HumanNotifyConfig::default(),
            message_delivery: bcs_config_api::message_delivery::MessageDeliveryConfig::default(),
            provider_http: ProviderHttpConfig::default(),
            collaboration: CollaborationConfig::default(),
            openapi_v1: OpenApiV1Config::default(),
            api: None,
            max_groups_as_driver: default_max_groups_as_driver(),
            max_group_members: default_max_group_members(),
            max_groups_as_member: default_max_groups_as_member(),
            group_chat_delay_min_ms: default_group_chat_delay_min_ms(),
            group_chat_delay_max_ms: default_group_chat_delay_max_ms(),
            max_group_messages: default_max_group_messages(),
            strict_container_validation: true,
            bcs_endpoint: None,
            friend_work_order_base_url: None,
            botchat_url: None,
            register_path: default_register_path(),
            default_visibility: None,
            manifest: ManifestConfig::default(),
            onboard_binding_enabled: false,
            logging: LoggingConfig::default(),
            telemetry: TelemetryConfig::default(),
            bcsfuse: BcsFuseConfig::default(),
            auth_sdk: AuthSdkConfig::default(),
            user_directory: UserDirectoryConfig::default(),
            auth: AuthChainConfig::default(),
            cors: CorsConfig::default(),
            group_logger: None,
            provider_chat_run_timeout_ms: default_provider_chat_run_timeout_ms(),
            async_chat_run_timeout_ms: default_async_chat_run_timeout_ms(),
            async_chat_run_retention_ms: default_async_chat_run_retention_ms(),
            async_chat_poll_wait_max_ms: default_async_chat_poll_wait_max_ms(),
            async_chat_run_max_entries: default_async_chat_run_max_entries(),
            async_chat_run_store: default_async_chat_run_store(),
            bot_run_context_store: default_bot_run_context_store(),
            security_gateway: SecurityGatewayConfig::default(),
            security: SecurityConfig::default(),
            eventing: EventingConfig::default(),
            message_history: MessageHistoryConfig::default(),
            api_keys: Vec::new(),
            metrics: MetricsConfig::default(),
            invite: InviteConfig::default(),
            session_files: SessionFilesConfig::default(),
            allowed_switch_provider_ids: Vec::new(),
            uplink: Default::default(),
            provider_stream_gray_enabled: default_provider_stream_gray_enabled(),
            provider_stream_gray_created_by: Vec::new(),
        }
    }
}
