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


type ChannelSlot = Arc<OnceLock<Arc<dyn ChannelService>>>;

type SessionChannelOutboundSlot = Arc<OnceLock<Arc<dyn SessionChannelOutboundPort>>>;


type ChannelRepos = (
    Arc<dyn ChannelBindingRepoPort>,
    Arc<dyn ConversationSessionRepoPort>,
    Arc<dyn ImParticipantRepoPort>,
    Arc<dyn HumanInputRequestRepoPort>,
);


#[cfg(test)]
mod gateway_principal_tests {
    use super::*;
    use bcs_secret_local::InMemorySecretAccess;

    fn trust_config() -> crate::config::GatewayPrincipalConfig {
        crate::config::GatewayPrincipalConfig::default()
    }

    #[test]
    fn configured_invite_token_secret_is_preserved() {
        let mut config = BcsConfig::default();
        config.invite.token_secret = Some("configured-invite-secret".to_string());

        assert_eq!(
            resolve_invite_token_secret(&config),
            b"configured-invite-secret"
        );
    }

    #[tokio::test]
    async fn all_config_secret_references_resolve_together() {
        use bcs_config_api::{OAuthSettings, ProviderSettings};
        use std::collections::BTreeMap;
        let mut config = BcsConfig::default();
        config.auth_sdk.secret_key_secret = Some("auth".into());
        config.llm.api_key_secret = Some("llm".into());
        config.bcsfuse.authorization_ref = Some("bcsfuse".into());
        config.invite.token_secret_secret = Some("invite".into());
        config.session_files.share.token_secret_secret = Some("share".into());
        let mut account = config.dingtalk_accounts.first().cloned().unwrap_or_default();
        account.client_secret_secret = Some("ding".into());
        config.dingtalk_accounts = vec![account];
        let logger = ding_logger::GroupLoggerConfig { enabled: true, client_id: "id".into(), client_secret: String::new(), client_secret_secret: Some("logger".into()), group_ids: vec!["g".into()] };
        config.group_logger = Some(logger.clone());
        config.human_notify.providers = vec![bcs_config_api::HumanNotifyProviderConfig {
            name: "dingtalk".to_string(),
            enabled: true,
            options: {
                let mut human_options = BTreeMap::new();
                human_options.insert(
                    "client_secret_secret".to_string(),
                    serde_json::Value::String("human".into()),
                );
                human_options
            },
        }];
        let mut providers = BTreeMap::new();
        providers.insert("google".into(), ProviderSettings { kind: None, client_id: "id".into(), client_secret: None, client_secret_secret: Some("oauth".into()), private_key: None, alipay_public_key: None });
        config.auth.oauth = Some(OAuthSettings { providers, ..OAuthSettings::default() });
        let access = InMemorySecretAccess::with_entries([
            ("auth", String::new(), "auth-value".into()), ("llm", String::new(), "llm-value".into()),
            ("bcsfuse", String::new(), "bcsfuse-value".into()),
            ("invite", String::new(), "invite-value".into()), ("share", String::new(), "share-value".into()),
            ("ding", String::new(), "ding-value".into()), ("logger", String::new(), "logger-value".into()),
            ("oauth", String::new(), "oauth-value".into()), ("human", String::new(), "human-value".into()),
        ]);
        resolve_config_secrets(&mut config, &access).await.unwrap();
        assert_eq!(config.auth_sdk.secret_key.as_deref(), Some("auth-value"));
        assert_eq!(config.llm.api_key.as_ref().map(|v| v.expose_secret().as_str()), Some("llm-value"));
        assert_eq!(config.bcsfuse.auth_token(), Some("bcsfuse-value"));
        assert_eq!(config.invite.token_secret.as_deref(), Some("invite-value"));
        assert_eq!(config.session_files.share.token_secret.as_deref(), Some("share-value"));
        assert_eq!(config.dingtalk_accounts[0].client_secret.as_ref().map(|v| v.expose_secret().as_str()), Some("ding-value"));
        assert_eq!(config.group_logger.as_ref().unwrap().client_secret, "logger-value");
        let dingtalk = &config.human_notify.providers[0];
        assert_eq!(
            dingtalk.options["client_secret"],
            serde_json::Value::String("human-value".into())
        );
        assert!(!dingtalk.options.contains_key("client_secret_secret"));
        assert_eq!(config.auth.oauth.as_ref().unwrap().providers["google"].client_secret.as_ref().map(|v| v.expose_secret().as_str()), Some("oauth-value"));
    }

