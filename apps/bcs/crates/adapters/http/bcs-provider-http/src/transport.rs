use super::*;

impl HttpProviderTransport {
    pub fn new() -> Self {
        Self::with_url_guard(OutboundUrlGuard::strict())
    }

    pub fn allowing_private_networks_for_tests() -> Self {
        Self::with_url_guard_and_sse_policy(
            OutboundUrlGuard::allowing_private_networks_for_tests(),
            ProviderClientPolicy::for_request(true),
        )
    }

    pub fn with_url_guard(url_guard: OutboundUrlGuard) -> Self {
        Self::with_url_guard_and_sse_policy(url_guard, ProviderClientPolicy::for_request(true))
    }

    fn with_url_guard_and_sse_policy(
        url_guard: OutboundUrlGuard,
        sse_policy: ProviderClientPolicy,
    ) -> Self {
        Self {
            client: provider_client_builder(ProviderClientPolicy::for_request(false))
                .build()
                .expect("build provider http client"),
            sse_client: provider_client_builder(sse_policy)
                .build()
                .expect("build provider sse client"),
            url_guard,
            chat_run_timeout_ms: DEFAULT_PROVIDER_CALLBACK_TIMEOUT_MS,
            event_ingest: std::sync::RwLock::new(None),
            bot_run_context: std::sync::RwLock::new(None),
            interactions: std::sync::RwLock::new(None),
        }
    }

    /// Override the fallback deadline for `chat.send` frames that do not
    /// provide an explicit `params.timeout_ms`.
    pub fn with_chat_run_timeout_ms(mut self, timeout_ms: u64) -> Self {
        self.chat_run_timeout_ms = timeout_ms;
        self
    }

    /// Inject the ingest dependencies needed by the 2.0 SSE branch after
    /// construction. Using a shared `&self` setter allows the transport to be
    /// Arc-shared into bot_delivery before message_flow exists, resolving the
    /// circular-dependency bootstrap cycle.
    pub fn set_ingest(
        &self,
        event_ingest: Arc<dyn ProviderEventIngestService>,
        bot_run_context: Arc<dyn BotRunContextPort>,
    ) {
        *self
            .event_ingest
            .write()
            .expect("event_ingest lock poisoned") = Some(event_ingest);
        *self
            .bot_run_context
            .write()
            .expect("bot_run_context lock poisoned") = Some(bot_run_context);
    }

    /// Build the standalone observe injector for one OBSERVER target (spec
    /// §10.10): the composition root resolves the observer's own Provider
    /// webhook coordinates and reuses THIS transport's client and outbound
    /// URL guard — the same policy that governs ensure/send. Non-Provider
    /// targets (WebSocket bots) have no observe transport: callers pause
    /// that target's sync.
    pub fn observe_injector(&self, target: BotDeliveryTarget) -> Option<HttpObserveInjector> {
        if !matches!(target, BotDeliveryTarget::HttpProvider { .. }) {
            return None;
        }
        Some(HttpObserveInjector::new(self.client.clone(), self.url_guard.clone(), target))
    }

    /// Inject the Application service that owns Provider 2.0 HITL state.
    pub fn set_interactions(&self, interactions: Arc<dyn InteractionService>) {
        *self
            .interactions
            .write()
            .expect("interactions lock poisoned") = Some(Arc::downgrade(&interactions));
    }
}

