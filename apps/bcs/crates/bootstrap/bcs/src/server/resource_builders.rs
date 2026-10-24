//! Extracted verbatim from `server.rs`: storage, auth, secret, and OpenAPI state resource builders.
use super::*;

#[derive(Clone)]
pub(super) struct ProviderRepoBundle {
    pub(super) memory_store: Option<Arc<MemoryProviderStore>>,
    pub(super) bot_providers: Arc<dyn bcs_service_api::port::repo::bot_provider::BotProviderRepoPort>,
    pub(super) provider_repo: Arc<dyn ProviderRepoPort>,
    pub(super) provider_credentials: Arc<dyn ProviderCredentialRepoPort>,
    pub(super) provider_bindings: Arc<dyn ProviderBotBindingRepoPort>,
    organization_candidates: Arc<dyn bcs_service_api::OrganizationCandidateReadPort>,
}

pub(super) fn memory_provider_repos(bots: std::sync::Arc<bcs_bot_store::MemoryBotRepo>, source: bcs_domain::bot_provider::DownlinkDetectionSource) -> ProviderRepoBundle {
    let store = Arc::new(MemoryProviderStore::new());
    let bot_providers = Arc::new(bcs_bot_store::provider::MemoryBotProviderStore::new(bots, store.clone()));
    let provider_bindings = Arc::new(bcs_bot_store::provider::ProviderBindingProjection::new(store.clone(), bot_providers.clone(), source));
    ProviderRepoBundle {
        memory_store: Some(store.clone()),
        bot_providers,
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings,
        organization_candidates: store,
    }
}

pub(super) fn db_sql_flavor(db_kind: &DbPluginKind) -> DbSqlFlavor {
    match db_kind {
        DbPluginKind::LocalSqlite => DbSqlFlavor::Sqlite,
        DbPluginKind::Mysql => DbSqlFlavor::Mysql,
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no SQL flavor wiring",
                provider
            )
        }
    }
}

pub(super) fn db_provider_repos(
    db_plugin: Arc<dyn bcs_db_api::DbPlugin>,
    db_kind: &DbPluginKind,
    source: bcs_domain::bot_provider::DownlinkDetectionSource,
) -> ProviderRepoBundle {
    let store = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbProviderStore::sqlite(db_plugin)),
        DbPluginKind::Mysql => Arc::new(DbProviderStore::mysql(db_plugin)),
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no provider store wiring",
                provider
            )
        }
    };
    let provider_bindings = Arc::new(bcs_bot_store::provider::ProviderBindingProjection::new(store.clone(), store.clone(), source));
    ProviderRepoBundle {
        memory_store: None,
        bot_providers: store.clone(),
        provider_repo: store.clone(),
        provider_credentials: store.clone(),
        provider_bindings,
        organization_candidates: store,
    }
}

pub(super) fn memory_organization_services(
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_repo: Arc<dyn OrganizationRepoPort> = Arc::new(MemoryOrganizationRepo::new());
    build_organization_services(
        organization_repo,
        provider_repos,
        provider_core,
        bot_registry,
    )
}

pub(super) fn db_organization_services(
    db_plugin: Arc<dyn bcs_db_api::DbPlugin>,
    db_kind: &DbPluginKind,
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_repo: Arc<dyn OrganizationRepoPort> = match db_kind {
        DbPluginKind::LocalSqlite => Arc::new(DbOrganizationStore::sqlite(db_plugin.clone())),
        DbPluginKind::Mysql => Arc::new(DbOrganizationStore::mysql(db_plugin.clone())),
        DbPluginKind::External(provider) => {
            panic!(
                "external database plugin '{}' has no organization store wiring",
                provider
            )
        }
    };
    build_organization_services(
        organization_repo,
        provider_repos,
        provider_core,
        bot_registry,
    )
}

