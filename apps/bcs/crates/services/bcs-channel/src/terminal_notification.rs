use super::*;
use bcs_service_api::StateMachineTerminalNotification;

impl BcsChannelService {
    pub(super) async fn prepare_terminal_notifications(&self, event: &StateMachineTerminalEvent) -> ServiceResult<Vec<StateMachineTerminalNotification>> {
        let mut requests = self.human_input_requests.list_by_run(&event.run_id).await?;
        requests.sort_by(|a, b| a.request_id.cmp(&b.request_id));
        let text = match event.status {
            StateMachineTerminalStatus::Completed => {
                let mut text = format!("【协同已完成】{}", event.workflow_name);
                if let Some(output) = event.output.as_deref().filter(|v| !v.trim().is_empty()) {
                    text.push_str("\n\n"); text.push_str(&truncate_chars(output, 2_000));
                }
                text
            }
            StateMachineTerminalStatus::Failed => format!("【协同执行失败】{}\n\n请在 Workbench 查看详情。", event.workflow_name),
        };
        let mut destinations = HashSet::new();
        let mut notifications = Vec::new();
        for r in requests {
            if r.session_id != event.session_id {
                return Err(ServiceError::Conflict("terminal IM request belongs to another Session".into()));
            }
            if !destinations.insert((r.binding_id.clone(), r.im_conversation_id.clone(), r.im_conversation_type.clone(), r.im_user_id.clone())) { continue; }
            notifications.push(StateMachineTerminalNotification { binding_id: r.binding_id, channel_type: r.channel_type,
                account_ref: r.account_ref, im_conversation_id: r.im_conversation_id, im_conversation_type: r.im_conversation_type,
                im_user_id: r.im_user_id, request_id: r.request_id, node_id: r.node_id, text: text.clone() });
        }
        Ok(notifications)
    }

    pub(super) async fn terminal_notification_provider(&self, n: &StateMachineTerminalNotification) -> ServiceResult<Arc<dyn ChannelProvider>> {
        let binding = self.bindings.get(&n.binding_id).await?.ok_or_else(|| ServiceError::InvalidOperation {
            message: "terminal IM binding is unavailable".into(), request_id: None,
        })?;
        if binding.status != BindingStatus::Active || binding.channel_type != n.channel_type || binding.account_ref != n.account_ref {
            return Err(ServiceError::Conflict("terminal IM binding no longer matches saved destination".into()));
        }
        self.provider_for(&n.channel_type).map_err(|e| ServiceError::InternalError(e.to_string()))
    }

    pub(super) async fn send_terminal_notification(&self, event: &StateMachineTerminalEvent, n: &StateMachineTerminalNotification) -> ServiceResult<Option<String>> {
        let provider = self.terminal_notification_provider(n).await?;
        let result = provider.delivery().deliver_event(ChannelOutboundEvent {
            binding_ref: ChannelBindingRef { channel_type: n.channel_type.clone(), account_ref: n.account_ref.clone() },
            im_conversation_id: n.im_conversation_id.clone(), im_conversation_type: n.im_conversation_type.clone(),
            im_user_id: n.im_user_id.clone(), im_user_display_name: None, bcs_session_id: event.session_id.clone(),
            run_id: format!("state-machine-terminal-{}", event.run_id), sender_actor_id: "bcs_state_machine".into(),
            sender_label: "BCS State Machine".into(), render_sender_label: false, sender_role: ParticipantRole::Driver,
            kind: ChannelOutboundEventKind::System,
            purpose: match event.status { StateMachineTerminalStatus::Completed => ChannelOutboundPurpose::StateMachineCompleted,
                StateMachineTerminalStatus::Failed => ChannelOutboundPurpose::StateMachineFailed },
            text: Some(n.text.clone()), raw_payload: serde_json::json!({ "request_id": n.request_id, "run_id": event.run_id, "node_id": n.node_id }),
            render_hint: ChannelRenderHint::Render, source_im_message_id: None,
        }).await?;
        if !result.delivered {
            return Err(result.error.unwrap_or_else(|| ServiceError::InternalError("terminal IM delivery was not confirmed".into())));
        }
        Ok(result.provider_message_ref)
    }

    pub(super) async fn finish_terminal_notification(&self, event: &StateMachineTerminalEvent) -> ServiceResult<()> {
        self.observe_state_machine_terminal(event).await;
        let requests = self.human_input_requests.list_by_run(&event.run_id).await?;
        let mut nodes = HashSet::new(); let mut scopes = HashSet::new();
        for request in requests {
            if event.status == StateMachineTerminalStatus::Failed && nodes.insert(request.node_id.clone()) {
                self.human_input_requests.close_for_run_node(&event.run_id, &request.node_id, HumanInputRequestStatus::Cancelled).await?;
            }
            scopes.insert(request.reply_scope_key);
        }
        for scope in scopes {
            self.advance_human_input_queue(&scope).await.map_err(|e| ServiceError::InternalError(e.to_string()))?;
        }
        Ok(())
    }
}
