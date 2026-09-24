//! server implementation.
use super::*;
impl BcsServer {
pub(super) fn build_full_oauth_route_state(&self) -> Option<Arc<bcs_http::oauth::OAuthRouteState>> {
        let auth_chain = Arc::clone(&self.state.auth_chain);
        // `auth_config.oauth` is the resolved form: present only when a
        // non-empty jwt_secret was configured (I6 gate lives in resolve).
        if let (Some(resolved), Some(raw)) = (
            self.state.auth_config.oauth.as_ref(),
            self.config.auth.oauth.as_ref(),
        ) {
            if !resolved.jwt_secret.is_empty() {
                let base = resolved.base_url.trim();
                if base.starts_with("http://") || base.starts_with("https://") {
                    let mut providers: std::collections::HashMap<
                        String,
                        Arc<dyn bcs_auth_api::OAuthProvider>,
                    > = std::collections::HashMap::new();

                    // Build every configured provider instance via the
                    // composition-root factory. A misconfigured provider
                    // (unknown kind / empty client_id) is an operator error:
                    // fail fast at startup rather than silently dropping it and
                    // surfacing a runtime 404.
                    for (name, cfg) in &raw.providers {
                        match crate::auth_wiring::build_oauth_provider(name, cfg) {
                            Ok(provider) => {
                                providers.insert(name.clone(), provider);
                            }
                            Err(e) => {
                                panic!("Invalid OAuth provider configuration: {e}");
                            }
                        }
                    }

                    if !providers.is_empty() {
                        if let Some(user_port) = self.state.user_identity_port.clone() {
                            let route_state = Arc::new(bcs_http::oauth::OAuthRouteState::new(
                                &resolved.jwt_secret,
                                user_port,
                                providers,
                                resolved.clone(),
                                Some(auth_chain),
                            ));

                            info!(
                                providers = ?route_state.providers.keys().collect::<Vec<_>>(),
                                cookie_secure = resolved.cookie_secure,
                                env = %resolved.env,
                                "Mounting OAuth routes"
                            );
                            return Some(route_state);
                        }
                    } else {
                        warn!(
                            "[auth.oauth] present but no OAuth providers configured; \
                             mounting identity-only /auth/user"
                        );
                    }
                } else {
                    warn!(
                        base_url = %resolved.base_url,
                        "[auth.oauth] base_url must be an http(s) URL"
                    );
                }
            } else {
                warn!("[auth.oauth] jwt_secret is empty");
            }
        }

        None
    }

pub(super) fn openapi_auth_public_base_url(&self) -> Option<String> {
        self.state
            .auth_config
            .oauth
            .as_ref()
            .map(|oauth| format!("{}/openapi/v1/auth", oauth.base_url.trim_end_matches('/')))
    }

/// Build the `/auth/*` router.
    ///
    /// Two mounting paths:
    /// 1. **Full OAuth** — when `[auth.oauth]` is configured with a non-empty
    ///    `jwt_secret`, an `http(s)` `base_url`, and at least one provider:
    ///    mounts the complete OAuth protocol routes with the auth chain
    ///    injected as the `/auth/user` fallback.
    /// 2. **Identity-only** — when no usable OAuth config exists but an auth
    ///    chain (e.g. the `local` mock plugin) is present: mounts just
    ///    `GET /auth/user`, resolved solely via the chain. This lets deployments
    ///    use `/auth/user` without configuring any OAuth provider.
    ///
    /// `jwt_secret` comes from the resolved `auth_config` (see
    /// `auth_wiring::resolve_auth_config`).
    pub(super) fn build_auth_router(
        &self,
        oauth_state: Option<Arc<bcs_http::oauth::OAuthRouteState>>,
    ) -> Option<Router> {
        if let Some(route_state) = oauth_state {
            info!("Mounting OAuth /auth/* routes");
            return Some(bcs_http::oauth::routes(route_state));
        }

        // Identity-only (chain-backed, no OAuth).
        if let Some(user_port) = self.state.user_identity_port.clone() {
            let route_state = Arc::new(bcs_http::oauth::OAuthRouteState::new_chain_only(
                user_port,
                Arc::clone(&self.state.auth_chain),
            ));
            info!("Mounting identity-only /auth/user (no OAuth providers configured)");
            return Some(bcs_http::oauth::identity_routes(route_state));
        }

        None
    }

/// Build the Axum router.
    pub(super) async fn build_router(&self) -> crate::Result<Router> {
        let api_router = bcs_http::router::build_router(
            crate::http_adapter::build_http_app_state(Arc::clone(&self.state)).await,
        );
        let group_session_connections = build_group_session_connection_service(
            self.state.openapi_v1.session_service.clone(),
            &self.config.group_session_ws,
            self.state.group_session_secret_access.clone(),
        )
        .await?;
        let group_session_websocket_router = bcs_ws::web::group_session_websocket_router(
            group_session_connections.clone(),
            web_ws_dispatch_state(&self.state, Some(group_session_connections.clone())),
            ws_lifecycle_hook(&self.state),
        );
        let oauth_state = self.build_full_oauth_route_state();
        let mut openapi_v1 = self.state.openapi_v1.clone()
            .with_bot_self_service(Arc::new(bcs_app_bot::BotSelfServiceImpl::new(
                crate::agent_identity::build_agent_identity_port()?,
                self.state.services.registry.clone(),
            )));
        if let (Some(auth_service), Some(public_base_url)) =
            (oauth_state.clone(), self.openapi_auth_public_base_url())
        {
            openapi_v1 = openapi_v1.with_auth_service(auth_service, public_base_url);
        }

        let mut router = Router::new()
            // WebSocket endpoint for frontend clients (via gateway)
            .route(bcs_ws::web::FRONTEND_WS_ENDPOINT, get(ws_upgrade_handler))
            // WebSocket for bot connections
            .route(bcs_ws::bot::BOT_WS_ENDPOINT, get(bot_ws_handler));

        if let Some(metrics) = &self.state.metrics {
            router = router.route(&metrics.endpoint_path, get(metrics_handler));
        }

        let mut router = router
            .with_state(Arc::clone(&self.state))
            .merge(api_router)
            .merge(bcs_api_http::router(openapi_v1))
            .merge(bcs_api_http::group_session_connection_router(
                group_session_connections,
                self.state.gateway_principal_verifier.clone(),
            ))
            .merge(group_session_websocket_router);

        if let Some(oauth_router) = self.build_auth_router(oauth_state) {
            router = router.merge(oauth_router);
        }

        let allowed_origins = Arc::new(
            self.config
                .cors
                .allowed_origins
                .iter()
                .cloned()
                .collect::<std::collections::HashSet<_>>(),
        );

        Ok(router
            .layer(middleware::from_fn(debug_middleware))
            .layer(CatchPanicLayer::custom(
                |_: Box<dyn std::any::Any + Send>| {
                    let body = serde_json::json!({
                        "error": "Internal server error",
                        "status": 500
                    });
                    axum::response::Response::builder()
                        .status(axum::http::StatusCode::INTERNAL_SERVER_ERROR)
                        .header("content-type", "application/json")
                        .body(axum::body::Body::from(body.to_string()))
                        .unwrap()
                },
            ))
            // Keep metrics outside panic handling so handler panics are
            // recorded as the generated 500 response, not as cancellations.
            .layer(middleware::from_fn_with_state(
                Arc::clone(&self.state),
                http_metrics_middleware,
            ))
            .layer(middleware::from_fn(bcs_http::gateway_trace::observe_request))
            .layer(
                TraceLayer::new_for_http()
                    .make_span_with(bcs_http::gateway_trace::BcnMakeSpan)
                    .on_response(bcs_http::gateway_trace::BcnOnResponse),
            )
            .layer(
                CorsLayer::new()
                    .allow_origin(AllowOrigin::predicate(move |origin, _| {
                        origin
                            .to_str()
                            .is_ok_and(|origin| allowed_origins.contains(origin))
                    }))
                    .allow_methods(AllowMethods::mirror_request())
                    .allow_headers(AllowHeaders::mirror_request())
                    .allow_credentials(true),
            ))
    }

pub(super) async fn initialize_lifecycle(&self) -> Result<()> {
        self.state
            .lifecycle
            .lock()
            .await
            .initialize_all()
            .await
            .map_err(|error| {
                crate::BcsError::InvalidConfig(format!(
                    "service lifecycle initialize failed: {error}"
                ))
            })
    }

pub(super) fn spawn_state_machine_timeout_scanner(&self) -> tokio::task::JoinHandle<()> {
        crate::state_machine_timeout_scanner::spawn(
            self.state.leader_election.clone(),
            self.state.services.collaboration_runtime.clone(),
            crate::state_machine_timeout_scanner::DEFAULT_SCAN_INTERVAL,
            crate::state_machine_timeout_scanner::DEFAULT_BATCH_SIZE,
            crate::state_machine_timeout_scanner::DEFAULT_TIMEOUT_GRACE_MS,
        )
    }

pub(super) fn spawn_state_machine_progression_scanner(&self) -> crate::state_machine_progression_scanner::ProgressionRecoveryTask {
        tracing::info!(loop_execution_enabled = self.config.collaboration.loop_execution_enabled,
            "State Machine progression recovery started");
        crate::state_machine_progression_scanner::spawn(
            self.state.leader_election.clone(),
            self.state.services.collaboration_runtime.clone(),
        )
    }

pub(super) fn spawn_callback_recovery_scanner(&self) -> tokio::task::JoinHandle<()> {
        crate::callback_recovery_scanner::spawn(
            self.state.leader_election.clone(),
            self.state.services.session_management.clone(),
            self.state.services.group.clone(),
            crate::callback_recovery_scanner::DEFAULT_SCAN_INTERVAL,
            crate::callback_recovery_scanner::DEFAULT_BATCH_SIZE,
            self.state.outbound_url_guard.clone(),
        )
    }

/// Run the server with graceful shutdown support.
    pub async fn run(self) -> Result<()> {
        // Also stop durable workers if address parsing, lifecycle setup or bind
        // fails before the normal graceful-shutdown path is installed.
        let _delivery_shutdown_guard = crate::message_delivery_wiring::StartupGuard(
            Some(self.state.services.message_flow.clone()),
        );
        let addr: SocketAddr = format!("{}:{}", self.config.bind, self.config.port)
            .parse()
            .map_err(|e| crate::BcsError::InvalidConfig(format!("Invalid address: {}", e)))?;

        self.initialize_lifecycle().await?;
        let ws_leadership = crate::ws_leadership::start(self.state.clone()).await;
        let _state_machine_timeout_handle = self.spawn_state_machine_timeout_scanner();
        let state_machine_progression_handle = Arc::new(Mutex::new(self.spawn_state_machine_progression_scanner()));
        let _callback_recovery_handle = self.spawn_callback_recovery_scanner();

        // Spawn async chat-run TTL cleanup loop.
        {
            let a2a_chat = self.state.services.a2a_chat.clone();
            let bot_run_context = self.state.services.bot_run_context.clone();
            let provider_bot_events = self.state.services.provider_bot_events.clone();
            let retention_ms = self.config.async_chat_run_retention_ms;
            tokio::spawn(async move {
                let mut ticker = tokio::time::interval(std::time::Duration::from_secs(10));
                ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                loop {
                    ticker.tick().await;
                    let now_ms = std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis() as u64;
                    match a2a_chat.cleanup_expired(now_ms, retention_ms).await {
                        Ok((expired, dropped)) => {
                            if !expired.is_empty() || !dropped.is_empty() {
                                info!(
                                    expired = expired.len(),
                                    dropped = dropped.len(),
                                    "chat_run: cleanup_expired"
                                );
                            }
                        }
                        Err(err) => {
                            warn!(error = %err, "chat_run: cleanup_expired failed");
                        }
                    }
                    let removed_contexts =
                        bot_run_context.cleanup_expired(now_ms, retention_ms).await;
                    if removed_contexts > 0 {
                        info!(
                            removed = removed_contexts,
                            "bot_run_context: cleanup_expired"
                        );
                    }
                    let removed_provider_events = provider_bot_events.cleanup_expired(now_ms).await;
                    if removed_provider_events > 0 {
                        info!(
                            removed = removed_provider_events,
                            "provider_event: cleanup_expired"
                        );
                    }
                }
            });
        }

        let app = self.build_router().await?;

        info!(
            bind = %self.config.bind,
            port = self.config.port,
            bots_base_dir = %self.config.bots_base_dir.display(),
            "Bot Coordination Service starting"
        );

        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .map_err(crate::BcsError::IoError)?;

        let shutdown_lifecycle = self.state.lifecycle.clone();
        let final_lifecycle = self.state.lifecycle.clone();
        let shutdown_metrics = self.state.metrics.clone();
        let final_metrics = self.state.metrics.clone();
        let shutdown_message_flow = self.state.services.message_flow.clone();
        let final_message_flow = self.state.services.message_flow.clone();
        // Axum spawns the signal future; it must not keep recovery alive if
        // the server future itself is cancelled before receiving a signal.
        let shutdown_progression = Arc::downgrade(&state_machine_progression_handle);
        let shutdown_ws = ws_leadership.stop_handle();

        let serve_result = axum::serve(
            listener,
            app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
        )
            .with_graceful_shutdown(async move {
                use tokio::signal::unix::{signal, SignalKind};

                // Wait for shutdown signal (Ctrl+C or SIGTERM from kill)
                let mut sigterm = signal(SignalKind::terminate()).ok();
                let mut sigint = signal(SignalKind::interrupt()).ok();

                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {}
                    _ = async { if let Some(ref mut s) = sigterm { s.recv().await } else { std::future::pending().await } } => {}
                    _ = async { if let Some(ref mut s) = sigint { s.recv().await } else { std::future::pending().await } } => {}
                }

                info!("Shutdown signal received, gracefully shutting down...");
                shutdown_ws.cancel();

                if let Some(progression) = shutdown_progression.upgrade() {
                    progression.lock().await.shutdown().await;
                }

                if let Err(error) = shutdown_message_flow.shutdown_managed_delivery().await {
                    warn!(%error, "message delivery shutdown failed");
                }

                if let Err(error) = shutdown_lifecycle.lock().await.shutdown_all().await {
                    warn!(error = %error, "service lifecycle shutdown failed");
                }
                if let Some(metrics) = shutdown_metrics {
                    metrics.shutdown().await;
                }
            })
            .await
            .map_err(|e| crate::BcsError::InvalidConfig(e.to_string()));

