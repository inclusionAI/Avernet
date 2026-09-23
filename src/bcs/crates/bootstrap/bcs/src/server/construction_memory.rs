//! Memory-backed construction: impl BcsServer::{new, new_allowing_private_outbound_for_tests, new_with_outbound_url_guards}.
//! Behavior-preserving split from server.rs's `impl BcsServer` block.

use super::*;

impl BcsServer {
    pub fn new(config: BcsConfig) -> Self {
        let outbound_url_guard = outbound_url_guard_from_config(&config);
        let group_session_secret_access = build_secret_access_blocking(&config)
            .expect("Secret provider configuration must be valid");
        let gateway_principal_verifier = std::thread::scope(|scope| {
            scope
                .spawn(|| {
                    tokio::runtime::Runtime::new()
                        .expect("temp runtime for Gateway Principal verifier build")
                        .block_on(build_gateway_principal_verifier_from_secret_access(
                            &config.gateway_principal,
                            group_session_secret_access.clone(),
                        ))
                })
                .join()
                .expect("Gateway Principal verifier build thread panicked")
        })
        .expect("Gateway Principal verifier configuration must be valid");
        Self::new_with_outbound_url_guards(
            config,
            outbound_url_guard.clone(),
            outbound_url_guard.clone(),
            outbound_url_guard,
            group_session_secret_access,
            gateway_principal_verifier,
            local_eventing_endpoints_allowed(),
        )
    }

    pub fn new_allowing_private_outbound_for_tests(config: BcsConfig) -> Self {
        let group_session_secret_access = group_session_test_secret_access(&config);
        Self::new_with_outbound_url_guards(
            config,
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            group_session_secret_access,
            gateway_principal_verifier_for_tests(),
            true,
        )
    }

