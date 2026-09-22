//! Legacy Group create/provisioning route handling and helpers.

use super::*;

mod definition;
mod participants;
mod response;
mod spec;

pub use definition::*;
pub use participants::*;
pub use response::*;
pub use spec::*;

pub async fn create_group(
    State(state): State<HttpAppState>,
    headers: HeaderMap,
    uri: Uri,
    Json(req): Json<CreateGroupRequest>,
) -> Response {
    // Reject unsupported combinations before definition persistence or event
    // subscription provisioning can create resources.
    if !req.create_initial_session
        && (req.group_kind.as_deref().is_some_and(|kind| kind != "normal")
            || !matches!(
                req.group_strategy.as_deref(),
                None | Some("chat" | "manager_worker" | "state_machine")
            )
            || !req.event_subscriptions.is_empty())
    {
        return HttpAdapterError::BadRequest(
            "create_initial_session=false supports only normal groups without event_subscriptions"
                .to_string(),
        )
        .into_response();
    }
    if req.event_subscriptions.is_empty() {
        return create_group_without_inline_subscriptions(state, headers, uri, req)
            .await
            .into_response();
    }

    create_group_with_inline_subscriptions(state, headers, uri, req).await
}

pub(crate) async fn create_group_without_inline_subscriptions(
    state: HttpAppState,
    headers: HeaderMap,
    uri: Uri,
    req: CreateGroupRequest,
) -> Result<Json<Value>, HttpAdapterError> {
    let start_initial_run = req.create_initial_session && req.start_initial_run.unwrap_or(true);
    let caller_actor_id = resolve_group_create_caller(&state, &headers, &uri).await?;
    validate_legacy_opening_message(&req)?;
    let collaboration_definition_yaml = req.collaboration_definition_yaml.clone();
    let auto_start_on_service_invocation = req.auto_start_on_service_invocation.unwrap_or(false);
    let group_kind = req
        .group_kind
        .as_deref()
        .map(group_kind_from_str)
        .transpose()?;
    if collaboration_definition_yaml.is_some() && group_kind == Some(GroupKind::Dm) {
        return Err(HttpAdapterError::BadRequest(
            "collaboration_definition_yaml is only supported for normal groups".to_string(),
        ));
    }
    if collaboration_definition_yaml.is_some() {
        if let Some(strategy) = req.group_strategy.as_deref() {
            if strategy != "state_machine" {
                return Err(HttpAdapterError::BadRequest(
                    "collaboration_definition_yaml requires group_strategy=state_machine"
                        .to_string(),
                ));
            }
        }
    }
    if !req.participant_bindings.is_empty() && collaboration_definition_yaml.is_none() {
        return Err(HttpAdapterError::BadRequest(
            "participant_bindings requires collaboration_definition_yaml".to_string(),
        ));
    }
    let collaboration_definition =
        if let Some(definition_yaml) = collaboration_definition_yaml.as_deref() {
            let definition = parse_authoring_collaboration_definition_yaml(definition_yaml)?;
            reject_judge_definition_when_unavailable(&state, &definition)?;
            Some(definition)
        } else {
            None
        };
    if group_kind == Some(GroupKind::Dm) {
        let target_actor_id = dm_target_actor_id(&req)?.to_string();

        let result = state
            .services
            .group_management
            .create_dm(DmCreateCommand {
                group_id: req.id,
                caller_actor_id,
                driver_bot: req.driver_bot.clone(),
                target_actor_id,
                label: req.label,
                topic: req.topic,
                context: req.context,
                provisioning: false,
            })
            .await
            .map_err(group_use_case_error_to_http)?;
        let mut detail = result.group;
        detail.chat_url = state.botchat_url.as_ref().map(|base| {
            build_group_chat_url(
                base,
                &detail.group_id,
                &detail.driver_bot_id,
                detail.latest_running_session_id.as_deref(),
            )
        });

        return Ok(Json(group_detail_to_create_json(detail, result.created)));
    }

    let driver_bot = req.driver_bot.clone().ok_or_else(|| {
        HttpAdapterError::BadRequest("driver_bot is required for normal group creation".to_string())
    })?;
    let originator = req.originator.clone().unwrap_or_else(|| driver_bot.clone());
    let state_machine_group = collaboration_definition_yaml.is_some()
        || req.group_strategy.as_deref() == Some("state_machine");
    let participants = group_create_participants(
        &req,
        collaboration_definition.as_ref(),
        &driver_bot,
        state_machine_group,
    )?;
    let member_bot_ids = participants
        .iter()
        .map(|participant| participant.bot_id.clone())
        .collect::<Vec<_>>();
    let group_strategy = if collaboration_definition_yaml.is_some() {
        Some(bcs_service_api::GroupStrategy::StateMachine)
    } else {
        req.group_strategy.as_deref().map(|s| match s {
            "manager_worker" => bcs_service_api::GroupStrategy::ManagerWorker,
            "state_machine" => bcs_service_api::GroupStrategy::StateMachine,
            _ => bcs_service_api::GroupStrategy::Chat,
        })
    };
    let participant_bindings = runtime_participant_bindings_from_request(&req);
    validate_state_machine_runtime_bindings_before_create(
        collaboration_definition.as_ref(),
        &participants,
        &req.participant_bindings,
    )?;
    let collaboration_definition_ref = if let (Some(definition), Some(source_yaml)) = (
        collaboration_definition.as_ref(),
        collaboration_definition_yaml.clone(),
    ) {
        let definition_ref = CollaborationDefinitionRef {
            id: definition.id.clone(),
            version: definition.version,
        };
        state
            .services
            .collaboration_runtime
            .upsert_definition_with_source_yaml(definition.clone(), source_yaml)
            .await
            .map_err(collaboration_runtime_error_to_http)?;
        Some(definition_ref)
    } else {
        None
    };
    let cmd = GroupCreateCommand {
        create_initial_session: req.create_initial_session,
        group_id: req.id,
        caller_actor_id: caller_actor_id.clone(),
        driver_bot_id: driver_bot,
        originator: Some(originator),
        label: req.label,
        topic: req.topic,
        context: req.context,
        opening_message: req.opening_message,
        routing_policy: routing_policy_from_protocol_value(req.routing_policy)?,
        participants,
        member_bot_ids,
        group_kind,
        service_spec: req
            .service_spec
            .clone()
            .and_then(|v| serde_json::from_value(v).ok()),
        group_strategy,
        visibility: req.visibility,
        provisioning: false,
    };
    let mut result = state
        .services
        .group_management
        .create_group(cmd)
        .await
        .map_err(group_use_case_error_to_http)?;

    if let Some(definition_ref) = collaboration_definition_ref {
        state
            .services
            .collaboration_runtime
            .configure_group_runtime(ConfigureGroupRuntimeCommand {
                group_id: result.group_id.clone(),
                definition_yaml: None,
                definition: None,
                definition_ref: Some(definition_ref),
                participant_bindings,
                auto_start_on_service_invocation,
            })
            .await
            .map_err(collaboration_runtime_error_to_http)?;
        if start_initial_run
            && result.group_strategy == bcs_service_api::GroupStrategy::StateMachine
        {
            let authenticated_human = optional_authenticated_human(&state, &headers, &uri).await;
            if let Some(run_id) = start_initial_state_machine_run_for_group(
                &state,
                &result,
                caller_actor_id.clone(),
                authenticated_human,
            )
            .await?
            {
                tracing::info!(
                    group_id = %result.group_id,
                    run_id = %run_id,
                    "started default state-machine run for new group"
                );
            }
        }
    }

    result.chat_url = state.botchat_url.as_ref().map(|base| {
        build_group_chat_url(
            base,
            &result.group_id,
            &result.driver_bot_id,
            result.latest_running_session_id.as_deref(),
        )
    });

    Ok(Json(group_detail_to_create_json(result, true)))
}