fn build_organization_services(
    organization_repo: Arc<dyn OrganizationRepoPort>,
    provider_repos: &ProviderRepoBundle,
    provider_core: Arc<dyn ProviderCoreService>,
    bot_registry: Arc<dyn BotRegistryCoreService>,
) -> (
    Arc<dyn OrganizationCoreService>,
    Arc<dyn OrganizationManagementService>,
) {
    let organization_core: Arc<dyn OrganizationCoreService> = Arc::new(OrganizationCore::new(
        crate::env::resolve_env(),
        organization_repo,
        provider_repos.provider_repo.clone(),
        provider_repos.provider_bindings.clone(),
        provider_repos.organization_candidates.clone(),
        bot_registry,
    ));
    let organization_management: Arc<dyn OrganizationManagementService> = Arc::new(
        OrganizationManagement::new(provider_core, organization_core.clone()),
    );
    (organization_core, organization_management)
}

pub(super) fn build_provider_services_with_webhook_url_guard(
    repos: &ProviderRepoBundle,
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn bcs_service_api::RelationCoreService>,
    user_directory: Option<Arc<dyn UserDirectoryPlugin>>,
    webhook_url_guard: OutboundUrlGuard,
    control_plane: Arc<dyn BotControlPlaneCoreService>,
    channel_binding_cleanup: Arc<dyn ChannelBindingCleanupPort>,
    bot_catalog_cleanup: Arc<dyn BotCatalogCleanupPort>,
) -> (
    Arc<dyn ProviderCoreService>,
    Arc<dyn ProviderBotCoreService>,
    Arc<dyn ProviderManagementService>,
) {
    let provider_core_impl = Arc::new(ProviderCore::new_with_webhook_url_guard(
        repos.provider_repo.clone(),
        repos.provider_credentials.clone(),
        repos.provider_bindings.clone(),
        registry.clone(),
        webhook_url_guard,
    ).with_bot_provider_repo(repos.bot_providers.clone()));
    let provider_core: Arc<dyn ProviderCoreService> = provider_core_impl.clone();
    let provider_bot_core: Arc<dyn ProviderBotCoreService> = provider_core_impl;
    let mut provider_management = ProviderManagement::new(
        provider_core.clone(),
        provider_bot_core.clone(),
        registry,
        relation,
    )
    .with_channel_binding_cleanup(channel_binding_cleanup)
    .with_bot_catalog_cleanup(bot_catalog_cleanup);
    if let Some(user_directory) = user_directory {
        provider_management = provider_management.with_user_directory(user_directory);
    }
    provider_management = provider_management.with_control_plane(control_plane);
    let provider_management: Arc<dyn ProviderManagementService> = Arc::new(provider_management);
    (provider_core, provider_bot_core, provider_management)
}

pub(super) fn create_user_directory_plugin(
    config: &BcsConfig,
) -> crate::Result<Option<Arc<dyn UserDirectoryPlugin>>> {
    let directory = &config.user_directory;
    if !directory.enabled {
        return Ok(None);
    }

    let provider = directory
        .provider
        .as_deref()
        .map(str::trim)
        .filter(|provider| !provider.is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(
                "user_directory.provider is required when user_directory.enabled = true"
                    .to_string(),
            )
        })?;

    let provider_config = directory
        .providers
        .get(provider)
        .cloned()
        .unwrap_or_default();
    build_registered_user_directory(config, provider, provider_config)?
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig(format!(
                "user_directory provider '{provider}' is not available in this binary"
            ))
        })
        .map(|registration| Some(registration.plugin))
}

pub(super) fn create_provider_stream_gray_list(config: &BcsConfig) -> Arc<ProviderStreamGrayList> {
    let entries = config.provider_stream_gray_created_by.clone();
    if config.provider_stream_gray_enabled {
        Arc::new(ProviderStreamGrayList::new(entries))
    } else {
        Arc::new(ProviderStreamGrayList::new_disabled(entries))
    }
}

