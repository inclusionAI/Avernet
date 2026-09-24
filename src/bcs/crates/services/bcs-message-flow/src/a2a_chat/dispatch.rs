//! Direct chat validation and legacy transport dispatch.
use super::*;

impl A2aChat {
    pub(super) async fn chat_with_queue_mode(&self, cmd: A2aChatCommand, selected_queue_mode: Option<bool>) -> ServiceResult<A2aChatOutcome> {
        let client_kind = direct_chat_client_kind(cmd.client.as_deref());
        let from_bot_id = bot_caller_id(&cmd.caller)?;
        bcs_observability::observe_result("chat.authorize.owner", self.ensure_source_owner(&from_bot_id, cmd.authenticated_staff_id.as_deref())).await?;
        let target_bot = if let Some(code) = cmd.organization_code.as_deref() {
            let organization = self.organization.as_ref().ok_or_else(|| {
                ServiceError::InvalidOperation {
                    message: "organization service is not configured".to_string(),
                    request_id: None,
                }
            })?;
            bcs_observability::observe_result("chat.authorize.organization", organization
                .authorize_pair(code, &from_bot_id, &cmd.target_bot_id)).await?;
            bcs_observability::observe_result("chat.authorize.organization_target", self.ensure_organization_target_reachable(&from_bot_id, &cmd.target_bot_id)).await?
        } else {
            bcs_observability::observe_result("chat.authorize.target", self.ensure_target_reachable(&from_bot_id, &cmd.target_bot_id)).await?
        };
        if target_bot.status == ActorStatus::Hidden {
            let name = target_bot
                .capabilities
                .name
                .as_deref()
                .unwrap_or(&cmd.target_bot_id);
            return Err(ServiceError::BotHidden(name.to_string()));
        }
        let run_id = cmd
            .run_id
            .clone()
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
        let session_key = cmd
            .session_key
            .clone()
            .unwrap_or_else(|| format!("chat:{}", &run_id[..run_id.len().min(8)]));
        let timeout_ms = cmd.timeout_ms.unwrap_or(self.default_timeout_ms);
        let now_ms = now_ms();
        let expires_at_ms = now_ms.saturating_add(timeout_ms);
        bcs_service_api::port::repo::session_registry::validate_direct_session_id(&session_key)?;
        if let Some(sessions) = &self.session_management {
            sessions.ensure_direct_session(&session_key).await.map_err(|e| match e {
                bcs_service_api::SessionUseCaseError::Conflict(message) => ServiceError::Conflict(message),
                other => ServiceError::InternalError(other.to_string()),
            })?;
        }
        if cmd.async_mode && match selected_queue_mode { Some(queued) => queued, None => self.queue_enabled(&cmd.target_bot_id).await } {
            return self.admit_direct(&cmd, &from_bot_id, &run_id, &session_key, expires_at_ms).await;
        }
        self.guard_legacy(&cmd.target_bot_id, &session_key).await?;
        let delivery_target = bcs_observability::observe_result("chat.delivery.resolve", self
            .registry
            .resolve_delivery_target(&cmd.target_bot_id)).await?;
        let target_is_http_provider = delivery_target.is_http_provider();
        if !bcs_observability::observe_value("chat.delivery.available", self.bot_delivery.is_available(&delivery_target)).await {
            self.emit_run_lifecycle(
                DirectChatRunEvent::Failed,
                MetricsResult::Error,
                client_kind,
                DirectChatRunReason::BotNotConnected,
            )
            .await;
            return Err(ServiceError::BotNotConnected(cmd.target_bot_id));
        }

        let submit_on_provider_ack =
            cmd.async_mode
                && target_is_http_provider
                && is_detached_wait_mode(cmd.caller_wait_mode.as_deref());
        let completion_policy = if submit_on_provider_ack {
            ChatRunCompletionPolicy::DetachDeliveryAck
        } else {
            ChatRunCompletionPolicy::WaitForFinal
        };

        let from_bot_name = bcs_observability::observe_value("chat.sender.load", self.sender_display_name(&from_bot_id)).await;
        let frame = build_chat_send_frame(
            &run_id,
            &session_key,
            &cmd.target_bot_id,
            &from_bot_id,
            &from_bot_name,
            cmd.from_actor_id.as_deref().unwrap_or(&from_bot_id),
            &cmd.message,
            &cmd.tags,
            cmd.caller_wait_mode.as_deref(),
        )?;
        // Audit snapshot of the request actually sent to the target bot: the
        // whole `chat.send` frame serialized as {"method","params"}. Written
        // once at create; no UPDATE path touches it, it is never SELECTed back
        // nor exposed over the API — inspect it by querying the
        // `original_request` column directly.
        let original_request = match &frame {
            BcsFrame::Request(req) => serde_json::to_string(&serde_json::json!({
                "method": req.method.clone(),
                "params": req.params.clone(),
            }))
            .unwrap_or_default(),
            _ => String::new(),
        };
        let mut record = ChatRunRecord::new(
            run_id.clone(),
            cmd.target_bot_id.clone(),
            from_bot_id.clone(),
            session_key.clone(),
            now_ms,
            expires_at_ms,
            cmd.client.clone(),
            cmd.response_mode,
            completion_policy,
        );
        record.original_request = original_request;
        if let Err(err) = bcs_observability::observe_result("chat.run.create", self.run_store.create(record)).await {
            let reason = err.direct_chat_reason();
            let event = if reason == DirectChatRunReason::StoreCapacity {
                DirectChatRunEvent::CapacityRejected
            } else {
                DirectChatRunEvent::Failed
            };
            self.emit_run_lifecycle(event, MetricsResult::Error, client_kind, reason)
                .await;
            return Err(ServiceError::InternalError(format!("cannot accept run: {err}")));
        }
        self.emit_run_lifecycle(
            DirectChatRunEvent::Created,
            MetricsResult::Success,
            client_kind,
            DirectChatRunReason::None,
        )
        .await;

        // Outbound interceptor chain (security gateway etc.). Block here is a
        // hard refusal — the run is marked failed and the error surfaces to
        // the caller. Mirrors the missing-credential skip in
        // group_flow::apply_outbound_interceptors so legacy bots are not
        // mass-blocked.
        if !self.interceptors.is_empty() {
            use bcs_service_api::interceptor::{
                InterceptorDecision, OutboundMessage,
            };
            use bcs_domain::{GroupMessage, GroupMessageType, MessageRole};

            let credentials_pair = match (
                bcs_observability::observe_value("chat.credentials.sender", self.registry.get_agent_credentials(&from_bot_id)).await,
                bcs_observability::observe_value("chat.credentials.target", self.registry.get_agent_credentials(&cmd.target_bot_id)).await,
            ) {
                (Some(c), Some(r))
                    if c.agent_code.as_deref().is_some_and(|s| !s.is_empty())
                        && r.agent_code.as_deref().is_some_and(|s| !s.is_empty()) =>
                {
                    Some((c, r))
                }
                _ => {
                    tracing::warn!(
                        sender = %from_bot_id,
                        receiver = %cmd.target_bot_id,
                        run_id = %run_id,
                        "skipping a2a outbound interceptor chain: missing agent_code (legacy/unregistered bot)"
                    );
                    None
                }
            };

            if let Some((caller, receiver)) = credentials_pair {
                let synthetic = GroupMessage {
                    id: run_id.clone(),
                    timestamp: 0,
                    sender: from_bot_id.clone(),
                    content: cmd.message.clone(),
                    message_type: GroupMessageType::default(),
                    bot_name: None,
                    role: MessageRole::default(),
                    run_id: String::new(),
                    history_meta: None,
                    metadata: None,
                    attachments: None,
                };
                let mut outbound = OutboundMessage {
                    group_id: run_id.clone(),
                    message: synthetic,
                    receiver_bot_id: cmd.target_bot_id.clone(),
                    caller,
                    receiver,
                };
                let block_context = DeliveryBlockContext {
                    target: DeliveryMetricTarget::Bot,
                    delivery_kind: DeliveryMetricKind::Send,
                    surface: DeliveryBlockSurface::DirectChat,
                    reason: DeliveryBlockReason::PolicyBlocked,
                };
                if let InterceptorDecision::Block(reason) = bcs_observability::observe_value("chat.security.check", self
                    .interceptors
                    .on_outbound_with_context(&mut outbound, block_context)).await
                {
                    if self
                        .run_store
                        .mark_failed(&run_id, &format!("blocked:{}", reason.code))
                        .await
                    {
                        self.emit_run_lifecycle(
                            DirectChatRunEvent::Failed,
                            MetricsResult::Error,
                            client_kind,
                            DirectChatRunReason::Blocked,
                        )
                        .await;
                    }
                    tracing::warn!(
                        interceptor = %reason.interceptor_id,
                        code = %reason.code,
                        run_id = %run_id,
                        "a2a chat blocked by interceptor chain"
                    );
                    return Err(ServiceError::Forbidden(if reason.user_visible {
                        reason.message
                    } else {
                        "a2a chat blocked by policy".to_string()
                    }));
                }
            }
        }

        let delivery = match bcs_observability::observe_result("chat.delivery.send", self
            .bot_delivery
            .deliver(BotDeliveryCommand {
                target: delivery_target,
                run_id: run_id.clone(),
                frame,
                delivery_kind: BotDeliveryKind::Send,
                provider_transport: if cmd
                    .client
                    .as_deref()
                    .is_some_and(|client| client.starts_with("bcs-cli"))
                {
                    ProviderTransportPreference::Callback
                } else {
                    ProviderTransportPreference::SseFirst
                },
                provider_bypass_headers: cmd.provider_bypass_headers.clone(),
            })).await
        {
            Ok(delivery) => delivery,
            Err(error) => {
                let reason = direct_chat_service_error_reason(&error);
                if self.run_store.mark_failed(&run_id, error.to_string()).await {
                    self.emit_run_lifecycle(
                        DirectChatRunEvent::Failed,
                        MetricsResult::Error,
                        client_kind,
                        reason,
                    )
                    .await;
                }
                return Err(error);
            }
        };

        if !delivery.delivered {
            if self
                .run_store
                .mark_failed(&run_id, "bot_not_connected")
                .await
            {
                self.emit_run_lifecycle(
                    DirectChatRunEvent::Failed,
                    MetricsResult::Error,
                    client_kind,
                    DirectChatRunReason::BotNotConnected,
                )
                .await;
            }
            return Err(ServiceError::BotNotConnected(cmd.target_bot_id));
        }

        if submit_on_provider_ack {
            if bcs_observability::observe_value("chat.run.mark_submitted", self.run_store.mark_submitted(&run_id)).await {
                self.emit_run_lifecycle(
                    DirectChatRunEvent::Submitted,
                    MetricsResult::Success,
                    client_kind,
                    DirectChatRunReason::None,
                )
                .await;
            }
        } else if bcs_observability::observe_value("chat.run.mark_running", self.run_store.mark_running(&run_id)).await {
            self.emit_run_lifecycle(
                DirectChatRunEvent::Running,
                MetricsResult::Success,
                client_kind,
                DirectChatRunReason::None,
            )
            .await;
        }

        Ok(A2aChatOutcome {
            run_id,
            status: if submit_on_provider_ack {
                "submitted"
            } else if cmd.async_mode {
                "running"
            } else {
                "started"
            }
            .to_string(),
            response: None,
        })
    }

}
