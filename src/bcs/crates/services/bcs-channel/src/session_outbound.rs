use super::*;

#[async_trait]
impl SessionChannelOutboundPort for BcsChannelService {
    async fn prepare_state_machine_terminal(&self, event: &StateMachineTerminalEvent) -> ServiceResult<Vec<bcs_service_api::StateMachineTerminalNotification>> {
        self.prepare_terminal_notifications(event).await
    }
    async fn validate_terminal_notification(&self, notification: &bcs_service_api::StateMachineTerminalNotification) -> ServiceResult<()> {
        let provider = self.terminal_notification_provider(notification).await?;
        if !provider.delivery().is_available(&ChannelBindingRef { channel_type: notification.channel_type.clone(), account_ref: notification.account_ref.clone() }).await {
            return Err(ServiceError::InternalError("terminal IM channel is unavailable before send".into()));
        }
        Ok(())
    }
    async fn deliver_terminal_notification(&self, event: &StateMachineTerminalEvent, notification: &bcs_service_api::StateMachineTerminalNotification) -> ServiceResult<Option<String>> {
        self.send_terminal_notification(event, notification).await
    }
    async fn finish_state_machine_terminal(&self, event: &StateMachineTerminalEvent) -> ServiceResult<()> {
        self.finish_terminal_notification(event).await
    }

    async fn validate_human_input_channel(
        &self,
        group_id: &str,
        channel_type: &str,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let target = BindingTarget::Group {
            group_id: group_id.to_string(),
        };
        let bindings = self
            .bindings
            .list_by_target(&target, Some(channel_type))
            .await?
            .into_iter()
            .filter(|binding| binding.status == BindingStatus::Active)
            .collect::<Vec<_>>();
        let binding = match bindings.as_slice() {
            [binding] => binding,
            [] => {
                return Err(ServiceError::InvalidOperation {
                    message: format!(
                        "no active {channel_type} ChannelBinding exists for group {group_id}"
                    ),
                    request_id: None,
                });
            }
            _ => {
                return Err(ServiceError::Conflict(format!(
                    "multiple active {channel_type} ChannelBindings exist for group {group_id}"
                )));
            }
        };
        let provider =
            self.providers
                .get(channel_type)
                .ok_or_else(|| ServiceError::InvalidOperation {
                    message: format!("channel provider '{channel_type}' is not available"),
                    request_id: None,
                })?;
        let binding_ref = ChannelBindingRef {
            channel_type: binding.channel_type.clone(),
            account_ref: binding.account_ref.clone(),
        };
        if !provider.delivery().is_available(&binding_ref).await {
            return Err(ServiceError::InvalidOperation {
                message: format!(
                    "channel provider '{channel_type}' is unavailable for group {group_id}"
                ),
                request_id: None,
            });
        }
        Ok(SessionChannelDeliveryOutcome::Delivered)
    }

    async fn recover_human_input_requests(&self, run_id: &str, session_id: &str) -> ServiceResult<Vec<String>> {
        let requests = self.human_input_requests.list_by_run(run_id).await?;
        let covered_nodes = requests.iter().map(|request| request.node_id.clone()).collect();
        let mut scopes = HashSet::new();
        for request in requests {
            if request.session_id != session_id { return Err(ServiceError::Conflict("HumanInput recovery Session mismatch".into())); }
            if !matches!(request.status, HumanInputRequestStatus::Queued | HumanInputRequestStatus::NotificationPending
                | HumanInputRequestStatus::Notifying | HumanInputRequestStatus::Active) { continue; }
            if !self.human_notification_is_current(&request).await.map_err(|e| ServiceError::InternalError(e.to_string()))? {
                self.human_input_requests.close_for_run_node(run_id, &request.node_id,
                    if request.deadline_ms <= (self.now_ms)() { HumanInputRequestStatus::Expired } else { HumanInputRequestStatus::Cancelled }).await?;
                scopes.insert(request.reply_scope_key);
            } else {
                self.resume_human_input_notification(&request).await?;
            }
        }
        for scope in scopes { self.advance_human_input_queue(&scope).await.map_err(|e| ServiceError::InternalError(e.to_string()))?; }
        Ok(covered_nodes)
    }