    #[tokio::test]
    async fn token_secret_reference_resolves_and_rejects_missing_or_empty() {
        let access = InMemorySecretAccess::with_entries([("invite-key", String::new(), "resolved".to_string())]);
        let resolved = resolve_token_secret_secret(Some(" invite-key "), &access, "invite.token_secret_secret")
            .await
            .expect("secret resolves");
        assert_eq!(resolved.as_deref(), Some("resolved"));

        let missing = resolve_token_secret_secret(Some("missing"), &InMemorySecretAccess::new(), "invite.token_secret_secret")
            .await
            .expect_err("missing secret fails");
        assert!(missing.to_string().contains("invite.token_secret_secret"));

        let empty_access = InMemorySecretAccess::with_entries([("empty", String::new(), "  ".to_string())]);
        let empty = resolve_token_secret_secret(Some("empty"), &empty_access, "invite.token_secret_secret")
            .await
            .expect_err("empty secret fails");
        assert!(empty.to_string().contains("is empty"));

        assert_eq!(resolve_token_secret_secret(None, &InMemorySecretAccess::new(), "field").await.unwrap(), None);
        let access = InMemorySecretAccess::with_entries([("auth", String::new(), "auth-value".to_string())]);
        assert_eq!(resolve_secret_value(Some(" auth "), &access, "auth_sdk.secret_key_secret").await.unwrap().as_deref(), Some("auth-value"));
        assert!(resolve_secret_value(Some("missing"), &InMemorySecretAccess::new(), "llm.api_key_secret").await.is_err());
    }

    #[tokio::test]
    async fn config_secret_resolution_preserves_legacy_values_without_references() {
        use std::collections::BTreeMap;
        let mut config = BcsConfig::default();
        config.auth_sdk.secret_key = Some("legacy-auth".into());
        config.llm.api_key = Some(Secret::new("legacy-llm".into()));
        config.invite.token_secret = Some("legacy-invite".into());
        config.session_files.share.token_secret = Some("legacy-share".into());
        config.human_notify.providers = vec![bcs_config_api::HumanNotifyProviderConfig {
            name: "dingtalk".to_string(),
            enabled: true,
            options: {
                let mut options = BTreeMap::new();
                options.insert("client_secret".into(), serde_json::Value::String("legacy-human".into()));
                options
            },
        }];

        resolve_config_secrets(&mut config, &InMemorySecretAccess::new()).await.unwrap();

        assert_eq!(config.auth_sdk.secret_key.as_deref(), Some("legacy-auth"));
        assert_eq!(config.llm.api_key.as_ref().map(|v| v.expose_secret().as_str()), Some("legacy-llm"));
        assert_eq!(config.invite.token_secret.as_deref(), Some("legacy-invite"));
        assert_eq!(config.session_files.share.token_secret.as_deref(), Some("legacy-share"));
        assert_eq!(config.human_notify.providers[0].options["client_secret"], serde_json::Value::String("legacy-human".into()));
    }

