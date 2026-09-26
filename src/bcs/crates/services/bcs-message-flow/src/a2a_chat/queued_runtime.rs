//! Send-time authorization and scoped Direct A2A events.
use super::*;
use super::queued::{DirectProjection, invalid, transition, transition_conflict};
use bcs_domain::message_delivery::{DeliveryFlowKind, PersistedMessageDelivery};
use bcs_service_api::{BotDeliveryTarget, BotRunTransportOwner, PreparedManagedDelivery, BotEventCommand, BotEventOutcome, ChatEventState};
use bcs_service_api::core::message_delivery::DeliveryLifecycleEvent as Event;

impl A2aChat {
    pub(crate) async fn prepare_direct(&self, flow: &crate::BcsMessageFlow, row: &PersistedMessageDelivery) -> ServiceResult<PreparedManagedDelivery> {
        let p: DirectProjection = serde_json::from_value(row.semantic_projection_json.clone()).map_err(|_| invalid("invalid direct projection"))?;
        if p.version != 1 || row.flow_kind != DeliveryFlowKind::DirectA2a || !row.group_id.is_empty() { return Err(invalid("invalid direct projection scope")); }
        let sessions = self.session_management.as_ref().ok_or_else(|| invalid("direct session service unavailable"))?;
        let registration = sessions.session_registration(&row.session_id).await.map_err(|_| invalid("direct session read failed"))?
            .ok_or_else(|| invalid("session_registry_missing"))?;
        if registration.session_type != bcs_service_api::port::repo::session_registry::SessionType::DirectA2a { return Err(invalid("session_type_conflict")); }
        let run = row.run_id.as_deref().ok_or_else(|| invalid("direct run missing"))?;
        let record = self.run_store.try_get(run).await?.ok_or_else(|| invalid("direct chat run missing"))?;
        if record.delivery_id.as_deref() != Some(&row.delivery_id) || record.source_message_id.as_deref() != Some(&row.source_message_id)
            || record.state.is_terminal() || record.expires_at_ms != p.expires_at_ms { return Err(invalid("direct chat run identity mismatch")); }
        let message = flow.message_repo.as_ref().ok_or_else(|| invalid("direct message store unavailable"))?
            .get_message_by_id(&row.session_id, &row.source_message_id).await.map_err(|_| invalid("direct source read failed"))?
            .ok_or_else(|| invalid("direct source missing"))?;
        if message.run_id != run || message.sender_id != p.from_bot_id || message.owner_bot_id.as_deref() != Some(&row.target_bot_id)
            || message.session_id != row.session_id || !message.group_id.is_empty() { return Err(invalid("direct source scope mismatch")); }
        let bot = if let Some(code) = &p.organization_code {
            self.organization.as_ref().ok_or_else(|| invalid("organization service unavailable"))?.authorize_pair(code, &p.from_bot_id, &row.target_bot_id).await?;
            self.ensure_organization_target_reachable(&p.from_bot_id, &row.target_bot_id).await?
        } else { self.ensure_target_reachable(&p.from_bot_id, &row.target_bot_id).await? };
        if bot.status == ActorStatus::Hidden { return Err(invalid("direct target hidden")); }
        let text = message.content.get("text").and_then(Value::as_str).ok_or_else(|| invalid("direct source text missing"))?;
        self.authorize_queued_text(run, &p.from_bot_id, &row.target_bot_id, text).await?;
        let target = self.registry.resolve_delivery_target(&row.target_bot_id).await?;
        let headers = bcs_config_api::queued_provider_headers::snapshot(&p.provider_route_headers, &flow.queue_persistable_headers).map_err(invalid)?;
        let connection_id = if target.is_http_provider() { None } else {
            Some(self.bot_delivery.connection_identity(&target).await.ok_or_else(|| invalid("direct connection unavailable"))?)
        };
        let owner = match &target {
            BotDeliveryTarget::WebSocket { .. } => BotRunTransportOwner::WebSocket,
            BotDeliveryTarget::HttpProvider { provider_id, provider_bot_ref, .. } => BotRunTransportOwner::HttpProvider { provider_id: provider_id.clone(), provider_bot_ref: provider_bot_ref.clone() },
        };
        let headers = if target.is_http_provider() { headers } else { Vec::new() };
        let mut frame = build_chat_send_frame(run, &row.session_id, &row.target_bot_id, &p.from_bot_id, &p.sender_name, &p.from_actor_id, text, &p.target_tags, p.caller_wait_mode.as_deref())?;
        if let BcsFrame::Request(request) = &mut frame {
            if let Some(params) = &mut request.params { params["idempotency_key"] = serde_json::json!(row.idempotency_key); }
        }
        let transport = crate::queued_group::QueuedTransportContext { version: 1, owner, connection_id,
            downstream_run_id: None, cancel_reason: None, downstream_session_key: row.session_id.clone(),
            provider_route_headers: headers.clone(), relay_route_headers: None };
        Ok(PreparedManagedDelivery {
            command: BotDeliveryCommand { target, run_id: run.into(), frame, delivery_kind: BotDeliveryKind::Send,
                provider_transport: if p.client.as_deref().is_some_and(|c| c.starts_with("bcs-cli")) { ProviderTransportPreference::Callback } else { ProviderTransportPreference::SseFirst },
                provider_bypass_headers: headers },
            transport_context_json: serde_json::to_value(transport)?,
        })
    }