    async fn publish_human_input_ready(
        &self,
        event: HumanInputReadyEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        // Re-delivery of the same logical interaction must use its original
        // text and destination, even after a restart or a Definition change.
        if let Some(request) = self.human_input_requests.get(&event.event_id).await? {
            if request.run_id != event.run_id || request.session_id != event.session_id
                || request.node_id != event.node_id || request.assignee_actor_id != event.assignee_actor_id
                || request.channel_type != event.channel_type || request.notification_mode != event.notification_mode
            {
                return Err(ServiceError::Conflict("HumanInput event identity conflicts with its saved request".into()));
            }
            let binding = self.bindings.get(&request.binding_id).await?
                .ok_or_else(|| ServiceError::InvalidOperation {
                    message: "HumanInput saved binding no longer exists".into(), request_id: Some(event.event_id.clone()),
                })?;
            if binding.target != (BindingTarget::Group { group_id: event.group_id.clone() }) {
                return Err(ServiceError::Conflict("HumanInput event group conflicts with its saved binding".into()));
            }
            return self.resume_human_input_notification(&request).await;
        }
        let target = BindingTarget::Group {
            group_id: event.group_id.clone(),
        };
        let active_bindings = self
            .bindings
            .list_by_target(&target, Some(&event.channel_type))
            .await?
            .into_iter()
            .filter(|binding| binding.status == BindingStatus::Active)
            .collect::<Vec<_>>();
        let binding = match active_bindings.as_slice() {
            [binding] => binding.clone(),
            [] => {
                return Err(ServiceError::InvalidOperation {
                    message: format!(
                        "no active {} ChannelBinding exists for group {}",
                        event.channel_type, event.group_id
                    ),
                    request_id: Some(event.event_id),
                });
            }
            _ => {
                return Err(ServiceError::Conflict(format!(
                    "multiple active {} ChannelBindings exist for group {}",
                    event.channel_type, event.group_id
                )));
            }
        };
        let (im_conversation_id, im_conversation_type, im_user_id, reply_scope_key) =
            match event.notification_mode {
                HumanInputNotificationMode::FixedGroup => {
                    let conversation_id = event
                        .fixed_group_conversation_id
                        .clone()
                        .filter(|value| !value.trim().is_empty())
                        .ok_or_else(|| ServiceError::InvalidOperation {
                            message: "fixed_group HumanInput notification has no conversation id"
                                .to_string(),
                            request_id: Some(event.event_id.clone()),
                        })?;
                    (
                        conversation_id.clone(),
                        "2".to_string(),
                        None,
                        fixed_group_reply_scope(
                            &binding.id,
                            &conversation_id,
                            &event.assignee_actor_id,
                        ),
                    )
                }
                HumanInputNotificationMode::DirectAssignee => {
                    let provider = self.providers.get(&binding.channel_type).ok_or_else(|| {
                        ServiceError::InvalidOperation {
                            message: format!(
                                "channel provider '{}' is not available",
                                binding.channel_type
                            ),
                            request_id: Some(event.event_id.clone()),
                        }
                    })?;
                    // COSEC: only provider-validated actor identities may become
                    // external direct-message recipients.
                    let im_user_id = provider
                        .resolve_direct_recipient(&event.assignee_actor_id)
                        .map_err(|error| ServiceError::InvalidOperation {
                            message: error.to_string(),
                            request_id: Some(event.event_id.clone()),
                        })?
                        .filter(|value| !value.is_empty() && value.trim().len() == value.len())
                        .ok_or_else(|| ServiceError::InvalidOperation {
                            message: format!(
                                "channel provider '{}' cannot resolve HumanInput assignee {}",
                                binding.channel_type, event.assignee_actor_id
                            ),
                            request_id: Some(event.event_id.clone()),
                        })?;
                    (
                        im_user_id.clone(),
                        "1".to_string(),
                        Some(im_user_id.clone()),
                        direct_reply_scope(&binding.id, &im_user_id, &event.assignee_actor_id),
                    )
                }
            };
        let text = human_input_notification::render(&event)?;
        let deadline_ms =
            event
                .timeout_deadline_ms
                .ok_or_else(|| ServiceError::InvalidOperation {
                    message: "HumanInput notification requires a deadline".to_string(),
                    request_id: Some(event.event_id.clone()),
                })?;
        let request = HumanInputRequest {
            request_id: event.event_id,
            session_id: event.session_id,
            run_id: event.run_id,
            node_id: event.node_id,
            binding_id: binding.id,
            channel_type: binding.channel_type,
            account_ref: binding.account_ref,
            notification_mode: event.notification_mode,
            reply_scope_key: reply_scope_key.clone(),
            active_slot_key: None,
            assignee_actor_id: event.assignee_actor_id,
            im_conversation_id,
            im_conversation_type,
            im_user_id,
            node_display_name: event.display_name,
            notification_text: text,
            deadline_ms,
            status: HumanInputRequestStatus::Queued,
            provider_message_ref: None,
            delivery_attempts: 0,
            last_delivery_error: None,
            created_at: (self.now_ms)(),
            activated_at: None,
            responded_at: None,
        };
        match self.human_input_requests.enqueue(request.clone()).await? {
            HumanInputEnqueueDisposition::Queued => {
                let queued = self
                    .human_input_requests
                    .count_queued(&request.reply_scope_key)
                    .await?;
                if let Err(error) = self
                    .deliver_human_input_event(
                        &request,
                        ChannelOutboundPurpose::HumanInputQueueSummary,
                        format!(
                            "【待办队列更新】当前已有请求等待回复，另有 {queued} 项排队；完成当前项后会继续通知。"
                        ),
                        None,
                    )
                    .await
                {
                    warn!(
                        request_id = %request.request_id,
                        run_id = %request.run_id,
                        error = %error,
                        "human_input: queued summary delivery failed"
                    );
                }
            }
            HumanInputEnqueueDisposition::Notifying => {
                let request = self
                    .human_input_requests
                    .get(&request.request_id)
                    .await?
                    .ok_or_else(|| {
                        ServiceError::InternalError(
                            "enqueued HumanInput request is missing".to_string(),
                        )
                    })?;
                return self.resume_human_input_notification(&request).await;
            }
        }
        Ok(SessionChannelDeliveryOutcome::Delivered)
    }

    async fn publish_state_machine_terminal(
        &self,
        event: StateMachineTerminalEvent,
    ) -> ServiceResult<SessionChannelDeliveryOutcome> {
        let notifications = self.prepare_terminal_notifications(&event).await?;
        self.finish_terminal_notification(&event).await?;
        if notifications.is_empty() { return Ok(SessionChannelDeliveryOutcome::NotApplicable); }
        let mut errors = Vec::new();
        for notification in notifications {
            let result = async {
                self.validate_terminal_notification(&notification).await?;
                self.send_terminal_notification(&event, &notification).await
            }.await;
            if let Err(error) = result { errors.push(error.to_string()); }
        }
        if errors.is_empty() { Ok(SessionChannelDeliveryOutcome::Delivered) }
        else { Err(ServiceError::InternalError(format!("terminal IM delivery incomplete: {}", errors.join("; ")))) }
    }
}