    #[tokio::test]
    async fn generic_secret_options_resolve_and_literal_values_win() {
        use std::collections::BTreeMap;
        let mut config = BcsConfig::default();
        let mut signing_key_options = BTreeMap::new();
        signing_key_options.insert(
            "signing_key_secret".to_string(),
            serde_json::Value::String("principal-key".into()),
        );
        let mut literal_options = BTreeMap::new();
        literal_options.insert(
            "client_secret".to_string(),
            serde_json::Value::String("literal".into()),
        );
        literal_options.insert(
            "client_secret_secret".to_string(),
            serde_json::Value::String("ding".into()),
        );
        config.human_notify.providers = vec![
            bcs_config_api::HumanNotifyProviderConfig {
                name: "work_order".to_string(),
                enabled: true,
                options: signing_key_options,
            },
            bcs_config_api::HumanNotifyProviderConfig {
                name: "dingtalk".to_string(),
                enabled: true,
                options: literal_options,
            },
        ];
        let access = InMemorySecretAccess::with_entries([
            ("principal-key", String::new(), "principal-key-value".into()),
            ("ding", String::new(), "ding-value".into()),
        ]);

        resolve_config_secrets(&mut config, &access).await.unwrap();

        let work_order = &config.human_notify.providers[0];
        assert_eq!(
            work_order.options["signing_key"],
            serde_json::Value::String("principal-key-value".into())
        );
        assert!(!work_order.options.contains_key("signing_key_secret"));
        let dingtalk = &config.human_notify.providers[1];
        assert_eq!(
            dingtalk.options["client_secret"],
            serde_json::Value::String("literal".into()),
            "literal value wins over the reference"
        );
        assert!(
            dingtalk.options.contains_key("client_secret_secret"),
            "unresolved reference is kept untouched"
        );
    }

    #[test]
    fn legacy_token_secret_is_preserved_without_secret_reference() {
        let mut config = BcsConfig::default();
        config.invite.token_secret = Some("legacy-invite".to_string());
        assert_eq!(resolve_invite_token_secret(&config), b"legacy-invite");
    }

    #[test]
    fn gateway_principal_material_must_be_explicit_and_non_blank() {
        for material in [None, Some(""), Some("   ")] {
            assert!(matches!(
                gateway_principal_signing_key(material),
                Err(crate::BcsError::InvalidConfig(message))
                    if message.contains("Gateway Principal signing key")
            ));
        }
    }

    #[test]
    fn explicit_gateway_principal_material_is_accepted() {
        assert_eq!(
            gateway_principal_signing_key(Some("explicit-test-key")).expect("explicit material"),
            "explicit-test-key"
        );
    }

    #[tokio::test]
    async fn gateway_principal_signing_key_can_come_from_secret_access() {
        let mut config = trust_config();
        config.signing_key_secret =
            Some("other_manual_teamclawgw_principal_signing_key".to_string());
        let access: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::with_entries([(
                "other_manual_teamclawgw_principal_signing_key",
                "teamclawgw".to_string(),
                "mist-test-signing-key".to_string(),
            )]));

        let result = build_gateway_principal_verifier_from_secret_access(&config, access).await;

