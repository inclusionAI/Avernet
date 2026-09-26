//! HTTP server for the Bot Coordination Service.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};
use std::time::Instant;
use std::time::{SystemTime, UNIX_EPOCH};

use async_trait::async_trait;
use axum::extract::MatchedPath;
use axum::extract::ws::WebSocketUpgrade as WsUpgrade;
use axum::{
    Router,
    body::Body,
    extract::{State, WebSocketUpgrade},
    http::{Request, StatusCode, header::CONTENT_TYPE},
    middleware::{self, Next},
    response::{IntoResponse, Json, Response},
    routing::get,
};
use tokio::sync::Mutex;
use tower_http::catch_panic::CatchPanicLayer;
use tower_http::cors::{AllowHeaders, AllowMethods, AllowOrigin, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing::{debug, info, warn};

use crate::Result;
use crate::auth_wiring::AuthPluginFactory;
use crate::config::{
    BcsConfig, CollaborationTemplateStorageKind, GatewayPrincipalConfig, GroupSessionWsConfig,
    LlmConfig, LlmProviderType,
};
use crate::friend_connect_notification::HttpFriendConnectNotificationPort;
use crate::HttpFriendAuthSyncPort;
use crate::lifecycle::LifecycleOrchestrator;
use crate::plugins::{
    CachePluginKind, DbPluginKind, InfrastructurePlugins, LeaderElectionRegistration,
    build_human_mention_notify_port, build_registered_channel_provider,
    build_registered_leader_election, build_registered_llm_provider,
    build_registered_security_gateway, build_registered_user_directory,
};
use bcs_api_http::v1::gateway_principal::{GatewayPrincipalTokenVerifier, GatewayPrincipalTrust};
use bcs_api_http::v1::openapi::SessionFileUrlProjector;
use bcs_api_http::{ApiState, PrincipalVerifier};
use bcs_app_bot::{BotServiceConfig, BotServiceImpl, InternalBotAttributesServiceImpl};
use bcs_app_collaboration_definition::CollaborationDefinitionServiceImpl as V1CollaborationDefinitionServiceImpl;
use bcs_app_collaboration_template::CollaborationTemplateServiceImpl as V1CollaborationTemplateServiceImpl;
use bcs_app_group::{GroupServiceConfig, GroupServiceImpl};
use bcs_app_invitation::{InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl};
use bcs_app_invite_code::InviteCodeServiceImpl;
use bcs_app_register;
use bcs_app_session::{
    GroupSessionConnectionServiceImpl, SessionFileApplicationServiceImpl, SessionServiceConfig,
    SessionServiceImpl,
};
use bcs_bot::{
    Bot, BotCandidateSearchCore, BotControlPlaneCore, BotCore, EmptyWorkerProfileCoreService,
    ProviderBotEvents, ProviderCore, ProviderManagement,
};
use bcs_bot_store::{DbProviderStore, MemoryBotRepo, MemoryProviderStore, PersistentBotRepo};
use bcs_channel::{BcsChannelService, ChannelServiceInboundSink};
use bcs_channel_api::{ChannelHttpIngressRegistry, ChannelProvider, ChannelProviderRegistry};
use bcs_channel_store::{
    DbChannelBindingStore, DbConversationSessionStore, DbHumanInputRequestStore,
    DbImParticipantStore, MemoryChannelBindingRepo, MemoryConversationSessionRepo,
    MemoryHumanInputRequestRepo, MemoryImParticipantRepo,
};
use bcs_collaboration_runtime::CollaborationRuntime;
use bcs_collaboration_store::{
    DbCollaborationTemplateRepo, MemoryCollaborationStore, MySqlCollaborationStore,
};
use bcs_collaboration_template::{CollaborationTemplateServiceImpl, FileCollaborationTemplateRepo};
use bcs_db_api::DbSqlFlavor;
use bcs_domain::{MessageAudience, MessageVisibilityDomain, NewMessage, SenderType};
use bcs_friend::{FriendCore, FriendRequestCore};
use bcs_friend_store::{
    DbFriendRequestStore, DbFriendStore, MemoryFriendRepo, MemoryFriendRequestRepo,
};
use bcs_invite_code_store::{DbInviteCodeStore, MemoryInviteCodeRepo};
use bcs_fuse_client::FuseClient;
use bcs_fusion::{FuseClientService, FuseWorkerProfileService, LocalFusionService};
use bcs_group::{GroupConfig, GroupCore, GroupManagement, GroupManagementWithRuntimeCleanup};
use bcs_group_store::{MemoryGroupRepo, MySqlGroupStore};
use bcs_http::{
    admin_invocation_terminal::AdminInvocationTerminalObserver, state::AdminInvocationStore,
};
use bcs_interaction::{InteractionManagement, InteractionTerminalObserver, MemoryInteractionStore};
use bcs_judge::{LlmJudgeService, NoopJudgeEvaluator};
use bcs_jwt::GroupSessionJwtService;
use bcs_leader_election::StandaloneLeaderElection;
use bcs_llm_anthropic::AnthropicLlmClient;
use bcs_llm_api::LlmChatCompletionPort;
use bcs_llm_openai_compatible::OpenAiCompatibleLlmClient;
use bcs_message::MessageService;
use bcs_message_flow::{A2aChat, BcsGroupFusion, BcsGroupMessageHistory, BcsMessageFlow};
use bcs_message_store::{MemoryMessageRepo, MySqlMessageStore};
use bcs_organization::{OrganizationCore, OrganizationManagement};
use bcs_organization_store::{DbOrganizationStore, MemoryOrganizationRepo};
use bcs_proposal::{GroupProposalUseCases, GroupProposalUseCasesConfig, ProposalStore};
use bcs_relation::RelationCore;
use bcs_relation_store::DbRelationStore;
use bcs_route_security::OutboundUrlGuard;
use bcs_routing::MessageRouter;
use bcs_routing::security::SecurityInterceptor;
use bcs_secret_local::InMemorySecretAccess;
use bcs_security_gateway_api::SecurityGatewayPort;
use bcs_security_gateway_local::NoopSecurityGateway;
use bcs_service_api::application::v1::GroupSessionConnectionService;
use bcs_service_api::core::WorkerProfileCoreService;
use bcs_service_api::interceptor::InterceptorChain;
use bcs_service_api::lifecycle::ServiceLifecycle;
use bcs_service_api::port::{
    EventRecordFactoryPort, EventRecorderPort, GroupSessionTokenPort, SecretAccessPort,
};
use bcs_service_api::{
    A2aChatRunService, A2aChatService, BotActor, BotCandidateSearchCoreService,
    BotCatalogCleanupPort,
    BotControlPlaneCoreService, BotControlPlaneRepoPort, BotDeliveryPort, BotDeliveryTarget,
    BotMetricsSnapshotPort, BotRegistryCoreService, BotRunContextPort, BotTerminalEvent,
    BotTerminalObserverPort, BotTerminalState, CallerContext, CanResolveInteraction,
    ChannelBindingCleanupPort, ChannelService, CollaborationRuntimeService,
    CollaborationTemplateService, CompositeBotTerminalObserver, DirectChatClientKind,
    DirectChatRunEvent, DirectChatRunLifecycleHook, DirectChatRunReason, DirectChatRunSnapshotPort,
    FriendCoreService, FriendRequestCoreService, FrontendDeliveryPort, GroupCoreService,
    GroupHistoryBotRequestPort, GroupManagementService, GroupMessageHistoryService,
    GroupMetricsSnapshotPort, GroupRepoPort, GroupSessionMetricsSnapshotPort,
    HandleBotTerminalEventCommand, HumanInputReadyEvent, InteractionService, InviteService,
    JudgeEvaluatorPort, LeaderElectionPort, MessageFlowService, MetricsResult,
    OrganizationCoreService, OrganizationManagementService, OrganizationRepoPort,
    ProviderBotBindingRepoPort, ProviderBotCoreService, ProviderBotEventService,
    ProviderCoreService, ProviderCredentialRepoPort, ProviderManagementService, ProviderRepoPort,
    NoopBotCatalogCleanupPort, ProviderStreamGrayList, RelationCoreService, RoutingCoreService,
    ServiceError, ServiceResult,
    SessionChannelDeliveryOutcome, SessionChannelOutboundPort, SessionManagementService,
    StateMachineResultPublishCommand, StateMachineResultPublisherPort, StateMachineTerminalEvent,
    SystemMessageService, WebSendCommand, WsCloseReason, WsErrorKind,
    WsLifecycleInstrumentationHook, WsPeer,
    port::repo::{
        ChannelBindingRepoPort, ConversationSessionRepoPort, HumanInputRequestRepoPort,
        ImParticipantRepoPort, MessageRepoPort, SessionRepoPort,
    },
};
use bcs_services_container::{Services, ServicesBuilder};
use bcs_session::{
    SessionLaunchApplication, SessionManagementServiceImpl, SessionManagementWithRuntimeCleanup,
};
use bcs_session_store::{MemorySessionRepo, MySqlSessionStore};
use bcs_system_message::{
    SystemMessageDispatcherImpl, SystemMessageServiceImpl,
    producers::bot_hidden_notice::BotHiddenNoticeProducer,
    producers::bot_joined::BotJoinedMessageProducer, producers::bot_left::BotLeftMessageProducer,
    producers::generic::GenericNotificationMessageProducer,
    producers::human_joined::HumanJoinedMessageProducer,
    producers::participant_mode_changed::ParticipantModeChangedMessageProducer,
    producers::session_context::SessionContextMessageProducer,
};
use bcs_user_directory_api::UserDirectoryPlugin;
use bcs_ws::bot::BotConnectionRegistry;
use bcs_ws::shared::RunChannelManager;
use bcs_ws::web::{WorkbenchConnectionRegistry, WorkbenchFrontendDelivery};
use secrecy::{ExposeSecret, Secret};

// ---------------------------------------------------------------------------
// Module subtree: split-out bodies. Each child file groups related items
// (state structs, defaults, construction, runtime wiring, etc.). All pub
// items stay reachable through the re-exports below; behavior is preserved
// exactly.
// ---------------------------------------------------------------------------

mod channels;
mod collaboration;
mod construction;
mod construction_finish;
mod construction_memory;
mod construction_storage;
mod api_auth_build;
mod defaults;
mod gateway_trust;
mod helpers;
mod protocol;
mod router;
mod runtime;
mod secrets;
mod session_files;
mod state;

// Re-export pub items from child submodules so legacy crate::server::Foo /
// bcs::server::Foo paths continue to resolve.
#[allow(unused_imports)]
pub use channels::*;
#[allow(unused_imports)]
pub use collaboration::*;
#[allow(unused_imports)]
pub use api_auth_build::*;
#[allow(unused_imports)]
pub use construction::*;
#[allow(unused_imports)]
pub use construction_finish::*;
#[allow(unused_imports)]
pub use construction_memory::*;
#[allow(unused_imports)]
pub use construction_storage::*;
#[allow(unused_imports)]
pub use defaults::*;
#[allow(unused_imports)]
pub use gateway_trust::*;
#[allow(unused_imports)]
pub use helpers::*;
#[allow(unused_imports)]
pub use protocol::*;
#[allow(unused_imports)]
pub use router::*;
#[allow(unused_imports)]
pub use runtime::*;
#[allow(unused_imports)]
pub use secrets::*;
#[allow(unused_imports)]
pub use session_files::*;
#[allow(unused_imports)]
pub use state::*;

// Test modules moved out of server.rs into child files. These are
// #[cfg(test)] modules and use `super::*;` to pull in the parent scope.
#[cfg(test)]
mod tests_auth;
#[cfg(test)]
mod tests_collaboration;
#[cfg(test)]
mod tests_main;
#[cfg(test)]
mod tests_runtime;
#[cfg(test)]
#[path = "state_machine_result_tests.rs"]
mod state_machine_result_tests;