pub(super) fn outbound_url_guard_from_config(config: &BcsConfig) -> OutboundUrlGuard {
    let policy = &config.security.outbound_url;
    OutboundUrlGuard::new(policy.block_private_networks, policy.allow_loopback)
}

fn gateway_principal_signing_key(material: Option<&str>) -> crate::Result<&str> {
    material
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| {
            crate::BcsError::InvalidConfig("Gateway Principal signing key is required".to_string())
        })
}

fn build_gateway_principal_verifier(
    config: &GatewayPrincipalConfig,
    material: Option<&str>,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    config.validate().map_err(crate::BcsError::InvalidConfig)?;
    let signing_key = gateway_principal_signing_key(material)?;
    let trust = GatewayPrincipalTrust::new(
        config.issuers.clone(),
        config.audience.clone(),
        config.key_id.clone(),
    )
    .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?;
    let verifier = GatewayPrincipalTokenVerifier::new(signing_key.as_bytes(), trust)
        .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?;
    Ok(Arc::new(verifier))
}

pub(super) fn build_gateway_principal_verifier_from_process(
    config: &GatewayPrincipalConfig,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    let material = std::env::var(&config.signing_key_env).ok();
    build_gateway_principal_verifier(config, material.as_deref())
}

async fn resolve_gateway_principal_material(
    config: &GatewayPrincipalConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Option<String>> {
    let secret_name = config
        .signing_key_secret
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty());

    if let Some(secret_name) = secret_name {
        let record = secret_access.get_secret(secret_name).await.map_err(|_| {
            crate::BcsError::InvalidConfig(format!(
                "Gateway Principal signing key secret '{secret_name}' is required"
            ))
        })?;
        return Ok(Some(record.value));
    }

    Ok(std::env::var(&config.signing_key_env).ok())
}

pub(super) async fn build_gateway_principal_verifier_from_secret_access(
    config: &GatewayPrincipalConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn PrincipalVerifier>> {
    config.validate().map_err(crate::BcsError::InvalidConfig)?;
    let material = resolve_gateway_principal_material(config, secret_access).await?;
    build_gateway_principal_verifier(config, material.as_deref())
}

/// Build the principal re-signer that friend-auth-sync hands the backend a
/// `bcs`-issued credential with. Same trust the openapi admission gate runs
/// under — a principal the re-signer verifies is one BCN admits elsewhere —
/// and the same key resolution the verifier mandates at boot.
pub(super) async fn build_gateway_principal_resigner(
    config: &GatewayPrincipalConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<GatewayPrincipalReSigner> {
    config.validate().map_err(crate::BcsError::InvalidConfig)?;
    let trust = GatewayPrincipalTrust::new(
        config.issuers.clone(),
        config.audience.clone(),
        config.key_id.clone(),
    )
    .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))?;
    let material = resolve_gateway_principal_material(config, secret_access).await?;
    let signing_key = gateway_principal_signing_key(material.as_deref())?;
    GatewayPrincipalReSigner::new(signing_key.as_bytes(), trust)
        .map_err(|error| crate::BcsError::InvalidConfig(error.to_string()))
}

const GROUP_SESSION_WS_TEST_SIGNING_KEY: &str = "test-only-group-session-key-at-least-32-bytes";

pub(super) fn group_session_test_secret_access(config: &BcsConfig) -> Arc<dyn SecretAccessPort> {
    Arc::new(InMemorySecretAccess::with_entries([(
        config
            .group_session_ws
            .signing_key_secret
            .trim()
            .to_string(),
        String::new(),
        GROUP_SESSION_WS_TEST_SIGNING_KEY.to_string(),
    )]))
}

pub(super) fn build_secret_access_blocking(config: &BcsConfig) -> crate::Result<Arc<dyn SecretAccessPort>> {
    std::thread::scope(|scope| {
        scope
            .spawn(|| {
                tokio::runtime::Runtime::new()
                    .expect("temp runtime for secret provider build")
                    .block_on(crate::http_adapter::build_secret_access(config))
            })
            .join()
            .expect("secret provider build thread panicked")
    })
}