    async fn authorize_queued_text(&self, run: &str, sender: &str, target: &str, text: &str) -> ServiceResult<()> {
        use bcs_service_api::interceptor::{InterceptorDecision, OutboundMessage};
        if self.interceptors.is_empty() { return Ok(()); }
        let (Some(caller), Some(receiver)) = (self.registry.get_agent_credentials(sender).await, self.registry.get_agent_credentials(target).await) else { return Ok(()); };
        if caller.agent_code.as_deref().is_none_or(str::is_empty) || receiver.agent_code.as_deref().is_none_or(str::is_empty) { return Ok(()); }
        let mut outbound = OutboundMessage { group_id: run.into(), receiver_bot_id: target.into(), caller, receiver,
            message: bcs_domain::GroupMessage { id: run.into(), timestamp: 0, sender: sender.into(), content: text.into(),
                message_type: Default::default(), bot_name: None, role: Default::default(), run_id: String::new(), history_meta: None, metadata: None, attachments: None } };
        let context = DeliveryBlockContext { target: DeliveryMetricTarget::Bot, delivery_kind: DeliveryMetricKind::Send,
            surface: DeliveryBlockSurface::DirectChat, reason: DeliveryBlockReason::PolicyBlocked };
        if let InterceptorDecision::Block(_) = self.interceptors.on_outbound_with_context(&mut outbound, context).await {
            return Err(ServiceError::Forbidden("direct outbound policy rejected delivery".into()));
        }
        Ok(())
    }

    pub(crate) async fn direct_event(&self, flow: &crate::BcsMessageFlow, row: &PersistedMessageDelivery, cmd: &BotEventCommand) -> ServiceResult<BotEventOutcome> {
        let service = flow.managed_deliveries.as_ref().ok_or_else(|| invalid("direct queue unavailable"))?;
        let run = row.run_id.as_deref().ok_or_else(|| invalid("direct run missing"))?;
        let record = self.run_store.try_get(run).await?.ok_or_else(|| invalid("direct chat run missing"))?;
        if !row.state.status.is_terminal() {
            if !row.state.may_have_been_sent { return Err(invalid("direct event before send-start")); }
            let mut next = record.clone();
            let mut payload = cmd.event_payload.clone();
            if payload.get("state").is_none() { payload["state"] = serde_json::to_value(&cmd.state)?; }
            let event_name = if cmd.event_type == "chat" { "chat.event" } else { &cmd.event_type };
            let frame = serde_json::json!({"type":"event", "event":event_name, "payload":payload});
            let parsed = drain_chat_event_with_mode(&frame.to_string(), &mut next.accumulated_content, record.response_mode);
            if let DrainOutcome::Error(_) = &parsed { next.error_message = Some("downstream_error".into()); }
            // Checkpoint the final body BEFORE committing the delivery terminal.
            // Until that commit ChatRun remains nonterminal and cannot release a lane.
            if next.accumulated_content != record.accumulated_content || next.error_message != record.error_message {
                crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "direct_response_checkpoint",
                    |error| matches!(error, ServiceError::InternalError(_)),
                    || self.run_store.save_managed(&record, next.clone())).await?;
            }
            let event = if matches!(cmd.event_type.as_str(), "chat" | "chat.event") {
                match cmd.state { ChatEventState::Final => Some(Event::Completed), ChatEventState::Error => Some(Event::Failed), ChatEventState::Aborted => Some(Event::Aborted), _ => None }
            } else { None };
            if let Some(event) = event {
                let mut current = row.clone();
                let mut committed = false;
                for _ in 0..4 {
                    let command = transition(&current, event, None);
                    match crate::storage_retry::retry(crate::storage_retry::shutdown(flow), "direct_terminal",
                        crate::storage_retry::managed_storage, || service.transition(command.clone())).await {
                        Ok(_) => { committed = true; break; }
                        Err(error) if transition_conflict(&error) => {
                            current = self.managed_row(&record).await?.ok_or_else(|| invalid("direct delivery missing"))?;
                            if current.state.status.is_terminal() { committed = true; break; }
                        }
                        Err(_) => return Err(invalid("direct terminal persistence failed")),
                    }
                }
                if !committed { return Err(invalid("direct terminal changed concurrently")); }
            } else if cmd.state == ChatEventState::Delta {
                if let Some(request) = &row.request_id {
                    service.accept_run(request, &row.target_bot_id, None, now_ms() as i64).await.map_err(|_| invalid("direct receipt persistence failed"))?;
                }
            }
        }
        let fresh = self.managed_row(&record).await?.ok_or_else(|| invalid("direct delivery missing"))?;
        self.reconcile_direct(&fresh).await?;
        let terminal = fresh.state.status.is_terminal();
        Ok(BotEventOutcome { bot_deliveries: Vec::new(), frontend_deliveries: Vec::new(), unregistered_run_ids: if terminal { vec![run.into()] } else { Vec::new() },
            mentions: Vec::new(), delivered_count: 0, failed_count: 0, delivery_results: Vec::new() })
    }
}
