//! Bot Coordination Service (BCS) for Multi-Bot Collaboration
//!
//! This crate provides:
//! - **BCS Server**: Bot registry, proposal generation, group management
//! - **WebSocket Support**: Bot connections via WebSocket protocol
//! - **DingTalk Integration**: DingTalk channel support
//!
//! # Architecture
//!
//! ```text
//! ┌─────────────────────────────────────────────────────────────┐
//! │              Bot Coordination Service (BCS)                  │
//! │                                                             │
//! │  Bot Base Dir: /bots/                                       │
//! │       ├── zhangsan/ (IDENTITY, SOUL, RULES, MEMORY)         │
//! │       ├── lisi/                                             │
//! │       └── security/                                         │
//! │                                                             │
//! │  Services:                                                  │
//! │  • Bot registry (bot_uuid → bot with TTL)                   │
//! │  • Proposal generation (request-group-help → proposal)      │
//! │  • Group management (Agent/Fusion modes)                    │
//! │  • Context fusion (reads files, single LLM call)            │
//! │  • Message routing (to driver or @mentioned bot)            │
//! │  • WebSocket connections for bots                           │
//! │  • DingTalk channel integration                             │
//! └─────────────────────────────────────────────────────────────┘
//! ```
//!
//! # Usage
//!
//! ## As a BCS Server
//!
//! ```ignore
//! use bcs::{BcsServer, BcsConfig};
//!
//! let config = BcsConfig::from_env();
//! let server = BcsServer::new(config);
//! server.run().await?;
//! ```

// Re-export shared DTOs and protocol types from bcs_protocol
pub use bcs_protocol::{
    AgentEventPayload,
    AgentStream,
    // WebSocket protocol types
    BcsFrame,
    BindingChannel as WireBindingChannel,
    BindingChannels as WireBindingChannels,
    BotCapabilities as WireBotCapabilities,
    BotContextSummary as WireBotContextSummary,
    BotDynamicStatus as WireBotDynamicStatus,
    BotInfo as WireBotInfo,
    BotStatus,
    BotStatusParams,
    ChannelInfo,
    ChannelSource,
    ChatAbortParams,
    ChatEventPayload,
    ChatEventRouting as WireChatEventRouting,
    ChatEventState,
    ChatSendParams,
    ChatSendResponse,
    ConfirmProposalResponse,
    Conflict as WireConflict,
    ConflictPosition as WireConflictPosition,
    ContentBlock,
    CreateGroupRequest,
    CreateGroupResponse,
    DynamicStatusResponse as WireDynamicStatusResponse,
    EngineType,
    ErrorShape,
    EventFrame,
    FusionRequest as WireFusionRequest,
    FusionResponse as WireFusionResponse,
    GroupContext,
    MessageContent,
    ParticipantInfo,
    ParticipantPerspective as WireParticipantPerspective,
    ProposalContext,
    ProposalResponse,
    RequestFrame,
    ResponseFrame,
    ResponseMode as WireResponseMode,
    RouteSelectorWire as WireRouteSelectorWire,
    Skill as WireSkill,
    UsageInfo,
};

// Re-export gateway types from bcs_ws::gateway (ex bcs-gateway, moved in C3)
pub use bcs_ws::gateway::{
    AuthValidator, BotSendResult, ChatAbortManager, ChatAbortParams as GatewayChatAbortParams,
    ChatAbortResult, ChatEvent, ChatEventState as GatewayChatEventState, ChatHistoryParams,
    ChatHistoryResult, ChatSendParams as GatewayChatSendParams, ChatSendResult, ChatSendStatus,
    ErrorShape as GatewayErrorShape, EventBroadcaster, EventFrame as GatewayEventFrame,
    GatewayContext, GatewayFrame, GatewaySession, MessageRouting,
    RequestFrame as GatewayRequestFrame, ResponseFrame as GatewayResponseFrame, RouteAndSendResult,
    RoutingDecision as GatewayRoutingDecision, RoutingTarget as GatewayRoutingTarget,
    SessionAccess,
};

// Re-export service container types.
pub use bcs_services_container::{Services, ServicesBuilder};

