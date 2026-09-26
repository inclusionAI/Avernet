//! Construction-final wiring: maybe_wrap_* helpers and group-history request port.
//!
//! Split out from server.rs as part of the V1 API auth plugin chain (Task 1) refactor. Behavior preserved exactly.

use super::*;

pub(super) fn maybe_wrap_bot_delivery(
    _config: &BcsConfig,
    delivery: Arc<dyn BotDeliveryPort>,
) -> Arc<dyn BotDeliveryPort> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsBotDeliveryPort::new(
                delivery,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    delivery
}

pub(super) fn maybe_wrap_frontend_delivery(
    _config: &BcsConfig,
    delivery: Arc<dyn FrontendDeliveryPort>,
) -> Arc<dyn FrontendDeliveryPort> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsFrontendDeliveryPort::new(
                delivery,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    delivery
}

pub(super) fn maybe_wrap_group_management(
    _config: &BcsConfig,
    service: Arc<dyn GroupManagementService>,
) -> Arc<dyn GroupManagementService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::MetricsGroupManagementService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}

pub(super) fn maybe_wrap_message_flow(
    _config: &BcsConfig,
    service: Arc<dyn MessageFlowService>,
) -> Arc<dyn MessageFlowService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::InstrumentedMessageFlowService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}

pub(super) fn maybe_wrap_a2a_chat_runs(
    _config: &BcsConfig,
    service: Arc<dyn A2aChatRunService>,
) -> Arc<dyn A2aChatRunService> {
    #[cfg(feature = "prometheus-metrics")]
    {
        if _config.metrics.enabled {
            return Arc::new(crate::metrics::InstrumentedA2aChatRunService::new(
                service,
                Arc::from(bcs_config::resolve_env_str()),
            ));
        }
    }

    service
}

pub(super) struct BootstrapGroupHistoryBotRequestPort {
    pub(super) bot_connections: Arc<BotConnectionRegistry>,
}

#[async_trait::async_trait]
impl GroupHistoryBotRequestPort for BootstrapGroupHistoryBotRequestPort {
    async fn send_history_request(
        &self,
        target: BotDeliveryTarget,
        method: &str,
        params: serde_json::Value,
        timeout_ms: u64,
    ) -> std::result::Result<serde_json::Value, String> {
        let BotDeliveryTarget::WebSocket { bot_id } = target else {
            return Err("history request target is not a websocket bot".to_string());
        };
        self.bot_connections
            .send_request(&bot_id, method, params, timeout_ms)
            .await
    }
}

// Construction finish (impl BcsServer methods): build_auth_router, build_router, run, run_on_random_port, run_on_random_port_with_state, initialize_lifecycle, spawn_state_machine_timeout_scanner, spawn_callback_recovery_scanner, build_full_oauth_route_state, openapi_auth_public_base_url.
// Behavior-preserving split from server.rs's `impl BcsServer` block.

/// Task 11: the legacy non-OAuth `/auth/user` fallback, projected through the
/// SAME auth plugin chain as before. This preserves the old response shape
/// (`user_id`/`name`/`provider`/`avatar`) while the NEW verifier chain runs
/// first at the V1 boundary; a Gateway caller resolves there and never needs
/// this port.
pub(super) struct AuthChainUserProjection {
    pub chain: Arc<bcs_auth_api::AuthPluginChain>,
}

#[async_trait::async_trait]
impl bcs_api_http::v1::common::ChainUserProjection for AuthChainUserProjection {
    async fn current_user(
        &self,
        headers: &axum::http::HeaderMap,
    ) -> std::result::Result<
        bcs_service_api::application::v1::AuthUserInfo,
        bcs_service_api::application::v1::ApplicationError,
    > {
        match self.chain.authenticate(headers).await {
            Ok(result) => match result.principal {
                // A principal without a non-empty `user_id` is NOT a human
                // login (bots stay anonymous to the who-am-i endpoint).
                Some(principal)
                    if principal
                        .user_id
                        .as_deref()
                        .is_some_and(|id| !id.is_empty()) =>
                {
                    Ok(bcs_service_api::application::v1::AuthUserInfo {
                        user_id: principal.user_id.expect("checked above"),
                        name: principal.user_name,
                        provider: principal.source_name.unwrap_or_else(|| "chain".to_string()),
                        avatar: principal.avatar,
                    })
                }
                _ => Err(bcs_service_api::application::v1::ApplicationError::Unauthenticated),
            },
            Err(e) => {
                tracing::warn!(error = %e, "auth chain failed in OpenAPI auth user");
                Err(bcs_service_api::application::v1::ApplicationError::internal(
                    "auth chain failed",
                ))
            }
        }
    }
}


impl BcsServer {
    /// Read-only view of the assembled server state (construction artifacts,
    /// e.g. the Task 12 `built_api_auth` verifier Arc, are inspectable after
    /// construction without starting the server).
    pub fn state(&self) -> Arc<BcsServerState> {
        Arc::clone(&self.state)
    }