async fn build_group_session_token_port(
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
) -> crate::Result<Arc<dyn GroupSessionTokenPort>> {
    let secret_name = config.signing_key_secret.trim();
    let secret = secret_access.get_secret(secret_name).await.map_err(|_| {
        crate::BcsError::InvalidConfig(format!(
            "group_session_ws.signing_key_secret '{secret_name}' is required"
        ))
    })?;
    let tokens = GroupSessionJwtService::new(&secret.value).map_err(|_| {
        crate::BcsError::InvalidConfig(format!(
            "group_session_ws.signing_key_secret '{secret_name}' must resolve to non-empty material"
        ))
    })?;
    Ok(Arc::new(tokens))
}

pub(super) async fn build_group_session_connection_service(
    sessions: Arc<dyn bcs_service_api::application::v1::SessionService>,
    config: &GroupSessionWsConfig,
    secret_access: Arc<dyn SecretAccessPort>,
    session_authorization_core: Arc<
        dyn bcs_service_api::core::SessionAuthorizationCoreService,
    >,
    bot_authority_hook: Option<Arc<dyn bcs_service_api::port::bot_authority::BotAuthorityPort>>,
) -> crate::Result<Arc<dyn GroupSessionConnectionService>> {
    let tokens = build_group_session_token_port(config, secret_access).await?;
    Ok(Arc::new(GroupSessionConnectionServiceImpl::new(
        sessions,
        tokens,
        session_authorization_core,
        bot_authority_hook,
    )))
}

pub(super) fn build_invite_code_service(
    config: &BcsConfig,
    db_plugin: Option<Arc<dyn bcs_db_api::DbPlugin>>,
    db_kind: Option<&DbPluginKind>,
    invite_token_secret: Vec<u8>,
) -> Arc<dyn bcs_service_api::application::v1::InviteCodeService> {
    let repo: Arc<dyn bcs_service_api::port::repo::InviteCodeRepoPort> = match (db_plugin, db_kind) {
        (Some(db_plugin), Some(db_kind)) => match db_kind {
            DbPluginKind::LocalSqlite => Arc::new(DbInviteCodeStore::sqlite(db_plugin)),
            DbPluginKind::Mysql => Arc::new(DbInviteCodeStore::mysql(db_plugin)),
            DbPluginKind::External(provider) => {
                panic!(
                    "external database plugin '{}' has no invite-code store wiring",
                    provider
                )
            }
        },
        _ => Arc::new(MemoryInviteCodeRepo::with_data_dir(config.bots_base_dir.clone())),
    };
    Arc::new(
        InviteCodeServiceImpl::new(
            repo,
            invite_token_secret,
            config.invite.public_claim_max_count,
        )
        // 与 invite-code gate 中间件同源接线：开关关闭时 /me 合成“已绑定”，
        // 客户端提示行为完全由该后端开关控制。
        .with_gate_enabled(config.invite.invite_code_gate_enabled),
    )
}

async fn resolve_secret_value(
    name: Option<&str>,
    access: &dyn SecretAccessPort,
    field: &str,
) -> crate::Result<Option<String>> {
    let Some(name) = name.map(str::trim).filter(|v| !v.is_empty()) else { return Ok(None); };
    let record = access.get_secret(name).await.map_err(|e| crate::BcsError::InvalidConfig(format!("{field} '{name}' unavailable: {e}")))?;
    if record.value.trim().is_empty() { return Err(crate::BcsError::InvalidConfig(format!("{field} '{name}' is empty"))); }
    Ok(Some(record.value))
}

async fn resolve_token_secret_secret(
    secret_key: Option<&str>,
    secret_access: &dyn SecretAccessPort,
    field: &str,
) -> crate::Result<Option<String>> {
    resolve_secret_value(secret_key, secret_access, field).await
}

