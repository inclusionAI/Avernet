//! Legacy create-request validation and V1 spec translation.

use super::*;

pub(crate) fn validate_legacy_opening_message(req: &CreateGroupRequest) -> Result<(), HttpAdapterError> {
    let Some(opening_message) = req.opening_message.as_ref() else {
        return Ok(());
    };
    if req.group_kind.as_deref() == Some("dm") {
        return Err(HttpAdapterError::InvalidOpeningMessage(
            "opening_message is not supported for DM Groups".to_string(),
        ));
    }
    let scope = if req.collaboration_definition_yaml.is_some()
        || req.group_strategy.as_deref() == Some("state_machine")
    {
        OpeningMessageScope::StateMachineRun
    } else {
        OpeningMessageScope::Session
    };
    opening_message
        .validate_for(scope)
        .map_err(|error| HttpAdapterError::InvalidOpeningMessage(error.to_string()))
}

pub(crate) fn legacy_create_group_spec(
    state: &HttpAppState,
    req: &CreateGroupRequest,
) -> Result<CreateGroupSpec, HttpAdapterError> {
    if req.id.is_some() {
        return Err(HttpAdapterError::BadRequest(
            "id cannot be supplied when event_subscriptions are requested".to_string(),
        ));
    }
    if req.service_spec.is_some() {
        return Err(HttpAdapterError::BadRequest(
            "service_spec is not supported with event_subscriptions".to_string(),
        ));
    }

    let group_kind = req
        .group_kind
        .as_deref()
        .map(group_kind_from_str)
        .transpose()?
        .unwrap_or(GroupKind::Normal);
    if group_kind == GroupKind::Dm {
        if req.collaboration_definition_yaml.is_some() {
            return Err(HttpAdapterError::BadRequest(
                "collaboration_definition_yaml is only supported for normal groups".to_string(),
            ));
        }
        let target_actor_id = dm_target_actor_id(req)?.to_string();
        let name = req
            .topic
            .as_ref()
            .map(|topic| format!("DM: {topic}"))
            .or_else(|| req.label.clone());
        return Ok(CreateGroupSpec::DirectMessage(CreateDirectMessageGroup {
            name,
            context: req.context.clone(),
            target_actor_id,
        }));
    }

    let driver_bot = req.driver_bot.clone().ok_or_else(|| {
        HttpAdapterError::BadRequest("driver_bot is required for normal group creation".to_string())
    })?;
    let collaboration_definition = req
        .collaboration_definition_yaml
        .as_deref()
        .map(parse_authoring_collaboration_definition_yaml)
        .transpose()?;
    if let Some(definition) = collaboration_definition.as_ref() {
        reject_judge_definition_when_unavailable(state, definition)?;
    }
    if req.collaboration_definition_yaml.is_some()
        && req
            .group_strategy
            .as_deref()
            .is_some_and(|strategy| strategy != "state_machine")
    {
        return Err(HttpAdapterError::BadRequest(
            "collaboration_definition_yaml requires group_strategy=state_machine".to_string(),
        ));
    }
    if !req.participant_bindings.is_empty() && req.collaboration_definition_yaml.is_none() {
        return Err(HttpAdapterError::BadRequest(
            "participant_bindings requires collaboration_definition_yaml".to_string(),
        ));
    }

    let strategy = if req.collaboration_definition_yaml.is_some() {
        "state_machine"
    } else {
        req.group_strategy.as_deref().unwrap_or("chat")
    };
    let state_machine_group = strategy == "state_machine";
    let participants = group_create_participants(
        req,
        collaboration_definition.as_ref(),
        &driver_bot,
        state_machine_group,
    )?;
    validate_state_machine_runtime_bindings_before_create(
        collaboration_definition.as_ref(),
        &participants,
        &req.participant_bindings,
    )?;
    let participants = participants
        .into_iter()
        .map(|participant| {
            Ok(CreateParticipant {
                role: legacy_participant_role(
                    participant.role.as_deref(),
                    &participant.bot_id,
                    &driver_bot,
                    strategy,
                )?,
                actor_id: participant.bot_id,
                tags: participant.tags,
                message_view_scope: participant.message_view_scope,
            })
        })
        .collect::<Result<Vec<_>, HttpAdapterError>>()?;
    let collaboration = match strategy {
        "manager_worker" => {
            CollaborationConfiguration::ManagerWorker(ManagerWorkerConfiguration::default())
        }
        "state_machine" => {
            if req.auto_start_on_service_invocation == Some(false)
                || req.start_initial_run == Some(false)
            {
                return Err(HttpAdapterError::BadRequest(
                    "state-machine event_subscriptions require automatic initial run startup"
                        .to_string(),
                ));
            }
            let definition_yaml = req.collaboration_definition_yaml.clone().ok_or_else(|| {
                HttpAdapterError::BadRequest(
                    "state-machine event_subscriptions require collaboration_definition_yaml"
                        .to_string(),
                )
            })?;
            CollaborationConfiguration::StateMachine(StateMachineConfiguration {
                definition: StateMachineDefinition::Content(StateMachineDefinitionContent {
                    content_yaml: definition_yaml,
                }),
                participant_bindings: req
                    .participant_bindings
                    .iter()
                    .map(|(binding, value)| StateMachineParticipantBinding {
                        binding: binding.clone(),
                        actor_ids: value.bot_ids.clone(),
                    })
                    .collect(),
            })
        }
        _ => {
            let routing_policy = routing_policy_from_protocol_value(req.routing_policy.clone())?
                .unwrap_or_default();
            CollaborationConfiguration::Chat(ChatConfiguration {
                delivery_policy: GroupDeliveryPolicy {
                    bot_final_delivery: match routing_policy.default_bot_final_delivery {
                        DefaultDelivery::SendToDriver => BotFinalDelivery::SendToDriver,
                        DefaultDelivery::InjectObservers => BotFinalDelivery::InjectObservers,
                    },
                },
            })
        }
    };
    let visibility = match req.visibility.as_deref().unwrap_or("private") {
        "private" => GroupVisibility::Private,
        "public" => GroupVisibility::Public,
        other => {
            return Err(HttpAdapterError::BadRequest(format!(
                "invalid visibility: '{other}'"
            )));
        }
    };
    let name = req
        .topic
        .as_ref()
        .map(|topic| format!("Group: {topic}"))
        .or_else(|| req.label.clone());

    Ok(CreateGroupSpec::Collaboration(CreateCollaborationGroup {
        name,
        context: req.context.clone(),
        opening_message: req.opening_message.clone(),
        visibility,
        driver_bot_uuid: driver_bot,
        participants,
        collaboration,
        originator: req.originator.clone(),
    }))
}