    /// Build the SHARED secure OAuth services plus the legacy route state
    /// (Task 11): both entrypoints consume one `AuthApplicationService`
    /// mechanism over the same engine / pending store / providers, isolated
    /// only by flow namespace. Returns `(legacy route state, V1 service,
    /// compat trusted origins)`.
    pub(super) fn build_full_oauth_route_state(
        &self,
    ) -> Option<(
        Arc<bcs_http::oauth::OAuthRouteState>,
        Arc<dyn bcs_service_api::application::v1::AuthService>,
        Arc<Vec<String>>,
    )> {
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
                        let (user_port, session_engine, session_identities) = (
                            self.state.user_identity_port.clone(),
                            self.state.oauth_session_engine.clone(),
                            self.state.auth_session_identities.clone(),
                        );
                        if let (Some(user_port), Some(engine), Some(identities)) =
                            (user_port, session_engine, session_identities)
                        {
                            // Legacy reply order = alphabetical instance names
                            // (the pre-switchover /auth/url behavior).
                            let chain_providers =
                                crate::api_auth_wiring::provider_instance_map(providers);
                            let entry = crate::api_auth_wiring::build_oauth_entry_services(
                                chain_providers,
                                engine,
                                identities,
                                Arc::new(bcs_auth_oauth::MemoryPendingOAuthLoginStore::new()),
                                resolved,
                            );

                            // Compat-mode tightening (spec §9): BOTH
                            // entrypoints require cookie-backed unsafe
                            // requests to carry a matching Origin from the
                            // finite exact cors.allowed_origins set
                            // (wildcard/null excluded). No usable entries →
                            // fail-closed rejection of cookie refresh/logout.
                            let trusted_origins = crate::api_auth_wiring::compat_trusted_browser_origins(
                                &self.config.cors.allowed_origins,
                            )
                            .unwrap_or_else(|| Arc::new(Vec::new()));

                            let route_state = Arc::new(bcs_http::oauth::OAuthRouteState::new(
                                entry.legacy.clone(),
                                &resolved.jwt_secret,
                                user_port,
                                resolved.clone(),
                                Some(auth_chain),
                                Some(trusted_origins.clone()),
                            ));

                            info!(
                                providers = ?route_state.config.base_url,
                                cookie_secure = resolved.cookie_secure,
                                env = %resolved.env,
                                "Mounting OAuth routes (shared secure service, legacy+v1 flows)"
                            );
                            return Some((route_state, entry.v1, trusted_origins));
                        } else {
                            warn!(
                                "[auth.oauth] OAuth configured but the strict session engine / \
                                 identity stores are not wired; refusing to mount /auth/*"
                            );
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
        let oauth_wiring = self.build_full_oauth_route_state();
        let mut openapi_v1 = self.state.openapi_v1.clone()
            .with_bot_self_service(Arc::new(bcs_app_bot::BotSelfServiceImpl::new(
                crate::agent_identity::build_agent_identity_port()?,
                self.state.services.registry.clone(),
            )));
        // Task 12: when `[api.auth]` is ACTIVE, the V1 facade's auth service
        // and CSRF origins come from the NEW assembly (never the compat
        // cors-derived list); when absent, the Task 11 compat wiring below
        // applies unchanged.
        match self.state.built_api_auth.as_ref() {
            Some(built) => {
                // Explicit chain: NO legacy-chain fallback projection
                // (spec §4.3 — the new config decides completely, no
                // implicit backfill from the old [auth] chain).
                openapi_v1 = openapi_v1.with_trusted_browser_origins(built.origins.clone());
                if let (Some(service), Some(base)) =
                    (built.auth_service.clone(), built.public_base_url.clone())
                {
                    // Contract §4.1: `api.auth.public_base_url` IS the full
                    // V1 auth route base — the callback folds to
                    // `{public_base_url}/callback/{provider}` and must not
                    // have `/openapi/v1/auth` appended a second time (the
                    // doubled path used to leak into the OAuth redirect_uri,
                    // breaking provider-side callback registration).
                    openapi_v1 = openapi_v1.with_auth_service(service, base);
                }
            }
            None => {
                if let Some((route_state, v1_service, trusted_origins)) = oauth_wiring.clone() {
                    if let Some(public_base_url) = self.openapi_auth_public_base_url() {
                        openapi_v1 = openapi_v1
                            .with_auth_service(v1_service, public_base_url)
                            .with_trusted_browser_origins(
                                bcs_api_http::TrustedBrowserOrigins::new(
                                    trusted_origins.iter().cloned().collect(),
                                )
                                .unwrap_or_else(|_| {
                                    bcs_api_http::TrustedBrowserOrigins::new(Vec::new())
                                        .expect("empty is valid")
                                }),
                            )
                            // /auth/user legacy non-OAuth fallback over the auth
                            // chain (old response shape preserved).
                            .with_chain_user_projection(Arc::new(AuthChainUserProjection {
                                chain: Arc::clone(&self.state.auth_chain),
                            }));
                    }
                    let _ = route_state;
                }
            }
        }

        let mut router = Router::new()
            // WebSocket endpoint for frontend clients (via gateway)
            .route(bcs_ws::web::FRONTEND_WS_ENDPOINT, get(ws_upgrade_handler))
            // WebSocket for bot connections
            .route(bcs_ws::bot::BOT_WS_ENDPOINT, get(bot_ws_handler));

        if let Some(metrics) = &self.state.metrics {
            router = router.route(&metrics.endpoint_path, get(metrics_handler));
        }

        // Task 12: the standalone connection-token router honors the SAME
        // verifier Arc and CSRF origins as the main V1 ApiState. In compat
        // mode origins stay unset (gateway-only; the Gateway credential kind
        // never triggers an Origin check).
        let (connection_origins, verifier) = match self.state.built_api_auth.as_ref() {
            Some(built) => (Some(built.origins.clone()), built.verifier.clone()),
            None => (None, self.state.gateway_principal_verifier.clone()),
        };

        let mut router = router
            .with_state(Arc::clone(&self.state))
            .merge(api_router)
            .merge(bcs_api_http::router(openapi_v1))
            .merge(bcs_api_http::group_session_connection_router(
                group_session_connections,
                verifier,
                connection_origins,
            ))
            .merge(group_session_websocket_router);

        if let Some(oauth_router) = self.build_auth_router(oauth_wiring.map(|(state, _, _)| state)) {
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

    fn spawn_state_machine_progression_scanner(&self) -> crate::state_machine_progression_scanner::ProgressionRecoveryTask {
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
