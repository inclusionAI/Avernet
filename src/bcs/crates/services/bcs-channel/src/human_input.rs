use super::*;

impl BcsChannelService {
    pub(super) async fn try_consume_human_input(
        &self,
        binding: &ChannelBinding,
        msg: &InboundMessage,
        actor_id: &str,
    ) -> Result<bool, ChannelUseCaseError> {
        let reply_scope_key = match msg.conversation_type.as_str() {
            "2" => fixed_group_reply_scope(&binding.id, &msg.im_conversation_id, actor_id),
            "1" => direct_reply_scope(&binding.id, &msg.im_user_id, actor_id),
            _ => return Ok(false),
        };
        let Some(request) = self
            .human_input_requests
            .find_active_by_scope(&reply_scope_key)
            .await?
        else {
            return Ok(false);
        };

        // COSEC: never trust message text or a visible card title to select a
        // request. Match the authenticated binding, actor and destination
        // against the persisted active HumanInputRequest snapshot.
        let destination_matches = request.binding_id == binding.id
            && request.account_ref == binding.account_ref
            && request.assignee_actor_id == actor_id
            && match request.notification_mode {
                HumanInputNotificationMode::FixedGroup => {
                    msg.conversation_type == "2"
                        && request.im_conversation_id == msg.im_conversation_id
                }
                HumanInputNotificationMode::DirectAssignee => {
                    msg.conversation_type == "1"
                        && request.im_user_id.as_deref() == Some(msg.im_user_id.as_str())
                }
            };
        if !destination_matches {
            return Ok(false);
        }

        let now = (self.now_ms)();
        if request.deadline_ms <= now {
            self.human_input_requests
                .close_for_run_node(
                    &request.run_id,
                    &request.node_id,
                    HumanInputRequestStatus::Expired,
                )
                .await?;
            self.advance_human_input_queue(&request.reply_scope_key)
                .await?;
            return Ok(true);
        }

        let outcome = self
            .collaboration_runtime
            .respond_human_node(RespondHumanNodeCommand {
                run_id: request.run_id.clone(),
                node_id: request.node_id.clone(),
                caller_actor_id: actor_id.to_string(),
                content: msg.text.trim().to_string(),
                source: HumanResponseSource::Channel {
                    binding_id: binding.id.clone(),
                    conversation_id: msg.im_conversation_id.clone(),
                    message_id: msg.msg_id.clone(),
                },
            })
            .await;
        match outcome {
            Ok(outcome) => {
                if !self
                    .human_input_requests
                    .mark_responded(&request.request_id, now)
                    .await?
                {
                    return Err(ChannelUseCaseError::Internal(ServiceError::Conflict(
                        "HumanInput request lost the response completion race".to_string(),
                    )));
                }
                if outcome.run.status != bcs_domain::StateMachineRunStatus::Completed {
                    if let Err(error) = self
                        .deliver_human_input_event(
                            &request,
                            ChannelOutboundPurpose::HumanInputAck,
                            format!(
                                "【输入已接收】{}\n\n流程继续执行。",
                                request.node_display_name
                            ),
                            Some(&msg.msg_id),
                        )
                        .await
                    {
                        warn!(
                            request_id = %request.request_id,
                            run_id = %request.run_id,
                            error = %error,
                            "human_input: response persisted but acknowledgement delivery failed"
                        );
                    }
                }
                self.advance_human_input_queue(&request.reply_scope_key)
                    .await?;
                Ok(true)
            }
            Err(bcs_service_api::CollaborationRuntimeError::Conflict(_)) => {
                self.human_input_requests
                    .close_for_run_node(
                        &request.run_id,
                        &request.node_id,
                        HumanInputRequestStatus::Cancelled,
                    )
                    .await?;
                self.advance_human_input_queue(&request.reply_scope_key)
                    .await?;
                Ok(true)
            }
            Err(error) => Err(ChannelUseCaseError::Internal(ServiceError::InternalError(
                error.to_string(),
            ))),
        }
    }