    pub(super) fn new_with_outbound_url_guards(
        config: BcsConfig,
        provider_webhook_url_guard: OutboundUrlGuard,
        provider_request_url_guard: OutboundUrlGuard,
        callback_url_guard: OutboundUrlGuard,
        group_session_secret_access: Arc<dyn SecretAccessPort>,
        mut gateway_principal_verifier: Arc<dyn PrincipalVerifier>,
        allow_local_eventing_endpoints: bool,
    ) -> Self {
        let invite_token_secret = resolve_invite_token_secret(&config);
        let admin_invocation_runs = Arc::new(AdminInvocationStore::default());
        // Create service implementations (synchronous, in-memory mode)
        assert!(!config.message_delivery.flow_enabled.group,
            "managed Group delivery requires the durable async server constructor");
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(config.bots_base_dir.clone()));
        let provider_repos = memory_provider_repos(bot_repo.clone(), config.provider_http.downlink_detection_source);
        let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> = bot_repo.clone();
        let bot_metrics_snapshot: Arc<dyn BotMetricsSnapshotPort> = bot_repo.clone();
        let bot_core_arc: Arc<BotCore> = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repos.provider_repo.clone(),
            provider_repos.provider_credentials.clone(),
            provider_repos.provider_bindings.clone(),
        ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
        let bot_registry: Arc<dyn BotRegistryCoreService> = bot_core_arc.clone();
        // Local single-node mode uses an in-memory relation graph.
        // F.1/F.2 dual-write wiring: relation store must be created BEFORE
        // friend_store and provider_management so it can be injected into both.
        let relation_store: Arc<RelationCore> = Arc::new(RelationCore::memory());
        let user_directory = create_user_directory_plugin(&config)
            .expect("user directory config is valid for in-memory server");
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
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>,
                user_directory.clone(),
                provider_webhook_url_guard,
                provider_control_plane.clone(),
                channel_binding_cleanup.clone(),
                build_bot_catalog_cleanup(&config),
            );
        let (organization_core, organization_management) = memory_organization_services(
            &provider_repos,
            provider_core.clone(),
            bot_registry.clone(),
        );
        let event_repo = crate::eventing_wiring::memory_event_repo();
        let group_event_factory =
            crate::eventing_wiring::event_record_factory(&config, event_repo.clone());
        let group_repo = Arc::new(
            MemoryGroupRepo::new().with_event_store(event_repo.clone(), crate::env::resolve_env()),
        );
        let group_metrics_snapshot: Arc<dyn GroupMetricsSnapshotPort> = group_repo.clone();
        let group_repo_for_session: Arc<dyn GroupRepoPort> = group_repo.clone();
        let sessions = Arc::new(
            GroupCore::with_repo(group_repo).with_event_record_factory(group_event_factory.clone()),
        );
        let router = Arc::new(MessageRouter::new());
        let proposals = Arc::new(ProposalStore::new());
        let friend_repo = Arc::new(MemoryFriendRepo::with_data_dir(
            config.bots_base_dir.clone(),
        ));
        let friend_store: Arc<FriendCore> =
            Arc::new(FriendCore::with_repo(friend_repo).with_relation(
                relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>
            ));
        let friend_request_repo = Arc::new(MemoryFriendRequestRepo::with_data_dir(
            config.bots_base_dir.clone(),
        ));
        let friend_request_store: Arc<FriendRequestCore> = Arc::new(FriendRequestCore::with_repo(
            friend_request_repo,
            friend_store.clone(),
            bot_registry.clone(),
        ));

        let (fusion, fuse_client) = create_fusion_service(&config);
        let bot_connections = Arc::new(BotConnectionRegistry::new());
        let mut bot_use_cases = Bot::new_with_friend(bot_registry.clone(), friend_store.clone())
            .with_uplink_config(config.uplink.clone())
            .with_bot_core(bot_core_arc.clone())
            .with_organization(organization_core.clone())
            .with_relation(relation_store.clone() as Arc<dyn bcs_service_api::RelationCoreService>)
            .with_connection_control(
                bot_connections.clone() as Arc<dyn bcs_service_api::BotConnectionControlPort>
            );
        if let Some(user_directory) = user_directory.clone() {
            bot_use_cases = bot_use_cases.with_user_directory(user_directory);
        }
        let bot_use_cases = Arc::new(bot_use_cases);
        let frontend_bot_query: Arc<dyn bcs_service_api::BotQueryService> = bot_use_cases.clone();
        let frontend_connections = Arc::new(
            WorkbenchConnectionRegistry::with_bot_query(frontend_bot_query)
                .with_scope_changes_enabled(
                    !config
                        .leader_election
                        .as_ref()
                        .is_some_and(|leader_election| leader_election.enabled),
                ),
        );
        let run_channels: Arc<RunChannelManager> = Arc::new(RunChannelManager::new());
        let frontend_run_channels = run_channels.clone();
        let ws_bot_delivery: Arc<dyn BotDeliveryPort> = bot_connections.clone();
        let provider_transport = Arc::new(
            bcs_provider_http::HttpProviderTransport::with_url_guard(provider_request_url_guard)
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
        let interceptors = create_interceptor_chain(&config)
            .expect("security gateway config is valid for in-memory server");
        let cutoff_timestamp = config.message_history.cutoff_timestamp;
        let manager_worker_cutoff_timestamp =
            config.message_history.manager_worker_cutoff_timestamp;
        let session_repo = Arc::new(MemorySessionRepo::new().with_event_store(event_repo.clone()));
        let message_repo: Arc<dyn MessageRepoPort> =
            Arc::new(MemoryMessageRepo::new().with_event_store(event_repo.clone()));
        let group_session_metrics_snapshot: Arc<dyn GroupSessionMetricsSnapshotPort> =
            session_repo.clone();
        let session_management: Arc<dyn SessionManagementService> = Arc::new(
            SessionManagementServiceImpl::new(session_repo.clone(), group_repo_for_session.clone())
                .with_bot_runtime(bot_use_cases.clone())
                .with_event_record_factory(group_event_factory.clone())
                .with_opening_message_delivery(message_repo.clone(), frontend_delivery.clone()),
        );
        let bot_run_context: Arc<dyn BotRunContextPort> =
            Arc::new(bcs_message_flow::MemoryBotRunContextStore::new());
        let session_file_service = build_session_files_service_blocking(
            &config,
            crate::env::resolve_env(),
            None,
            None,
            session_repo.clone(),
        );
        let interaction_terminal_observer = Arc::new(InteractionTerminalObserver::default());
        let terminal_observer: Arc<dyn BotTerminalObserverPort> =
            Arc::new(CompositeBotTerminalObserver::new(vec![
                interaction_terminal_observer.clone(),
                Arc::new(AdminInvocationTerminalObserver::new(
                    admin_invocation_runs.clone(),
                    callback_url_guard.clone(),
                )),
            ]));
        let state_machine_terminal_observer =
            Arc::new(DeferredStateMachineTerminalObserver::new(terminal_observer));
        if config.human_notify.providers.iter().any(|provider| provider.enabled) {
            // This synchronous construction path cannot await the notifier
            // factory build; keep the configured backend from being silently
            // ignored.
            tracing::warn!(
                "human_notify providers are configured but ignored in this construction path; \
                 use BcsServer::new_with_storage to enable human mention notifications"
            );
        }
        let coordination_intents = create_coordination_intents(Arc::new(bcs_cache_local::InMemoryCachePlugin::new()));
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
            config.eventing.enabled.then(|| group_event_factory.clone()),
            crate::eventing_wiring::event_recorder(&config, event_repo.clone()),
            Arc::new(bcs_service_api::port::NoopHumanMentionNotifyPort),
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
        let a2a_run_store = Arc::new(bcs_message_flow::a2a_chat::ChatRunStore::with_capacity(
            config.async_chat_run_max_entries,
        ));
        let a2a_run_port = Arc::new(crate::http_adapter::BootstrapRunChannelPort {
            run_channels: run_channels.clone(),
        });
        let metrics = crate::metrics::MetricsRuntime::install(&config)
            .expect("metrics runtime must initialize");
        let a2a_chat_impl = Arc::new(
            A2aChat::new_with_run_ports(
                bot_delivery.clone(),
                a2a_run_store,
                config.async_chat_run_timeout_ms,
                bot_registry.clone(),
                friend_store.clone(),
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
            friend_store.clone(),
            friend_request_store.clone(),
            relation_store.clone(),
            provider_control_plane.clone(),
            None,
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
            callback_url_guard.clone(),
            provider_stream_gray_list.clone(),
            Arc::new(bcs_test_support::NoopPermissionProfileRepo),
        );
        let (message_flow, channel_slot) =
            finalize_message_flow(message_flow_builder, use_cases.system_message.clone());

        let collaboration_store = Arc::new(
            MemoryCollaborationStore::new()
                .with_event_store(event_repo.clone())
                .with_session_repo(session_repo.clone()),
        );
        let extensions = BcsServerExtensions::default();
        let judge_evaluator: Arc<dyn JudgeEvaluatorPort> =
            create_judge_evaluator(&config, &extensions).unwrap_or_else(|error| {
                warn!(
                    error = %error,
                    "Failed to initialize judge evaluator; state-machine judge nodes will fail"
                );
                Arc::new(NoopJudgeEvaluator::default())
            });
        let (session_channel_outbound_slot, session_channel_outbound) =
            deferred_session_channel_outbound();
        let collaboration_runtime = Arc::new(
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
            .with_callback_url_guard(callback_url_guard.clone())
            .with_session_channel_outbound(session_channel_outbound)
            .with_result_publisher(Arc::new(MessageFlowStateMachineResultPublisher::new(
                message_flow.clone(),
                message_repo.clone(),
            )))
            .with_message_repo(message_repo.clone())
            .with_frontend_delivery(frontend_delivery.clone())
            .with_event_record_factory(crate::eventing_wiring::event_record_factory(
                &config,
                event_repo.clone(),
            )),
        );
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
        let collaboration_templates = build_standalone_collaboration_template_service(&config);
        let invite_code_service =
            build_invite_code_service(&config, None, None, invite_token_secret.clone());
        let eventing_runtime = build_eventing_runtime_blocking(
            &config,
            event_repo,
            sessions.clone(),
            session_management.clone(),
            collaboration_runtime.clone(),
            bot_registry.clone(),
            allow_local_eventing_endpoints,
        )
        .expect("Eventing configuration must initialize");
        let (openapi_v1, internal_bot_attributes_service) = build_openapi_v1_state(
            &config,
            invite_token_secret.clone(),
            control_plane_repo,
            &provider_repos,
            bot_registry.clone(),
            sessions.clone(),
            friend_store.clone(),
            use_cases.candidate_search.clone(),
            friend_request_store,
            relation_store.clone(),
            session_management.clone(),
            session_launch.clone(),
            group_management_v1.clone(),
            collaboration_runtime.clone(),
            config.llm.is_enabled(),
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
            Arc::new(bcs_test_support::NoopConnectService),
            frontend_connections.clone(),
            eventing_runtime.service.clone(),
            eventing_runtime.group_provisioner.clone(),
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
        let channel_runtime = build_channel_runtime(
            &config,
            channel_slot,
            channel_binding_cleanup,
            session_channel_outbound_slot,
            memory_channel_repos(None),
            session_repo.clone(),
            message_flow.clone(),
            use_cases.system_message.clone(),
            collaboration_runtime.clone(),
            sessions.clone(),
            bot_registry.clone(),
        )
        .expect("in-memory channel runtime must initialize");
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
            .friend(friend_store)
            .relation(relation_store)
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
            callback_url_guard.clone(),
        );
        // Start Pending-sweep for session-file workspace
        spawn_session_files_pending_sweep(services.session_files.clone());

        let (leader_election, lifecycle) = create_standalone_leader_lifecycle();
        register_late_lifecycles(&lifecycle, fuse_client.as_ref());
        register_eventing_lifecycles(
            &lifecycle,
            eventing_runtime.lifecycle.as_ref(),
            eventing_runtime.provisioning_lifecycle.as_ref(),
        );
        register_channel_lifecycles(&lifecycle, &channel_runtime.lifecycles);
        let auth_config = crate::auth_wiring::resolve_auth_config(
            &config.auth,
            crate::config_loader::Environment::resolve().as_str(),
        );
        // Shared store pair (spec §8.5) + strict engine for the OAuth
        // entrypoints; the identity-only auth chain shares the legacy port.
        let identity_stores = crate::identity_wiring::memory_identity_stores();
        let user_identity_port = Some(crate::identity_wiring::user_identity_port(
            &identity_stores,
        ));
        let auth_session_identities = Some(crate::identity_wiring::auth_session_identity_port(
            &identity_stores,
        ));
        let oauth_session_engine = auth_config
            .oauth
            .as_ref()
            .filter(|oauth| !oauth.jwt_secret.is_empty())
            .map(|oauth| {
                Arc::new(bcs_auth_oauth::OAuthSessionEngine::new(
                    bcs_jwt::OAuthSessionJwt::new(&oauth.jwt_secret),
                    auth_session_identities
                        .clone()
                        .expect("memory identity store wired"),
                    oauth.env.clone(),
                    oauth.idle_timeout_secs(),
                ))
            });
        let auth_chain = Arc::new(crate::auth_wiring::build_auth_chain(
            &auth_config,
            bot_registry.clone(),
            user_identity_port.clone(),
        ));
        // Task 12: assemble the explicit `[api.auth]` chain (None = compat
        // mode). The composite verifier is published into BOTH the V1
        // ApiState and the retained state verifier — the SAME Arc — before
        // the state exists; a build failure aborts construction entirely.
        let built_api_auth = build_built_api_auth_blocking(
            &config,
            group_session_secret_access.clone(),
            auth_session_identities.clone(),
        )
        .expect("[api.auth] source assembly must be valid");
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
            outbound_url_guard: callback_url_guard,
            admin_invocation_runs,
            connect_service: Arc::new(bcs_test_support::NoopConnectService),
            admission_service: Arc::new(bcs_test_support::NoopAdmissionService),
            built_api_auth,
        });

        Self { config, state }
    }
}