        state_machine_progression_handle.lock().await.shutdown().await;
        if let Err(error) = final_message_flow.shutdown_managed_delivery().await {
            warn!(%error, "message delivery final shutdown failed");
        }
        if let Err(error) = final_lifecycle.lock().await.shutdown_all().await {
            warn!(error = %error, "service lifecycle shutdown failed");
        }
        if let Some(metrics) = final_metrics {
            metrics.shutdown().await;
        }

        info!("Bot Coordination Service stopped");
        serve_result
    }

/// Run the server on a random port and return the bound address.
    /// This is useful for integration tests.
    #[cfg(any(test, feature = "test-utils"))]
    pub async fn run_on_random_port(
        self,
    ) -> Result<(std::net::SocketAddr, tokio::task::JoinHandle<Result<()>>)> {
        let addr: SocketAddr = format!("{}:0", self.config.bind)
            .parse()
            .map_err(|e| crate::BcsError::InvalidConfig(format!("Invalid address: {}", e)))?;

        self.initialize_lifecycle().await?;
        let ws_leadership = crate::ws_leadership::start(self.state.clone()).await;
        let _state_machine_timeout_handle = self.spawn_state_machine_timeout_scanner();
        let state_machine_progression_handle = self.spawn_state_machine_progression_scanner();
        let _callback_recovery_handle = self.spawn_callback_recovery_scanner();

        let app = self.build_router().await?;

        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .map_err(crate::BcsError::IoError)?;

        let bound_addr = listener.local_addr().map_err(crate::BcsError::IoError)?;
        let lifecycle = self.state.lifecycle.clone();
        let metrics = self.state.metrics.clone();

        let handle = tokio::spawn(async move {
            // Keep supervision alive for the server lifetime, including random-port tests.
            let _ws_leadership = ws_leadership;
            let mut state_machine_progression_handle = state_machine_progression_handle;
            let result = axum::serve(
                listener,
                app.into_make_service_with_connect_info::<std::net::SocketAddr>(),
            )
            .await
            .map_err(|e| crate::BcsError::InvalidConfig(e.to_string()));
            state_machine_progression_handle.shutdown().await;
            if let Err(error) = lifecycle.lock().await.shutdown_all().await {
                warn!(error = %error, "service lifecycle shutdown failed");
            }
            if let Some(metrics) = metrics {
                metrics.shutdown().await;
            }
            result
        });

        Ok((bound_addr, handle))
    }