pub(crate) fn legacy_participant_role(
    role: Option<&str>,
    bot_id: &str,
    driver_bot: &str,
    strategy: &str,
) -> Result<V1ParticipantRole, HttpAdapterError> {
    let inferred = if bot_id == driver_bot {
        if strategy == "manager_worker" {
            "manager"
        } else {
            "driver"
        }
    } else if strategy == "manager_worker" {
        "worker"
    } else {
        "consultant"
    };
    match role.unwrap_or(inferred) {
        "driver" => Ok(V1ParticipantRole::Driver),
        "consultant" => Ok(V1ParticipantRole::Consultant),
        "manager" => Ok(V1ParticipantRole::Manager),
        "worker" => Ok(V1ParticipantRole::Worker),
        "observer" => Ok(V1ParticipantRole::Observer),
        other => Err(HttpAdapterError::BadRequest(format!(
            "invalid participant role: '{other}'"
        ))),
    }
}

pub(crate) fn legacy_inline_event_subscription(
    subscription: &InlineGroupEventSubscriptionInfo,
) -> InlineGroupEventSubscriptionRequest {
    InlineGroupEventSubscriptionRequest {
        name: subscription.name.clone(),
        event_filters: subscription.event_filters.clone(),
        payload: EventPayload {
            mode: match subscription.payload.mode {
                InlineEventPayloadMode::MetadataOnly => EventPayloadMode::MetadataOnly,
                InlineEventPayloadMode::Full => EventPayloadMode::Full,
            },
        },
        sink: match &subscription.sink {
            InlineEventSinkInfo::Webhook {
                url,
                request_timeout_ms,
            } => EventSinkInput::Webhook {
                url: url.clone(),
                request_timeout_ms: *request_timeout_ms,
            },
        },
    }
}