pub(super) fn resolve_invite_token_secret(config: &BcsConfig) -> Vec<u8> {
    config
        .invite
        .token_secret
        .as_deref()
        .map(str::as_bytes)
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| {
            tracing::warn!(
                "invite.token_secret not configured — generating random secret (tokens will not survive restart)"
            );
            (0..32).map(|_| fastrand::u8(..)).collect()
        })
}

pub(super) struct CandidateSearchBindings {
    pub(super) worker_profiles: Arc<dyn WorkerProfileCoreService>,
    pub(super) legacy: Arc<dyn BotCandidateSearchCoreService>,
    pub(super) openapi_v1: Arc<dyn BotCandidateSearchCoreService>,
}

pub(super) fn build_candidate_search_bindings(
    config: &BcsConfig,
    registry: Arc<dyn BotRegistryCoreService>,
    friends: Arc<dyn FriendCoreService>,
    fuse_client: Option<Arc<FuseClient>>,
) -> CandidateSearchBindings {
    let worker_profiles: Arc<dyn WorkerProfileCoreService> = match fuse_client {
        Some(client) => Arc::new(FuseWorkerProfileService::new(client)),
        None => Arc::new(EmptyWorkerProfileCoreService),
    };
    let candidate_search: Arc<dyn BotCandidateSearchCoreService> =
        Arc::new(BotCandidateSearchCore::new(
            registry,
            friends,
            worker_profiles.clone(),
            config.bcsfuse.recommend_min_score,
        ));

    CandidateSearchBindings {
        worker_profiles,
        legacy: candidate_search.clone(),
        openapi_v1: candidate_search,
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn build_openapi_v1_state(
    config: &BcsConfig,
    invite_token_secret: Vec<u8>,
    control_plane_repo: Arc<dyn BotControlPlaneRepoPort>,
    provider_repos: &ProviderRepoBundle,
    registry: Arc<dyn BotRegistryCoreService>,
    groups: Arc<dyn GroupCoreService>,
    friends: Arc<dyn FriendCoreService>,
    candidate_search: Arc<dyn BotCandidateSearchCoreService>,
    friend_requests: Arc<dyn FriendRequestCoreService>,
    relation: Arc<dyn RelationCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    session_launch: Arc<dyn bcs_service_api::SessionLaunchService>,
    group_management: Arc<dyn GroupManagementService>,
    collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService>,
    judge_available: bool,
    session_repo: Arc<dyn SessionRepoPort>,
    group_message_history: Arc<dyn GroupMessageHistoryService>,
    session_files: Arc<dyn bcs_service_api::application::session_files::SessionFileService>,
    system_message: Arc<dyn SystemMessageService>,
    collaboration_templates: Arc<dyn CollaborationTemplateService>,
    invite_code_service: Arc<dyn bcs_service_api::application::v1::InviteCodeService>,
    invite_code_gate_enabled: bool,
    public_invite_code_claim_enabled: bool,
    principal_verifier: Arc<dyn PrincipalVerifier>,
    connect_service: Arc<dyn bcs_service_api::application::ConnectService>,
    participant_view_bindings: Arc<dyn bcs_service_api::port::ParticipantViewBindingPort>,
    event_subscription_service: Arc<dyn bcs_service_api::application::v1::EventSubscriptionService>,
    group_event_subscription_provisioner: Arc<
        dyn bcs_service_api::application::v1::GroupEventSubscriptionProvisioner,
    >,
    // The composed authority facade (spec §5/§6); the
    // composition root builds it from the env-bound
    // authority Core.
    bot_authority_service: Arc<
        dyn bcs_service_api::application::v1::bot_authority::BotAuthorityService,
    >,
    // The narrow authority hook consumed by the
    // business facades (bot-authority plan Task 6.3).
    bot_authority_hook: Arc<
        dyn bcs_service_api::port::bot_authority::BotAuthorityPort,
    >,
    // The authority Core (the current-ownership view
    // for the Group driver-owner display projection).
    bot_authority_core: Arc<
        dyn bcs_service_api::core::bot_authority::BotAuthorityCoreService,
    >,
) -> (
    ApiState,
    Arc<dyn bcs_service_api::InternalBotAttributesService>,
) {
    let relation_env = crate::env::resolve_env();
    // AgentPass: the verifier the OpenAPI V1 state hands out
    // is the stateful one (registry-backed), shared onward as
    // `openapi_v1.principal_verifier` — downstream state must
    // never re-create a bare verifier around it.
    let principal_verifier = Arc::new(crate::principal_state::StatefulPrincipalVerifier {
        inner: principal_verifier, registry: registry.clone(),
    });
    let provider_registration = Arc::new(bcs_bot::core::provider_registration::ProviderRegistrationCore::new(
        provider_repos.provider_repo.clone(),
        provider_repos.provider_credentials.clone(),
        provider_repos.provider_bindings.clone(),
        provider_repos.bot_providers.clone(),
        registry.clone(), relation.clone(), relation_env.clone(),
        config.openapi_v1.registration_self_service_provider_ids.clone(),
        outbound_url_guard_from_config(config),
    ));
    let control_plane = Arc::new(BotControlPlaneCore::new(
        control_plane_repo,
        provider_repos.provider_repo.clone(),
        provider_repos.provider_bindings.clone(),
    ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
    let bot_service = Arc::new(
        BotServiceImpl::new(
            control_plane.clone(),
            registry.clone(),
            friends.clone(),
            connect_service.clone(),
            candidate_search,
            BotServiceConfig {
                env: relation_env.clone(),
            },
        )
        .with_bot_authority_hook(bot_authority_hook.clone())
        .with_legacy_created_by_fallback(
            config.bot_authority.legacy_created_by_fallback,
        ),
    );
    let internal_bot_attributes_service = Arc::new(InternalBotAttributesServiceImpl::new(
        control_plane,
        BotServiceConfig {
            env: relation_env.clone(),
        },
    ));
    let mut group_service = GroupServiceImpl::new(
        groups.clone(),
        registry.clone(),
        friends.clone(),
        sessions.clone(),
        group_management,
    )
    .with_bot_authority_hook(bot_authority_hook.clone())
    .with_bot_authority_core(bot_authority_core.clone())
    .with_participant_view_bindings(participant_view_bindings.clone())
    .with_collaboration_runtime(collaboration_runtime.clone());
    if config.eventing.enabled {
        group_service =
            group_service.with_event_subscription_provisioner(group_event_subscription_provisioner);
    }
    let group_service = Arc::new(group_service);
    // The strict Session authorization-fact Core the
    // V1 Session facade consumes for its resource-role
    // legs (spec §8.3): the same repo the runtime
    // writes through.
    let session_authorization_core: Arc<
        dyn bcs_service_api::core::SessionAuthorizationCoreService,
    > = Arc::new(bcs_session::SessionAuthorizationCoreImpl::new(
        session_repo.clone(),
    ));
    let session_service = Arc::new(
        SessionServiceImpl::new(
            session_launch,
            sessions.clone(),
            groups.clone(),
            registry.clone(),
            friends.clone(),
            session_repo,
            group_message_history,
            collaboration_runtime.clone(),
            system_message.clone(),
        )
        .with_bot_authority_hook(bot_authority_hook.clone())
        .with_participant_view_bindings(participant_view_bindings)
        .with_session_authorization_core(session_authorization_core.clone()),
    );
    let session_file_url_projector = SessionFileUrlProjector::new(
        config
            .openapi_v1
            .validated_internal_collaboration_base_url()
            .expect("OpenAPI V1 internal collaboration URL was validated at config load"),
    )
    .expect("validated OpenAPI V1 session-file URL projector");
    let session_file_service = Arc::new(
        SessionFileApplicationServiceImpl::new(
            session_files,
            sessions.clone(),
            groups.clone(),
            registry.clone(),
            system_message.clone(),
            Arc::new(session_file_url_projector.clone()),
        )
        .with_bot_authority_hook(bot_authority_hook.clone()),
    );
    let invitation_groups = groups.clone();
    let invitation_sessions = sessions.clone();
    let invite: Arc<dyn InviteService> =
        Arc::new(bcs_group::application::invite::InviteServiceImpl {
            registry: registry.clone(),
            group: groups,
            session: sessions,
            system_message,
            token_secret: invite_token_secret.clone(),
            default_ttl_seconds: config.invite.default_ttl_seconds,
            base_url: config.invite.base_url.clone(),
            group_link_url: config.invite.group_link_url.clone(),
            session_link_url: config.invite.session_link_url.clone(),
            bot_authority: Some(bot_authority_hook.clone()),
        });
    let invitation_service = Arc::new(
        InvitationFriendshipServiceImpl::new(
            friends,
            friend_requests,
            invitation_groups,
            invitation_sessions,
            registry.clone(),
            invite,
            invite_token_secret.clone(),
            InvitationFriendshipServiceConfig {
                default_ttl_seconds: config.invite.default_ttl_seconds,
            },
        )
        .with_bot_authority_hook(bot_authority_hook.clone())
        .with_friend_connection_service(connect_service),
    );
    // The trusted chain: the SAME registry core instance
    // the runtime uses; the app never touches a repo. The
    // legacy v1/v2 token surface maps through this facade.
    let register_service: Arc<dyn bcs_service_api::application::v1::RegisterService> =
        Arc::new(bcs_app_register::RegisterServiceImpl::new(
            invite_token_secret.clone(),
        )
        .with_registry(registry.clone())
        .with_provider_registration(provider_registration));
    let collaboration_template_service: Arc<
        dyn bcs_service_api::application::v1::CollaborationTemplateService,
    > = Arc::new(V1CollaborationTemplateServiceImpl::new(
        collaboration_templates,
    ));
    let collaboration_definition_service: Arc<
        dyn bcs_service_api::application::v1::CollaborationDefinitionService,
    > = Arc::new(V1CollaborationDefinitionServiceImpl::new(
        collaboration_runtime.clone(),
        judge_available,
    ));

    (
        ApiState::new(
            group_service,
            session_service.clone(),
            session_service,
            invitation_service.clone(),
            register_service,
            invitation_service.clone(),
            principal_verifier,
        )
        .with_bot_service(bot_service)
        .with_bot_authority_service(bot_authority_service)
        .with_invite_code_service(invite_code_service)
        .with_invite_code_gate_enabled(invite_code_gate_enabled)
        .with_public_invite_code_claim_enabled(public_invite_code_claim_enabled)
        .with_friend_connection_service(invitation_service)
        .with_session_file_service(session_file_service, session_file_url_projector)
        .with_event_subscription_service(event_subscription_service)
        .with_collaboration_template_service(collaboration_template_service)
        .with_collaboration_definition_service(collaboration_definition_service)
        .with_collaboration_runtime_service(collaboration_runtime.clone())
        .with_manifest_config(
            crate::config_loader::Environment::resolve()
                .as_str()
                .to_string(),
            config.manifest.clone(),
        ),
        internal_bot_attributes_service,
    )
}

pub(crate) fn gateway_principal_verifier_for_tests() -> Arc<dyn PrincipalVerifier> {
    build_gateway_principal_verifier(
        &GatewayPrincipalConfig::default(),
        Some("test-only-gateway-principal-signing-key"),
    )
    .expect("default Gateway Principal test verifier")
}

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

pub async fn resolve_config_secrets(config: &mut BcsConfig, access: &dyn SecretAccessPort) -> crate::Result<()> {
    if let Some(value) = resolve_secret_value(config.auth_sdk.secret_key_secret.as_deref(), access, "auth_sdk.secret_key_secret").await? {
        config.auth_sdk.secret_key = Some(value);
    }
    if let Some(value) = resolve_secret_value(config.llm.api_key_secret.as_deref(), access, "llm.api_key_secret").await? {
        config.llm.api_key = Some(Secret::new(value));
        config.llm.api_key_env = None;
    }
    if let Some(value) = resolve_secret_value(
        config.bcsfuse.authorization_ref.as_deref(),
        access,
        "bcsfuse.authorization_ref",
    )
    .await?
    {
        config.bcsfuse.set_resolved_authorization(value);
    }
    if config.invite.token_secret_secret.as_deref().is_some_and(|v| !v.trim().is_empty()) {
        config.invite.token_secret = resolve_token_secret_secret(config.invite.token_secret_secret.as_deref(), access, "invite.token_secret_secret").await?;
    }
    if config.session_files.share.token_secret_secret.as_deref().is_some_and(|v| !v.trim().is_empty()) {
        config.session_files.share.token_secret = resolve_token_secret_secret(config.session_files.share.token_secret_secret.as_deref(), access, "session_files.share.token_secret_secret").await?;
    }
    for account in &mut config.dingtalk_accounts {
        if let Some(value) = resolve_secret_value(account.client_secret_secret.as_deref(), access, "dingtalk_accounts.client_secret_secret").await? {
        account.client_secret = Some(Secret::new(value));
        }
    }
    if let Some(logger) = config.group_logger.as_mut() {
        if let Some(value) = resolve_secret_value(logger.client_secret_secret.as_deref(), access, "group_logger.client_secret_secret").await? {
        logger.client_secret = value;
        }
    }
    // Any `<key>_secret` option inside a human_notify provider entry is a
    // secret-provider reference (e.g. Mist): resolve it here and inject the
    // plain value as `<key>`, then drop the reference. A non-blank literal
    // `<key>` wins over the reference so explicit values are never clobbered.
    // `client_secret_secret` (DingTalk notifier) and `signing_key_secret`
    // (work-order notifier) are instances of this rule. The DingTalk
    // notifier's literal secret field is `client_secret`, which itself ends
    // in `_secret`; it is excluded below so it is never mistaken for a
    // reference to a (nonexistent) `client` literal.
    for provider in &mut config.human_notify.providers {
        let provider_name = provider.name.clone();
        let reference_keys: Vec<String> = provider
            .options
            .keys()
            .filter(|key| key.ends_with("_secret") && key.as_str() != "client_secret")
            .cloned()
            .collect();
        for reference_key in reference_keys {
            let Some(reference) = provider
                .options
                .get(&reference_key)
                .and_then(|value| value.as_str())
                .map(str::trim)
                .filter(|value| !value.is_empty())
            else {
                continue;
            };
            let target_key = reference_key
                .strip_suffix("_secret")
                .expect("reference keys end with _secret")
                .to_string();
            let literal_wins = provider
                .options
                .get(&target_key)
                .and_then(|value| value.as_str())
                .is_some_and(|value| !value.trim().is_empty());
            if literal_wins {
                continue;
            }
            let field = format!("human_notify.providers.{provider_name}.{reference_key}");
            if let Some(value) = resolve_secret_value(Some(reference), access, &field).await? {
                provider
                    .options
                    .insert(target_key, serde_json::Value::String(value));
                provider.options.remove(&reference_key);
            }
        }
    }
    if let Some(oauth) = config.auth.oauth.as_mut() {
        for (provider_name, provider) in &mut oauth.providers {
        let field = format!("auth.oauth.providers.{provider_name}.client_secret_secret");
        if let Some(value) = resolve_secret_value(provider.client_secret_secret.as_deref(), access, &field).await? {
            provider.client_secret = Some(Secret::new(value));
        }
        }
    }
    Ok(())
}