/// Run the server on a random port and return the shared state for integration tests.
    #[cfg(any(test, feature = "test-utils"))]
    pub async fn run_on_random_port_with_state(
        self,
    ) -> Result<(
        std::net::SocketAddr,
        tokio::task::JoinHandle<Result<()>>,
        Arc<BcsServerState>,
    )> {
        let state = self.state.clone();
        let (addr, handle) = self.run_on_random_port().await?;
        Ok((addr, handle, state))
    }
}



pub(super) struct AgentCredentialBackfill {
    pub(super) registry: Arc<dyn BotRegistryCoreService>,
}



#[async_trait::async_trait]
impl bcs_ws::bot::AgentCredentialBackfillPort for AgentCredentialBackfill {
    async fn backfill(
        &self,
        bot_uuid: &str,
        agent_token: Option<String>,
        agent_code_header: Option<String>,
    ) {
        let agent_token_str = match &agent_token {
            Some(t) if !t.is_empty() => t.clone(),
            _ => return,
        };

        // agent_token: always write to memory only (not DB) for security
        self.registry
            .add_bot_info(bot_uuid, "agent_token", agent_token_str.clone())
            .await;

        let agent_code = agent_code_header.filter(|s| !s.is_empty());

        let Some(agent_code) = agent_code else {
            warn!(
                bot_uuid = %bot_uuid,
                "no agent_code resolved, skipping backfill"
            );
            return;
        };

        // agent_code: persist to DB
        if let Some(mut caps) = self.registry.load_from_storage(bot_uuid).await {
            if caps.agent_code.as_deref() == Some(&agent_code) {
                debug!(
                    bot_uuid = %bot_uuid,
                    "agent_code unchanged, skipping write"
                );
                return;
            }
            caps.agent_code = Some(agent_code.clone());
            if let Err(e) = self.registry.save_to_storage(bot_uuid, &caps).await {
                warn!(
                    bot_uuid = %bot_uuid,
                    error = %e,
                    "failed to backfill agent_code"
                );
            } else {
                let _ = self.registry.register(bot_uuid.to_string(), caps).await;
                info!(
                    bot_uuid = %bot_uuid,
                    agent_code = %agent_code,
                    "agent_code backfilled"
                );
            }
        } else {
            warn!(
                bot_uuid = %bot_uuid,
                "bot not yet onboarded, skipping agent credential backfill"
            );
        }
    }
}