impl Default for HttpProviderTransport {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl InteractionProviderPort for HttpProviderTransport {
    async fn resolve_interaction(
        &self,
        command: InteractionProviderCommand,
    ) -> ServiceResult<InteractionProviderAck> {
        let BotDeliveryTarget::HttpProvider {
            provider_id,
            provider_bot_ref,
            protocol_version,
            ..
        } = &command.target
        else {
            return Err(ServiceError::InvalidOperation {
                message: "interaction.resolve requires an HTTP Provider target".to_string(),
                request_id: Some(command.bcs_run_id),
            });
        };
        if protocol_version != "2.0" {
            return Err(ServiceError::InvalidOperation {
                message: "interaction.resolve requires Provider protocol 2.0".to_string(),
                request_id: Some(command.bcs_run_id),
            });
        }

        let mut params = match command.resolution {
            Value::Object(map) => map,
            _ => {
                return Err(ServiceError::InvalidOperation {
                    message: "interaction resolution must be a JSON object".to_string(),
                    request_id: Some(command.bcs_run_id),
                });
            }
        };
        params.insert(
            "bcsRunId".to_string(),
            Value::String(command.bcs_run_id.clone()),
        );
        params.insert("runId".to_string(), Value::String(command.provider_run_id));
        params.insert(
            "interactionId".to_string(),
            Value::String(command.interaction_id),
        );
        params.insert(
            "kind".to_string(),
            Value::String(command.kind.as_slug().to_string()),
        );
        params.insert(
            "idempotencyKey".to_string(),
            Value::String(command.idempotency_key),
        );

        let body = ProviderWebhookRequest {
            frame_type: "req".to_string(),
            id: uuid::Uuid::new_v4().to_string(),
            method: "interaction.resolve".to_string(),
            params: Some(Value::Object(params)),
            session_id: command.bcs_session_id,
            bcn_group_id: command.group_id,
            to_bot: ProviderWebhookBotRef {
                provider_id: provider_id.clone(),
                provider_bot_ref: provider_bot_ref.clone(),
                tags: Vec::new(),
            },
            from: None,
            message: None,
            attachments: Vec::new(),
            before: None,
            after: None,
            limit: None,
            timeout_ms: NON_CHAT_PROVIDER_REQUEST_TIMEOUT_MS,
            extensions: None,
        };
        let ack = post_provider::<ProviderAckResponse>(
            &self.client,
            &self.url_guard,
            &command.target,
            &body,
            &command.provider_bypass_headers,
        )
        .await?;
        Ok(InteractionProviderAck {
            ok: ack.ok,
            retryable: ack.retryable,
            error: ack.error,
        })
    }
}

#[async_trait]
impl BotDeliveryPort for HttpProviderTransport {
    async fn is_available(&self, target: &BotDeliveryTarget) -> bool {
        target.is_http_provider()
    }

    async fn deliver(&self, cmd: BotDeliveryCommand) -> ServiceResult<BotDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        if matches!(
            cmd.delivery_kind,
            BotDeliveryKind::Send
                | BotDeliveryKind::TaskDispatch
                | BotDeliveryKind::TaskMessage
                | BotDeliveryKind::TaskResult
        ) {
            let BcsFrame::Request(_) = &cmd.frame else {
                return Err(ServiceError::DeliveryNotSent {
                    code: "provider_request_frame_required", retryable: false,
                });
            };
        }
        let mut body = provider_request_from_frame(&cmd.target, &cmd.frame, self.chat_run_timeout_ms)
            .map_err(|_| ServiceError::DeliveryNotSent { code: "provider_request_invalid", retryable: false })?;
        if body.method == "chat.send" {
            if cmd.run_id.is_empty() {
                return Err(ServiceError::DeliveryNotSent {
                    code: "provider_canonical_run_id_required", retryable: false,
                });
            }
            // Queue frames carry a per-attempt request id. Provider requests,
            // callbacks and SSE contexts use the canonical run id instead.
            // Do not rewrite non-running requests (inject/history/abort).
            body.id = cmd.run_id.clone();
        }
        let provider_id = body.to_bot.provider_id.clone();
        let provider_bot_ref = body.to_bot.provider_bot_ref.clone();
        let method = body.method.clone();
        let run_id = cmd.run_id.clone();
        let delivery_kind = format!("{:?}", cmd.delivery_kind);
        info!(
            target_bot_id = %target_bot_id,
            provider_id = %provider_id,
            provider_bot_ref = %provider_bot_ref,
            method = %method,
            run_id = %run_id,
            delivery_kind = %delivery_kind,
            "provider downlink: deliver start"
        );