pub(crate) async fn create_group_with_inline_subscriptions(
    state: HttpAppState,
    headers: HeaderMap,
    uri: Uri,
    req: CreateGroupRequest,
) -> Response {
    let caller = match resolve_group_chat_caller(&state, &headers, &uri).await {
        Ok(caller) => caller,
        Err(error) => return error.into_response(),
    };
    if let GroupChatCaller::Bot { bot_uuid } = &caller
        && let Err(error) = validate_container_header(&state, &headers, bot_uuid)
    {
        return error.into_response();
    }
    let caller = application_caller(&caller);
    if let Err(error) = validate_legacy_opening_message(&req) {
        return error.into_response();
    }
    let Some(application) = state.group_application.as_ref() else {
        return legacy_group_error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "eventing_disabled",
            "Group Event Subscription provisioning is unavailable",
        );
    };
    let group = match legacy_create_group_spec(&state, &req) {
        Ok(group) => group,
        Err(error) => return error.into_response(),
    };
    let event_subscriptions = req
        .event_subscriptions
        .iter()
        .map(legacy_inline_event_subscription)
        .collect::<Vec<_>>();

    match application
        .create_with_event_subscriptions(V1CreateGroup { caller, group }, event_subscriptions)
        .await
    {
        Ok(outcome) => {
            let group_id = v1_group_id(&outcome.group).to_string();
            let session_id = match state
                .services
                .session_management
                .list_by_group(&group_id, None, 0, 1, None, None)
                .await
            {
                Ok(sessions) => sessions.into_iter().next().map(|session| session.id),
                Err(error) => {
                    tracing::warn!(
                        request_id = %bcs_observability::CurrentRequestId,
                        group_id = %group_id,
                        error = %error,
                        "failed to load initial Session for legacy Group create response"
                    );
                    None
                }
            };
            Json(v1_group_detail_to_legacy_create_json(
                outcome.group,
                outcome.created,
                outcome.event_subscriptions,
                session_id,
                state.botchat_url.as_deref(),
            ))
            .into_response()
        }
        Err(error) => group_application_error_response(error),
    }
}