/// `GroupDispatchContextPort` backed by the core `GroupCoreService`. Lives in
/// the composition root, so it may depend on the core trait the WS adapter is
/// not allowed to name.
pub(super) struct CoreGroupDispatchContext {
    pub(super) group: Arc<dyn GroupCoreService>,
}



#[async_trait::async_trait]
impl bcs_service_api::GroupDispatchContextPort for CoreGroupDispatchContext {
    async fn participants(&self, group_id: &str) -> Option<Vec<bcs_service_api::Participant>> {
        self.group
            .get(group_id)
            .await
            .map(|group| group.participants)
    }
}



pub(super) fn bot_ws_dispatch_state(state: &Arc<BcsServerState>) -> Arc<bcs_ws::bot::BotDispatchState> {
    Arc::new(bcs_ws::bot::BotDispatchState {
        bot_runtime: state.services.bot_runtime.clone(),
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        bot_run_context: state.services.bot_run_context.clone(),
        bot_connections: state.bot_connections.clone(),
        run_channels: state.run_channels.clone(),
        task_callback: None,
        session_management: state.services.session_management.clone(),
        group_dispatch: Arc::new(CoreGroupDispatchContext {
            group: state.services.group.clone(),
        }),
        callback_dispatch: Arc::new(bcs_callback::SessionCallbackDispatcher::new(
            state.services.group.clone(),
            state.outbound_url_guard.clone(),
        )),
        system_message: Some(state.services.system_message.clone()),
        coordination_processed: state.coordination_processed.clone(),
        agent_credential_backfill: Some(Arc::new(AgentCredentialBackfill {
            registry: state.services.registry.clone(),
        })),
    })
}