// Re-export service contract types from bcs_service_api
pub use bcs_service_api::{
    AuditEntry, BindingChannel, BindingChannels, BotCapabilities, BotContextSummary,
    BotDynamicStatus, BotRegistryCoreService, BotSendResult as ServiceBotSendResult,
    ChatEventRouting, ContextConflict as Conflict, ContextConflictPosition as ConflictPosition,
    ContextFusionRequest as FusionRequest, ContextFusionResponse as FusionResponse,
    ContextParticipantPerspective as ParticipantPerspective, DynamicStatusResponse,
    FusionCoreService, Group, GroupChatProposal, GroupCoreService, GroupMessage, GroupMessageType,
    Participant, ParticipantKind, ParticipantRole, ProposalCoreService, RegisteredBot,
    ResponseMode, RouteAndSendResult as ServiceRouteAndSendResult, RouteSelectorWire,
    RoutingCoreService, RoutingDecision as ServiceRoutingDecision,
    RoutingTarget as ServiceRoutingTarget, ServiceError, ServiceResult, Skill, Task, TaskStatus,
    Workspace, deserialize_skills,
};

// Re-export implementations
pub use bcs_bot::BotCore;
pub use bcs_fusion::LocalFusionService;
pub use bcs_group::GroupStore;
pub use bcs_group_store::GroupBuilder;
pub use bcs_proposal::{ProposalBuilder, ProposalStore};
pub use bcs_routing::MessageRouter;

// Server modules
pub mod auth_wiring;
mod config;
mod config_loader;
mod env;
mod error;
pub mod http_adapter;
mod identity_wiring;
pub mod lifecycle;
pub mod metrics;
pub mod migrations;
pub mod plugins;
pub mod server;
pub mod state_machine_timeout_scanner;
pub mod timeout_scanner;
pub mod token_expiry_scanner;

pub mod leader_election {
    pub use bcs_leader_election::get_local_ip;
}

// Re-export env utilities
pub use env::resolve_env;

pub mod logging;

// Re-exports
pub use config::{
    AuthSdkConfig, BcsConfig, CacheConfig, DatabaseConfig, DatabaseType, DingTalkAccountConfig,
    InviteConfig, LlmConfig, LlmProviderType, LoggingConfig, MessageHistoryConfig, MetricsConfig,
    MetricsMode, MistConfig, RedisCacheConfig, SecurityGatewayProviderConfig,
    StructuredOutputMode, UserDirectoryConfig, UserDirectoryProviderConfig,
};
pub use error::{BcsError, Result};
pub use plugins::{CachePluginKind, DbPluginKind, InfrastructurePlugins};
pub use server::{BcsServer, BcsServerExtensions};
pub use http_adapter::set_health_version;

pub const BCS_VERSION: &str = concat!(
    env!("CARGO_PKG_VERSION"),
    " (",
    env!("GIT_COMMIT_HASH"),
    " ",
    env!("BUILD_DATE"),
    ")",
);

pub async fn run_from_env() -> Result<()> {
    run_from_env_with_config_dir(None).await
}

pub async fn run_from_env_with_config_dir(config_dir: Option<&std::path::PathBuf>) -> Result<()> {
    let mut config = BcsConfig::load_with_env(config_dir);
    config
        .validate_api_keys()
        .map_err(BcsError::InvalidConfig)?;

    logging::init(&config.logging);
    logging::spawn_cleanup_task(config.logging.outputs.clone());

    tracing::info!(
        version = %BCS_VERSION,
        bind = %config.bind,
        port = config.port,
        bots_base_dir = %config.bots_base_dir.display(),
        dingtalk_accounts = config.dingtalk_accounts.len(),
        "Starting Bot Coordination Service (BCS)"
    );

    for account in &config.dingtalk_accounts {
        tracing::info!(
            account_id = %account.account_id,
            client_id = ?account.client_id.is_some(),
            gateway_mode = account.gateway_mode,
            enable_scene_group = account.enable_scene_group,
            is_default_reply_bot = account.is_default_reply_bot,
            "DingTalk account configured"
        );
    }

    if config.database.database_type == DatabaseType::Mysql {
        tracing::info!(
            "MySQL/OceanBase database backend selected; remote migrations are not auto-applied at service startup"
        );
    }
    let group_logger = config.group_logger.take();
    let server = BcsServer::new_with_storage(config).await?;

    if let Some(logger_cfg) = group_logger {
        if logger_cfg.enabled {
            tracing::info!(
                groups = logger_cfg.group_ids.len(),
                "Starting DingTalk group message logger"
            );
            tokio::spawn(async move {
                if let Err(e) = ding_logger::run(logger_cfg).await {
                    tracing::error!(error = %e, "Group message logger failed");
                }
            });
        }
    }

    server.run().await
}