        // Protocol 2.0: prefer SSE. Send with an SSE-capable Accept header on the
        // no-total-timeout client, then branch on the response Content-Type.
        let is_proto2 = matches!(
            &cmd.target,
            BotDeliveryTarget::HttpProvider { protocol_version, .. } if protocol_version == "2.0"
        );
        if is_proto2 {
            let is_chat_send = method == "chat.send";
            let wants_sse =
                is_chat_send && cmd.provider_transport == ProviderTransportPreference::SseFirst;
            let client = if wants_sse {
                &self.sse_client
            } else {
                &self.client
            };
            let run_context = self
                .bot_run_context
                .read()
                .expect("bot_run_context lock poisoned")
                .clone();
            if is_chat_send {
                if let Some(context) = run_context.as_ref() {
                    let began = context
                        .begin_provider_transport(
                            &run_id,
                            bcs_protocol::now_ms().saturating_add(body.timeout_ms),
                        )
                        .await;
                    if !began {
                        return Err(ServiceError::InvalidOperation {
                            message: "provider run transport is already registered".to_string(),
                            request_id: Some(run_id),
                        });
                    }
                }
            }
            let resp = match send_provider_delivery_request(
                client,
                &self.url_guard,
                &cmd.target,
                &body,
                wants_sse,
                &cmd.provider_bypass_headers,
            )
            .await
            {
                Ok(response) => response,
                Err(error) => {
                    if let Some(context) = run_context.as_ref() {
                        context.clear_provider_transport(&run_id).await;
                    }
                    return Err(error);
                }
            };
            if !resp.status().is_success() {
                if let Some(context) = run_context.as_ref() {
                    context.clear_provider_transport(&run_id).await;
                }
                return Ok(provider_delivery_rejection(
                    target_bot_id,
                    resp.status(),
                ));
            }
            let ctype = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok())
                .unwrap_or("")
                .to_string();
            if wants_sse && ctype.starts_with("text/event-stream") {
                if let Some(context) = run_context.as_ref() {
                    let bound = context
                        .bind_provider_transport(&run_id, ProviderRunTransport::Sse)
                        .await;
                    if !bound {
                        return Err(ServiceError::InvalidOperation {
                            message: "provider run is already bound to another transport"
                                .to_string(),
                            request_id: Some(run_id),
                        });
                    }
                }
                let (Some(flow), Some(ctx)) = (
                    self.event_ingest
                        .read()
                        .expect("event_ingest lock poisoned")
                        .clone(),
                    self.bot_run_context
                        .read()
                        .expect("bot_run_context lock poisoned")
                        .clone(),
                ) else {
                    if let Some(context) = run_context.as_ref() {
                        context.clear_provider_transport(&run_id).await;
                    }
                    warn!(
                        target_bot_id = %target_bot_id,
                        provider_id = %provider_id,
                        run_id = %run_id,
                        "2.0 SSE response but ingest deps not wired"
                    );
                    return Err(ServiceError::InternalError(
                        "sse ingest deps not wired".to_string(),
                    ));
                };
                let spawn_run_id = run_id.clone();
                let spawn_bot_id = target_bot_id.clone();
                let interaction_context = self
                    .interactions
                    .read()
                    .expect("interactions lock poisoned")
                    .clone()
                    .and_then(|service| service.upgrade())
                    .map(|service| SseInteractionContext {
                        service,
                        provider_target: cmd.target.clone(),
                        provider_bypass_headers: cmd.provider_bypass_headers.clone(),
                    });
                info!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    run_id = %run_id,
                    "provider downlink: 2.0 SSE stream accepted; spawning reader"
                );
                tokio::spawn(async move {
                    stream_and_drive(
                        resp,
                        spawn_run_id,
                        spawn_bot_id,
                        flow,
                        ctx,
                        interaction_context,
                    )
                    .await;
                });
                return Ok(BotDeliveryResult {
                    target_bot_id,
                    delivered: true,
                    error: None,
                });
            }
            // 2.0 + application/json: branch on the request method (D5 relaxed).
            //   - inject / abort / history (and any non-send): the provider
            //     ack's the POST with JSON; treat it as a simple ack, exactly
            //     like the 1.0 path. The events (if any) arrive separately via
            //     the upstream /bot/events callback (handled by submit_event).
            //   - send: a JSON response means "callback streaming" (transport
            //     =callback) — the actual chat/agent events come later over
            //     /bot/events. We still accept the ack here; whether downstream
            //     events are honored is gated by submit_event's protocol_version
            //     check (Capability B). We do NOT require SSE for send anymore.
            let status = resp.status();
            let json_body_timeout = Duration::from_millis(JSON_FALLBACK_BODY_TIMEOUT_MS);
            let ack = match read_provider_ack_body(resp, json_body_timeout).await {
                Ok(ack) => ack,
                Err(ProviderAckBodyError::Decode(error)) => {
                    if let Some(context) = run_context.as_ref() {
                        context.clear_provider_transport(&run_id).await;
                    }
                    warn!(
                        target_bot_id = %target_bot_id,
                        provider_id = %provider_id,
                        method = %method,
                        run_id = %run_id,
                        status = %status.as_u16(),
                        error = %error,
                        "provider downlink: 2.0 JSON ack decode failed"
                    );
                    return Err(ServiceError::InternalError(format!(
                        "decode 2.0 json ack: {error}"
                    )));
                }
                Err(ProviderAckBodyError::Timeout) => {
                    if let Some(context) = run_context.as_ref() {
                        context.clear_provider_transport(&run_id).await;
                    }
                    warn!(
                        target_bot_id = %target_bot_id,
                        provider_id = %provider_id,
                        method = %method,
                        run_id = %run_id,
                        status = %status.as_u16(),
                        json_body_timeout_ms = %json_body_timeout.as_millis(),
                        "provider downlink: 2.0 JSON ack body timeout"
                    );
                    return Err(ServiceError::InternalError(format!(
                        "provider 2.0 JSON ack body timeout after {}ms",
                        json_body_timeout.as_millis()
                    )));
                }
            };
            if ack.ok {
                if is_chat_send {
                    if let Some(context) = run_context.as_ref() {
                        let bound = context
                            .bind_provider_transport(&run_id, ProviderRunTransport::Callback)
                            .await;
                        if !bound {
                            return Err(ServiceError::InvalidOperation {
                                message: "provider run is already bound to another transport"
                                    .to_string(),
                                request_id: Some(run_id),
                            });
                        }
                    }
                }
                info!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    method = %method,
                    run_id = %run_id,
                    "provider downlink: 2.0 JSON ack accepted (callback transport)"
                );
            } else {
                if let Some(context) = run_context.as_ref() {
                    context.clear_provider_transport(&run_id).await;
                }
                warn!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    method = %method,
                    run_id = %run_id,
                    error = %ack.error.as_deref().unwrap_or("provider rejected"),
                    "provider downlink: 2.0 JSON ack rejected by provider"
                );
            }
            return Ok(BotDeliveryResult {
                target_bot_id,
                delivered: ack.ok,
                error: (!ack.ok).then(|| {
                    ServiceError::InternalError(
                        ack.error.unwrap_or_else(|| "provider rejected".to_string()),
                    )
                }),
            });
        }

        let started = Instant::now();
        let response = send_provider_delivery_request(
            &self.client,
            &self.url_guard,
            &cmd.target,
            &body,
            false,
            &cmd.provider_bypass_headers,
        )
        .await;
        let elapsed_ms = started.elapsed().as_millis();
        let response = match response {
            Ok(response) => response,
            Err(error) => {
                warn!(
                    target_bot_id = %target_bot_id,
                    provider_id = %provider_id,
                    method = %method,
                    run_id = %run_id,
                    elapsed_ms = %elapsed_ms,
                    error = %error,
                    "provider downlink: deliver failed"
                );
                return Err(error);
            }
        };
        if !response.status().is_success() {
            return Ok(provider_delivery_rejection(
                target_bot_id,
                response.status(),
            ));
        }
        let status = response.status();
        let ack = response.json::<ProviderAckResponse>().await.map_err(|error| {
            warn!(
                target_bot_id = %target_bot_id,
                provider_id = %provider_id,
                method = %method,
                run_id = %run_id,
                status = %status.as_u16(),
                elapsed_ms = %elapsed_ms,
                error = %error,
                "provider downlink: decode response failed"
            );
            ServiceError::InternalError(format!("decode provider response: {error}"))
        })?;
        if ack.ok {
            info!(
                target_bot_id = %target_bot_id,
                provider_id = %provider_id,
                method = %method,
                run_id = %run_id,
                elapsed_ms = %elapsed_ms,
                "provider downlink: deliver acked"
            );
        } else {
            warn!(
                target_bot_id = %target_bot_id,
                provider_id = %provider_id,
                method = %method,
                run_id = %run_id,
                elapsed_ms = %elapsed_ms,
                error = %ack.error.as_deref().unwrap_or("provider rejected"),
                "provider downlink: deliver rejected by provider"
            );
        }
        Ok(BotDeliveryResult {
            target_bot_id,
            delivered: ack.ok,
            error: (!ack.ok).then(|| {
                ServiceError::InternalError(
                    ack.error.unwrap_or_else(|| "provider rejected".to_string()),
                )
            }),
        })
    }

    async fn abort(&self, cmd: BotAbortDeliveryCommand) -> ServiceResult<BotAbortDeliveryResult> {
        let target_bot_id = cmd.target_bot_id().to_string();
        let BotDeliveryTarget::HttpProvider {
            provider_id,
            provider_bot_ref,
            ..
        } = &cmd.target
        else {
            return Err(ServiceError::InvalidOperation {
                message: "Provider chat.abort requires an HTTP Provider target".to_string(),
                request_id: Some(cmd.command_id),
            });
        };
        if cmd.run_id.is_some() {
            return Err(ServiceError::InvalidOperation {
                message: "Provider chat.abort must use Bot/Session scope".to_string(),
                request_id: Some(cmd.command_id),
            });
        }
        let body = ProviderWebhookRequest {
            frame_type: "req".to_string(),
            id: cmd.command_id.clone(),
            method: "chat.abort".to_string(),
            params: None,
            session_id: cmd.session_id,
            bcn_group_id: cmd.group_id,
            to_bot: ProviderWebhookBotRef {
                provider_id: provider_id.clone(),
                provider_bot_ref: provider_bot_ref.clone(),
                tags: Vec::new(),
            },
            from: None,
            message: None,
            attachments: Vec::new(),
            before: None,
            after: None,
            limit: None,
            timeout_ms: cmd.timeout_ms,
            extensions: None,
        };
        // COSEC: this reuses the guarded Provider request path (redirects
        // disabled, URL policy enforced, DNS pinned, configured bearer only).
        let response = send_provider_request(
            &self.client,
            &self.url_guard,
            &cmd.target,
            &body,
            false,
            &cmd.provider_bypass_headers,
        )
        .await?;
        if response.status() == reqwest::StatusCode::GONE {
            let body = response.json::<Value>().await.map_err(|error| {
                ServiceError::InternalError(format!(
                    "decode Provider chat.abort 410 response: {error}"
                ))
            })?;
            let error_code = body.get("error").and_then(|error| {
                error
                    .as_str()
                    .or_else(|| error.get("code").and_then(Value::as_str))
            });
            if error_code != Some("run_terminated") {
                return Err(ServiceError::InvalidOperation {
                    message: "Provider returned an unexpected chat.abort 410 response".to_string(),
                    request_id: Some(cmd.command_id),
                });
            }
            return Ok(BotAbortDeliveryResult {
                target_bot_id,
                aborted_run_ids: Vec::new(),
            });
        }
        let status = response.status();
        let result = response
            .json::<ProviderAbortResponse>()
            .await
            .map_err(|error| {
                ServiceError::InternalError(format!(
                    "decode Provider chat.abort response ({status}): {error}"
                ))
            })?;
        if !result.ok {
            return Err(ServiceError::InvalidOperation {
                message: result
                    .error
                    .unwrap_or_else(|| "Provider rejected chat.abort".to_string()),
                request_id: Some(cmd.command_id),
            });
        }
        Ok(BotAbortDeliveryResult {
            target_bot_id,
            aborted_run_ids: result.aborted_run_ids,
        })
    }
}