pub(super) fn web_ws_dispatch_state(
    state: &Arc<BcsServerState>,
    group_session_connections: Option<Arc<dyn GroupSessionConnectionService>>,
) -> Arc<bcs_ws::web::WebDispatchState> {
    Arc::new(bcs_ws::web::WebDispatchState {
        message_flow: state.services.message_flow.clone(),
        collaboration_runtime: state.services.collaboration_runtime.clone(),
        workbench_sessions: state.services.workbench_sessions.clone(),
        interactions: state.services.interactions.clone(),
        group_session_connections,
        frontend_connections: state.frontend_connections.clone(),
        run_channels: state.frontend_run_channels.clone(),
    })
}



pub(super) struct NoopWsLifecycleInstrumentationHook;



#[async_trait::async_trait]
impl WsLifecycleInstrumentationHook for NoopWsLifecycleInstrumentationHook {
    async fn accepted(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn registered(&self, _peer: WsPeer, _endpoint: &'static str) {}

    async fn error(&self, _peer: WsPeer, _endpoint: &'static str, _kind: WsErrorKind) {}

    async fn closed(
        &self,
        _peer: WsPeer,
        _endpoint: &'static str,
        _close_reason: WsCloseReason,
        _duration: std::time::Duration,
    ) {
    }
}



pub(super) struct NoopDirectChatRunLifecycleHook;



#[async_trait::async_trait]
impl DirectChatRunLifecycleHook for NoopDirectChatRunLifecycleHook {
    async fn event(
        &self,
        _event: DirectChatRunEvent,
        _result: MetricsResult,
        _client_kind: DirectChatClientKind,
        _reason: DirectChatRunReason,
    ) {
    }
}



pub(super) fn ws_lifecycle_hook(_state: &Arc<BcsServerState>) -> Arc<dyn WsLifecycleInstrumentationHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = &_state.metrics {
            return metrics.clone();
        }
    }

    Arc::new(NoopWsLifecycleInstrumentationHook)
}



pub(super) fn state_machine_loop_instrumentation(
    _metrics: Option<&Arc<crate::metrics::MetricsRuntime>>,
) -> Option<Arc<dyn bcs_service_api::StateMachineLoopInstrumentationHook>> {
    #[cfg(feature = "prometheus-metrics")]
    if let Some(metrics) = _metrics {
        return Some(Arc::new(crate::metrics::MetricsStateMachineLoopHook::new(metrics.env.clone())));
    }
    None
}



pub(super) fn direct_chat_run_lifecycle_hook(
    _metrics: Option<&Arc<crate::metrics::MetricsRuntime>>,
) -> Arc<dyn DirectChatRunLifecycleHook> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if let Some(metrics) = _metrics {
            return Arc::new(crate::metrics::MetricsDirectChatRunLifecycleHook::new(
                metrics.env.clone(),
            ));
        }
    }

    Arc::new(NoopDirectChatRunLifecycleHook)
}



