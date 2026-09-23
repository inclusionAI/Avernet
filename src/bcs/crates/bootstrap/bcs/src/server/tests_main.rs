//! Tests mod split out from server.rs: tests.

    use super::*;

use super::*;
    use axum::body::to_bytes;
    use bcs_protocol::{BcsFrame, EventFrame, RequestFrame};
    use bcs_service_api::RegisterProviderCommand;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;
    use tokio::time::{Duration, timeout};
    use tower::ServiceExt;

    #[cfg(feature = "prometheus-metrics")]
    #[tokio::test]
    #[serial_test::serial]
    async fn dropped_http_metrics_guard_records_cancelled_request() {
        let mut config = BcsConfig::default();
        config.metrics.enabled = true;
        let metrics = crate::metrics::MetricsRuntime::install(&config)
            .expect("install metrics")
            .expect("metrics enabled");
        let route = "/metrics-test/{request_id}";

        {
            let _guard = HttpRequestMetricsGuard::new(
                metrics.clone(),
                route.to_string(),
                "POST".to_string(),
            );
        }

        let body = metrics.render();
        let env = metrics.env.as_ref();
        assert!(body.contains(&format!(
            "bcs_http_requests_total{{env=\"{env}\",route=\"{route}\",method=\"POST\",status_class=\"4xx\",result=\"error\"}}"
        )));
        assert!(body.contains(&format!(
            "bcs_http_request_cancellations_total{{env=\"{env}\",route=\"{route}\",method=\"POST\"}}"
        )));

        metrics.shutdown().await;
    }

    #[derive(Default)]
    struct RecordingSessionChannelOutbound {
        events: tokio::sync::Mutex<Vec<HumanInputReadyEvent>>,
    }

    #[derive(Default)]
    struct RecordingChannelBindingCleanup {
        deleted_bot_ids: tokio::sync::Mutex<Vec<String>>,
    }

    #[async_trait]
    impl ChannelBindingCleanupPort for RecordingChannelBindingCleanup {
        async fn delete_bindings_for_group(
            &self,
            _group_id: &str,
        ) -> bcs_service_api::ServiceResult<u64> {
            Ok(0)
        }

        async fn delete_bindings_for_bot(
            &self,
            bot_id: &str,
        ) -> bcs_service_api::ServiceResult<u64> {
            self.deleted_bot_ids.lock().await.push(bot_id.to_string());
            Ok(1)
        }
    }

    #[tokio::test]
    async fn provider_management_deletes_channel_bindings_when_provider_bot_is_deleted() {
        let temp_dir = tempfile::TempDir::new().expect("temp dir");
        let bot_repo = Arc::new(MemoryBotRepo::with_base_dir(temp_dir.path().to_path_buf()));
        let provider_repos = memory_provider_repos(bot_repo.clone(), Default::default());
        let control_plane_repo: Arc<dyn BotControlPlaneRepoPort> = bot_repo.clone();
        let bot_registry: Arc<dyn BotRegistryCoreService> = Arc::new(BotCore::with_provider_repos(
            bot_repo,
            provider_repos.provider_repo.clone(),
            provider_repos.provider_credentials.clone(),
            provider_repos.provider_bindings.clone(),
        ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
        let relation: Arc<dyn bcs_service_api::RelationCoreService> =
            Arc::new(RelationCore::memory());
        let cleanup = Arc::new(RecordingChannelBindingCleanup::default());
        let provider_control_plane: Arc<dyn BotControlPlaneCoreService> =
            Arc::new(BotControlPlaneCore::new(
                control_plane_repo,
                provider_repos.provider_repo.clone(),
                provider_repos.provider_bindings.clone(),
            ).with_bot_provider_repo(provider_repos.bot_providers.clone()));
        let (_provider_core, _provider_bot_core, provider_management) =
            build_provider_services_with_webhook_url_guard(
                &provider_repos,
                bot_registry,
                relation,
                None,
                OutboundUrlGuard::allowing_private_networks_for_tests(),
                provider_control_plane.clone(),
                cleanup.clone(),
                Arc::new(NoopBotCatalogCleanupPort),
            );

        let registered = provider_management
            .register_provider(RegisterProviderCommand {
                name: "Provider".to_string(),
                webhook_url: Some("https://provider.example.com/bcs/webhook".to_string()),
                admin_callback_url: None,
                auth_mode: bcs_domain::ProviderAuthMode::StaticBearer,
                created_by: "11111111".to_string(),
                protocol_version: None,
                coordination: None,
            })
            .await
            .expect("register provider");
        let bot = provider_management
            .register_provider_bot(bcs_service_api::RegisterProviderBotCommand {
                webhook_url: None,
                provider_id: registered.provider_id.clone(),
                provider_admin_token: registered.provider_admin_token.clone(),
                name: "Bot".to_string(),
                summary: None,
                owners: vec!["11111111".to_string()],
                provider_bot_ref: "bot-ref-1".to_string(),
                domains: Vec::new(),
                skills: Vec::new(),
                scopes: Vec::new(),
                bot_uuid: None,
                reject_existing_bot_uuid: false,
                connection_mode: bcs_service_api::ProviderBotConnectionMode::Gateway,
            })
            .await
            .expect("register provider bot");

        let outcome = provider_management
            .delete_provider_bot(bcs_service_api::DeleteProviderBotCommand {
                provider_id: registered.provider_id,
                provider_admin_token: registered.provider_admin_token,
                provider_bot_ref: "bot-ref-1".to_string(),
                allow_unbound_owner_suffixed_bot: false,
            })
            .await
            .expect("delete provider bot");

        assert!(outcome.deleted);
        assert_eq!(
            cleanup.deleted_bot_ids.lock().await.as_slice(),
            &[bot.bot_uuid]
        );
    }

    #[tokio::test]
    async fn new_allowing_private_outbound_for_tests_seeds_configured_group_session_secret() {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );
        config.group_session_ws.signing_key_secret =
            "custom-group-session-ws-test-secret".to_string();

        let server = BcsServer::new_allowing_private_outbound_for_tests(config);

        let _ = server.build_router().await.expect(
            "test constructor should seed group-session signing material under configured name",
        );
    }

    #[tokio::test]
    #[serial_test::serial]
    async fn public_constructor_does_not_install_test_group_session_signing_key() {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.gateway_principal.signing_key_env =
            "BCS_TEST_GATEWAY_PRINCIPAL_SIGNING_KEY".to_string();
        config.secret.provider = "noop".to_string();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );

        unsafe {
            std::env::set_var(
                "BCS_TEST_GATEWAY_PRINCIPAL_SIGNING_KEY",
                "test-only-gateway-principal-signing-key",
            );
        }
        let server = BcsServer::new(config);
        unsafe {
            std::env::remove_var("BCS_TEST_GATEWAY_PRINCIPAL_SIGNING_KEY");
        }
        let error = match server.build_router().await {
            Ok(_) => panic!("public constructor must not install the fixed test signing key"),
            Err(error) => error,
        };

        assert!(error.to_string().contains(
            "group_session_ws.signing_key_secret 'bcn-group-session-ws-jwt' is required"
        ));
    }

    #[async_trait]
    impl SessionChannelOutboundPort for RecordingSessionChannelOutbound {
        async fn recover_human_input_requests(&self, run_id: &str, session_id: &str) -> ServiceResult<Vec<String>> {
            if run_id == "failed" { return Err(bcs_service_api::ServiceError::InternalError("recovery read failed".into())); }
            Ok(vec![format!("{run_id}/{session_id}/review")])
        }
        async fn publish_human_input_ready(
            &self,
            event: HumanInputReadyEvent,
        ) -> ServiceResult<SessionChannelDeliveryOutcome> {
            self.events.lock().await.push(event);
            Ok(SessionChannelDeliveryOutcome::Delivered)
        }
    }

    #[tokio::test]
    async fn deferred_session_channel_outbound_is_inert_until_initialized() {
        let (slot, deferred) = deferred_session_channel_outbound();
        assert!(deferred.recover_human_input_requests("run-1", "session-1").await.unwrap().is_empty());
        let event = HumanInputReadyEvent {
            event_id: "event-1".to_string(),
            group_id: "group-1".to_string(),
            session_id: "session-1".to_string(),
            run_id: "run-1".to_string(),
            node_id: "review".to_string(),
            display_name: "Review".to_string(),
            instruction: "Review the draft".to_string(),
            assignee_actor_id: "human-1".to_string(),
            channel_type: "dingtalk".to_string(),
            notification_mode: bcs_domain::HumanInputNotificationMode::DirectAssignee,
            fixed_group_conversation_id: None,
            response_ref: "run-1/review".to_string(),
            upstream_artifacts: Vec::new(),
            judge_outcomes: vec!["approved".to_string()],
            timeout_deadline_ms: Some(60_000),
            loop_context: None,
        };

        assert_eq!(
            deferred
                .publish_human_input_ready(event.clone())
                .await
                .expect("uninitialized outbound"),
            SessionChannelDeliveryOutcome::NotApplicable
        );

        let recording = Arc::new(RecordingSessionChannelOutbound::default());
        assert!(slot.set(recording.clone()).is_ok());
        assert_eq!(deferred.recover_human_input_requests("run-1", "session-1").await.unwrap(), vec!["run-1/session-1/review"]);
        assert!(deferred.recover_human_input_requests("failed", "session-1").await.unwrap_err().to_string().contains("recovery read failed"));
        assert_eq!(
            deferred
                .publish_human_input_ready(event)
                .await
                .expect("initialized outbound"),
            SessionChannelDeliveryOutcome::Delivered
        );
        assert_eq!(recording.events.lock().await.len(), 1);
    }

    async fn response_json(response: Response) -> serde_json::Value {
        let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
        serde_json::from_slice(&body).unwrap()
    }

    async fn read_http_request(socket: &mut tokio::net::TcpStream) -> Vec<u8> {
        let mut request = Vec::new();
        loop {
            let mut chunk = [0; 1024];
            let count = socket.read(&mut chunk).await.unwrap();
            assert!(count > 0, "callback closed before request completed");
            request.extend_from_slice(&chunk[..count]);
            let Some(headers_end) = request.windows(4).position(|window| window == b"\r\n\r\n")
            else {
                continue;
            };
            let headers_end = headers_end + 4;
            let headers = String::from_utf8_lossy(&request[..headers_end]);
            let content_length = headers
                .lines()
                .find_map(|line| {
                    line.split_once(':').and_then(|(name, value)| {
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().unwrap())
                    })
                })
                .unwrap_or(0);
            if request.len() >= headers_end + content_length {
                return request;
            }
        }
    }

    struct AdminRunTestOrganization {
        provider_id: String,
    }

    impl AdminRunTestOrganization {
        fn organization(&self, code: &str) -> bcs_domain::Organization {
            bcs_domain::Organization {
                env: "local".to_string(),
                code: code.to_string(),
                name: "Admin WS Org".to_string(),
                description: None,
                managing_provider_id: self.provider_id.clone(),
                disabled: false,
                created_at: 1,
                updated_at: 1,
            }
        }

        fn member(&self, code: &str, bot_uuid: &str) -> bcs_domain::OrganizationMember {
            bcs_domain::OrganizationMember {
                env: "local".to_string(),
                organization_code: code.to_string(),
                bot_uuid: bot_uuid.to_string(),
                role: None,
                disabled: false,
                created_at: 1,
                updated_at: 1,
            }
        }
    }

    #[async_trait]
    impl OrganizationCoreService for AdminRunTestOrganization {
        async fn create(
            &self,
            _managing_provider_id: &str,
            code: &str,
            _name: &str,
            _description: Option<&str>,
        ) -> bcs_service_api::ServiceResult<bcs_domain::Organization> {
            Ok(self.organization(code))
        }

        async fn get_for_manager(
            &self,
            _managing_provider_id: &str,
            code: &str,
        ) -> bcs_service_api::ServiceResult<bcs_domain::Organization> {
            Ok(self.organization(code))
        }

        async fn list_for_manager(
            &self,
            _managing_provider_id: &str,
            _include_disabled: bool,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::Organization>> {
            Ok(Vec::new())
        }

        async fn update_for_manager(
            &self,
            _managing_provider_id: &str,
            code: &str,
            _name: Option<&str>,
            _description: Option<Option<&str>>,
            _disabled: Option<bool>,
        ) -> bcs_service_api::ServiceResult<bcs_domain::Organization> {
            Ok(self.organization(code))
        }

        async fn put_member(
            &self,
            _managing_provider_id: &str,
            organization_code: &str,
            bot_uuid: &str,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<bcs_domain::OrganizationMember> {
            Ok(self.member(organization_code, bot_uuid))
        }

        async fn delete_member(
            &self,
            _managing_provider_id: &str,
            _organization_code: &str,
            _bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<()> {
            Ok(())
        }

        async fn get_member_for_manager(
            &self,
            _managing_provider_id: &str,
            organization_code: &str,
            bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<Option<bcs_domain::OrganizationMember>> {
            Ok(Some(self.member(organization_code, bot_uuid)))
        }

        async fn list_members_for_manager(
            &self,
            _managing_provider_id: &str,
            _organization_code: &str,
            _include_disabled: bool,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::OrganizationMember>> {
            Ok(Vec::new())
        }

        async fn candidate_bots(
            &self,
            _managing_provider_id: &str,
            _query: bcs_service_api::OrganizationCandidateQuery,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_service_api::OrganizationCandidateBot>>
        {
            Ok(Vec::new())
        }

        async fn require_effective_member(
            &self,
            organization_code: &str,
            bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<bcs_domain::OrganizationMember> {
            Ok(self.member(organization_code, bot_uuid))
        }

        async fn list_effective_members(
            &self,
            _organization_code: &str,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::OrganizationMember>> {
            Ok(Vec::new())
        }

        async fn require_runtime_member(
            &self,
            organization_code: &str,
            bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<bcs_domain::OrganizationMember> {
            Ok(self.member(organization_code, bot_uuid))
        }

        async fn list_runtime_members(
            &self,
            _organization_code: &str,
            _role: Option<&str>,
        ) -> bcs_service_api::ServiceResult<Vec<bcs_domain::OrganizationMember>> {
            Ok(Vec::new())
        }

        async fn authorize_pair(
            &self,
            organization_code: &str,
            sender_bot_uuid: &str,
            target_bot_uuid: &str,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::AuthorizedOrganizationPair> {
            Ok(bcs_service_api::AuthorizedOrganizationPair {
                organization: self.organization(organization_code),
                sender: self.member(organization_code, sender_bot_uuid),
                target: self.member(organization_code, target_bot_uuid),
            })
        }
    }

    struct AdminRunTestA2aRuns;

    #[async_trait]
    impl A2aChatRunService for AdminRunTestA2aRuns {
        async fn start_async_chat(
            &self,
            cmd: bcs_service_api::AsyncA2aChatCommand,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::AsyncA2aChatAccepted> {
            Ok(bcs_service_api::AsyncA2aChatAccepted {
                run_id: cmd.run_id,
                bot_uuid: cmd.target_bot_id,
                session_id: cmd.session_key,
                status: "dispatched".to_string(),
                expires_at_ms: u64::MAX,
            })
        }

        async fn get_run(
            &self,
            cmd: bcs_service_api::ChatRunQueryCommand,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::A2aRunStatus> {
            Ok(bcs_service_api::A2aRunStatus {
                run_id: cmd.run_id,
                status: "running".to_string(),
                response: None,
            })
        }

        async fn cancel_run(
            &self,
            _cmd: bcs_service_api::ChatRunCancelCommand,
        ) -> bcs_service_api::ServiceResult<bcs_service_api::A2aRunStatus> {
            unreachable!("admin run test does not cancel")
        }
    }

    #[test]
    fn judge_llm_provider_selection_uses_public_provider_types() {
        let mut config = BcsConfig::default();
        assert_eq!(
            select_judge_llm_provider(&config).unwrap(),
            JudgeLlmProviderKind::None
        );

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
    #[should_panic(expected = "standalone BCS server cannot use mysql")]
    fn standalone_template_service_rejects_mysql_storage() {
        let mut config = BcsConfig::default();
        config.collaboration.templates.storage_type = CollaborationTemplateStorageKind::Mysql;

        let _service = build_standalone_collaboration_template_service(&config);
    }

    #[test]
    fn configured_missing_channel_provider_fails_startup() {
        let mut config = BcsConfig::default();
        config.channels.enabled = true;
        config.channels.providers.insert(
            "missing-provider".to_string(),
            bcs_config_api::ChannelProviderConfig {
                enabled: true,
                ..Default::default()
            },
        );

        let result = build_configured_channel_providers(
            &config,
            Arc::new(MemoryChannelBindingRepo::new("test")),
        );

        assert!(matches!(
            result,
            Err(crate::BcsError::InvalidConfig(message))
                if message.contains("missing-provider")
        ));
    }

    #[tokio::test]
    async fn chat_run_events_registered_by_http_are_visible_to_frontend_fallback() {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );
        let server = BcsServer::new_allowing_private_outbound_for_tests(config);
        let (tx, mut rx) = tokio::sync::mpsc::channel(1);

        server
            .state
            .run_channels
            .register(
                "http-run".to_string(),
                "bcs-cli:caller:http-run".to_string(),
                tx,
                Some("http-chat-async".to_string()),
                None,
            )
            .await;

        let result = server
            .state
            .services
            .frontend_delivery
            .publish(bcs_service_api::FrontendDeliveryCommand {
                target: bcs_service_api::FrontendDeliveryTarget::Group {
                    group_id: "bcs-cli:caller:http-run".to_string(),
                },
                event_json: r#"{"type":"event","event":"chat"}"#.to_string(),
                delivery_kind: bcs_service_api::FrontendDeliveryKind::WorkbenchEvent,
                run_fallback: Some(bcs_service_api::RunFallbackDelivery {
                    run_id: "bot-generated-run".to_string(),
                    session_id: "bcs-cli:caller:http-run".to_string(),
                    event_json: r#"{"type":"event","event":"chat.event"}"#.to_string(),
                }),
                exclude_conn_id: None,
                visibility_domain: bcs_domain::MessageVisibilityDomain::Chat,
                audience: Some(bcs_domain::MessageAudience::Public),
            })
            .await
            .unwrap();

        assert_eq!(result.delivered, 1);
        assert_eq!(
            rx.recv().await,
            Some(r#"{"type":"event","event":"chat.event"}"#.to_string())
        );
    }

    #[tokio::test]
    async fn bot_ws_dispatch_state_reuses_coordination_dedup_store_for_reconnects() {
        let tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(tmp.path().to_string_lossy().into_owned()),
        );
        let server = BcsServer::new_allowing_private_outbound_for_tests(config);

        let first = bot_ws_dispatch_state(&server.state);
        let second = bot_ws_dispatch_state(&server.state);

        assert!(Arc::ptr_eq(
            &first.coordination_processed,
            &second.coordination_processed
        ));
    }

    #[tokio::test]
    async fn detached_admin_run_observes_websocket_terminal_and_callbacks_once() {
        let callback_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let callback_url = format!(
            "http://{}/admin-terminal",
            callback_listener.local_addr().unwrap()
        );
        let _tmp = tempfile::TempDir::new().expect("temp dir");
        let mut config = BcsConfig::default();
        config.session_files.backend.insert(
            "data_dir".to_string(),
            toml::Value::String(_tmp.path().to_string_lossy().into_owned()),
        );
        config.async_chat_run_timeout_ms = 5_000;
        let server = BcsServer::new_allowing_private_outbound_for_tests(config);

        let provider = server
            .state
            .services
            .provider_management
            .register_provider(RegisterProviderCommand {
                name: "Admin Provider".to_string(),
                webhook_url: Some(callback_url.clone()),
                admin_callback_url: Some(callback_url),
                auth_mode: bcs_domain::ProviderAuthMode::StaticBearer,
                created_by: "admin-owner".to_string(),
                protocol_version: None,
                coordination: None,
            })
            .await
            .unwrap();

        let dispatch_state = bot_ws_dispatch_state(&server.state);
        let (bot_tx, mut bot_rx) = tokio::sync::mpsc::channel(16);
        let mut registered_bot_id = None;
        let connect = BcsFrame::Request(RequestFrame::new(
            "connect-1",
            "bot.connect",
            Some(serde_json::json!({
                "bot_id": "ws-admin-target",
                "protocol_version": 1
            })),
        ));
        bcs_ws::bot::dispatch_frame(
            &dispatch_state,
            &serde_json::to_string(&connect).unwrap(),
            &bot_tx,
            &mut registered_bot_id,
        )
        .await
        .unwrap();
        assert_eq!(registered_bot_id.as_deref(), Some("ws-admin-target"));
        let _connect_response = bot_rx.recv().await.unwrap();

        let mut http_state = crate::http_adapter::build_http_app_state(server.state.clone()).await;
        http_state.services.organization_management = Arc::new(OrganizationManagement::new(
            http_state.services.provider_core.clone(),
            Arc::new(AdminRunTestOrganization {
                provider_id: provider.provider_id.clone(),
            }),
        ));
        http_state.services.a2a_chat_runs = Arc::new(AdminRunTestA2aRuns);
        let app = bcs_http::router::build_router(http_state);

        let create_response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/organizations/admin-ws-org/admin-runs")
                    .header("content-type", "application/json")
                    .header("x-bcn-provider-id", &provider.provider_id)
                    .header(
                        "authorization",
                        format!("Bearer {}", provider.provider_admin_token),
                    )
                    .body(Body::from(
                        serde_json::json!({
                            "target_bot_uuid": "ws-admin-target",
                            "message": {
                                "role": "user",
                                "content": [{"type": "text", "text": "run detached"}]
                            },
                            "detach": true,
                            "run_timeout_ms": 5_000
                        })
                        .to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(create_response.status(), StatusCode::OK);
        let create_body = response_json(create_response).await;
        let run_id = create_body["data"]["run_id"].as_str().unwrap().to_string();

        let terminal = BcsFrame::Event(EventFrame::new(
            "chat.event",
            Some(serde_json::json!({
                "run_id": run_id,
                "bcs_group_id": "",
                "state": "final",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "detached websocket result"}],
                    "timestamp": 1
                }
            })),
            Some(1),
        ));
        bcs_ws::bot::dispatch_frame(
            &dispatch_state,
            &serde_json::to_string(&terminal).unwrap(),
            &bot_tx,
            &mut registered_bot_id,
        )
        .await
        .unwrap();
        bcs_ws::bot::dispatch_frame(
            &dispatch_state,
            &serde_json::to_string(&terminal).unwrap(),
            &bot_tx,
            &mut registered_bot_id,
        )
        .await
        .unwrap();

        let get_response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("GET")
                    .uri(format!("/organizations/admin-ws-org/admin-runs/{run_id}"))
                    .header("x-bcn-provider-id", &provider.provider_id)
                    .header(
                        "authorization",
                        format!("Bearer {}", provider.provider_admin_token),
                    )
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(get_response.status(), StatusCode::OK);
        let get_body = response_json(get_response).await;
        assert_eq!(get_body["data"]["status"], "completed");
        assert_eq!(
            get_body["data"]["message"]["content"][0]["text"],
            "detached websocket result"
        );

        let (mut callback_socket, _) = timeout(Duration::from_secs(2), callback_listener.accept())
            .await
            .unwrap()
            .unwrap();
        let callback_request = read_http_request(&mut callback_socket).await;
        callback_socket
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            .await
            .unwrap();
        let callback_request = String::from_utf8_lossy(&callback_request);
        assert!(callback_request.contains("detached websocket result"));
        assert!(callback_request.contains(&run_id));
        assert!(
            timeout(Duration::from_millis(100), callback_listener.accept())
                .await
                .is_err()
        );
    }
