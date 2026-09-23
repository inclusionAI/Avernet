//! Storage-backed construction: impl BcsServer::{new_with_storage, new_with_infrastructure}.
//! Behavior-preserving split from server.rs's `impl BcsServer` block.

use super::*;

impl BcsServer {
    pub async fn new_with_storage(config: BcsConfig) -> crate::Result<Self> {
        let infrastructure_plugins = InfrastructurePlugins::from_config(&config).await?;
        Self::new_with_infrastructure(
            config,
            infrastructure_plugins,
            BcsServerExtensions::default(),
        )
        .await
    }

    pub async fn new_with_infrastructure(
        config: BcsConfig,
        infrastructure_plugins: InfrastructurePlugins,
        extensions: BcsServerExtensions,
    ) -> crate::Result<Self> {
        use bcs_service_api::BotRegistryCoreService;

        let group_session_secret_access = crate::http_adapter::build_secret_access(&config).await?;
        let invite_token_secret = resolve_invite_token_secret(&config);
        let mut gateway_principal_verifier = build_gateway_principal_verifier_from_secret_access(
            &config.gateway_principal,
            group_session_secret_access.clone(),
        )
        .await?;
        info!(
            issuers = ?config.gateway_principal.issuers,
            audience = %config.gateway_principal.audience,
            key_id = %config.gateway_principal.key_id,
            "Gateway Principal verifier initialized"
        );
        let outbound_url_guard = outbound_url_guard_from_config(&config);
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        let user_directory = match extensions.user_directory_plugin.clone() {
            Some(plugin) => Some(plugin),
            None => create_user_directory_plugin(&config)?,
        };
        info!(
            cache_plugin = %infrastructure_plugins.cache_kind(),
            db_plugin = %infrastructure_plugins.db_kind(),
            cache_adapter_ready = infrastructure_plugins.cache().is_some(),
            db_adapter_ready = infrastructure_plugins.db().is_some(),
            "Selected infrastructure plugins"
        );

        // Run SQLite DDL initialization when local SQLite is selected.
        if infrastructure_plugins.db_kind() == DbPluginKind::LocalSqlite {
            if let Some(db) = infrastructure_plugins.db() {
                let report = crate::migrations::run_sqlite_migrations_with_report(db.as_ref())
                    .await
                    .map_err(|err| {
                        crate::BcsError::StorageInitError(format!("run sqlite migrations: {}", err))
                    })?;
                tracing::info!(
                    current_version = ?report.current_version,
                    target_version = report.target_version,
                    applied_versions = report.applied_versions.len(),
                    repaired_columns = report.repaired_columns.len(),
                    "SQLite migrations completed"
                );
            }
        }

        let db_plugin = infrastructure_plugins.db().ok_or_else(|| {
            crate::BcsError::StorageInitError(
                "LocalSqlite storage selected but DbPlugin handle is unavailable".to_string(),
            )
        })?;
        let db_kind = infrastructure_plugins.db_kind();
        let db_flavor = db_sql_flavor(&db_kind);
        let event_repo = crate::eventing_wiring::db_event_repo(db_plugin.clone(), db_flavor);
        let provider_repos = db_provider_repos(db_plugin.clone(), &db_kind, config.provider_http.downlink_detection_source);

        let cache_plugin = infrastructure_plugins
            .cache()
            .unwrap_or_else(|| Arc::new(bcs_cache_local::InMemoryCachePlugin::new()));
        let cache_key_prefix = config.cache.redis.effective_key_prefix();
        info!(db_plugin = %db_kind, "Initializing DB-backed bot registry");
        let bot_repo = Arc::new(PersistentBotRepo::with_sql_flavor(
            db_plugin.clone(),
            db_flavor,
        ));
        let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> = bot_repo.clone();
        let bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort> = bot_repo.clone();
        let bot_core_arc = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repos.provider_repo.clone(),
            provider_repos.provider_credentials.clone(),
            provider_repos.provider_bindings.clone(),
        ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
        let bot_registry: Arc<dyn BotRegistryCoreService> = bot_core_arc.clone();

        let leader_election_registration = if extensions.leader_election.is_some() {
            extensions.leader_election.clone()
        } else {
            create_configured_leader_election(&config).await?
        };
        let (leader_election, lifecycle) = create_leader_lifecycle(leader_election_registration);

        // Create group session storage.
        let (sessions, group_metrics_snapshot, group_repo): (
            Arc<dyn GroupCoreService>,
            Arc<dyn GroupMetricsSnapshotPort>,
            Arc<dyn GroupRepoPort>,
        ) = {
            let env = crate::env::resolve_env();
            info!(env = %env, db_plugin = %db_kind, "DB-backed group storage initialized");
            let repo = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlGroupStore::sqlite(db_plugin.clone(), env))
                }
                DbPluginKind::Mysql => Arc::new(MySqlGroupStore::new(db_plugin.clone(), env)),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no group store wiring",
                        provider
                    )
                }
            };
            let event_factory =
                crate::eventing_wiring::event_record_factory(&config, event_repo.clone());
            (
                Arc::new(
                    GroupCore::with_repo(repo.clone()).with_event_record_factory(event_factory),
                ),
                repo.clone() as Arc<dyn GroupMetricsSnapshotPort>,
                repo as Arc<dyn GroupRepoPort>,
            )
        };

        // Create other service implementations
        let router = Arc::new(MessageRouter::new());
        let proposals = Arc::new(ProposalStore::new());

        let (fusion, fuse_client) = create_fusion_service(&config);
        register_late_lifecycles(&lifecycle, fuse_client.as_ref());

        // F.1/F.2 dual-write wiring: relation_svc MUST be constructed BEFORE
        // friend_svc so it can be injected via `with_relation(...)`.
        info!(db_plugin = %db_kind, "Initializing DB-backed relation storage");
        let relation_repo = match db_kind {
            DbPluginKind::LocalSqlite => Arc::new(DbRelationStore::sqlite(db_plugin.clone())),
            DbPluginKind::Mysql => Arc::new(DbRelationStore::mysql(db_plugin.clone())),
            DbPluginKind::External(provider) => {
                panic!(
                    "external database plugin '{}' has no relation store wiring",
                    provider
                )
            }
        };
        let relation_svc: Arc<dyn bcs_service_api::RelationCoreService> =
            Arc::new(RelationCore::with_repo(relation_repo));

        let provider_control_plane: Arc<dyn BotControlPlaneCoreService> =
            Arc::new(BotControlPlaneCore::new(
                control_plane_repo.clone(),
                provider_repos.provider_repo.clone(),
                provider_repos.provider_bindings.clone(),
            ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
        let channel_binding_cleanup = Arc::new(DeferredChannelBindingCleanupPort::default());
        let (provider_core, provider_bot_core, provider_management) =
            build_provider_services_with_webhook_url_guard(
                &provider_repos,
                bot_registry.clone(),
                relation_svc.clone(),
                user_directory.clone(),
                outbound_url_guard.clone(),
                provider_control_plane.clone(),
                channel_binding_cleanup.clone(),
                build_bot_catalog_cleanup(&config),
            );
        let (organization_core, organization_management) = db_organization_services(
            db_plugin.clone(),
            &db_kind,
            &provider_repos,
            provider_core.clone(),
            bot_registry.clone(),
        );

        // Create DB-backed legacy friend repositories. The FriendCore service is
        // built after edge-permission wiring so legacy friendship mutations can
        // dual-write both coexistence paths.
        let (friend_repo, friend_request_repo): (
            Arc<dyn bcs_service_api::FriendRepoPort>,
            Arc<dyn bcs_service_api::FriendRequestRepoPort>,
        ) = {
            info!(
                db_plugin = %db_kind,
                "Initializing DB-backed friend storage with relation and edge-permission dual-write"
            );
            let friend_repo: Arc<dyn bcs_service_api::FriendRepoPort> = match db_kind {
                DbPluginKind::LocalSqlite => Arc::new(DbFriendStore::sqlite(db_plugin.clone())),
                DbPluginKind::Mysql => Arc::new(DbFriendStore::mysql(db_plugin.clone())),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no friend store wiring",
                        provider
                    )
                }
            };
            let friend_request_repo: Arc<dyn bcs_service_api::FriendRequestRepoPort> = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(DbFriendRequestStore::sqlite(db_plugin.clone()))
                }
                DbPluginKind::Mysql => Arc::new(DbFriendRequestStore::mysql(db_plugin.clone())),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no friend request store wiring",
                        provider
                    )
                }
            };

            (friend_repo, friend_request_repo)
        };

        // Edge-permission stores + services (T16): real DB-backed impls back
        // the `connect`/`admission` `HttpAppState` fields. Stores follow the
        // same `db_kind` match as the relation/friend stores above; services
        // hold the env-isolation string the composition root resolved.
        let (edge_grant_store, profile_store, request_store, bot_config_store): (
            Arc<dyn bcs_service_api::port::repo::EdgeGrantRepoPort>,
            Arc<dyn bcs_service_api::port::repo::PermissionProfileRepoPort>,
            Arc<dyn bcs_service_api::port::repo::PermissionRequestRepoPort>,
            Arc<dyn bcs_service_api::port::repo::BotActorConfigRepoPort>,
        ) = {
            let edge_grant_repo = match db_kind {
                DbPluginKind::LocalSqlite => Arc::new(
                    bcs_edge_permission_store::DbEdgeGrantStore::sqlite(db_plugin.clone()),
                ),
                DbPluginKind::Mysql => Arc::new(
                    bcs_edge_permission_store::DbEdgeGrantStore::mysql(db_plugin.clone()),
                ),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no edge-grant store wiring",
                        provider
                    )
                }
            };
            let profile_repo = match db_kind {
                DbPluginKind::LocalSqlite => Arc::new(
                    bcs_edge_permission_store::DbPermissionProfileStore::sqlite(db_plugin.clone()),
                ),
                DbPluginKind::Mysql => Arc::new(
                    bcs_edge_permission_store::DbPermissionProfileStore::mysql(db_plugin.clone()),
                ),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no permission-profile store wiring",
                        provider
                    )
                }
            };
            let request_repo = match db_kind {
                DbPluginKind::LocalSqlite => Arc::new(
                    bcs_edge_permission_store::DbPermissionRequestStore::sqlite(db_plugin.clone()),
                ),
                DbPluginKind::Mysql => Arc::new(
                    bcs_edge_permission_store::DbPermissionRequestStore::mysql(db_plugin.clone()),
                ),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no permission-request store wiring",
                        provider
                    )
                }
            };
            let bot_config_repo = match db_kind {
                DbPluginKind::LocalSqlite => Arc::new(
                    bcs_edge_permission_store::DbBotActorConfigStore::sqlite(db_plugin.clone()),
                ),
                DbPluginKind::Mysql => Arc::new(
                    bcs_edge_permission_store::DbBotActorConfigStore::mysql(db_plugin.clone()),
                ),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no bot-actor-config store wiring",
                        provider
                    )
                }
            };
            (edge_grant_repo, profile_repo, request_repo, bot_config_repo)
        };
        let edge_permission_env = crate::env::resolve_env();
        let friend_connect_notification: Arc<dyn bcs_service_api::FriendConnectNotificationPort> =
            match config.friend_work_order_base_url.as_deref() {
                Some(base_url) => Arc::new(
                    HttpFriendConnectNotificationPort::new(base_url)
                        .expect("friend_work_order_base_url must be a valid HTTP(S) URL"),
                ),
                None => Arc::new(bcs_service_api::NoopFriendConnectNotificationPort),
            };
        let friend_auth_sync: Arc<dyn bcs_service_api::port::FriendAuthSyncPort> =
            match config.friend_work_order_base_url.as_deref() {
                Some(base_url) => Arc::new(
                    HttpFriendAuthSyncPort::new(base_url)
                        .expect("friend_work_order_base_url must be a valid HTTP(S) URL"),
                ),
                None => Arc::new(bcs_service_api::port::NoopFriendAuthSyncPort),
            };
        let connect_service_impl = Arc::new(bcs_edge_permission::DbConnectService::new(
            edge_grant_store.clone(),
            profile_store.clone(),
            request_store.clone(),
            bot_config_store.clone(),
            user_directory.clone(),
            friend_connect_notification,
            friend_auth_sync.clone(),
            edge_permission_env,
        ));
        let connect_service: Arc<dyn bcs_service_api::application::ConnectService> =
            connect_service_impl.clone();
        let edge_permission_friend_sync: Arc<dyn bcs_service_api::EdgePermissionFriendSyncService> =
            connect_service_impl.clone();
        // friend_svc = legacy FriendCore (reads bcs_friendships/old tables) plus
        // coexistence dual-write into edge_grants while the old and new friend
        // APIs run in parallel.
        // See docs/superpowers/plans/2026-08-19-v2-friends-parallel-interface.md.
        let friend_svc: Arc<dyn bcs_service_api::FriendCoreService> = Arc::new(
            FriendCore::with_repo(friend_repo)
                .with_relation(relation_svc.clone())
                .with_edge_permission_sync(edge_permission_friend_sync),
        );
        let friend_request_svc: Arc<dyn bcs_service_api::FriendRequestCoreService> =
            Arc::new(FriendRequestCore::with_repo(
                friend_request_repo,
                friend_svc.clone(),
                bot_registry.clone(),
            ));
        let admission_service: Arc<dyn bcs_service_api::application::AdmissionService> =
            Arc::new(bcs_edge_permission::DbAdmissionService::new(
                edge_grant_store.clone(),
                bot_config_store.clone(),
                profile_store.clone(),
            ));
        let bot_connections = Arc::new(BotConnectionRegistry::new());
        let mut bot_runtime_for_session =
            Bot::new_with_friend(bot_registry.clone(), friend_svc.clone())
                .with_uplink_config(config.uplink.clone())
                .with_bot_core(bot_core_arc.clone())
                .with_organization(organization_core.clone())
                .with_connection_control(
                    bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>
                );
        if let Some(user_directory) = user_directory.clone() {
            bot_runtime_for_session = bot_runtime_for_session.with_user_directory(user_directory);
        }
        let bot_runtime_for_session: Arc<dyn bcs_service_api::BotRuntimeConnectionService> =
            Arc::new(bot_runtime_for_session);
        let frontend_connections = Arc::new(
            WorkbenchConnectionRegistry::new().with_scope_changes_enabled(
                !config
                    .leader_election
                    .as_ref()
                    .is_some_and(|leader_election| leader_election.enabled),
            ),
        );
        let run_channels = Arc::new(RunChannelManager::new());
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(outbound_url_guard.clone())
                .with_chat_run_timeout_ms(config.provider_chat_run_timeout_ms),
        );
        let provider_stream_gray_list = create_provider_stream_gray_list(&config);
        let raw_bot_delivery: Arc<dyn BotDeliveryPort> = Arc::new(
            bcs_provider_http::BotTransportMux::new(ws_bot_delivery, provider_transport.clone()),
        );
        let bot_delivery = maybe_wrap_bot_delivery(&config, raw_bot_delivery);
        let raw_frontend_delivery: Arc<dyn FrontendDeliveryPort> =
            Arc::new(WorkbenchFrontendDelivery::new(
                frontend_connections.clone(),
                frontend_run_channels.clone(),
            ));
        let frontend_delivery = maybe_wrap_frontend_delivery(&config, raw_frontend_delivery);
        let interceptors = create_interceptor_chain(&config)?;
        let cutoff_timestamp = config.message_history.cutoff_timestamp;
        let manager_worker_cutoff_timestamp =
            config.message_history.manager_worker_cutoff_timestamp;
        let (session_repo, session_management, group_session_metrics_snapshot, message_repo): (
            Arc<dyn SessionRepoPort>,
            Arc<dyn SessionManagementService>,
            Arc<dyn GroupSessionMetricsSnapshotPort>,
            Arc<dyn MessageRepoPort>,
        ) = {
            let env = crate::env::resolve_env();
            info!(env = %env, db_plugin = %db_kind, "DB-backed session and message storage initialized");
            let session_repo = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlSessionStore::sqlite(db_plugin.clone(), env.clone()))
                }
                DbPluginKind::Mysql => {
                    Arc::new(MySqlSessionStore::new(db_plugin.clone(), env.clone()))
                }
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no session store wiring",
                        provider
                    )
                }
            };
            let message_repo: Arc<dyn MessageRepoPort> = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlMessageStore::sqlite(db_plugin.clone(), env))
                }
                DbPluginKind::Mysql => Arc::new(MySqlMessageStore::new(db_plugin.clone(), env)),
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no message store wiring",
                        provider
                    )
                }
            };
            let session_management: Arc<dyn SessionManagementService> = Arc::new(
                SessionManagementServiceImpl::new(session_repo.clone(), group_repo.clone())
                    .with_bot_runtime(bot_runtime_for_session.clone())
                    .with_event_record_factory(crate::eventing_wiring::event_record_factory(
                        &config,
                        event_repo.clone(),
                    ))
                    .with_opening_message_delivery(message_repo.clone(), frontend_delivery.clone()),
            );
            (
                session_repo.clone() as Arc<dyn SessionRepoPort>,
                session_management,
                session_repo as Arc<dyn GroupSessionMetricsSnapshotPort>,
                message_repo,
            )
        };
        if config.bot_run_context_store == "redis"
            && infrastructure_plugins.cache_kind() != CachePluginKind::Redis
        {
            return Err(crate::BcsError::InvalidConfig(
                "bot_run_context_store='redis' requires a Redis cache backend \
                 ([cache.redis]); the resolved cache is not Redis — refusing to \
                 start with silently process-local run context"
                    .to_string(),
            ));
        }
        let bot_run_context: Arc<dyn BotRunContextPort> = if config.bot_run_context_store == "redis"
        {
            Arc::new(bcs_message_flow::RedisBotRunContextStore::new(
                cache_plugin.clone(),
                cache_key_prefix.clone(),
                config.async_chat_run_retention_ms,
            ))
        } else {
            Arc::new(bcs_message_flow::MemoryBotRunContextStore::new())
        };
        let session_file_service = build_session_files_service(
            &config,
            crate::env::resolve_env(),
            infrastructure_plugins.db(),
            Some(db_flavor),
            session_repo.clone(),
        )
        .await;
        let interaction_terminal_observer = Arc::new(InteractionTerminalObserver::default());
        let terminal_observer: Arc<dyn BotTerminalObserverPort> =
            Arc::new(CompositeBotTerminalObserver::new(vec![
                interaction_terminal_observer.clone(),
                Arc::new(AdminInvocationTerminalObserver::new(
                    admin_invocation_runs.clone(),
                    outbound_url_guard.clone(),
                )),
            ]));
        let state_machine_terminal_observer =
            Arc::new(DeferredStateMachineTerminalObserver::new(terminal_observer));
        let coordination_intents = create_coordination_intents(cache_plugin.clone());
        let message_flow_builder = create_message_flow_builder(
            coordination_intents.clone(),
            bot_registry.clone(),
            sessions.clone(),
            router.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            config.max_group_messages,
            interceptors.clone(),
            session_management.clone(),
            bot_run_context.clone(),
            Some(message_repo.clone()),
            provider_stream_gray_list.clone(),
            state_machine_terminal_observer.clone(),
            config.provider_chat_run_timeout_ms,
            config
                .eventing
                .enabled
                .then(|| crate::eventing_wiring::event_record_factory(&config, event_repo.clone())),
            crate::eventing_wiring::event_recorder(&config, event_repo.clone()),
            build_human_mention_notify_port(&config).await?,
        );
        let pending_messages = message_flow_builder
            .pending_message_port()
            .expect("message flow pending reader requires run context");
        let group_message_history = create_group_message_history_service(
            sessions.clone(),
            bot_registry.clone(),
            bot_delivery.clone(),
            Arc::clone(&bot_connections),
            provider_transport.clone(),
            message_repo.clone(),
            session_repo.clone(),
            cutoff_timestamp,
            manager_worker_cutoff_timestamp,
            config.message_history.new_participant_visible_limit,
            config.message_history.default_page_limit,
            config.message_history.max_page_limit,
            session_file_service.clone(),
            pending_messages,
            config.session_files.share.history_attachment_ttl_seconds,
            config.state_machine_history.persistence_enabled,
            config.message_history.state_machine_cutoff_timestamp,
        );
        let a2a_run_store: Arc<bcs_message_flow::a2a_chat::ChatRunStore> =
            if config.async_chat_run_store == "persistent" {
                let chat_run_repo: Arc<dyn bcs_service_api::port::repo::ChatRunRepoPort> =
                    Arc::new(bcs_chat_run_store::SqlChatRunRepo::new(
                        db_plugin.clone(),
                        db_flavor,
                        cache_plugin.clone(),
                        cache_key_prefix.clone(),
                        config.async_chat_run_retention_ms,
                        crate::env::resolve_env(),
                    ));
                Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_repo(
                    chat_run_repo,
                ))
            } else {
                Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_capacity(
                    config.async_chat_run_max_entries,
                ))
            };
        let a2a_run_port = Arc::new(crate::http_adapter::BootstrapRunChannelPort {
            run_channels: run_channels.clone(),
        });
        let metrics = crate::metrics::MetricsRuntime::install(&config)?;
        let a2a_chat_impl = Arc::new(
            A2aChat::new_with_run_ports(
                bot_delivery.clone(),
                a2a_run_store,
                config.async_chat_run_timeout_ms,
                bot_registry.clone(),
                friend_svc.clone(),
                a2a_run_port.clone(),
                a2a_run_port.clone(),
            )
            .with_organization(organization_core.clone())
            .with_interceptors(interceptors.clone())
            .with_run_lifecycle_hook(direct_chat_run_lifecycle_hook(metrics.as_ref()))
            .with_bot_run_context(bot_run_context.clone()),
        );
        let a2a_chat: Arc<dyn A2aChatService> = a2a_chat_impl.clone();
        let a2a_chat_runs: Arc<dyn A2aChatRunService> = a2a_chat_impl.clone();
        let a2a_chat_runs = maybe_wrap_a2a_chat_runs(&config, a2a_chat_runs);
        let direct_chat_run_snapshot: Arc<dyn DirectChatRunSnapshotPort> = a2a_chat_impl;
        let use_cases = build_use_case_bundle(
            &config,
            message_flow_builder.system_queue_port(),
            bot_registry.clone(),
            bot_core_arc.clone(),
            organization_core.clone(),
            bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>,
            sessions.clone(),
            proposals.clone(),
            friend_svc.clone(),
            friend_request_svc.clone(),
            relation_svc.clone(),
            provider_control_plane.clone(),
            Some(edge_grant_store.clone()),
            fuse_client.clone(),
            fusion.clone(),
            bot_delivery.clone(),
            frontend_delivery.clone(),
            group_message_history.clone(),
            session_management.clone(),
            channel_binding_cleanup.clone(),
            frontend_connections.clone(),
            bot_run_context.clone(),
            user_directory.clone(),
            Some(message_repo.clone()),
            outbound_url_guard.clone(),
            provider_stream_gray_list.clone(),
            profile_store.clone(),
        );
        let message_flow_builder = message_flow_builder.with_system_message(use_cases.system_message.clone());
        let channel_slot = message_flow_builder.channel_slot();
        let message_flow: Arc<dyn MessageFlowService> = crate::message_delivery_wiring::wire_with_leader(message_flow_builder, &config, leader_election.clone()).await?;
        let mut delivery_startup_guard = crate::message_delivery_wiring::StartupGuard(Some(message_flow.clone()));
        frontend_connections
            .set_bot_query(use_cases.bot_query.clone())
            .await;

        let judge_evaluator = create_judge_evaluator(&config, &extensions)?;
        let (session_channel_outbound_slot, session_channel_outbound) =
            deferred_session_channel_outbound();
        let collaboration_runtime: Arc<dyn bcs_service_api::CollaborationRuntimeService> = {
            let env = crate::env::resolve_env();
            info!(env = %env, db_plugin = %db_kind, "DB-backed collaboration storage initialized");
            let collaboration_store = match db_kind {
                DbPluginKind::LocalSqlite => {
                    Arc::new(MySqlCollaborationStore::sqlite(db_plugin.clone(), env))
                }
                DbPluginKind::Mysql => {
                    Arc::new(MySqlCollaborationStore::new(db_plugin.clone(), env))
                }
                DbPluginKind::External(provider) => {
                    panic!(
                        "external database plugin '{}' has no collaboration store wiring",
                        provider
                    )
                }
            };
            Arc::new(
                CollaborationRuntime::new(
                    collaboration_store.clone(),
                    collaboration_store.clone(),
                    collaboration_store.clone(),
                    collaboration_store,
                    sessions.clone(),
                    session_management.clone(),
                    bot_delivery.clone(),
                    judge_evaluator,
                )
                .with_bot_registry(bot_registry.clone())
                .with_bot_run_context(bot_run_context.clone())
                .with_provider_chat_run_timeout_ms(config.provider_chat_run_timeout_ms)
                .with_fixed_loop_limits(config.collaboration.fixed_loop_limits)
                .with_loop_execution_enabled(config.collaboration.loop_execution_enabled)
            .with_history_persistence(config.state_machine_history.persistence_enabled)
            .with_history_cutoff_timestamp(config.message_history.state_machine_cutoff_timestamp)
                .with_loop_instrumentation(state_machine_loop_instrumentation(metrics.as_ref()))
                .with_callback_url_guard(outbound_url_guard.clone())
                .with_session_channel_outbound(session_channel_outbound)
                .with_result_publisher(Arc::new(MessageFlowStateMachineResultPublisher::new(
                    message_flow.clone(),
                    message_repo.clone(),
                )))
                .with_message_repo(message_repo.clone())
                .with_frontend_delivery(frontend_delivery.clone())
                .with_event_record_factory(
                    crate::eventing_wiring::event_record_factory(&config, event_repo.clone()),
                ),
            )
        };
        state_machine_terminal_observer.bind(collaboration_runtime.clone());
        let session_management = Arc::new(SessionManagementWithRuntimeCleanup::new(
            session_management.clone(),
            collaboration_runtime.clone(),
        ));
        let session_launch = Arc::new(SessionLaunchApplication::new(
            bot_registry.clone(),
            sessions.clone(),
            session_management.clone(),
            collaboration_runtime.clone(),
            use_cases.system_message.clone(),
        ));
        let group_management = maybe_wrap_group_management(
            &config,
            Arc::new(GroupManagementWithRuntimeCleanup::new(
                use_cases.group_management,
                collaboration_runtime.clone(),
            )),
        );
        let group_management_v1 = maybe_wrap_group_management(
            &config,
            Arc::new(GroupManagementWithRuntimeCleanup::new(
                use_cases.group_management_v1,
                collaboration_runtime.clone(),
            )),
        );
        let collaboration_templates = build_collaboration_template_service_with_storage(
            &config,
            &infrastructure_plugins,
            config.llm.is_enabled() || extensions.llm_provider.is_some(),
        )?;
        let invite_code_service = build_invite_code_service(
            &config,
            Some(db_plugin.clone()),
            Some(&db_kind),
            invite_token_secret.clone(),
        );
        let eventing_runtime = crate::eventing_wiring::build_eventing_runtime(
            &config,
            event_repo,
            sessions.clone(),
            session_management.clone(),
            collaboration_runtime.clone(),
            bot_registry.clone(),
            local_eventing_endpoints_allowed(),
        )
        .await?;
        register_eventing_lifecycles(
            &lifecycle,
            eventing_runtime.lifecycle.as_ref(),
            eventing_runtime.provisioning_lifecycle.as_ref(),
        );
        let (openapi_v1, internal_bot_attributes_service) = build_openapi_v1_state(
            &config,
            invite_token_secret.clone(),
            control_plane_repo,
            &provider_repos,
            bot_registry.clone(),
            sessions.clone(),
            friend_svc.clone(),
            use_cases.candidate_search.clone(),
            friend_request_svc,
            relation_svc.clone(),
            session_management.clone(),
            session_launch.clone(),
            group_management_v1.clone(),
            collaboration_runtime.clone(),
            config.llm.is_enabled() || extensions.llm_provider.is_some(),
            session_repo.clone(),
            group_message_history.clone(),
            session_file_service.clone(),
            use_cases.system_message.clone(),
            use_cases.bot_management.clone(),
            use_cases.bot_onboarding.clone(),
            collaboration_templates.clone(),
            invite_code_service.clone(),
            config.invite.invite_code_gate_enabled,
            config.invite.public_claim_enabled,
            gateway_principal_verifier.clone(),
            connect_service.clone(),
            frontend_connections.clone(),
            eventing_runtime.service,
            eventing_runtime.group_provisioner,
        );

        // Build services bundle
        let message_flow = maybe_wrap_message_flow(&config, message_flow);
        let interactions = create_interaction_service(
            provider_transport.clone(),
            use_cases.interaction_authorization.clone(),
            frontend_delivery.clone(),
            config.async_chat_run_retention_ms,
        );
        interaction_terminal_observer.set_service(interactions.clone());
        let channel_repos = if channel_bridge_enabled(&config) {
            channel_repos_with_storage(&infrastructure_plugins).await?
        } else {
            memory_channel_repos(None)
        };
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            channel_binding_cleanup,
            session_channel_outbound_slot,
            channel_repos,
            session_repo.clone(),
            message_flow.clone(),
            use_cases.system_message.clone(),
            collaboration_runtime.clone(),
            sessions.clone(),
            bot_registry.clone(),
        )?;
        let channel_service = channel_runtime.service.clone();
        // Only mount the OpenAPI channel surface when the bridge is enabled.
        // When disabled, `channel_runtime.service` is `DisabledChannelService`
        // whose set_binding_status/update_binding_config/delete_binding all
        // return Ok(()) without persisting — mounting it would make PATCH/DELETE
        // falsely 200 for any binding id. Leaving the slot unset makes the
        // handlers fail-closed as 500 internal_error instead.
        let mut openapi_v1 = if channel_bridge_enabled(&config) {
            openapi_v1.with_channel_service(channel_service.clone())
        } else {
            openapi_v1
        };
        register_channel_lifecycles(&lifecycle, &channel_runtime.lifecycles);
        let provider_bot_events_impl = Arc::new(
            ProviderBotEvents::new(
                provider_bot_core.clone(),
                bot_run_context.clone(),
                message_flow.clone(),
            )
            .with_coordination_intents(coordination_intents.clone())
            .with_collaboration_runtime(collaboration_runtime.clone()),
        );
        let provider_event_ingest: Arc<dyn bcs_service_api::ProviderEventIngestService> =
            provider_bot_events_impl.clone();
        let provider_bot_events: Arc<dyn ProviderBotEventService> = provider_bot_events_impl;
        provider_transport.set_ingest(provider_event_ingest, bot_run_context.clone());
        let services = ServicesBuilder::default()
            .registry(bot_registry.clone())
            .group(sessions)
            .routing(router)
            .fusion(fusion)
            .proposal(proposals)
            .friend(friend_svc)
            .relation(relation_svc)
            .bot_delivery(bot_delivery)
            .bot_run_context(bot_run_context)
            .frontend_delivery(frontend_delivery)
            .message_flow(message_flow)
            .interactions(interactions)
            .group_message_history(group_message_history)
            .a2a_chat(a2a_chat)
            .a2a_chat_runs(a2a_chat_runs)
            .collaboration_runtime(collaboration_runtime)
            .collaboration_templates(collaboration_templates)
            .actor_directory(use_cases.actor_directory)
            .friend_use_cases(use_cases.friend_use_cases)
            .human_actors(use_cases.human_actors)
            .bot_onboarding(use_cases.bot_onboarding)
            .bot_query(use_cases.bot_query)
            .bot_management(use_cases.bot_management)
            .bot_runtime(use_cases.bot_runtime)
            .bot_discovery(use_cases.bot_discovery)
            .provider_core(provider_core)
            .provider_bot_core(provider_bot_core)
            .provider_management(provider_management)
            .organization_management(organization_management)
            .provider_bot_events(provider_bot_events)
            .group_management(group_management)
            .group_query(use_cases.group_query)
            .workbench_sessions(use_cases.workbench_sessions)
            .group_proposals(use_cases.group_proposals)
            .group_fusion(use_cases.group_fusion)
            .system_message(use_cases.system_message)
            .session_management(session_management.clone())
            .session_launch(session_launch)
            .channel(channel_service.clone())
            .secret(default_bootstrap_secret_service())
            .session_files(session_file_service)
            .build()
            .expect("services must be fully wired");

        // Start timeout scanner for service-invocation sessions
        let _timeout_handle = crate::timeout_scanner::spawn_with_url_guard(
            services.session_management.clone(),
            services.group.clone(),
            crate::timeout_scanner::DEFAULT_SCAN_INTERVAL,
            outbound_url_guard.clone(),
        );
        // Start Pending-sweep for session-file workspace
        spawn_session_files_pending_sweep(services.session_files.clone());

        let auth_config = crate::auth_wiring::resolve_auth_config(
            &config.auth,
            crate::config_loader::Environment::resolve().as_str(),
        );
        // One shared store instance backs BOTH the legacy display lookups
        // and the strict CAS session writes (spec §8.5: no second writer).
        let identity_stores = match infrastructure_plugins.db() {
            Some(db) => crate::identity_wiring::db_identity_stores(
                infrastructure_plugins.db_kind(),
                db,
            ),
            None => crate::identity_wiring::memory_identity_stores(),
        };
        let user_identity_port = Some(crate::identity_wiring::user_identity_port(
            &identity_stores,
        ));
        let auth_session_identities = Some(crate::identity_wiring::auth_session_identity_port(
            &identity_stores,
        ));
        // Strict OAuth session engine when `[auth.oauth]` carries a secret:
        // BOTH auth entrypoints drive install/rotate/revoke through it.
        let oauth_session_engine = auth_config
            .oauth
            .as_ref()
            .filter(|oauth| !oauth.jwt_secret.is_empty())
            .map(|oauth| {
                Arc::new(bcs_auth_oauth::OAuthSessionEngine::new(
                    bcs_jwt::OAuthSessionJwt::new(&oauth.jwt_secret),
                    auth_session_identities
                        .clone()
                        .expect("identity store wired"),
                    oauth.env.clone(),
                    oauth.idle_timeout_secs(),
                ))
            });
        let auth_chain = Arc::new(
            crate::auth_wiring::try_build_auth_chain_with_factories(
                &auth_config,
                bot_registry.clone(),
                user_identity_port.clone(),
                &extensions.auth_plugin_factories,
            )
            .map_err(crate::BcsError::InvalidConfig)?,
        );
        // Task 12: assemble the explicit `[api.auth]` chain (None = compat
        // mode). The composite verifier is published into BOTH the V1
        // ApiState and the retained state verifier — the SAME Arc — before
        // the state exists; a build failure aborts construction entirely.
        let built_api_auth = build_built_api_auth_blocking(
            &config,
            group_session_secret_access.clone(),
            auth_session_identities.clone(),
        )?;
        if let Some(built) = built_api_auth.as_ref() {
            openapi_v1.principal_verifier = built.verifier.clone();
            gateway_principal_verifier = built.verifier.clone();
        }
        let state = Arc::new(BcsServerState {
            config: config.clone(),
            services,
            run_channels,
            bot_connections,
            frontend_connections,
            frontend_run_channels,
            coordination_processed: Arc::new(Mutex::new(std::collections::HashMap::new())),
            leader_election,
            lifecycle,
            fuse_client,
            provider_credentials: provider_repos.provider_credentials.clone(),
            provider_stream_gray_list,
            channel_http_ingress: channel_runtime.http_ingress.clone(),
            group_metrics_snapshot,
            group_session_metrics_snapshot,
            bot_metrics_snapshot,
            direct_chat_run_snapshot,
            metrics,
            auth_chain,
            auth_config,
            gateway_principal_verifier,
            invite_token_secret,
            openapi_v1,
            internal_bot_attributes_service,
            group_session_secret_access,
            user_identity_port,
            auth_session_identities,
            oauth_session_engine,
            outbound_url_guard,
            admin_invocation_runs,
            connect_service,
            admission_service,
            built_api_auth,
        });

        delivery_startup_guard.0 = None;
        Ok(Self { config, state })
    }
}