/// WebSocket upgrade handler for frontend clients (AI Workbench).
///
/// Bind the calling Human's actor id (`human_{staff_no}`) into the WS session
/// at the HTTP upgrade boundary. The bound id is computed once here from the
/// configured auth chain and then immutable for the lifetime of the session;
/// clients cannot rewrite their identity by sending a different
/// sender in subsequent frames.
///
/// If the cookie is missing / invalid / staff_no is empty, the session has
/// `bound_actor_id = None`; Workbench `connect` and `chat.send` then reject
/// request frames with `unauthorized`.
pub(super) async fn ws_upgrade_handler(
    ws: WebSocketUpgrade,
    headers: axum::http::HeaderMap,
    State(state): State<Arc<BcsServerState>>,
) -> Response {
    // Resolve identity BEFORE on_upgrade: once the connection switches to
    // WebSocket frames the original HTTP headers are gone, so cookie
    // extraction must happen here in the request scope.
    let bound_actor_id = match state.auth_chain.authenticate(&headers).await {
        Ok(result) => result
            .principal
            .and_then(|p| p.user_id)
            .filter(|s| !s.is_empty())
            .map(|staff_no| format!("human_{}", staff_no)),
        Err(_) => None,
    };

    if let Some(ref actor_id) = bound_actor_id {
        info!(actor_id = %actor_id, "WS upgrade: bound human actor id");
    } else {
        debug!("WS upgrade: anonymous session (no staff_no in cookie)");
    }

    let request_id = bcs_observability::current_request_id();
    ws.on_upgrade(move |socket| {
        let ws_state = web_ws_dispatch_state(&state, None);
        let metrics_hook = ws_lifecycle_hook(&state);
        bcs_observability::with_request_id(request_id, bcs_ws::web::handle_client_connection(
            socket,
            ws_state,
            bcs_ws::web::WorkbenchConnectionAuth::UserBound {
                actor_id: bound_actor_id,
            },
            metrics_hook,
        ))
    })
}



/// WebSocket handler for bot connections.
///
/// Token validation is handled by the bot.connect frame after upgrade:
/// - Valid token: reconnect to existing bot
/// - Invalid/missing token: treated as new bot, assigned new bot_id + token
///
/// The Authorization header and x-agentclaw-agent-code are captured before
/// the upgrade so they can be backfilled into bot_info after a successful
/// bot.connect handshake.
pub(super) async fn bot_ws_handler(
    State(state): State<Arc<BcsServerState>>,
    headers: axum::http::HeaderMap,
    ws: WsUpgrade,
) -> Response {
    let agent_token = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    let agent_code_header = headers
        .get("x-agentclaw-agent-code")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());

    let request_id = bcs_observability::current_request_id();
    ws.on_upgrade(move |socket| {
        let ws_state = bot_ws_dispatch_state(&state);
        let metrics_hook = ws_lifecycle_hook(&state);
        bcs_observability::with_request_id(request_id, bcs_ws::bot::handle_connection(
            socket,
            ws_state,
            metrics_hook,
            agent_token,
            agent_code_header,
        ))
    })
}