        if let Err(error) = result {
            let message = error.to_string();
            assert!(!message.contains("mist-test-signing-key"));
            panic!("Mist-backed Gateway Principal signing key must be accepted: {message}");
        }
    }

    #[test]
    fn blank_gateway_principal_trust_or_lookup_config_is_rejected() {
        for field in ["issuers", "audience", "key_id", "signing_key_env"] {
            let mut config = trust_config();
            match field {
                "issuers" => config.issuers = vec![" ".to_string()],
                "audience" => config.audience = " ".to_string(),
                "key_id" => config.key_id = " ".to_string(),
                "signing_key_env" => config.signing_key_env = " ".to_string(),
                _ => unreachable!("known trust config field"),
            }
            assert!(matches!(
                build_gateway_principal_verifier(&config, Some("explicit-test-key")),
                Err(crate::BcsError::InvalidConfig(_))
            ));
        }
        // Empty issuer list and duplicate issuers are also rejected.
        let mut empty = trust_config();
        empty.issuers = vec![];
        assert!(matches!(
            build_gateway_principal_verifier(&empty, Some("explicit-test-key")),
            Err(crate::BcsError::InvalidConfig(_))
        ));
        let mut duplicate = trust_config();
        duplicate.issuers = vec!["gateway".to_string(), "gateway".to_string()];
        assert!(matches!(
            build_gateway_principal_verifier(&duplicate, Some("explicit-test-key")),
            Err(crate::BcsError::InvalidConfig(_))
        ));
    }

    #[tokio::test]
    async fn group_session_websocket_signing_key_is_required_and_non_empty() {
        let missing: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::new());
        let config = GroupSessionWsConfig::default();
        let missing_error = match build_group_session_token_port(&config, missing).await {
            Ok(_) => panic!("missing group-session WebSocket key must fail"),
            Err(error) => error,
        };
        assert!(matches!(missing_error, crate::BcsError::InvalidConfig(_)));
        assert!(missing_error.to_string().contains(
            "group_session_ws.signing_key_secret 'bcn-group-session-ws-jwt' is required"
        ));

        let empty: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::with_entries([(
                config.signing_key_secret.clone(),
                String::new(),
                "   ".to_string(),
            )]));
        let empty_error = match build_group_session_token_port(&config, empty).await {
            Ok(_) => panic!("empty group-session WebSocket key must fail"),
            Err(error) => error,
        };
        assert!(matches!(empty_error, crate::BcsError::InvalidConfig(_)));
        assert!(!empty_error.to_string().contains("   "));
    }

    #[tokio::test]
    async fn explicit_group_session_websocket_signing_key_is_accepted() {
        let secret_material = "test-only-group-session-key-at-least-32-bytes";
        let config = GroupSessionWsConfig {
            signing_key_secret: "other_manual_teamclawgw_principal_signing_key".to_string(),
        };
        let access: Arc<dyn bcs_service_api::port::SecretAccessPort> =
            Arc::new(InMemorySecretAccess::with_entries([(
                config.signing_key_secret.clone(),
                String::new(),
                secret_material.to_string(),
            )]));

        let result = build_group_session_token_port(&config, access).await;

        if let Err(error) = result {
            let message = error.to_string();
            assert!(!message.contains(secret_material));
            panic!("explicit group-session WebSocket key must be accepted: {message}");
        }
    }
}


#[cfg(test)]
mod candidate_search_wiring_tests {
    use super::*;
    use bcs_test_support::{NoopBotRegistryCoreService, NoopFriendCoreService};

    #[test]
    fn bindings_share_one_core_between_legacy_and_v1() {
        let bindings = build_candidate_search_bindings(
            &BcsConfig::default(),
            Arc::new(NoopBotRegistryCoreService),
            Arc::new(NoopFriendCoreService),
            None,
        );

        assert!(Arc::ptr_eq(&bindings.legacy, &bindings.openapi_v1));
    }
}


#[cfg(test)]
mod judge_provider_tests {
    use super::*;
    use crate::plugins::LlmProviderFactory;
    use bcs_llm_api::{LlmChatCompletionRequest, LlmChatCompletionResponse, LlmError};
    use bcs_service_api::{JudgeArtifact, JudgeRequest};
    use serde_json::json;

    struct RecordingLlm {
        requests: Mutex<Vec<LlmChatCompletionRequest>>,
    }

    #[async_trait::async_trait]
    impl LlmChatCompletionPort for RecordingLlm {
        async fn complete(
            &self,
            request: LlmChatCompletionRequest,
        ) -> std::result::Result<LlmChatCompletionResponse, LlmError> {
            self.requests.lock().await.push(request);
            Ok(LlmChatCompletionResponse {
                content: json!({
                    "outcome": "approved",
                    "reason": "ok",
                    "confidence": 0.9,
                    "checked_criteria": [],
                    "retry_instruction": "",
                })
                .to_string(),
                raw: json!({}),
            })
        }
    }