    pub(super) async fn prepare_human_input_event(
        &self,
        request: &HumanInputRequest,
        purpose: ChannelOutboundPurpose,
        text: String,
        source_im_message_id: Option<&str>,
    ) -> Result<(Arc<dyn ChannelDeliveryPort>, ChannelOutboundEvent), ChannelUseCaseError> {
        let binding = self
            .bindings
            .get(&request.binding_id)
            .await?
            .ok_or_else(|| ChannelUseCaseError::NotFound(request.binding_id.clone()))?;
        if binding.status != BindingStatus::Active
            || binding.channel_type != request.channel_type
            || binding.account_ref != request.account_ref
        {
            return Err(ChannelUseCaseError::InvalidParams(
                "HumanInput request binding snapshot is no longer active".to_string(),
            ));
        }
        let provider = self.provider_for(&binding.channel_type)?;
        let binding_ref = ChannelBindingRef {
            channel_type: binding.channel_type,
            account_ref: binding.account_ref,
        };
        if !provider.delivery().is_available(&binding_ref).await {
            return Err(ChannelUseCaseError::InvalidParams(
                "HumanInput channel delivery is unavailable".to_string(),
            ));
        }
        Ok((provider.delivery(), ChannelOutboundEvent {
                binding_ref,
                im_conversation_id: request.im_conversation_id.clone(),
                im_conversation_type: request.im_conversation_type.clone(),
                im_user_id: request.im_user_id.clone(),
                im_user_display_name: None,
                bcs_session_id: request.session_id.clone(),
                run_id: match purpose {
                    ChannelOutboundPurpose::StateMachineCompleted
                    | ChannelOutboundPurpose::StateMachineFailed => {
                        format!("state-machine-terminal-{}", request.run_id)
                    }
                    _ => format!("human-input-{}", request.request_id),
                },
                sender_actor_id: "bcs_state_machine".to_string(),
                sender_label: "BCS State Machine".to_string(),
                render_sender_label: false,
                sender_role: ParticipantRole::Driver,
                kind: ChannelOutboundEventKind::System,
                purpose,
                text: Some(text),
                raw_payload: serde_json::json!({
                    "request_id": request.request_id,
                    "run_id": request.run_id,
                    "node_id": request.node_id,
                }),
                render_hint: ChannelRenderHint::Render,
                source_im_message_id: source_im_message_id.map(str::to_string),
        }))
    }

    pub(super) async fn deliver_human_input_event(
        &self, request: &HumanInputRequest, purpose: ChannelOutboundPurpose,
        text: String, source_im_message_id: Option<&str>,
    ) -> Result<Option<String>, ChannelUseCaseError> {
        let (delivery, event) = self.prepare_human_input_event(request, purpose, text, source_im_message_id).await?;
        let result = delivery.deliver_event(event).await?;
        if !result.delivered {
            return Err(ChannelUseCaseError::Internal(result.error.unwrap_or_else(|| ServiceError::InternalError("HumanInput channel delivery was not confirmed".into()))));
        }
        Ok(result.provider_message_ref)
    }

    pub(super) async fn human_notification_is_current(&self, request: &HumanInputRequest) -> Result<bool, ChannelUseCaseError> {
        if request.deadline_ms <= (self.now_ms)() { return Ok(false); }
        self.collaboration_runtime.human_input_notification_is_current(
            &request.run_id, &request.session_id, &request.node_id, request.deadline_ms,
        ).await.map_err(|error| ChannelUseCaseError::Internal(ServiceError::InternalError(error.to_string())))
    }

    pub(super) async fn resume_human_input_notification(
        &self,
        request: &HumanInputRequest,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        match request.status {
            HumanInputRequestStatus::Active => Ok(SessionChannelDeliveryOutcome::Delivered),
            HumanInputRequestStatus::NotificationPending | HumanInputRequestStatus::Notifying => {
                if request.deadline_ms <= (self.now_ms)() {
                    self.human_input_requests.close_for_run_node(&request.run_id, &request.node_id,
                        HumanInputRequestStatus::Expired).await?;
                    self.advance_human_input_queue(&request.reply_scope_key).await
                        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
                    return Ok(SessionChannelDeliveryOutcome::NotApplicable);
                }
                // Includes legacy rows: an old writer may already have sent.
                if request.status == HumanInputRequestStatus::Notifying {
                    return Ok(SessionChannelDeliveryOutcome::NotApplicable);
                }
                let activation = self.activate_human_input_request(request).await
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?;
                if let HumanInputActivation::DeliveryFailed(error) = activation {
                    self.advance_human_input_queue(&request.reply_scope_key).await
                        .map_err(|error| ServiceError::InternalError(error.to_string()))?;
                    return Err(ServiceError::InternalError(error));
                }
                Ok(SessionChannelDeliveryOutcome::Delivered)
            }
            HumanInputRequestStatus::Queued => {
                self.advance_human_input_queue(&request.reply_scope_key).await
                    .map_err(|error| ServiceError::InternalError(error.to_string()))?;
                Ok(SessionChannelDeliveryOutcome::Delivered)
            }
            HumanInputRequestStatus::DeliveryFailed => Err(ServiceError::InvalidOperation {
                message: "HumanInput notification delivery previously failed; use Workbench to respond".into(),
                request_id: Some(request.request_id.clone()),
            }),
            HumanInputRequestStatus::Responded | HumanInputRequestStatus::Expired
            | HumanInputRequestStatus::Cancelled => Ok(SessionChannelDeliveryOutcome::NotApplicable),
        }
    }

