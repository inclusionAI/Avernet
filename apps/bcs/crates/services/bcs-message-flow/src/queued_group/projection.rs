use super::*;

impl QueuedGroupProjection {
    pub(crate) fn timeout_notice(row: &PersistedMessageDelivery, manager: &str) -> ServiceResult<Self> {
        let mut projection = Self::decode(row).map_err(|e| invalid(&e))?;
        // The notice rides the ordinary lifecycle toward the Manager: a
        // timed-out caller dispatched row hands over NONE of its caller
        // marks — caller_required would make the system notice permanently
        // un-preparable under the §15.4 guard.
        projection.caller_execution = None;
        projection.caller_required = false;
        projection.caller_task = String::new();
        projection.caller_delegation = None;
        projection.caller_lineage = None;
        projection.caller_channel = None;
        projection.caller_dispatch = None;
        projection.caller_terminal = None;
        projection.caller_cancel = None;
        projection.task = None;
        projection.mentions = vec![manager.to_string()];
        projection.target_tags.clear();
        projection.sender_name = bcs_service_api::core::BCS_SYSTEM_MESSAGE.into();
        projection.sender_owner = None;
        projection.text_projection = TextProjection::Original;
        projection.reply_context = None;
        projection.forward_hop = None;
        projection.provider_route_headers.clear();
        Ok(projection)
    }
    pub(crate) fn task_result(row: &PersistedMessageDelivery, task: crate::queued_task::TaskIntent, tags: Vec<String>) -> ServiceResult<Self> {
        let mut projection = Self::decode(row).map_err(|e| invalid(&e))?;
        projection.caller_execution = None;
        projection.sender_name = task.worker_name.clone();
        projection.target_tags = tags;
        projection.mentions = vec![task.manager.clone()];
        projection.task = Some(task);
        // The result delivery is a DIFFERENT execution than the dispatch it
        // answers (review P1.III.2): the worker's caller marks, task text
        // and the target-config-decided purpose belong to the WORKER's
        // protected dispatch, never to the Manager's result delivery — a
        // copied projection would re-send the ORIGINAL TASK text toward
        // the Manager and carry the Worker's execution purpose onto it.
        // Cleared here so the result leg rides the ordinary lifecycle with
        // its own canonical result message as the content of record.
        projection.caller_required = false;
        projection.caller_task = String::new();
        projection.caller_delegation = None;
        projection.caller_channel = None;
        projection.caller_dispatch = None;
        projection.caller_terminal = None;
        projection.caller_cancel = None;
        // The result leg is also never a lineage carrier (§7.2.1): the
        // Manager's own Owner association never rides onto the Worker's
        // result delivery.
        projection.caller_lineage = None;
        Ok(projection)
    }
    pub(crate) fn task(group: &Group, recipient: &str, sender_name: &str, task: crate::queued_task::TaskIntent) -> ServiceResult<Self> {
        let mut projection = Self::system(group, recipient, false)?;
        projection.sender_name = sender_name.into();
        projection.mentions = vec![recipient.into()];
        projection.task = Some(task);
        Ok(projection)
    }
    pub(crate) fn system(group: &Group, recipient: &str, required_context: bool) -> ServiceResult<Self> {
        let participant = group.participants.iter().find(|p| p.bot_uuid == recipient)
            .ok_or_else(|| invalid("system queue target is not a session participant"))?;
        let snapshot = crate::protocol_context::group_context_input(group);
        Ok(Self {
            caller_execution: None,
            version: 1, caller_required: false, caller_task: String::new(), caller_delegation: None, caller_lineage: None, caller_channel: None,
            caller_dispatch: None, caller_terminal: None, caller_cancel: None,
            task: None, task_rejected_id: None, task_uncertain_id: None, task_control_notice: false, required_context, drain_context: false, policy_version: None,
            driver_bot: snapshot.driver_bot, originator: snapshot.originator,
            participants: snapshot.participants.into_iter().map(|p| ParticipantProjection {
                id: p.id, name: p.name, role: p.role, is_bot: p.is_bot,
            }).collect(),
            direct_bot: false,
            group_type: crate::protocol_context::group_type_wire(group.group_strategy),
            mentions: Vec::new(), target_tags: participant.tags.clone(),
            sender_name: bcs_service_api::core::BCS_SYSTEM_MESSAGE.into(), sender_owner: None,
            thinking: None, text_projection: TextProjection::Original,
            reply_context: None, forward_hop: None, provider_route_headers: Vec::new(),
            admission_rejection: None,
        })
    }
    pub fn with_reply_context(
        mut self,
        mut context: bcs_protocol::GroupContext,
        forward_hop: Option<u32>,
        strip_mentions: bool,
    ) -> Self {
        context.message.clear();
        self.reply_context = Some(context);
        self.forward_hop = forward_hop;
        self.text_projection = if strip_mentions {
            TextProjection::RoutedMentions
        } else {
            TextProjection::Original
        };
        self
    }
    pub async fn capture(
        flow: &BcsMessageFlow,
        group: &Group,
        command: &WebSendCommand,
        decision: &RoutingDecision,
        target: &RoutingTarget,
        sender_name: String,
        sender_owner: Option<String>,
    ) -> ServiceResult<Self> {
        if command.session_id.as_deref().is_none_or(|s| s.is_empty()) {
            return Err(invalid("queue admission requires a canonical session"));
        }
        let is_provider = flow.registry.resolve_delivery_target(&target.bot_uuid).await?.is_http_provider();
        // WS does not consume Provider-only headers. Preserve only explicitly
        // approved routing metadata for a later Bot relay, never credentials.
        let headers: Vec<_> = command.provider_bypass_headers.iter().filter(|(name, _)|
            is_provider || flow.queue_persistable_headers.iter().any(|a| a.eq_ignore_ascii_case(name))).cloned().collect();
        let header_snapshot = bcs_config_api::queued_provider_headers::snapshot(&headers, &flow.queue_persistable_headers);
        let admission_rejection = header_snapshot.as_ref().err().map(|_| bcs_service_api::port::repo::message_delivery::DeliveryAdmissionRejection::ProviderHeadersUnsupported);
        let provider_route_headers = header_snapshot.unwrap_or_default();
        let participant = group
            .participants
            .iter()
            .find(|p| p.bot_uuid == target.bot_uuid)
            .ok_or_else(|| invalid("queue target is not a session participant"))?;
        let snapshot = crate::protocol_context::group_context_input(group);
        Ok(Self {
            caller_execution: crate::caller_execution::ExecutionRef::from_command(command),
            caller_required: false,
            caller_task: String::new(),
            caller_delegation: None,
            caller_lineage: None,
            caller_channel: None,
            caller_dispatch: None,
            caller_terminal: None,
            caller_cancel: None,
            task: None, task_rejected_id: None, task_uncertain_id: None, task_control_notice: false,
            required_context: false,
            drain_context: false,
            policy_version: None,
            provider_route_headers,
            admission_rejection,
            version: 1,
            reply_context: None,
            forward_hop: None,
            driver_bot: snapshot.driver_bot,
            originator: snapshot.originator,
            participants: snapshot
                .participants
                .into_iter()
                .map(|p| ParticipantProjection {
                    id: p.id,
                    name: p.name,
                    role: p.role,
                    is_bot: p.is_bot,
                })
                .collect(),
            direct_bot: context_projection_for_delivery(flow, group, command.session_id.as_deref())
                .await
                == ContextProjection::DirectBot,
            group_type: crate::protocol_context::group_type_wire(group.group_strategy),
            mentions: decision.mentions.clone(),
            target_tags: participant.tags.clone(),
            sender_name,
            sender_owner,
            thinking: command.thinking.clone(),
            text_projection: if group.group_kind == GroupKind::Dm {
                TextProjection::Original
            } else if command.mentions.is_empty() {
                TextProjection::RoutedMentions
            } else {
                TextProjection::ExplicitMentions
            },
        })
    }

