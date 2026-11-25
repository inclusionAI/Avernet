use std::{
        io::{self, Write},
        sync::{Arc, Mutex as StdMutex},
    };

    use async_trait::async_trait;
    use bcs_domain::{BotDeliveryTarget, RedactedToken};
    use bcs_service_api::{
        CanResolveInteraction, CanResolveInteractionCommand, InteractionFrontendEvent,
        InteractionFrontendPort, InteractionKey, InteractionKind, InteractionProviderAck,
        InteractionProviderCommand, InteractionProviderPort, InteractionService,
        InteractionServiceError, InteractionStatus, InteractionStorePort,
        ProviderInteractionRequestedCommand, ProviderInteractionResolvedCommand,
        ResolveInteractionCommand, ServiceError, ServiceResult,
    };
    use serde_json::json;
    use tokio::sync::{Mutex, Notify};

    use super::InteractionManagement;
    use crate::MemoryInteractionStore;

    struct FixedAuthorization(bool);

    #[derive(Clone, Default)]
    struct SharedLogBuffer(Arc<StdMutex<Vec<u8>>>);

    struct SharedLogWriter(Arc<StdMutex<Vec<u8>>>);

    impl Write for SharedLogWriter {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            self.0
                .lock()
                .expect("log buffer lock")
                .extend_from_slice(buffer);
            Ok(buffer.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedLogBuffer {
        type Writer = SharedLogWriter;

        fn make_writer(&'a self) -> Self::Writer {
            SharedLogWriter(self.0.clone())
        }
    }

    // Install a process-wide tracing subscriber ONCE that writes into a
    // shared buffer. `warn!` consults the process-global callsite Interest
    // cache: with per-test scoped subscribers, the first test to evaluate a
    // callsite without any subscriber installed poisons it as
    // Interest::never for every later test, so scoped-subscriber log
    // assertions flake under parallel test runs. One global subscriber keeps
    // every callsite's cached interest consistent for the whole process.
    static SHARED_LOGS: std::sync::OnceLock<SharedLogBuffer> = std::sync::OnceLock::new();
    static GLOBAL_LOG_SUBSCRIBER: std::sync::OnceLock<()> = std::sync::OnceLock::new();

    fn capture_logs() -> SharedLogBuffer {
        let installed = SHARED_LOGS.set(SharedLogBuffer::default());
        let shared = SHARED_LOGS.get().expect("shared log buffer");
        if installed.is_ok() {
            let _ = GLOBAL_LOG_SUBSCRIBER.set(());
            // set_global_default alone does NOT recompute the global callsite
            // Interest cache or the dynamic max level, and a callsite first
            // evaluated on a plain test thread (no scoped dispatch installed)
            // registers against NO_SUBSCRIBER and caches Interest::never for
            // the whole process. Registering two leaked dispatches flips the
            // tracing-core dispatcher registry into direct-enumeration mode,
            // so every subsequent Interest rebuild/registration evaluates
            // against this always-accepting subscriber instead of the
            // registering thread's (missing) default. The explicit rebuild
            // un-poisons callsites cached before this first call.
            let _ = tracing::subscriber::set_global_default(
                tracing_subscriber::fmt()
                    .with_ansi(false)
                    .with_level(false)
                    .with_target(false)
                    .with_writer(shared.clone())
                    .finish(),
            );
            std::mem::forget(tracing::Dispatch::new(
                tracing_subscriber::fmt()
                    .with_ansi(false)
                    .with_level(false)
                    .with_target(false)
                    .with_writer(shared.clone())
                    .finish(),
            ));
            std::mem::forget(tracing::Dispatch::new(
                tracing_subscriber::fmt()
                    .with_ansi(false)
                    .with_level(false)
                    .with_target(false)
                    .with_writer(shared.clone())
                    .finish(),
            ));
            tracing::callsite::rebuild_interest_cache();
        }
        shared.clone()
    }

    #[async_trait]
    impl CanResolveInteraction for FixedAuthorization {
        async fn can_resolve(&self, _command: CanResolveInteractionCommand) -> ServiceResult<bool> {
            Ok(self.0)
        }
    }

    #[derive(Default)]
    struct RecordingProvider {
        calls: Mutex<Vec<InteractionProviderCommand>>,
        response: Mutex<Option<ServiceResult<InteractionProviderAck>>>,
    }

    struct ResolvingBeforeAckProvider {
        store: Arc<MemoryInteractionStore>,
        terminal: InteractionStatus,
        response: Mutex<Option<ServiceResult<InteractionProviderAck>>>,
    }

    #[derive(Default)]
    struct BlockingProvider {
        calls: Mutex<Vec<InteractionProviderCommand>>,
        entered: Notify,
        release: Notify,
    }

    #[async_trait]
    impl InteractionProviderPort for BlockingProvider {
        async fn resolve_interaction(
            &self,
            command: InteractionProviderCommand,
        ) -> ServiceResult<InteractionProviderAck> {
            self.calls.lock().await.push(command);
            self.entered.notify_one();
            self.release.notified().await;
            Ok(InteractionProviderAck {
                ok: true,
                retryable: None,
                error: None,
            })
        }
    }

    #[async_trait]
    impl InteractionProviderPort for ResolvingBeforeAckProvider {
        async fn resolve_interaction(
            &self,
            command: InteractionProviderCommand,
        ) -> ServiceResult<InteractionProviderAck> {
            let key = InteractionKey {
                bcs_run_id: command.bcs_run_id,
                interaction_id: command.interaction_id,
            };
            match self.terminal {
                InteractionStatus::Resolved => {
                    self.store.mark_resolved(&key, super::now_ms()).await?;
                }
                InteractionStatus::Invalidated => {
                    self.store
                        .invalidate_run(&key.bcs_run_id, "run_terminal", super::now_ms())
                        .await?;
                }
                _ => {}
            }
            self.response.lock().await.take().unwrap_or_else(|| {
                Ok(InteractionProviderAck {
                    ok: true,
                    retryable: None,
                    error: None,
                })
            })
        }
    }

    #[async_trait]
    impl InteractionProviderPort for RecordingProvider {
        async fn resolve_interaction(
            &self,
            command: InteractionProviderCommand,
        ) -> ServiceResult<InteractionProviderAck> {
            self.calls.lock().await.push(command);
            self.response.lock().await.take().unwrap_or_else(|| {
                Ok(InteractionProviderAck {
                    ok: true,
                    retryable: None,
                    error: None,
                })
            })
        }
    }

    #[derive(Default)]
    struct RecordingFrontend {
        calls: Mutex<Vec<InteractionFrontendEvent>>,
    }

    #[async_trait]
    impl InteractionFrontendPort for RecordingFrontend {
        async fn publish_interaction(&self, event: InteractionFrontendEvent) -> ServiceResult<()> {
            self.calls.lock().await.push(event);
            Ok(())
        }
    }

    fn target() -> BotDeliveryTarget {
        BotDeliveryTarget::HttpProvider {
            bot_id: "bot-1".to_string(),
            provider_id: "provider-1".to_string(),
            provider_bot_ref: "ref-1".to_string(),
            webhook_url: "https://provider.example/webhook".to_string(),
            bcs_to_provider_token: RedactedToken::new("secret"),
            protocol_version: "2.0".to_string(),
        }
    }

    fn requested(interaction_id: &str) -> ProviderInteractionRequestedCommand {
        ProviderInteractionRequestedCommand {
            bcs_run_id: "bcs-run-1".to_string(),
            provider_run_id: "provider-run-1".to_string(),
            interaction_id: interaction_id.to_string(),
            kind: InteractionKind::Exec,
            bcs_session_id: "session-1".to_string(),
            group_id: "group-1".to_string(),
            bot_id: "bot-1".to_string(),
            run_deadline_ms: u64::MAX,
            provider_target: target(),
            provider_bypass_headers: vec![("x-trace".to_string(), "trace-1".to_string())],
            payload: json!({
                "runId":"provider-run-1",
                "seq":7,
                "phase":"requested",
                "interactionId":interaction_id,
                "kind":"exec",
                "command":"deploy",
                "options":[
                    {"decision":"allow_once","label":"Allow once"},
                    {"decision":"deny","label":"Deny"}
                ]
            }),
            received_at_ms: 100,
        }
    }

    fn resolve(interaction_id: &str, idempotency_key: &str) -> ResolveInteractionCommand {
        ResolveInteractionCommand {
            bcs_run_id: "bcs-run-1".to_string(),
            interaction_id: interaction_id.to_string(),
            idempotency_key: idempotency_key.to_string(),
            resolver_actor_id: "human_1".to_string(),
            expected_bcs_session_id: None,
            expected_group_id: None,
            resolution: json!({"decision":"allow_once"}),
        }
    }

    fn service(
        authorized: bool,
    ) -> (
        InteractionManagement,
        Arc<MemoryInteractionStore>,
        Arc<RecordingProvider>,
        Arc<RecordingFrontend>,
    ) {
        let store = Arc::new(MemoryInteractionStore::new());
        let provider = Arc::new(RecordingProvider::default());
        let frontend = Arc::new(RecordingFrontend::default());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(authorized)),
            provider.clone(),
            frontend.clone(),
            120_000,
        );
        (service, store, provider, frontend)
    }

    #[tokio::test]
    async fn requested_is_stored_before_one_frontend_publication_and_replayable() {
        let (service, _store, _provider, frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let calls = frontend.calls.lock().await;
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].bcs_run_id, "bcs-run-1");
        assert_eq!(calls[0].bcs_session_id, "session-1");
        assert_eq!(calls[0].payload["interactionId"], "interaction-1");
        drop(calls);

        let replay = service.list_pending("session-1").await.unwrap();
        assert_eq!(replay.len(), 1);
        assert_eq!(replay[0].payload["interactionId"], "interaction-1");
    }

    #[tokio::test]
    async fn exec_requested_without_command_is_stored_published_and_replayable() {
        let (service, store, _provider, frontend) = service(true);
        let mut request = requested("interaction-without-command");
        request.payload.as_object_mut().unwrap().remove("command");

        service.on_provider_requested(request).await.unwrap();

        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-without-command".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert!(stored.requested_payload.get("command").is_none());

        let calls = frontend.calls.lock().await;
        assert_eq!(calls.len(), 1);
        assert!(calls[0].payload.get("command").is_none());
        drop(calls);

        let replay = service.list_pending("session-1").await.unwrap();
        assert_eq!(replay.len(), 1);
        assert!(replay[0].payload.get("command").is_none());
    }

    #[tokio::test]
    async fn resolve_rechecks_authorization_and_never_calls_provider_when_denied() {
        let (service, _store, provider, _frontend) = service(false);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let error = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap_err();
        assert_eq!(error, InteractionServiceError::Unauthorized);
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn resolve_enforces_session_bound_connection_scope_before_provider_call() {
        let (service, _store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        let mut command = resolve("interaction-1", "idem-1");
        command.expected_bcs_session_id = Some("session-other".to_string());
        command.expected_group_id = Some("group-1".to_string());

        let error = service.resolve(command).await.unwrap_err();

        assert_eq!(error, InteractionServiceError::Unauthorized);
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn accepted_retry_is_suppressed_but_different_resolution_is_rejected() {
        let (service, store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let accepted = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap();
        assert_eq!(accepted.status, InteractionStatus::Accepted);
        let repeated = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap();
        assert_eq!(repeated.status, InteractionStatus::Accepted);
        assert_eq!(provider.calls.lock().await.len(), 1);

        let mut different = resolve("interaction-1", "idem-2");
        different.resolution = json!({"decision":"deny"});
        let error = service.resolve(different).await.unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: false,
                status: InteractionStatus::Accepted,
                ..
            }
        ));

        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(stored.status, InteractionStatus::Accepted);
    }

    #[tokio::test]
    async fn accepted_retry_after_authoritative_resolved_is_still_success() {
        let (service, _store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap();
        service
            .on_provider_resolved(ProviderInteractionResolvedCommand {
                bcs_run_id: "bcs-run-1".to_string(),
                provider_run_id: "provider-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
                kind: InteractionKind::Exec,
                payload: json!({
                    "runId":"provider-run-1",
                    "seq":8,
                    "phase":"resolved",
                    "interactionId":"interaction-1",
                    "kind":"exec",
                    "decision":"allow_once"
                }),
                received_at_ms: super::now_ms(),
            })
            .await
            .unwrap();

        let repeated = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("same acknowledged resolution remains idempotent after SSE resolved");

        assert_eq!(repeated.status, InteractionStatus::Resolved);
        assert_eq!(provider.calls.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn resolved_sse_racing_before_provider_ack_is_reported_as_success() {
        let store = Arc::new(MemoryInteractionStore::new());
        let frontend = Arc::new(RecordingFrontend::default());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(true)),
            Arc::new(ResolvingBeforeAckProvider {
                store: store.clone(),
                terminal: InteractionStatus::Resolved,
                response: Mutex::new(None),
            }),
            frontend,
            120_000,
        );
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let result = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("Provider ACK after authoritative SSE resolved is still success");

        assert!(result.accepted);
        assert_eq!(result.status, InteractionStatus::Resolved);

        let repeated = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("ACK metadata survives an SSE-resolved race for later retries");
        assert_eq!(repeated.status, InteractionStatus::Resolved);
    }

    #[tokio::test]
    async fn resolved_sse_racing_before_transport_error_is_reported_as_success() {
        let store = Arc::new(MemoryInteractionStore::new());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(true)),
            Arc::new(ResolvingBeforeAckProvider {
                store: store.clone(),
                terminal: InteractionStatus::Resolved,
                response: Mutex::new(Some(Err(ServiceError::InternalError(
                    "ack lost".to_string(),
                )))),
            }),
            Arc::new(RecordingFrontend::default()),
            120_000,
        );
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let result = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .expect("authoritative resolved SSE wins over transport failure");

        assert_eq!(result.status, InteractionStatus::Resolved);
    }

    #[tokio::test]
    async fn run_invalidation_racing_before_retryable_ack_is_non_retryable() {
        let store = Arc::new(MemoryInteractionStore::new());
        let service = InteractionManagement::new(
            store.clone(),
            Arc::new(FixedAuthorization(true)),
            Arc::new(ResolvingBeforeAckProvider {
                store: store.clone(),
                terminal: InteractionStatus::Invalidated,
                response: Mutex::new(Some(Ok(InteractionProviderAck {
                    ok: false,
                    retryable: Some(true),
                    error: Some("temporary".to_string()),
                }))),
            }),
            Arc::new(RecordingFrontend::default()),
            120_000,
        );
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let error = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap_err();

        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: false,
                status: InteractionStatus::Invalidated,
                ..
            }
        ));
    }

    #[tokio::test]
    async fn provider_failures_follow_retryable_default_and_explicit_false() {
        let (service, store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("retryable"))
            .await
            .unwrap();
        *provider.response.lock().await = Some(Ok(InteractionProviderAck {
            ok: false,
            retryable: None,
            error: Some("temporary".to_string()),
        }));
        let error = service
            .resolve(resolve("retryable", "idem-1"))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: true,
                status: InteractionStatus::Pending,
                ..
            }
        ));

        service
            .on_provider_requested(requested("terminal"))
            .await
            .unwrap();
        *provider.response.lock().await = Some(Ok(InteractionProviderAck {
            ok: false,
            retryable: Some(false),
            error: Some("engine rejected".to_string()),
        }));
        let error = service
            .resolve(resolve("terminal", "idem-2"))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: false,
                status: InteractionStatus::Invalidated,
                ..
            }
        ));
        let terminal = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "terminal".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(terminal.status, InteractionStatus::Invalidated);
    }

    #[tokio::test]
    async fn concurrent_humans_share_one_per_interaction_delivery_guard() {
        let store = Arc::new(MemoryInteractionStore::new());
        let provider = Arc::new(BlockingProvider::default());
        let service = Arc::new(InteractionManagement::new(
            store,
            Arc::new(FixedAuthorization(true)),
            provider.clone(),
            Arc::new(RecordingFrontend::default()),
            120_000,
        ));
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let first_service = service.clone();
        let first = tokio::spawn(async move {
            first_service
                .resolve(resolve("interaction-1", "idem-first"))
                .await
        });
        provider.entered.notified().await;

        let mut second = resolve("interaction-1", "idem-second");
        second.resolution = json!({"decision":"deny"});
        let second_error = service.resolve(second).await.unwrap_err();
        assert!(matches!(
            second_error,
            InteractionServiceError::ResolveFailed {
                retryable: true,
                status: InteractionStatus::Pending,
                ..
            }
        ));
        assert_eq!(provider.calls.lock().await.len(), 1);

        provider.release.notify_one();
        assert!(first.await.unwrap().unwrap().accepted);
    }

    #[tokio::test]
    async fn provider_resolved_is_authoritative_and_publishes_completion() {
        let (service, store, _provider, frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        service
            .on_provider_resolved(ProviderInteractionResolvedCommand {
                bcs_run_id: "bcs-run-1".to_string(),
                provider_run_id: "provider-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
                kind: InteractionKind::Exec,
                payload: json!({
                    "runId":"provider-run-1",
                    "seq":8,
                    "phase":"resolved",
                    "interactionId":"interaction-1",
                    "kind":"exec",
                    "decision":"allow_once"
                }),
                received_at_ms: 300,
            })
            .await
            .unwrap();

        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(stored.status, InteractionStatus::Resolved);
        assert_eq!(frontend.calls.lock().await.len(), 2);
    }

    #[tokio::test]
    async fn provider_resolved_exec_must_echo_an_offered_decision() {
        let (service, store, _provider, frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();

        let error = service
            .on_provider_resolved(ProviderInteractionResolvedCommand {
                bcs_run_id: "bcs-run-1".to_string(),
                provider_run_id: "provider-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
                kind: InteractionKind::Exec,
                payload: json!({
                    "runId":"provider-run-1",
                    "seq":8,
                    "phase":"resolved",
                    "interactionId":"interaction-1",
                    "kind":"exec",
                    "decision":"allow_persistent"
                }),
                received_at_ms: 300,
            })
            .await
            .unwrap_err();

        assert!(error.to_string().contains("was not offered"));
        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert_eq!(stored.status, InteractionStatus::Pending);
        assert_eq!(frontend.calls.lock().await.len(), 1);
    }

    #[tokio::test]
    async fn transport_error_stays_pending_and_is_retryable() {
        let (service, store, provider, _frontend) = service(true);
        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        *provider.response.lock().await =
            Some(Err(ServiceError::InternalError("timeout".to_string())));

        let error = service
            .resolve(resolve("interaction-1", "idem-1"))
            .await
            .unwrap_err();
        assert!(matches!(
            error,
            InteractionServiceError::ResolveFailed {
                retryable: true,
                status: InteractionStatus::Pending,
                ..
            }
        ));
        let stored = store
            .get(&InteractionKey {
                bcs_run_id: "bcs-run-1".to_string(),
                interaction_id: "interaction-1".to_string(),
            })
            .await
            .unwrap()
            .unwrap();
        assert!(!stored.in_flight);
    }

    #[tokio::test]
    async fn rejects_invalid_requested_shape_and_unoffered_exec_decision() {
        let (service, _store, provider, _frontend) = service(true);
        for (index, command) in [json!(null), json!(""), json!("   "), json!(42)]
            .into_iter()
            .enumerate()
        {
            let mut invalid_command = requested(&format!("invalid-command-{index}"));
            invalid_command.payload["command"] = command;
            assert!(
                service
                    .on_provider_requested(invalid_command)
                    .await
                    .is_err()
            );
        }

        let mut invalid = requested("invalid");
        invalid.payload = json!({
            "phase":"requested",
            "interactionId":"invalid",
            "kind":"exec",
            "command":"deploy",
            "options":[]
        });
        assert!(service.on_provider_requested(invalid).await.is_err());

        service
            .on_provider_requested(requested("interaction-1"))
            .await
            .unwrap();
        let mut unoffered = resolve("interaction-1", "idem-1");
        unoffered.resolution = json!({"decision":"always_allow"});
        assert!(matches!(
            service.resolve(unoffered).await,
            Err(InteractionServiceError::InvalidRequest(_))
        ));
        assert!(provider.calls.lock().await.is_empty());
    }

    #[tokio::test]
    async fn websocket_provider_targets_are_accepted_but_require_a_bot_id() {
        let (service, _store, provider, _frontend) = service(true);

        // A WebSocket target with a bot_id routes the downstream resolve via
        // the Bot WebSocket registry — accepted like an HTTP Provider target.
        let mut websocket = requested("interaction-ws");
        websocket.provider_target = BotDeliveryTarget::WebSocket {
            bot_id: "bot-ws".to_string(),
        };
        service
            .on_provider_requested(websocket)
            .await
            .expect("WebSocket targets are deliverable through the Bot WebSocket registry");

        // An empty bot_id cannot route the downstream resolve.
        let mut anonymous = requested("interaction-ws-anonymous");
        anonymous.provider_target = BotDeliveryTarget::WebSocket {
            bot_id: "  ".to_string(),
        };
        let error = service
            .on_provider_requested(anonymous)
            .await
            .expect_err("a WebSocket target without a bot id cannot be resolved downstream");
        assert!(matches!(error, ServiceError::InvalidOperation { .. }));
        assert!(provider.calls.lock().await.is_empty());
    }


#[path = "management_ask_user_tests.rs"]
mod ask_user;