    fn test_llm_factory(_config: BcsConfig) -> crate::Result<Arc<dyn LlmChatCompletionPort>> {
        Ok(Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        }))
    }

    inventory::submit! {
        LlmProviderFactory {
            name: "test-internal-llm",
            build: test_llm_factory,
        }
    }

    fn judge_request() -> JudgeRequest {
        JudgeRequest {
            run_id: "run-1".to_string(),
            node_id: "judge".to_string(),
            attempt: 1,
            judge_type: "llm".to_string(),
            criteria: vec!["must pass".to_string()],
            allowed_outcomes: vec!["approved".to_string(), "rejected".to_string()],
            input: json!({"question": "ready?"}),
            upstream_outputs: vec![JudgeArtifact {
                node_id: "work".to_string(),
                text: "candidate output".to_string(),
            }],
            artifact_text: "candidate output".to_string(),
        }
    }

    #[test]
    fn judge_llm_provider_selection_uses_public_provider_types() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::OpenAiCompatible;

        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::OpenAiCompatible
        );

        config.llm.provider_type = LlmProviderType::Anthropic;
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::Anthropic
        );
    }

    #[test]
    fn anthropic_llm_provider_requires_api_key() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = None;

        let error = match create_judge_evaluator(&config, &BcsServerExtensions::default()) {
            Ok(_) => panic!("anthropic provider without an API key should fail"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("anthropic api_key is required"));
    }

    #[test]
    fn anthropic_llm_provider_builds_judge_evaluator() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Anthropic;
        config.llm.base_url = "https://api.anthropic.com/v1".to_string();
        config.llm.api_key_env = None;
        config.llm.api_key = Some(Secret::new("anthropic-key".to_string()));

        create_judge_evaluator(&config, &BcsServerExtensions::default())
            .expect("valid anthropic provider should build a judge evaluator");
    }

    #[tokio::test]
    async fn none_llm_without_injection_uses_noop_judge() {
        let config = BcsConfig::default();
        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");

        let error = match evaluator.judge(judge_request()).await {
            Ok(_) => panic!("noop judge should reject LLM judge requests"),
            Err(error) => error,
        };

        assert!(error.to_string().contains("requires an enabled LLM"));
    }

    #[tokio::test]
    async fn injected_llm_provider_is_used_when_present() {
        let llm = Arc::new(RecordingLlm {
            requests: Mutex::new(Vec::new()),
        });
        let llm_provider: Arc<dyn LlmChatCompletionPort> = llm.clone();
        let mut config = BcsConfig::default();
        config.llm.model = "custom-judge-model".to_string();
        let extensions = BcsServerExtensions {
            llm_provider: Some(llm_provider),
            ..BcsServerExtensions::default()
        };

        let evaluator = create_judge_evaluator(&config, &extensions).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
        let requests = llm.requests.lock().await;
        assert_eq!(requests.len(), 1);
        assert_eq!(requests[0].model, "custom-judge-model");
    }

    #[tokio::test]
    async fn registered_llm_provider_is_selected_by_type() {
        let mut config = BcsConfig::default();
        config.llm.provider_type = LlmProviderType::Other("test-internal-llm".to_string());
        config.llm.model = "registered-model".to_string();

        let evaluator =
            create_judge_evaluator(&config, &BcsServerExtensions::default()).expect("evaluator");
        let decision = evaluator
            .judge(judge_request())
            .await
            .expect("judge decision");

        assert_eq!(decision.outcome, "approved");
    }
}


#[cfg(test)]
#[path = "server_tests/mod.rs"]
mod tests;


#[cfg(test)]
#[path = "state_machine_result_tests.rs"]
mod state_machine_result_tests;



#[path = "server/is_debug_enabled.rs"]
mod is_debug_enabled;
use is_debug_enabled::*;
#[path = "server/metrics_handler.rs"]
mod metrics_handler;
use metrics_handler::*;
pub use metrics_handler::{BcsServerState, BcsServerExtensions};
#[path = "server/build_openapi_v1_state.rs"]
mod build_openapi_v1_state;
use build_openapi_v1_state::*;
pub use build_openapi_v1_state::{BcsServer};
#[path = "server/create_configured_leader_election.rs"]
mod create_configured_leader_election;
use create_configured_leader_election::*;
#[path = "server/new_with_outbound_url_guards.rs"]
mod new_with_outbound_url_guards;
#[path = "server/new_with_infrastructure.rs"]
mod new_with_infrastructure;
#[path = "server/build_full_oauth_route_state.rs"]
mod build_full_oauth_route_state;
use build_full_oauth_route_state::*;
#[path = "server/crate_bcserror.rs"]
mod crate_bcserror;
pub use crate_bcserror::{resolve_config_secrets};

#[cfg(test)]
pub(crate) use build_openapi_v1_state::gateway_principal_verifier_for_tests;