    pub(super) fn decode(row: &PersistedMessageDelivery) -> Result<Self, String> {
        let projection: Self = serde_json::from_value(row.semantic_projection_json.clone())
            .map_err(|_| "invalid queued group projection".to_string())?;
        if projection.version != 1 || !matches!(row.flow_kind, DeliveryFlowKind::Group | DeliveryFlowKind::System | DeliveryFlowKind::Task)
            || (row.flow_kind != DeliveryFlowKind::System && projection.required_context)
            || (projection.task.is_some() != (row.flow_kind == DeliveryFlowKind::Task)) {
            return Err("unsupported queued group projection version or flow".into());
        }
        Ok(projection)
    }

    pub(super) fn project_text(&self, text: &str) -> Result<String, String> {
        if let Some(task) = self.task.as_ref().filter(|task| task.leg == crate::queued_task::TaskLeg::Dispatch) {
            return Ok(crate::task_presentation::dispatch(&self.sender_name,
                task.assignment_intent_id.as_deref(), &task.task_id, text));
        }
        let pattern = match self.text_projection {
            TextProjection::Original => return Ok(text.to_string()),
            TextProjection::RoutedMentions => r"@([-\w\p{Unified_Ideograph}:]+)",
            TextProjection::ExplicitMentions => r"@([\w\p{Unified_Ideograph}:]+)",
        };
        let mentions = regex::Regex::new(pattern).map_err(|_| "invalid mention projection")?;
        Ok(mentions.replace_all(text, "$1").into_owned())
    }
}