    pub(super) async fn activate_human_input_request(
        &self, request: &HumanInputRequest,
    ) -> Result<HumanInputActivation, ChannelUseCaseError> {
        let queued = self.human_input_requests.count_queued(&request.reply_scope_key).await?;
        let mut text = request.notification_text.clone();
        if queued > 0 { text.push_str(&format!("\n\n另有 {queued} 项等待处理。")); }
        // Read-only preflight failures leave the request provably unsent.
        let (delivery, event) = self.prepare_human_input_event(request, ChannelOutboundPurpose::HumanInputRequest, text, None).await?;
        if !self.human_notification_is_current(request).await? {
            self.human_input_requests.close_for_run_node(&request.run_id, &request.node_id,
                if request.deadline_ms <= (self.now_ms)() { HumanInputRequestStatus::Expired } else { HumanInputRequestStatus::Cancelled }).await?;
            return Ok(HumanInputActivation::Unchanged);
        }
        if !self.human_input_requests.begin_notification(&request.request_id, (self.now_ms)()).await? {
            return Ok(HumanInputActivation::Unchanged);
        }
        // From this durable marker onwards every interruption is ambiguous.
        // Only this CAS winner can acknowledge/fail this external invocation.
        let result = delivery.deliver_event(event).await;
        match result {
            Ok(result) if result.delivered => {
                if !self.human_notification_is_current(request).await? {
                    self.human_input_requests.close_for_run_node(&request.run_id, &request.node_id,
                        if request.deadline_ms <= (self.now_ms)() { HumanInputRequestStatus::Expired } else { HumanInputRequestStatus::Cancelled }).await?;
                    return Ok(HumanInputActivation::Unchanged);
                }
                if !self.human_input_requests.mark_active(&request.request_id, result.provider_message_ref.as_deref(), (self.now_ms)()).await? {
                    return Ok(HumanInputActivation::Unchanged);
                }
                Ok(HumanInputActivation::Active)
            }
            result => {
                let error = match result {
                    Err(error) => error,
                    Ok(result) => result.error.unwrap_or_else(|| ServiceError::InternalError("HumanInput channel delivery was not confirmed".into())),
                };
                let diagnostic = error.to_string();
                if !self.human_input_requests.mark_delivery_failed(&request.request_id, &diagnostic).await? {
                    return Ok(HumanInputActivation::Unchanged);
                }
                Ok(HumanInputActivation::DeliveryFailed(diagnostic))
            }
        }
    }

    pub(super) async fn advance_human_input_queue(
        &self,
        reply_scope_key: &str,
    ) -> Result<(), ChannelUseCaseError> {
        let mut processed = 0;
        loop {
            // The head can belong to a terminal/deleted Run which is no longer
            // in the active-Run page. A waiting Run must be able to release it.
            if let Some(head) = self.human_input_requests.find_occupying_by_scope(reply_scope_key).await? {
                if self.human_notification_is_current(&head).await? { return Ok(()); }
                self.human_input_requests.close_for_run_node(&head.run_id, &head.node_id,
                    if head.deadline_ms <= (self.now_ms)() { HumanInputRequestStatus::Expired } else { HumanInputRequestStatus::Cancelled }).await?;
            }
            let Some(next) = self
                .human_input_requests
                .promote_next(reply_scope_key, (self.now_ms)())
                .await?
            else {
                return Ok(());
            };
            // Only a durably recorded delivery failure releases this slot.
            // Persistence errors must reach the caller instead of being skipped.
            if matches!(self.activate_human_input_request(&next).await?, HumanInputActivation::Active) {
                return Ok(());
            }
            processed += 1;
            if processed == 32 {
                // Yield between batches, but keep responsibility for draining
                // this scope: the recovery scanner may be disabled.
                tokio::task::yield_now().await;
                processed = 0;
            }
        }
    }

}
